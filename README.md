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

## Validation

The database validates clean under inGitDB, including foreign-key referential
integrity: every `subdivisions.country`, `settlements.country`, and
`settlements.subdivision` value resolves to an existing record.

## Data license

Geographic data © GeoNames, used under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
See [DATA-LICENSE.md](DATA-LICENSE.md). The inGitDB schema and import code in
this repository are under the repository [LICENSE](LICENSE).
