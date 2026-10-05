# geo-ingitdb

A geographic reference database stored in [inGitDB](https://github.com/ingitdb/ingitdb-go)
format: countries, their first-order subdivisions, and settlements, generated
from open data by [`cmd/geo-import`](cmd/geo-import).

The additive [GeoNames W1 exporter](scripts/GEONAMES-W1.md) retains full selected
global source fields and native keys in reproducible private SQLite exports.
Its source-qualified projections and exception counts are separate from the
legacy showcase collections below.

The [reviewed W1 artifact package](scripts/GEONAMES-W1-ARTIFACTS.md) publishes
the unchanged native snapshot as five ordered gzip members with immutable
source/generator provenance. Runtime serving and discovery remain separate gates.

## Layout

Three flat root collections linked by foreign keys (not nested subcollections —
inGitDB validates FK referential integrity across root collections, and this
model exercises that):

| Collection | Records | Key | Links |
|---|---|---|---|
| [`countries`](countries) | ~250 | lowercase ISO 3166-1 alpha-2 (`us`) | — |
| [`subdivisions`](subdivisions) | ~3,900 | `<iso2>-<admin1code>` (`us-ca`) | `country` → countries |
| [`settlements`](settlements) | showcase only | `<slug>-<geonameid>` | `country` → countries, `subdivision` → subdivisions |
| [`population_wb`](population_wb) | ~216 | lowercase iso2 (`ie`) | `country` → countries |
| [`country_aliases`](country_aliases) | 24 | slug of the alias (`czech-republic`) | `country` → countries |

`population_wb` is the latest World Bank `SP.POP.TOTL` observation per country
(with `year`, `indicator`, `source_url`, `fetched_at`); unlike
`countries.population` (GeoNames) it is dated and attributable. `country_aliases`
maps the country spellings another system uses to a `countries` record — today the
24 `Invoice.BillingCountry` values of the Chinook sample database (`USA`, `Czech
Republic`, `Netherlands`, ...), each with its `source` provenance. Together they
let a query join Chinook sales to population without guessing at names.

Each collection is one JSON file per record under `$records/`. Multilingual
names use inGitDB's `map[locale]string` column type (currently `en` only, from
the ASCII source; richer locales can be added from GeoNames `alternateNames`).

## Regenerating the data

```
go run ./cmd/geo-import --out .
```

Sources are downloaded from GeoNames and cached under `.cache/` (git-ignored);
re-runs are offline. Flags:

- `--settlements GB,IE,US,DE` — which countries to import settlements for
  (default). `--settlements ""` imports none. Settlements are scoped to keep the
  repository git-friendly; all countries and subdivisions are always global.
- `--refresh` — re-download sources instead of using the cache.
- `--out <dir>` — database root (default `.`).

The importer owns only the `$records/` directories — it clears and rewrites
them — and never touches the `.collection/` schemas or `.ingitdb/` config.

### World Bank population

```
go run ./cmd/wb-import --out .
```

Fetches `SP.POP.TOTL` from the World Bank API v2 (`mrnev=1`: most recent
non-empty value; all pages), drops aggregates (regions, income groups, "World":
anything whose ISO3 code is not in `countries`), regenerates
`population_wb/$records/` (written to a sibling directory and swapped in, so a
failed run keeps the previous records) and registers the collection in
`.ingitdb/root-collections.yaml`. Run `geo-import` first. Flags: `--out`,
`--cache`, `--refresh`, `--per-page`. The indicator is fixed: the schema, the
integer `population` column and the collection name all assume `SP.POP.TOTL`.
Requests send a `User-Agent`; transport errors, HTTP 429 and 5xx are retried
(three requests at most), and an API error payload served with HTTP 200 is
rejected before it reaches the cache. The committed records are a snapshot, and
Git history is its provenance. The importer has unit tests with a fake fetcher;
none touch the network (`go test ./...`).

**Re-runs are deterministic.** Pages are cached under `.cache/wb-import`
together with the time they were downloaded, and `fetched_at` is the oldest
download time of the pages a record came from. A run that reads only cached
pages therefore reproduces the committed records byte for byte (zero Git diff);
only `--refresh` takes a new snapshot and moves `fetched_at`.

