package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// fakeFetcher serves canned bodies by URL and records every request, so tests
// never touch the network.
type fakeFetcher struct {
	bodies map[string]string
	err    error
	calls  []string
}

func (f *fakeFetcher) Get(u string) ([]byte, error) {
	f.calls = append(f.calls, u)
	if f.err != nil {
		return nil, f.err
	}
	body, ok := f.bodies[u]
	if !ok {
		return nil, fmt.Errorf("unexpected URL %s", u)
	}
	return []byte(body), nil
}

func writeFixture(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

// newDB builds a database root with a countries collection (IE, US, and AQ
// without an ISO3 code) and a root collections file.
func newDB(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	rec := filepath.Join(root, "countries", "$records")
	writeFixture(t, filepath.Join(rec, "ie.json"), `{"iso2":"IE","iso3":"IRL"}`)
	writeFixture(t, filepath.Join(rec, "us.json"), `{"iso2":"US","iso3":"USA"}`)
	writeFixture(t, filepath.Join(rec, "aq.json"), `{"iso2":"AQ"}`)
	writeFixture(t, filepath.Join(rec, "notes.txt"), `ignored`)
	if err := os.MkdirAll(filepath.Join(rec, "dir.json"), 0o755); err != nil {
		t.Fatal(err)
	}
	writeFixture(t, filepath.Join(root, ".ingitdb", "root-collections.yaml"), "countries: countries\n")
	return root
}

func page(meta string, obs ...string) string {
	return "[" + meta + ",[" + strings.Join(obs, ",") + "]]"
}

func ob(iso3, date, value string) string {
	return fmt.Sprintf(`{"country":{"id":%q},"countryiso3code":%q,"date":%q,"value":%s}`, iso3, iso3, date, value)
}

func twoPageFetcher(perPage int) *fakeFetcher {
	return &fakeFetcher{bodies: map[string]string{
		indicatorURL(defaultIndicator, perPage, 1): page(`{"page":1,"pages":2,"lastupdated":"2026-07-13"}`,
			ob("AFE", "2025", "788844284"), // aggregate
			ob("IRL", "2024", "5400000"),
			ob("IRL", "2025", "5484367"), // newer year wins
		),
		indicatorURL(defaultIndicator, perPage, 2): page(`{"page":2,"pages":2,"lastupdated":"2026-07-13"}`,
			ob("USA", "2025", "341784857"),
			ob("USA", "2026", "null"), // empty value ignored
			ob("IRL", "bad", "1"),     // unparsable year ignored
			ob("AFE", "2024", "1"),    // same aggregate again: counted once
			`{"country":{"id":"XD"},"countryiso3code":"","date":"2025","value":1}`, // aggregates without ISO3
			`{"country":{"id":"XM"},"countryiso3code":"","date":"2025","value":2}`,
		),
	}}
}

func TestRunWritesPopulationRecords(t *testing.T) {
	root := newDB(t)
	f := twoPageFetcher(2)
	var out bytes.Buffer
	opts := importOptions{Indicator: defaultIndicator, PerPage: 2, FetchedAt: time.Date(2026, 10, 2, 9, 30, 0, 0, time.UTC)}
	if err := run(root, f, opts, &out); err != nil {
		t.Fatal(err)
	}
	if len(f.calls) != 2 {
		t.Fatalf("want 2 page requests, got %v", f.calls)
	}
	if !strings.Contains(out.String(), "population_wb: 2 (dropped 3 aggregates") {
		t.Fatalf("unexpected summary: %q", out.String())
	}
	var ie map[string]any
	data, err := os.ReadFile(filepath.Join(root, "population_wb", "$records", "ie.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, &ie); err != nil {
		t.Fatal(err)
	}
	want := map[string]any{
		"country": "ie", "iso3": "IRL", "year": float64(2025), "population": float64(5484367),
		"indicator":      "SP.POP.TOTL",
		"source_url":     "https://api.worldbank.org/v2/country/IRL/indicator/SP.POP.TOTL?format=json&mrnev=1",
		"fetched_at":     "2026-10-02T09:30:00Z",
		"source_updated": "2026-07-13",
	}
	for k, v := range want {
		if ie[k] != v {
			t.Errorf("ie[%s] = %v, want %v", k, ie[k], v)
		}
	}
	if _, err := os.Stat(filepath.Join(root, "population_wb", "$records", "afe.json")); err == nil {
		t.Error("aggregate AFE must not be written")
	}
	reg, _ := os.ReadFile(filepath.Join(root, ".ingitdb", "root-collections.yaml"))
	if string(reg) != "countries: countries\npopulation_wb: population_wb\n" {
		t.Errorf("registration: %q", reg)
	}
	// A second run is idempotent: same files, no duplicate registration.
	if err := run(root, twoPageFetcher(2), opts, io.Discard); err != nil {
		t.Fatal(err)
	}
	reg2, _ := os.ReadFile(filepath.Join(root, ".ingitdb", "root-collections.yaml"))
	if string(reg2) != string(reg) {
		t.Errorf("registration changed on re-run: %q", reg2)
	}
}

func TestRunOmitsSourceUpdatedWhenAbsent(t *testing.T) {
	root := newDB(t)
	f := &fakeFetcher{bodies: map[string]string{
		indicatorURL(defaultIndicator, 5, 1): page(`{"page":1,"pages":1}`, ob("IRL", "2025", "5")),
	}}
	if err := run(root, f, importOptions{Indicator: defaultIndicator, PerPage: 5}, io.Discard); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(filepath.Join(root, "population_wb", "$records", "ie.json"))
	if strings.Contains(string(data), "source_updated") {
		t.Errorf("source_updated should be omitted: %s", data)
	}
}

func TestRunErrors(t *testing.T) {
	good := func() *fakeFetcher {
		return &fakeFetcher{bodies: map[string]string{
			indicatorURL(defaultIndicator, 5, 1): page(`{"page":1,"pages":1}`, ob("IRL", "2025", "5")),
		}}
	}
	opts := importOptions{Indicator: defaultIndicator, PerPage: 5}

	t.Run("missing countries collection", func(t *testing.T) {
		err := run(t.TempDir(), good(), opts, io.Discard)
		if err == nil || !strings.Contains(err.Error(), "run geo-import first") {
			t.Fatalf("got %v", err)
		}
	})
	t.Run("fetch failure", func(t *testing.T) {
		err := run(newDB(t), &fakeFetcher{err: errors.New("offline")}, opts, io.Discard)
		if err == nil || err.Error() != "offline" {
			t.Fatalf("got %v", err)
		}
	})
	t.Run("no matching country", func(t *testing.T) {
		f := &fakeFetcher{bodies: map[string]string{
			indicatorURL(defaultIndicator, 5, 1): page(`{"page":1,"pages":1}`, ob("WLD", "2025", "8")),
		}}
		err := run(newDB(t), f, opts, io.Discard)
		if err == nil || !strings.Contains(err.Error(), "no observation matches") {
			t.Fatalf("got %v", err)
		}
	})
	t.Run("records not writable", func(t *testing.T) {
		root := newDB(t)
		writeFixture(t, filepath.Join(root, "population_wb"), "a file where the collection dir should be")
		if err := run(root, good(), opts, io.Discard); err == nil {
			t.Fatal("want error")
		}
	})
	t.Run("registration file missing", func(t *testing.T) {
		root := newDB(t)
		if err := os.Remove(filepath.Join(root, ".ingitdb", "root-collections.yaml")); err != nil {
			t.Fatal(err)
		}
		if err := run(root, good(), opts, io.Discard); err == nil {
			t.Fatal("want error")
		}
	})
}

func TestLoadCountryISO3Errors(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "$records")
	writeFixture(t, filepath.Join(dir, "bad.json"), `{not json`)
	if _, err := loadCountryISO3(dir); err == nil || !strings.Contains(err.Error(), "bad.json") {
		t.Fatalf("got %v", err)
	}
	orig := readFile
	defer func() { readFile = orig }()
	readFile = func(string) ([]byte, error) { return nil, errors.New("read denied") }
	if _, err := loadCountryISO3(dir); err == nil || err.Error() != "read denied" {
		t.Fatalf("got %v", err)
	}
}

func TestFetchIndicatorErrors(t *testing.T) {
	t.Run("malformed page", func(t *testing.T) {
		f := &fakeFetcher{bodies: map[string]string{indicatorURL("X", 5, 1): `[1,2,3`}}
		_, _, err := fetchIndicator(f, "X", 5)
		if err == nil || !strings.Contains(err.Error(), "indicator X page 1") {
			t.Fatalf("got %v", err)
		}
	})
	t.Run("second page fails", func(t *testing.T) {
		f := &fakeFetcher{bodies: map[string]string{
			indicatorURL("X", 5, 1): page(`{"page":1,"pages":2}`, ob("IRL", "2025", "1")),
		}}
		if _, _, err := fetchIndicator(f, "X", 5); err == nil {
			t.Fatal("want error")
		}
	})
}

func TestDecodePage(t *testing.T) {
	cases := map[string]string{
		"not json":          `nope`,
		"api error payload": `[{"message":[{"id":"120","key":"Invalid value","value":"The provided parameter value is not valid"}]}]`,
		"bad meta":          `["str",[]]`,
		"bad observations":  `[{"page":1,"pages":1},{"not":"a list"}]`,
	}
	for name, body := range cases {
		if _, _, err := decodePage([]byte(body)); err == nil {
			t.Errorf("%s: want error", name)
		}
	}
	// A page with null data (no matching rows) decodes to no observations.
	m, obs, err := decodePage([]byte(`[{"page":1,"pages":1,"lastupdated":"d"},null]`))
	if err != nil || len(obs) != 0 || m.LastUpdated != "d" {
		t.Fatalf("got %v %v %v", m, obs, err)
	}
}

func TestMarshalRecord(t *testing.T) {
	got, err := marshalRecord(map[string]any{"u": "a&b<c>"})
	if err != nil || string(got) != "{\n  \"u\": \"a&b<c>\"\n}\n" {
		t.Fatalf("got %q %v", got, err)
	}
	if _, err := marshalRecord(func() {}); err == nil {
		t.Fatal("unsupported value must fail")
	}
}

func TestTruncate(t *testing.T) {
	if truncate("abc", 5) != "abc" || truncate("abcdef", 3) != "abc..." {
		t.Error("truncate")
	}
	_, _, err := decodePage([]byte(`[{"message":"` + strings.Repeat("x", 300) + `"}]`))
	if err == nil || !strings.HasSuffix(err.Error(), "...") {
		t.Fatalf("long error payload should be truncated: %v", err)
	}
}

func TestIndicatorURL(t *testing.T) {
	got := indicatorURL("SP.POP.TOTL", 500, 3)
	want := "https://api.worldbank.org/v2/country/all/indicator/SP.POP.TOTL?format=json&mrnev=1&page=3&per_page=500"
	if got != want {
		t.Fatalf("got %s", got)
	}
}

func TestWriteRecordsErrors(t *testing.T) {
	recs := []record{{Key: "ie", Data: map[string]any{"a": 1}}}
	origRemove, origMkdir, origMarshal, origWrite := removeAll, mkdirAll, marshalJSON, writeFile
	defer func() { removeAll, mkdirAll, marshalJSON, writeFile = origRemove, origMkdir, origMarshal, origWrite }()

	removeAll = func(string) error { return errors.New("rm") }
	if err := writeRecords(t.TempDir(), recs); err == nil || err.Error() != "rm" {
		t.Fatalf("got %v", err)
	}
	removeAll = origRemove

	mkdirAll = func(string, os.FileMode) error { return errors.New("mkdir") }
	if err := writeRecords(t.TempDir(), recs); err == nil || err.Error() != "mkdir" {
		t.Fatalf("got %v", err)
	}
	mkdirAll = origMkdir

	marshalJSON = func(any) ([]byte, error) { return nil, errors.New("marshal") }
	if err := writeRecords(t.TempDir(), recs); err == nil || !strings.Contains(err.Error(), "marshal ie") {
		t.Fatalf("got %v", err)
	}
	marshalJSON = origMarshal

	writeFile = func(string, []byte, os.FileMode) error { return errors.New("write") }
	if err := writeRecords(t.TempDir(), recs); err == nil || err.Error() != "write" {
		t.Fatalf("got %v", err)
	}
}

func TestRegisterCollection(t *testing.T) {
	path := filepath.Join(t.TempDir(), "root-collections.yaml")
	writeFixture(t, path, "countries: countries") // no trailing newline
	if err := registerCollection(path, "population_wb"); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(path)
	if string(got) != "countries: countries\npopulation_wb: population_wb\n" {
		t.Fatalf("got %q", got)
	}
	empty := filepath.Join(t.TempDir(), "empty.yaml")
	writeFixture(t, empty, "")
	if err := registerCollection(empty, "x"); err != nil {
		t.Fatal(err)
	}
	got, _ = os.ReadFile(empty)
	if string(got) != "x: x\n" {
		t.Fatalf("got %q", got)
	}
}

func TestHTTPFetcher(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/missing" {
			http.NotFound(w, r)
			return
		}
		_, _ = w.Write([]byte("body"))
	}))
	defer srv.Close()
	h := &httpFetcher{client: srv.Client()}
	if b, err := h.Get(srv.URL + "/ok"); err != nil || string(b) != "body" {
		t.Fatalf("got %q %v", b, err)
	}
	if _, err := h.Get(srv.URL + "/missing"); err == nil || !strings.Contains(err.Error(), "404") {
		t.Fatalf("got %v", err)
	}
	if _, err := h.Get("http://127.0.0.1:1/unreachable"); err == nil {
		t.Fatal("want connection error")
	}
}

