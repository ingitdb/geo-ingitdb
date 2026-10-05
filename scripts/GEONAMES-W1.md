# GeoNames W1 source projection

This is the additive global source path. The existing `cmd/geo-import`, collection
definitions, legacy keys and committed demo records retain their existing behavior.
Python 3.10+ and SQLite from the Python standard library are used only by offline
generation; no runtime Python service is introduced.

Capture the complete official sources once into a private directory outside Git:

```sh
python3 scripts/geonames_w1.py capture --directory /private/tmp/geonames-snapshot-inputs
```

The capture refuses redirects and unverified existing input files. `inputs.json`
records URL, exact bytes/SHA256, member, fetch time, upstream Last-Modified and
ETag. GeoNames files are daily mutable extracts; these captured bytes, rather than
the mutable download URL or inspection date, identify the snapshot. Keep all five
files and that manifest for reproducible offline rebuilding. No refresh is implicit.

The separate ROR provider supplies a complete release location-ID projection:
`complete: true`, `archive_url`, `release`, `release_date`, `archive_sha256`,
`archive_bytes`, `record_count`, `location_count`, `ids_file`, `ids_sha256`,
`id_count`. The ID file contains one unique native positive decimal GeoNames ID per
LF-terminated line. `archive_file` and `json_member` name the cached complete ROR
archive and its JSON member. The builder independently checks archive bytes/hash,
streams every organization/location and verifies the entire native ID set against
the ID-file hash/count and receipt. A sample
cannot satisfy the full-release prerequisite. This handoff is generation provenance,
not a user-schema acceptance contract or a competing canonical registry.

After committing the importer, build into a new private snapshot directory:

```sh
python3 scripts/geonames_w1.py build \
  --inputs /private/tmp/geonames-snapshot-inputs/inputs.json \
  --ror-closure /private/tmp/ror-snapshot/closure.json \
  --out /private/tmp/geonames-snapshot-output \
  --tool-revision <immutable-importer-git-revision> \
  --receipt /private/tmp/geonames-build-measurement.json
```

Four source-qualified physical tables represent four selected logical recordsets:

| Table | Native key | Coverage |
|---|---|---|
| `geonames_countries` | unmodified `iso` | Complete `countryInfo.txt` |
| `geonames_admin1` | unmodified `code`, e.g. `US.CA` | Complete `admin1CodesASCII.txt`, including orphans |
| `geonames_places` | unmodified `geonameid` | Global cities5000 union every complete-release ROR GeoNames reference |
| `geonames_alternate_names` | unmodified `alternate_name_id` | Every alternateNamesV2 row for retained places |

Every documented source column is retained as TEXT with SQLite binary equality;
numeric-looking strings remain exact, zero remains `"0"`, empty remains `""`, and
omitted trailing optional columns remain SQL NULL. ZIP members are streamed,
membership sets are bounded to retained IDs, and required-reference joins are
disk-backed. No name or code is trimmed, lowercased, slugged or guessed. Native
admin1 codes may be FIPS or provider-specific: they do not become ISO3166-2.
Retained feature classes/codes include non-settlements; no blanket Geo.City binding
is implied. `alternate_name_id` is a documented snake-case physical column for
GeoNames' alternateNameId; alias string, language, flags and periods are preserved.

`required_ror_places`, `missing_ror_places`, `missing_admin1_references`,
`missing_country_references` and `orphan_admin1` are diagnostic physical projections,
not additional selected public datasets. Dangling raw codes and unresolved ROR IDs
remain available and counted. The exporter never fabricates a target or clears a
raw reference to satisfy foreign-key validation. Duplicate selected native keys,
disagreeing cities5000/allCountries overlaps and malformed retained rows fail.

`bridges/accepted-country-bridges.json` contains only the previously accepted raw
label dictionaries for pinned Chinook Customer.Country, Northwind Customers.Country,
Northwind Orders.ShipCountry and Pubs publishers.country. The default build materializes
four separate source-qualified serving tables, retaining each raw label byte for byte.
`serving_id` is a generated SHA256 of raw-label UTF-8 bytes, explicitly separate from
the native target key. Each bridge's source model, original fixture, enumerated-value
receipt and accepted source-property decision retain immutable external pins. Targets
must exist in this country snapshot and repeated raw labels fail. Generation does not
authorize consumer eligibility: the publisher contract validates scope and linkage.

Each output includes SQLite, deterministic ordered 16MiB decoded gzip chunks,
`snapshot.json` with source/output checksums and counts, and `ATTRIBUTION.txt`.
The generic snapshot `generator` and `artifacts` fields bind the country key index,
four physical bridge descriptors, provider model/meaning files and immutable
accepted-input provenance. No attachment references its own containing commit.
Chunks obey the existing 25MiB fetch guard; their order/hash and decoded SQLite hash
are ready for the designated runtime owner. These files are private build outputs:
no production mount, Directory discovery or public availability is claimed here.
Provider model/manifest and accepted label bridge attachments require the separately
reviewed publisher contract before publication. Legacy Invoice country aliases
provide no Customer representation acceptance.

The builder checks the combined GeoNames+ROR download <=1GiB, sampled source/staging
disk <=8GiB, process peak RSS <=512MiB, generation elapsed <=30minutes, SQLite
<=256MiB and snapshot metadata <=2MiB. Measurement time/RSS/environment live in the
separate receipt so deterministic data/checksums do not vary with measurement.
Source/staging disk is logical file size sampled during parsing, not a filesystem
allocation high-water mark. An external whole-W1 capture/build observer must also
measure total wall time, disk and all sibling workers; this provider receipt alone
does not establish combined-wave resource acceptance. Failed or over-budget builds
write a blocked receipt, discard the candidate, and retain previous snapshots. They
never narrow coverage. Snapshots are monthly reviewed, with failed refresh leaving
explicit stale last-known-good state; this exporter does not supply live refresh.

Run offline checks with:

```sh
python3 -m unittest discover -s scripts -p test_geonames_w1.py -v
```

Data: GeoNames CC-BY-4.0; include attribution and changes on artifact download and
in consumers. Importer code: repository CC0-1.0. ROR metadata: CC0, with GeoNames
location attribution retained. Models and canonical meanings retain their separate
declared licences; neither this exporter nor its licence redefines them.
