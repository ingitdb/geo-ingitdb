// Command geo-import downloads geographic reference data from open sources and
// (re)writes the inGitDB record files for the countries, subdivisions, and
// settlements collections.
//
// Sources:
//   - GeoNames countryInfo.txt          (countries)          — CC BY 4.0
//   - GeoNames admin1CodesASCII.txt      (first-order admin)  — CC BY 4.0
//   - GeoNames cities15000.zip           (settlements)        — CC BY 4.0
//
// The program owns only the `$records/` directories: it clears and regenerates
// them, leaving the hand-authored `.collection/definition.yaml` schemas and
// `.ingitdb/` config untouched. Running it again refreshes the data.
//
// Downloads are cached (default: ./.cache/geo-import) so repeated runs are
// offline and fast; pass -refresh to re-fetch.
package main

import (
	"archive/zip"
	"bufio"
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

const geonamesBase = "https://download.geonames.org/export/dump/"

func main() {
	var (
		out         = flag.String("out", ".", "database root (holds .ingitdb/ and the collection dirs)")
		cacheDir    = flag.String("cache", ".cache/geo-import", "download cache directory")
		showcaseCSV = flag.String("settlements", "GB,IE,US,DE", "comma-separated ISO-2 codes to import settlements for (empty = none)")
		refresh     = flag.Bool("refresh", false, "re-download sources even if cached")
	)
	flag.Parse()

	showcase := parseShowcase(*showcaseCSV)
	dl := &downloader{cacheDir: *cacheDir, refresh: *refresh, client: &http.Client{Timeout: 120 * time.Second}}

	if err := run(*out, dl, showcase); err != nil {
		fmt.Fprintln(os.Stderr, "geo-import:", err)
		os.Exit(1)
	}
}

func run(out string, dl *downloader, showcase map[string]bool) error {
	// Countries.
	countryData, err := dl.get("countryInfo.txt", geonamesBase+"countryInfo.txt")
	if err != nil {
		return err
	}
	countries, err := parseCountries(countryData)
	if err != nil {
		return err
	}
	validISO := make(map[string]bool, len(countries))
	for _, c := range countries {
		validISO[c.ISO2] = true
	}
	if err := writeRecords(filepath.Join(out, "countries"), countryRecords(countries)); err != nil {
		return err
	}
	fmt.Printf("countries:    %d\n", len(countries))

	// Subdivisions (admin-1). Skip any whose country is not in the country set,
	// so the required country FK always resolves.
	adminData, err := dl.get("admin1CodesASCII.txt", geonamesBase+"admin1CodesASCII.txt")
	if err != nil {
		return err
	}
	subs, err := parseSubdivisions(adminData, validISO)
	if err != nil {
		return err
	}
	subKey := make(map[string]bool, len(subs))
	for _, s := range subs {
		subKey[s.Key()] = true
	}
	if err := writeRecords(filepath.Join(out, "subdivisions"), subdivisionRecords(subs)); err != nil {
		return err
	}
	fmt.Printf("subdivisions: %d\n", len(subs))

	// Settlements (cities15000), showcase countries only.
	settlements := []Settlement(nil)
	if len(showcase) > 0 {
		cityData, err := dl.getZipped("cities15000.zip", geonamesBase+"cities15000.zip", "cities15000.txt")
		if err != nil {
			return err
		}
		settlements, err = parseSettlements(cityData, showcase, subKey)
		if err != nil {
			return err
		}
	}
	if err := writeRecords(filepath.Join(out, "settlements"), settlementRecords(settlements)); err != nil {
		return err
	}
	fmt.Printf("settlements:  %d (showcase: %s)\n", len(settlements), showcaseList(showcase))

	return nil
}

// ---- Sources ----------------------------------------------------------------

type Country struct {
	ISO2, ISO3, Continent, Capital         string
	Population                             int
	AreaKm2                                float64
	CurrencyCode, CurrencyName, Phone, TLD string
	Name                                   string
	GeonameID                              int
}

// parseCountries reads GeoNames countryInfo.txt (tab-separated, '#' comments).
// Columns: 0 ISO 1 ISO3 2 ISO-Numeric 3 fips 4 Country 5 Capital 6 Area(km2)
// 7 Population 8 Continent 9 tld 10 Currency 11 CurrencyName 12 Phone
// 13 PostalFmt 14 PostalRegex 15 Languages 16 geonameid ...
func parseCountries(data []byte) ([]Country, error) {
	var out []Country
	sc := bufio.NewScanner(bytes.NewReader(data))
	sc.Buffer(make([]byte, 1024*1024), 1024*1024)
	for sc.Scan() {
		line := sc.Text()
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		f := strings.Split(line, "\t")
		if len(f) < 17 || f[0] == "" {
			continue
		}
		out = append(out, Country{
			ISO2:         f[0],
			ISO3:         f[1],
			Name:         f[4],
			Capital:      f[5],
			AreaKm2:      atofOr0(f[6]),
			Population:   atoiOr0(f[7]),
			Continent:    f[8],
			TLD:          f[9],
			CurrencyCode: f[10],
			CurrencyName: f[11],
			Phone:        f[12],
			GeonameID:    atoiOr0(f[16]),
		})
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ISO2 < out[j].ISO2 })
	return out, nil
}

