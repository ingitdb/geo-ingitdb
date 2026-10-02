// Command wb-import downloads country population from the World Bank and
// (re)writes the inGitDB record files for the population_wb collection.
//
// Source: World Bank Open Data indicator SP.POP.TOTL ("Population, total"),
// API v2 — CC BY 4.0. The latest non-empty observation of each country is kept,
// and World Bank aggregates (regions, income groups, "World", ...) are dropped:
// a row survives only when its ISO 3166-1 alpha-3 code belongs to a record of
// the countries collection, so the country foreign key always resolves.
//
// Like geo-import, the program owns only the collection's `$records/`
// directory: it clears and regenerates it, leaving the hand-authored
// `.collection/definition.yaml` schema untouched, and it registers the
// collection in `.ingitdb/root-collections.yaml` when missing. Git history of
// the generated snapshot is its provenance: every record also carries the
// indicator, the per-country source URL and the fetch time.
//
// Downloads are cached (default: ./.cache/wb-import) so repeated runs are
// offline and fast; pass -refresh to re-fetch. Run geo-import first so the
// countries collection exists.
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	worldBankBase    = "https://api.worldbank.org/v2"
	defaultIndicator = "SP.POP.TOTL"
	collectionName   = "population_wb"
)

// Seams over the process and the file system, replaced by tests so that every
// failure path is reachable without a hostile environment.
var (
	osExit       = os.Exit
	marshalJSON  = marshalRecord
	removeAll    = os.RemoveAll
	mkdirAll     = os.MkdirAll
	writeFile    = os.WriteFile
	readFile     = os.ReadFile
	nowUTC       = func() time.Time { return time.Now().UTC() }
	newTransport = func() fetcher { return &httpFetcher{client: &http.Client{Timeout: 120 * time.Second}} }
)

func main() {
	osExit(realMain(os.Args[1:], os.Stdout, os.Stderr))
}

// realMain parses flags and runs the import, returning the process exit code.
func realMain(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("wb-import", flag.ContinueOnError)
	fs.SetOutput(stderr)
	var (
		out       = fs.String("out", ".", "database root (holds .ingitdb/ and the collection dirs)")
		cacheDir  = fs.String("cache", ".cache/wb-import", "download cache directory")
		indicator = fs.String("indicator", defaultIndicator, "World Bank indicator code")
		perPage   = fs.Int("per-page", 500, "records requested per API page")
		refresh   = fs.Bool("refresh", false, "re-download sources even if cached")
	)
	if err := fs.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0
		}
		return 2
	}
	if *perPage < 1 {
		_, _ = fmt.Fprintln(stderr, "wb-import: -per-page must be at least 1")
		return 2
	}
	src := &cachedFetcher{dir: *cacheDir, refresh: *refresh, inner: newTransport(), log: stdout}
	if err := run(*out, src, importOptions{Indicator: *indicator, PerPage: *perPage, FetchedAt: nowUTC()}, stdout); err != nil {
		_, _ = fmt.Fprintln(stderr, "wb-import:", err)
		return 1
	}
	return 0
}

type importOptions struct {
	Indicator string
	PerPage   int
	FetchedAt time.Time
}

func run(out string, src fetcher, opts importOptions, stdout io.Writer) error {
	iso2ByISO3, err := loadCountryISO3(filepath.Join(out, "countries", "$records"))
	if err != nil {
		return err
	}
	obs, meta, err := fetchIndicator(src, opts.Indicator, opts.PerPage)
	if err != nil {
		return err
	}
	rows, dropped := latestPerCountry(obs, iso2ByISO3)
	if len(rows) == 0 {
		return fmt.Errorf("indicator %s: no observation matches a country in the countries collection", opts.Indicator)
	}
	records := populationRecords(rows, opts, meta)
	if err := writeRecords(filepath.Join(out, collectionName), records); err != nil {
		return err
	}
	if err := registerCollection(filepath.Join(out, ".ingitdb", "root-collections.yaml"), collectionName); err != nil {
		return err
	}
	_, _ = fmt.Fprintf(stdout, "%s: %d (dropped %d aggregates/unknown codes; source last updated %s)\n",
		collectionName, len(rows), dropped, meta.LastUpdated)
	return nil
}

// ---- Countries --------------------------------------------------------------