func TestCachedFetcher(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "cache")
	inner := &fakeFetcher{bodies: map[string]string{"u": "payload"}}
	var log bytes.Buffer
	c := &cachedFetcher{dir: dir, inner: inner, log: &log}

	for i := 0; i < 2; i++ {
		b, err := c.Get("u")
		if err != nil || string(b) != "payload" {
			t.Fatalf("got %q %v", b, err)
		}
	}
	if len(inner.calls) != 1 || strings.Count(log.String(), "downloading u") != 1 {
		t.Fatalf("second read must come from the cache: calls=%v log=%q", inner.calls, log.String())
	}
	c.refresh = true
	if _, err := c.Get("u"); err != nil || len(inner.calls) != 2 {
		t.Fatalf("refresh must bypass the cache: %v %v", err, inner.calls)
	}
	if _, err := (&cachedFetcher{dir: dir, refresh: true, inner: &fakeFetcher{err: errors.New("down")}, log: io.Discard}).Get("u"); err == nil {
		t.Fatal("want inner error")
	}

	origMkdir, origWrite := mkdirAll, writeFile
	defer func() { mkdirAll, writeFile = origMkdir, origWrite }()
	mkdirAll = func(string, os.FileMode) error { return errors.New("mkdir") }
	if _, err := (&cachedFetcher{dir: dir, refresh: true, inner: inner, log: io.Discard}).Get("u"); err == nil || err.Error() != "mkdir" {
		t.Fatalf("got %v", err)
	}
	mkdirAll = origMkdir
	writeFile = func(string, []byte, os.FileMode) error { return errors.New("write") }
	if _, err := (&cachedFetcher{dir: dir, refresh: true, inner: inner, log: io.Discard}).Get("u"); err == nil || err.Error() != "write" {
		t.Fatalf("got %v", err)
	}
}