type Subdivision struct {
	ISO2, Admin1Code, Name string
	GeonameID              int
}

// Key is the record key: "<iso2>-<admin1code>" lowercased.
func (s Subdivision) Key() string { return slug(s.ISO2 + "-" + s.Admin1Code) }

// parseSubdivisions reads admin1CodesASCII.txt. Columns: 0 code("US.CA")
// 1 name 2 asciiname 3 geonameid. Entries whose country is not in validISO are
// dropped so the country FK always resolves.
func parseSubdivisions(data []byte, validISO map[string]bool) ([]Subdivision, error) {
	var out []Subdivision
	sc := bufio.NewScanner(bytes.NewReader(data))
	sc.Buffer(make([]byte, 1024*1024), 1024*1024)
	for sc.Scan() {
		f := strings.Split(sc.Text(), "\t")
		if len(f) < 2 {
			continue
		}
		code := strings.SplitN(f[0], ".", 2)
		if len(code) != 2 || code[1] == "" {
			continue
		}
		iso2 := code[0]
		if !validISO[iso2] {
			continue
		}
		out = append(out, Subdivision{ISO2: iso2, Admin1Code: code[1], Name: f[1], GeonameID: atoiOr0(f[3])})
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Key() < out[j].Key() })
	return out, nil
}

type Settlement struct {
	Name, ISO2, Admin1Code string
	Population             int
	Lat, Lon               float64
	GeonameID              int
	SubdivisionKey         string // "" when no matching subdivision exists
}

func (s Settlement) Key() string { return slug(s.Name) + "-" + strconv.Itoa(s.GeonameID) }

// parseSettlements reads cities15000.txt. Columns: 0 geonameid 1 name 2 ascii
// 3 alt 4 lat 5 lon 6 fclass 7 fcode 8 country 9 cc2 10 admin1 11 admin2 ...
// 14 population ... Only rows whose country is in showcase are kept. The
// subdivision FK is set only when the derived subdivision key exists, so the
// optional FK never dangles.
func parseSettlements(data []byte, showcase, subKey map[string]bool) ([]Settlement, error) {
	var out []Settlement
	sc := bufio.NewScanner(bytes.NewReader(data))
	sc.Buffer(make([]byte, 4*1024*1024), 4*1024*1024)
	for sc.Scan() {
		f := strings.Split(sc.Text(), "\t")
		if len(f) < 15 {
			continue
		}
		iso2 := f[8]
		if !showcase[iso2] {
			continue
		}
		s := Settlement{
			Name:       f[1],
			ISO2:       iso2,
			Admin1Code: f[10],
			Lat:        atofOr0(f[4]),
			Lon:        atofOr0(f[5]),
			Population: atoiOr0(f[14]),
			GeonameID:  atoiOr0(f[0]),
		}
		if k := slug(iso2 + "-" + f[10]); f[10] != "" && subKey[k] {
			s.SubdivisionKey = k
		}
		out = append(out, s)
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Key() < out[j].Key() })
	return out, nil
}

// ---- Records ----------------------------------------------------------------

// record is one keyed record destined for <collection>/$records/<key>.json.
type record struct {
	Key  string
	Data map[string]any
}

func countryRecords(cs []Country) []record {
	out := make([]record, 0, len(cs))
	for _, c := range cs {
		d := map[string]any{
			"names": map[string]string{"en": c.Name},
			"iso2":  c.ISO2,
		}
		putStr(d, "iso3", c.ISO3)
		putStr(d, "continent", c.Continent)
		putStr(d, "capital", c.Capital)
		putInt(d, "population", c.Population)
		putFloat(d, "area_km2", c.AreaKm2)
		putStr(d, "currency_code", c.CurrencyCode)
		putStr(d, "currency_name", c.CurrencyName)
		putStr(d, "phone_prefix", c.Phone)
		putStr(d, "tld", c.TLD)
		putInt(d, "geonameid", c.GeonameID)
		out = append(out, record{Key: slug(c.ISO2), Data: d})
	}
	return out
}