// loadCountryISO3 maps ISO 3166-1 alpha-3 codes to the record keys of the
// countries collection in dir.
func loadCountryISO3(dir string) (map[string]string, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, fmt.Errorf("countries collection: %w (run geo-import first)", err)
	}
	out := make(map[string]string, len(entries))
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || !strings.HasSuffix(name, ".json") {
			continue
		}
		data, err := readFile(filepath.Join(dir, name))
		if err != nil {
			return nil, err
		}
		var c struct {
			ISO3 string `json:"iso3"`
		}
		if err := json.Unmarshal(data, &c); err != nil {
			return nil, fmt.Errorf("%s: %w", name, err)
		}
		if c.ISO3 != "" {
			out[c.ISO3] = strings.TrimSuffix(name, ".json")
		}
	}
	return out, nil
}

// ---- World Bank API ---------------------------------------------------------

// pageMeta is the first element of every World Bank API v2 JSON response.
type pageMeta struct {
	Page        int    `json:"page"`
	Pages       int    `json:"pages"`
	LastUpdated string `json:"lastupdated"`
}

// observation is one indicator value; Value is nil when the year is empty.
type observation struct {
	Country struct {
		ID string `json:"id"` // World Bank's own code, unique even for aggregates without an ISO3
	} `json:"country"`
	ISO3  string `json:"countryiso3code"`
	Date  string `json:"date"`
	Value *int64 `json:"value"`
}

// fetchIndicator returns every observation of the indicator, following
// pagination. mrnev=1 asks the API for the most recent non-empty value of each
// country; latestPerCountry still selects the newest year client-side, so the
// result stays correct if the API ever returns several years per country.
func fetchIndicator(src fetcher, indicator string, perPage int) ([]observation, pageMeta, error) {
	var (
		all  []observation
		meta pageMeta
	)
	for page := 1; ; page++ {
		data, err := src.Get(indicatorURL(indicator, perPage, page))
		if err != nil {
			return nil, meta, err
		}
		m, obs, err := decodePage(data)
		if err != nil {
			return nil, meta, fmt.Errorf("indicator %s page %d: %w", indicator, page, err)
		}
		all = append(all, obs...)
		meta = m
		if page >= m.Pages {
			return all, meta, nil
		}
	}
}

func indicatorURL(indicator string, perPage, page int) string {
	q := url.Values{}
	q.Set("format", "json")
	q.Set("mrnev", "1")
	q.Set("per_page", strconv.Itoa(perPage))
	q.Set("page", strconv.Itoa(page))
	return worldBankBase + "/country/all/indicator/" + url.PathEscape(indicator) + "?" + q.Encode()
}

// decodePage parses `[meta, [observation...]]`. The API reports problems such
// as an unknown indicator as a one-element array holding a message list.
func decodePage(data []byte) (pageMeta, []observation, error) {
	var parts []json.RawMessage
	if err := json.Unmarshal(data, &parts); err != nil {
		return pageMeta{}, nil, err
	}
	if len(parts) < 2 {
		return pageMeta{}, nil, fmt.Errorf("API returned an error payload: %s", truncate(string(data), 200))
	}
	var meta pageMeta
	if err := json.Unmarshal(parts[0], &meta); err != nil {
		return pageMeta{}, nil, err
	}
	var obs []observation
	if err := json.Unmarshal(parts[1], &obs); err != nil {
		return pageMeta{}, nil, err
	}
	return meta, obs, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

// ---- Selection --------------------------------------------------------------

// row is the newest non-empty observation of one country.
type row struct {
	CountryKey, ISO3 string
	Year             int
	Population       int64
}

// latestPerCountry keeps, per ISO3 code present in iso2ByISO3, the observation
// with the highest year and a value. dropped counts distinct World Bank entities that were
// discarded: aggregates and codes missing from the countries collection.
func latestPerCountry(obs []observation, iso2ByISO3 map[string]string) (rows []row, dropped int) {
	best := map[string]row{}
	skipped := map[string]bool{}
	for _, o := range obs {
		iso2, ok := iso2ByISO3[o.ISO3]
		if !ok {
			skipped[o.Country.ID] = true
			continue
		}
		year, err := strconv.Atoi(o.Date)
		if err != nil || o.Value == nil {
			continue
		}
		if cur, seen := best[o.ISO3]; !seen || year > cur.Year {
			best[o.ISO3] = row{CountryKey: iso2, ISO3: o.ISO3, Year: year, Population: *o.Value}
		}
	}
	for _, r := range best {
		rows = append(rows, r)
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].CountryKey < rows[j].CountryKey })
	return rows, len(skipped)
}