func TestRealMain(t *testing.T) {
	origNew, origNow := newTransport, nowUTC
	defer func() { newTransport, nowUTC = origNew, origNow }()
	nowUTC = func() time.Time { return time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC) }

	root := newDB(t)
	cache := filepath.Join(t.TempDir(), "cache")
	newTransport = func() fetcher { return twoPageFetcher(2) }
	var stdout, stderr bytes.Buffer
	code := realMain([]string{"-out", root, "-cache", cache, "-per-page", "2"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, stderr.String())
	}
	data, _ := os.ReadFile(filepath.Join(root, "population_wb", "$records", "us.json"))
	if !strings.Contains(string(data), `"fetched_at": "2026-01-02T03:04:05Z"`) {
		t.Fatalf("clock seam not used: %s", data)
	}

	// Import failure maps to exit 1.
	newTransport = func() fetcher { return &fakeFetcher{err: errors.New("offline")} }
	stderr.Reset()
	if code := realMain([]string{"-out", root, "-cache", t.TempDir(), "-per-page", "2"}, &stdout, &stderr); code != 1 || !strings.Contains(stderr.String(), "wb-import: offline") {
		t.Fatalf("exit %d: %s", code, stderr.String())
	}

	// Flag errors map to exit 2; -h is not a failure.
	if code := realMain([]string{"-bogus"}, io.Discard, io.Discard); code != 2 {
		t.Fatalf("bogus flag: exit %d", code)
	}
	if code := realMain([]string{"-h"}, io.Discard, io.Discard); code != 0 {
		t.Fatalf("-h: exit %d", code)
	}
	stderr.Reset()
	if code := realMain([]string{"-per-page", "0"}, io.Discard, &stderr); code != 2 || !strings.Contains(stderr.String(), "-per-page") {
		t.Fatalf("per-page 0: exit %d %s", code, stderr.String())
	}
}

func TestMainUsesExitSeam(t *testing.T) {
	origExit, origArgs := osExit, os.Args
	defer func() { osExit, os.Args = origExit, origArgs }()
	got := -1
	osExit = func(code int) { got = code }
	os.Args = []string{"wb-import", "-h"}
	main()
	if got != 0 {
		t.Fatalf("exit code %d", got)
	}
}

func TestDefaultSeams(t *testing.T) {
	if _, ok := newTransport().(*httpFetcher); !ok {
		t.Error("default transport must be the HTTP fetcher")
	}
	if nowUTC().Location() != time.UTC {
		t.Error("clock must be UTC")
	}
}
