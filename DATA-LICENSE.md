# Data sources & attribution

The geographic records in this database are generated from open data by
[`cmd/geo-import`](cmd/geo-import). Re-run it to refresh:

```
go run ./cmd/geo-import --out .
```

## GeoNames — CC BY 4.0

Countries, first-order subdivisions, and settlements are derived from the
[GeoNames geographical database](https://www.geonames.org/), licensed under
[Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).

- `countryInfo.txt` — country name, ISO codes, capital, area, population, continent, currency, calling code, TLD, GeoName ID
- `admin1CodesASCII.txt` — first-order administrative divisions (states/provinces/regions)
- `cities15000.zip` — settlements with population ≥ 15,000

© GeoNames, used under CC BY 4.0. This database is a derivative work; GeoNames
does not endorse it.

## Scope

- **Countries** — all ~250 territories in `countryInfo.txt`.
- **Subdivisions** — all first-order administrative divisions worldwide (~3,900).
- **Settlements** — cities ≥ 15,000 population for the showcase countries only
  (default `GB,IE,US,DE`), to keep the repository git-friendly. Change with
  `--settlements`, or `--settlements ""` for none.