// ---- Records ----------------------------------------------------------------

// record is one keyed record destined for <collection>/$records/<key>.json.
type record struct {
	Key  string
	Data map[string]any
}

func populationRecords(rows []row, opts importOptions, meta pageMeta) []record {
	fetchedAt := opts.FetchedAt.UTC().Format(time.RFC3339)
	out := make([]record, 0, len(rows))
	for _, r := range rows {
		d := map[string]any{
			"country":    r.CountryKey,
			"iso3":       r.ISO3,
			"year":       r.Year,
			"population": r.Population,
			"indicator":  opts.Indicator,
			"source_url": sourceURL(opts.Indicator, r.ISO3),
			"fetched_at": fetchedAt,
		}
		if meta.LastUpdated != "" {
			d["source_updated"] = meta.LastUpdated
		}
		out = append(out, record{Key: r.CountryKey, Data: d})
	}
	return out
}

// sourceURL is the API call that returns this country's observation.
func sourceURL(indicator, iso3 string) string {
	return worldBankBase + "/country/" + url.PathEscape(iso3) + "/indicator/" + url.PathEscape(indicator) + "?format=json&mrnev=1"
}

// writeRecords clears <collectionDir>/$records and writes one JSON file per
// record. Clearing first makes the run idempotent.
func writeRecords(collectionDir string, records []record) error {
	recDir := filepath.Join(collectionDir, "$records")
	if err := removeAll(recDir); err != nil {
		return err
	}
	if err := mkdirAll(recDir, 0o755); err != nil {
		return err
	}
	for _, r := range records {
		buf, err := marshalJSON(r.Data)
		if err != nil {
			return fmt.Errorf("marshal %s: %w", r.Key, err)
		}
		if err := writeFile(filepath.Join(recDir, r.Key+".json"), buf, 0o644); err != nil {
			return err
		}
	}
	return nil
}

// marshalRecord renders indented JSON with a trailing newline. HTML escaping is
// off so URLs keep a literal "&" instead of "\u0026".
func marshalRecord(v any) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// registerCollection appends "<name>: <name>" to the root collections file
// unless the collection is already listed.
func registerCollection(path, name string) error {
	data, err := readFile(path)
	if err != nil {
		return err
	}
	for _, line := range strings.Split(string(data), "\n") {
		if strings.HasPrefix(line, name+":") {
			return nil
		}
	}
	text := string(data)
	if text != "" && !strings.HasSuffix(text, "\n") {
		text += "\n"
	}
	return writeFile(path, []byte(text+name+": "+name+"\n"), 0o644)
}

// ---- Download cache ---------------------------------------------------------

// fetcher returns the body of a URL. Production code uses httpFetcher; tests
// use a fake, so nothing in the test suite touches the network.
type fetcher interface {
	Get(url string) ([]byte, error)
}

type httpFetcher struct {
	client *http.Client
}

func (h *httpFetcher) Get(url string) ([]byte, error) {
	resp, err := h.client.Get(url)
	if err != nil {
		return nil, err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("GET %s: %s", url, resp.Status)
	}
	return io.ReadAll(resp.Body)
}

// cachedFetcher serves a URL from dir when cached and otherwise delegates to
// inner and stores the answer; refresh bypasses the cache read.
type cachedFetcher struct {
	dir     string
	refresh bool
	inner   fetcher
	log     io.Writer
}

func (c *cachedFetcher) Get(u string) ([]byte, error) {
	sum := sha256.Sum256([]byte(u))
	cachePath := filepath.Join(c.dir, "wb-"+hex.EncodeToString(sum[:8])+".json")
	if !c.refresh {
		if data, err := readFile(cachePath); err == nil {
			return data, nil
		}
	}
	_, _ = fmt.Fprintf(c.log, "downloading %s\n", u)
	data, err := c.inner.Get(u)
	if err != nil {
		return nil, err
	}
	if err := mkdirAll(c.dir, 0o755); err != nil {
		return nil, err
	}
	if err := writeFile(cachePath, data, 0o644); err != nil {
		return nil, err
	}
	return data, nil
}