func subdivisionRecords(ss []Subdivision) []record {
	out := make([]record, 0, len(ss))
	for _, s := range ss {
		d := map[string]any{
			"names":       map[string]string{"en": s.Name},
			"country":     slug(s.ISO2),
			"admin1_code": s.Admin1Code,
		}
		putInt(d, "geonameid", s.GeonameID)
		out = append(out, record{Key: s.Key(), Data: d})
	}
	return out
}

func settlementRecords(ss []Settlement) []record {
	out := make([]record, 0, len(ss))
	for _, s := range ss {
		d := map[string]any{
			"names":   map[string]string{"en": s.Name},
			"country": slug(s.ISO2),
		}
		putStr(d, "subdivision", s.SubdivisionKey)
		putInt(d, "population", s.Population)
		putFloat(d, "latitude", s.Lat)
		putFloat(d, "longitude", s.Lon)
		putInt(d, "geonameid", s.GeonameID)
		out = append(out, record{Key: s.Key(), Data: d})
	}
	return out
}

// writeRecords clears <collectionDir>/$records and writes one JSON file per
// record. Clearing first makes the run idempotent: a settlement dropped from the
// source is dropped from the DB.
func writeRecords(collectionDir string, records []record) error {
	recDir := filepath.Join(collectionDir, "$records")
	if err := os.RemoveAll(recDir); err != nil {
		return err
	}
	if err := os.MkdirAll(recDir, 0o755); err != nil {
		return err
	}
	for _, r := range records {
		buf, err := json.MarshalIndent(r.Data, "", "  ")
		if err != nil {
			return fmt.Errorf("marshal %s: %w", r.Key, err)
		}
		buf = append(buf, '\n')
		if err := os.WriteFile(filepath.Join(recDir, r.Key+".json"), buf, 0o644); err != nil {
			return err
		}
	}
	return nil
}

// ---- Download cache ---------------------------------------------------------

type downloader struct {
	cacheDir string
	refresh  bool
	client   *http.Client
}

func (d *downloader) get(name, url string) ([]byte, error) {
	cachePath := filepath.Join(d.cacheDir, name)
	if !d.refresh {
		if data, err := os.ReadFile(cachePath); err == nil {
			return data, nil
		}
	}
	fmt.Printf("downloading %s\n", url)
	resp, err := d.client.Get(url)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("GET %s: %s", url, resp.Status)
	}
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(d.cacheDir, 0o755); err != nil {
		return nil, err
	}
	if err := os.WriteFile(cachePath, data, 0o644); err != nil {
		return nil, err
	}
	return data, nil
}

// getZipped downloads a .zip (cached) and returns the named entry's bytes.
func (d *downloader) getZipped(zipName, url, entry string) ([]byte, error) {
	raw, err := d.get(zipName, url)
	if err != nil {
		return nil, err
	}
	zr, err := zip.NewReader(bytes.NewReader(raw), int64(len(raw)))
	if err != nil {
		return nil, err
	}
	for _, f := range zr.File {
		if f.Name == entry {
			rc, err := f.Open()
			if err != nil {
				return nil, err
			}
			defer rc.Close()
			return io.ReadAll(rc)
		}
	}
	return nil, fmt.Errorf("%s not found in %s", entry, zipName)
}

// ---- helpers ----------------------------------------------------------------

var slugRe = regexp.MustCompile(`[^a-z0-9]+`)

// slug lowercases and replaces runs of non-alphanumerics with a single dash.
// NEVER produces a "/", which inGitDB reads as a nested path (ingitdb-go#1).
func slug(s string) string {
	s = strings.ToLower(strings.TrimSpace(s))
	s = slugRe.ReplaceAllString(s, "-")
	return strings.Trim(s, "-")
}

func parseShowcase(csv string) map[string]bool {
	out := map[string]bool{}
	for _, p := range strings.Split(csv, ",") {
		if p = strings.TrimSpace(strings.ToUpper(p)); p != "" {
			out[p] = true
		}
	}
	return out
}

func showcaseList(m map[string]bool) string {
	if len(m) == 0 {
		return "none"
	}
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return strings.Join(ks, ",")
}

func atoiOr0(s string) int {
	n, _ := strconv.Atoi(strings.TrimSpace(s))
	return n
}

func atofOr0(s string) float64 {
	f, _ := strconv.ParseFloat(strings.TrimSpace(s), 64)
	return f
}

func putStr(d map[string]any, k, v string) {
	if v != "" {
		d[k] = v
	}
}

func putInt(d map[string]any, k string, v int) {
	if v != 0 {
		d[k] = v
	}
}

func putFloat(d map[string]any, k string, v float64) {
	if v != 0 {
		d[k] = v
	}
}