**Coverage gaps.** `population_wb` has 216 of the 252 `countries` records, so an
inner join from a country-keyed source silently drops the other 36:

- The World Bank reports the Channel Islands as one entity (`CHI`), not as
  Jersey and Guernsey, so neither `je` nor `gg` has a row; the entity itself is
  dropped like any other aggregate, because `CHI` is not in `countries`.
- The remaining gaps are territories and historical entries the World Bank does
  not publish a population for: Anguilla, Netherlands Antilles, Antarctica,
  Aland Islands, Saint Barthelemy, Bonaire/Saint Eustatius/Saba, Bouvet Island,
  Cocos Islands, Cook Islands, Serbia and Montenegro, Christmas Island, Western
  Sahara, Falkland Islands, French Guiana, Guadeloupe, South Georgia and the
  South Sandwich Islands, Heard Island and McDonald Islands, British Indian
  Ocean Territory, Martinique, Montserrat, Norfolk Island, Niue, Saint Pierre
  and Miquelon, Pitcairn, Reunion, Saint Helena, Svalbard and Jan Mayen, French
  Southern Territories, Tokelau, Taiwan, United States Minor Outlying Islands,
  Vatican, Wallis and Futuna, Mayotte.

To list the countries without a population after any refresh:

```
comm -23 <(ls countries/\$records | sort) <(ls population_wb/\$records | sort) | sed 's/\.json$//'
```

Use a left join (or `countries.population` from GeoNames, which is undated) when
those territories matter. All 24 `Invoice.BillingCountry` values of the Chinook
sample database have a population.

## Validation

The database validates clean under inGitDB, including foreign-key referential
integrity: every `subdivisions.country`, `settlements.country`, and
`settlements.subdivision` value resolves to an existing record.

## Data license and attribution

- Countries, subdivisions and settlements: © [GeoNames](https://www.geonames.org/),
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- `population_wb`: [World Bank Open Data](https://data.worldbank.org/indicator/SP.POP.TOTL),
  indicator SP.POP.TOTL, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

Both datasets are modified here (see [DATA-LICENSE.md](DATA-LICENSE.md) for the
changes and the source URLs) and neither provider endorses this database. The
inGitDB schema and import code in this repository are under the repository
[LICENSE](LICENSE).

## Scoped representation metadata

[model/representations.json](model/representations.json) is bounded provider-local
execution metadata. [source/representation-attachment.json](source/representation-attachment.json)
contains the exact `path`/`sha256` envelope for future attachment through a reviewed
`ovdb.yaml`. Every own-provider reference is relative; the downstream Directory
record supplies the immutable provider commit.

The format 2 document preserves exactly four label-bridge scopes: Chinook `Customer.Country`, Northwind `Customers.Country` and `Orders.ShipCountry`, and Pubs `publishers.country`. It uses the existing small bridge exports and native country key index. The original source-specific decisions remain pinned at hub revision `2694ed58c8bb82277324030cf82a596ee1695db5`. It adds no native GeoNames user mapping or Invoice mapping.

Reproduce the documents without downloads or native-data reconstruction:

```sh
python3 scripts/generate_representations.py
python3 scripts/generate_representations.py --check
```

The generator refuses changes to its reviewed local metadata inputs and compares
exact generated bytes with `--check`. It retains the existing artifact snapshot,
models, bindings, native data, source provenance, licences and attribution. This
is a local reproduction check: full structural verification uses the released
OVDB v0.27.0 `publisher/representation.Check` with regular committed-file readers
for all exact external pins. Format 3 source-data byte proof is a separate stage;
metadata resolution must never fetch source data, native SQLite or ordered chunks.

These files grant no semantic acceptance or production eligibility. Independent
review must tie the final provider commit and attachment path/hash to the original
decision and reviewed source/wrapper continuity before canonical admission.
Publisher/Directory companions and immutable dependencies, root manifest opt-in,
truthful deployment/licence metadata, canonical registry pins, faithful runtime
identity, capacity/CORS/read-only receipts, Directory publication and the real app
journey remain separate prerequisites. This metadata change supplies none of
those publication receipts.
