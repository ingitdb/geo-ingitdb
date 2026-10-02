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
	"sync/atomic"
	"testing"
	"time"
)

var fixedTime = time.Date(2026, 10, 2, 9, 30, 0, 0, time.UTC)

// fakeFetcher serves canned bodies by URL and records every request, so tests
// never touch the network. Each page is stamped with times[url], else at, else
// fixedTime.
type fakeFetcher struct {
	bodies map[string]string
	times  map[string]time.Time
	at     time.Time
	err    error
	calls  []string
}

func (f *fakeFetcher) Get(u string) (response, error) {
	f.calls = append(f.calls, u)
	if f.err != nil {
		return response{}, f.err
	}
	body, ok := f.bodies[u]
	if !ok {
		return response{}, fmt.Errorf("unexpected URL %s", u)
	}
	at := fixedTime
	if !f.at.IsZero() {
		at = f.at
	}
	if t, ok := f.times[u]; ok {
		at = t
	}
	return response{Body: []byte(body), FetchedAt: at}, nil
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
	opts := importOptions{Indicator: defaultIndicator, PerPage: 2}
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
		_, _, _, err := fetchIndicator(f, "X", 5)
		if err == nil || !strings.Contains(err.Error(), "indicator X page 1") {
			t.Fatalf("got %v", err)
		}
	})
	t.Run("second page fails", func(t *testing.T) {
		f := &fakeFetcher{bodies: map[string]string{
			indicatorURL("X", 5, 1): page(`{"page":1,"pages":2}`, ob("IRL", "2025", "1")),
		}}
		if _, _, _, err := fetchIndicator(f, "X", 5); err == nil {
			t.Fatal("want error")
		}
	})
	t.Run("oldest page time wins", func(t *testing.T) {
		older := fixedTime.Add(-48 * time.Hour)
		for name, times := range map[string]map[string]time.Time{
			"first page older":  {indicatorURL("X", 5, 1): older},
			"second page older": {indicatorURL("X", 5, 2): older},
		} {
			f := &fakeFetcher{times: times, bodies: map[string]string{
				indicatorURL("X", 5, 1): page(`{"page":1,"pages":2}`, ob("IRL", "2025", "1")),
				indicatorURL("X", 5, 2): page(`{"page":2,"pages":2}`, ob("USA", "2025", "2")),
			}}
			_, _, got, err := fetchIndicator(f, "X", 5)
			if err != nil || !got.Equal(older) {
				t.Fatalf("%s: got %v %v, want %v", name, got, err, older)
			}
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

func TestWriteRecordsKeepsPreviousRecordsOnFailure(t *testing.T) {
	recs := []record{{Key: "ie", Data: map[string]any{"a": 1}}, {Key: "us", Data: map[string]any{"a": 2}}}
	dir := t.TempDir()
	rec := filepath.Join(dir, "$records")
	writeFixture(t, filepath.Join(rec, "old.json"), `{"old":true}`)

	origMarshal, origWrite := marshalJSON, writeFile
	defer func() { marshalJSON, writeFile = origMarshal, origWrite }()
	assertIntact := func(t *testing.T) {
		t.Helper()
		if _, err := os.Stat(filepath.Join(rec, "old.json")); err != nil {
			t.Fatalf("previous records must survive a failed write: %v", err)
		}
		for _, leftover := range []string{rec + ".next", rec + ".prev"} {
			if _, err := os.Stat(leftover); err == nil {
				t.Fatalf("leftover %s", leftover)
			}
		}
	}

	marshalJSON = func(any) ([]byte, error) { return nil, errors.New("marshal") }
	if err := writeRecords(dir, recs); err == nil || !strings.Contains(err.Error(), "marshal ie") {
		t.Fatalf("got %v", err)
	}
	assertIntact(t)
	marshalJSON = origMarshal

	writeFile = func(string, []byte, os.FileMode) error { return errors.New("write") }
	if err := writeRecords(dir, recs); err == nil || err.Error() != "write" {
		t.Fatalf("got %v", err)
	}
	assertIntact(t)
	writeFile = origWrite
}

func TestWriteRecordsReplacesRecords(t *testing.T) {
	dir := t.TempDir()
	rec := filepath.Join(dir, "$records")
	writeFixture(t, filepath.Join(rec, "old.json"), `{"old":true}`)
	if err := writeRecords(dir, []record{{Key: "ie", Data: map[string]any{"a": 1}}}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(rec, "old.json")); err == nil {
		t.Error("a stale record must be removed")
	}
	if _, err := os.Stat(filepath.Join(rec, "ie.json")); err != nil {
		t.Error(err)
	}
	// First run: no previous $records directory at all.
	fresh := t.TempDir()
	if err := writeRecords(fresh, []record{{Key: "ie", Data: map[string]any{"a": 1}}}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(fresh, "$records", "ie.json")); err != nil {
		t.Error(err)
	}
}

func TestWriteRecordsFileSystemErrors(t *testing.T) {
	recs := []record{{Key: "ie", Data: map[string]any{"a": 1}}}
	origRemove, origMkdir, origRename := removeAll, mkdirAll, rename
	defer func() { removeAll, mkdirAll, rename = origRemove, origMkdir, origRename }()

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

	// Moving the old records aside fails for a reason other than "missing".
	rename = func(string, string) error { return errors.New("rename-aside") }
	if err := writeRecords(t.TempDir(), recs); err == nil || err.Error() != "rename-aside" {
		t.Fatalf("got %v", err)
	}

	// Swapping the new records in fails: the old ones are put back.
	dir := t.TempDir()
	rec := filepath.Join(dir, "$records")
	writeFixture(t, filepath.Join(rec, "old.json"), `{}`)
	calls := 0
	rename = func(from, to string) error {
		calls++
		if calls == 2 { // the swap-in; the restore (call 3) is the real rename
			return errors.New("swap")
		}
		return origRename(from, to)
	}
	if err := writeRecords(dir, recs); err == nil || err.Error() != "swap" {
		t.Fatalf("got %v", err)
	}
	if _, err := os.Stat(filepath.Join(rec, "old.json")); err != nil {
		t.Fatalf("old records must be restored: %v", err)
	}
	// ... and when there was nothing to restore.
	rename = func(from, to string) error {
		if strings.HasSuffix(to, ".prev") {
			return origRename(from, to) // not exist: fresh collection
		}
		return errors.New("swap")
	}
	if err := writeRecords(t.TempDir(), recs); err == nil || err.Error() != "swap" {
		t.Fatalf("got %v", err)
	}
	rename = origRename

	// Deleting the superseded records fails after a successful swap.
	removeAll = func(p string) error {
		if strings.HasSuffix(p, ".prev") && strings.Contains(p, "late") {
			return errors.New("rm-prev")
		}
		return origRemove(p)
	}
	late := filepath.Join(t.TempDir(), "late")
	writeFixture(t, filepath.Join(late, "$records", "old.json"), `{}`)
	if err := writeRecords(late, recs); err == nil || err.Error() != "rm-prev" {
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

func noSleep(t *testing.T) *[]time.Duration {
	t.Helper()
	orig := sleep
	var slept []time.Duration
	sleep = func(d time.Duration) { slept = append(slept, d) }
	t.Cleanup(func() { sleep = orig })
	return &slept
}

func TestHTTPFetcher(t *testing.T) {
	slept := noSleep(t)
	origNow := nowUTC
	nowUTC = func() time.Time { return fixedTime }
	defer func() { nowUTC = origNow }()

	var flaky, tooMany, sent atomic.Int32
	var agent atomic.Value
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		agent.Store(r.Header.Get("User-Agent"))
		sent.Add(1)
		switch r.URL.Path {
		case "/missing":
			http.NotFound(w, r)
		case "/flaky": // two server errors, then success
			if flaky.Add(1) <= 2 {
				http.Error(w, "boom", http.StatusBadGateway)
				return
			}
			_, _ = w.Write([]byte("recovered"))
		case "/limited": // rate limited once
			if tooMany.Add(1) == 1 {
				http.Error(w, "slow down", http.StatusTooManyRequests)
				return
			}
			_, _ = w.Write([]byte("later"))
		case "/down":
			http.Error(w, "boom", http.StatusServiceUnavailable)
		case "/truncated": // promises 10 bytes, sends 3
			w.Header().Set("Content-Length", "10")
			_, _ = w.Write([]byte("abc"))
		default:
			_, _ = w.Write([]byte("body"))
		}
	}))
	defer srv.Close()
	h := &httpFetcher{client: srv.Client()}

	pg, err := h.Get(srv.URL + "/ok")
	if err != nil || string(pg.Body) != "body" || !pg.FetchedAt.Equal(fixedTime) {
		t.Fatalf("got %q %v %v", pg.Body, pg.FetchedAt, err)
	}
	if agent.Load() != userAgent {
		t.Errorf("User-Agent %q, want %q", agent.Load(), userAgent)
	}
	if len(*slept) != 0 {
		t.Errorf("a first-try success must not sleep: %v", *slept)
	}

	if pg, err := h.Get(srv.URL + "/flaky"); err != nil || string(pg.Body) != "recovered" || flaky.Load() != 3 {
		t.Fatalf("5xx must be retried: %q %v after %d requests", pg.Body, err, flaky.Load())
	}
	if len(*slept) != 2 || (*slept)[0] >= (*slept)[1] {
		t.Errorf("pauses must grow: %v", *slept)
	}
	if pg, err := h.Get(srv.URL + "/limited"); err != nil || string(pg.Body) != "later" {
		t.Fatalf("429 must be retried: %q %v", pg.Body, err)
	}

	sent.Store(0)
	if _, err := h.Get(srv.URL + "/missing"); err == nil || !strings.Contains(err.Error(), "404") || sent.Load() != 1 {
		t.Fatalf("404 is final: %v after %d requests", err, sent.Load())
	}
	sent.Store(0)
	if _, err := h.Get(srv.URL + "/down"); err == nil || !strings.Contains(err.Error(), "503") || sent.Load() != maxAttempts {
		t.Fatalf("persistent 5xx must stop after %d requests: %v after %d", maxAttempts, err, sent.Load())
	}
	sent.Store(0)
	if _, err := h.Get(srv.URL + "/truncated"); err == nil || sent.Load() != maxAttempts {
		t.Fatalf("a truncated body must be retried then fail: %v after %d requests", err, sent.Load())
	}
	if _, err := h.Get("http://127.0.0.1:1/unreachable"); err == nil {
		t.Fatal("want connection error")
	}
	if _, err := h.Get("http://[::1"); err == nil {
		t.Fatal("an unparsable URL must fail")
	}
}

func TestCachedFetcher(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "cache")
	first := fixedTime
	inner := &fakeFetcher{bodies: map[string]string{"u": `{"k":"v"}`}, at: first}
	var log bytes.Buffer
	c := &cachedFetcher{dir: dir, inner: inner, log: &log}

	pg, err := c.Get("u")
	if err != nil || string(pg.Body) != `{"k":"v"}` || !pg.FetchedAt.Equal(first) {
		t.Fatalf("got %q %v %v", pg.Body, pg.FetchedAt, err)
	}
	inner.at = first.Add(72 * time.Hour) // the upstream clock has moved on
	pg, err = c.Get("u")
	if err != nil || !pg.FetchedAt.Equal(first) {
		t.Fatalf("a cached page must keep its original fetch time: %v %v", pg.FetchedAt, err)
	}
	var body map[string]string
	if err := json.Unmarshal(pg.Body, &body); err != nil || body["k"] != "v" {
		t.Fatalf("cached body: %q %v", pg.Body, err)
	}
	if len(inner.calls) != 1 || strings.Count(log.String(), "downloading u") != 1 {
		t.Fatalf("second read must come from the cache: calls=%v log=%q", inner.calls, log.String())
	}

	c.refresh = true
	pg, err = c.Get("u")
	if err != nil || len(inner.calls) != 2 || !pg.FetchedAt.Equal(first.Add(72*time.Hour)) {
		t.Fatalf("refresh must bypass the cache: %v %v %v", err, pg.FetchedAt, inner.calls)
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
	writeFile = origWrite

	// A body that is not JSON cannot be stored in the cache.
	notJSON := &fakeFetcher{bodies: map[string]string{"u": "plain text"}}
	if _, err := (&cachedFetcher{dir: t.TempDir(), inner: notJSON, log: io.Discard}).Get("u"); err == nil || !strings.Contains(err.Error(), "cache u") {
		t.Fatalf("want a cache error for a non-JSON body, got %v", err)
	}
}

func cacheFiles(t *testing.T, dir string) []string {
	t.Helper()
	files, err := filepath.Glob(filepath.Join(dir, "*.json"))
	if err != nil {
		t.Fatal(err)
	}
	return files
}

func TestCachedFetcherValidatesBeforeCaching(t *testing.T) {
	u := indicatorURL(defaultIndicator, 2, 1)
	apiError := `[{"message":[{"id":"120","key":"Invalid value","value":"bad"}]}]` // served with HTTP 200
	good := page(`{"page":1,"pages":1}`, ob("IRL", "2025", "5"))
	dir := filepath.Join(t.TempDir(), "cache")

	inner := &fakeFetcher{bodies: map[string]string{u: apiError}}
	c := &cachedFetcher{dir: dir, inner: inner, log: io.Discard, validate: validatePage}
	if _, err := c.Get(u); err == nil || !strings.Contains(err.Error(), "error payload") || !strings.Contains(err.Error(), "not cached") {
		t.Fatalf("got %v", err)
	}
	if files := cacheFiles(t, dir); len(files) != 0 {
		t.Fatalf("an error payload must never be cached: %v", files)
	}

	// The upstream recovers: the next run downloads again instead of replaying the error.
	inner.bodies[u] = good
	if pg, err := c.Get(u); err != nil || string(pg.Body) == "" {
		t.Fatalf("got %v", err)
	}
	if files := cacheFiles(t, dir); len(files) != 1 {
		t.Fatalf("a valid page is cached: %v", files)
	}

	// Entries from before validation existed, or from another URL, are not trusted.
	files := cacheFiles(t, dir)
	for name, content := range map[string]string{
		"legacy raw body":  good,
		"poisoned entry":   `{"url":"` + u + `","fetched_at":"2026-01-01T00:00:00Z","body":` + apiError + `}`,
		"entry for others": `{"url":"other","fetched_at":"2026-01-01T00:00:00Z","body":` + good + `}`,
	} {
		writeFixture(t, files[0], content)
		inner.calls = nil
		pg, err := c.Get(u)
		if err != nil || len(inner.calls) != 1 || !pg.FetchedAt.Equal(fixedTime) {
			t.Fatalf("%s: want a re-download, got %v %v calls=%v", name, pg.FetchedAt, err, inner.calls)
		}
	}
}

func snapshotDir(t *testing.T, dir string) map[string]string {
	t.Helper()
	out := map[string]string{}
	err := filepath.WalkDir(dir, func(p string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		data, err := os.ReadFile(p)
		rel, _ := filepath.Rel(dir, p)
		out[rel] = string(data)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

// TestRerunIsByteIdentical is the determinism guarantee: once the pages are
// cached, running the importer again - on a later day, with the clock moved on -
// rewrites every record with the same bytes, so Git sees no change.
func TestRerunIsByteIdentical(t *testing.T) {
	origNew, origNow := newTransport, nowUTC
	defer func() { newTransport, nowUTC = origNew, origNow }()

	root := newDB(t)
	cache := filepath.Join(t.TempDir(), "cache")
	args := []string{"-out", root, "-cache", cache, "-per-page", "2"}

	inner := twoPageFetcher(2)
	inner.at = fixedTime
	newTransport = func() fetcher { return inner }
	if code := realMain(args, io.Discard, io.Discard); code != 0 {
		t.Fatalf("first run exit %d", code)
	}
	before := snapshotDir(t, root)
	if len(before) < 4 {
		t.Fatalf("expected records, got %v", before)
	}
	if !strings.Contains(before[filepath.Join("population_wb", "$records", "ie.json")], `"fetched_at": "2026-10-02T09:30:00Z"`) {
		t.Fatalf("fetched_at must be the page's fetch time: %v", before)
	}

	// Two days later, with a clock that would change every record if it were used.
	inner.at = fixedTime.Add(48 * time.Hour)
	nowUTC = func() time.Time { return inner.at }
	calls := len(inner.calls)
	var stdout bytes.Buffer
	if code := realMain(args, &stdout, io.Discard); code != 0 {
		t.Fatalf("second run exit %d", code)
	}
	if len(inner.calls) != calls || strings.Contains(stdout.String(), "downloading") {
		t.Fatalf("the second run must read the cache only: %v", inner.calls[calls:])
	}
	after := snapshotDir(t, root)
	if len(after) != len(before) {
		t.Fatalf("file set changed: %d -> %d", len(before), len(after))
	}
	for name, data := range before {
		if after[name] != data {
			t.Errorf("%s changed between runs:\n%s\n---\n%s", name, data, after[name])
		}
	}

	// -refresh is the deliberate way to take a new snapshot: only then does fetched_at move.
	if code := realMain(append(args, "-refresh"), io.Discard, io.Discard); code != 0 {
		t.Fatalf("refresh exit %d", code)
	}
	if !strings.Contains(snapshotDir(t, root)[filepath.Join("population_wb", "$records", "ie.json")], `"fetched_at": "2026-10-04T09:30:00Z"`) {
		t.Error("-refresh must record the new fetch time")
	}
}

func TestRealMain(t *testing.T) {
	origNew := newTransport
	defer func() { newTransport = origNew }()

	root := newDB(t)
	cache := filepath.Join(t.TempDir(), "cache")
	newTransport = func() fetcher {
		f := twoPageFetcher(2)
		f.at = time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)
		return f
	}
	var stdout, stderr bytes.Buffer
	code := realMain([]string{"-out", root, "-cache", cache, "-per-page", "2"}, &stdout, &stderr)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, stderr.String())
	}
	data, _ := os.ReadFile(filepath.Join(root, "population_wb", "$records", "us.json"))
	if !strings.Contains(string(data), `"fetched_at": "2026-01-02T03:04:05Z"`) {
		t.Fatalf("fetch time not recorded: %s", data)
	}

	// Import failure maps to exit 1.
	newTransport = func() fetcher { return &fakeFetcher{err: errors.New("offline")} }
	stderr.Reset()
	if code := realMain([]string{"-out", root, "-cache", t.TempDir(), "-per-page", "2"}, &stdout, &stderr); code != 1 || !strings.Contains(stderr.String(), "wb-import: offline") {
		t.Fatalf("exit %d: %s", code, stderr.String())
	}

	// Flag errors map to exit 2; -h is not a failure; the indicator is not configurable.
	if code := realMain([]string{"-bogus"}, io.Discard, io.Discard); code != 2 {
		t.Fatalf("bogus flag: exit %d", code)
	}
	if code := realMain([]string{"-indicator", "SP.POP.GROW"}, io.Discard, io.Discard); code != 2 {
		t.Fatalf("-indicator must not exist: exit %d", code)
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
	start := time.Now()
	sleep(time.Millisecond)
	if time.Since(start) < time.Millisecond {
		t.Error("default sleep must pause")
	}
}
