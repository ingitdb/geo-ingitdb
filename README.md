# geo-ingitdb

A geographic reference database stored in [inGitDB](https://github.com/ingitdb/ingitdb-go)
format: countries, their first-order subdivisions, and settlements, generated
from open data by [`cmd/geo-import`](cmd/geo-import).

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
anything whose ISO3 code is not in `countries`), rewrites `population_wb/$records/`
and registers the collection in `.ingitdb/root-collections.yaml`. Run
`geo-import` first. Pages are cached under `.cache/wb-import`; flags: `--out`,
`--cache`, `--refresh`, `--indicator`, `--per-page`. The committed records are a
snapshot, and Git history is its provenance. The importer has unit tests with
a fake fetcher; none touch the network (`go test ./...`).

## Validation

The database validates clean under inGitDB, including foreign-key referential
integrity: every `subdivisions.country`, `settlements.country`, and
`settlements.subdivision` value resolves to an existing record.

## Data license

Geographic data © GeoNames, used under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
See [DATA-LICENSE.md](DATA-LICENSE.md). The inGitDB schema and import code in
this repository are under the repository [LICENSE](LICENSE).
