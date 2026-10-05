# Reviewed GeoNames W1 artifact package

`source/artifact-snapshot.json` binds the five existing ordered gzip members to the
unchanged native `geonames.sqlite` logical artifact. Each compressed member is below
25MiB. Concatenate the members in descriptor order and decode the resulting gzip
stream; the members' decoded byte ranges concatenate to the exact reviewed SQLite.
The 75,304,960-byte decoded database is not committed as a separate file or read as
metadata. The descriptor records both aggregate encoded and decoded bytes/hashes.

The package preserves all native fields, keys, model/binding bytes, scoped bridges,
source coverage and visible missing references. The country index remains
`GeoNames:countryInfoISO2`, including provider-retained historic rows. No blanket city
equivalence, repaired foreign keys or fresh representation acceptance is introduced.

`source/generation-snapshot.json` is the exact original generator snapshot, including
its original `outputs.sqlite`, `outputs.chunks` and `outputs.attribution` shape.
`source/generation-validation.json` is byte-for-byte the receipt at landed source
commit `fc53a3537304dd77a4e97162608454b2da2ae241:w1/validation.json`. Packaging metadata
binds both originals, the original generator revision/code hash, and the packaging
script's own earlier immutable code commit. No artifact references its containing
commit. Actual measurements for the four source keys are in
`source/native-key-evidence.json`; they do not grant semantic eligibility.

The original GeoNames descriptor uses `outputs.sqlite.file = "geonames.sqlite"`
and also has an unrelated chunks array. The additive `source/native/<entity>.json`
generation receipts keep that original object unchanged and explicitly name its
bounded source file/hash through `snapshot_association.source` with literal
`output_key: "sqlite"`. They require the separately reviewed native helper extension;
they do not normalize originals or create representation contracts. The alternate
name namespace is a source-local `alternateNameId` generation annotation, without
canonical or user interoperability acceptance. Existing country bridges retain
their original scopes independently of this native generation evidence.

Rebuild only from the reviewed private full-build directory:

```sh
python3 scripts/package_geonames_w1.py build \
  --bundle /path/to/reviewed/full-build --out /path/to/new-package \
  --revision <immutable-packaging-script-commit> --receipt /path/to/measurements.json
python3 scripts/package_geonames_w1.py check
python3 -m unittest discover -s scripts -p 'test_*geonames_w1.py' -v
```

The checker verifies exact Git source/generator association, complete required file
and checksum closure, licence/attribution, original metadata hashes, native key
constraints/counts/values, model key declarations, chunk order and gzip byte identity.
It rejects mutated provenance even when altered metadata is repinned. Full Git
history is required to inspect immutable generator/source blobs; CI fetches it.

Packaging performs no source download and retains externally measured resources
separately from deterministic metadata. The original capture's missing whole-capture
elapsed/RSS measurement remains an explicit whole-wave acceptance gap. Publication
verification downloads only this bounded package at commit-pinned public raw URLs;
it does not redownload the bulk GeoNames/ROR sources.
The separate [packaging validation receipt](../w1/artifact-validation.json) records
the two-package comparison and measurements; it is not an eligibility artifact or
a member of its own checksum closure.

These are native source artifacts. Hosting, runtime serving identifiers/mounts,
Directory publication and representation eligibility remain separate owner gates.
There is no invented database endpoint or production publisher manifest here.
Data is GeoNames CC-BY-4.0: retain `DATA-LICENSE.md` and `ATTRIBUTION.txt` in downloads
and display attribution in consumers. Repository code/model/meaning CC0 licences
remain separate from the geographic data licence.

The checker requires exact physical closure of `source/`, `model/`, `bridges/`, and
`artifacts/geonames-w1-2026-10-05/`, plus named root attribution/licence/key files.
The existing `bridges/accepted-country-bridges.json` may remain only with its exact landed source bytes.
Repository code and `w1/` validation reports are outside these distributable namespaces.
Logical `geonames.sqlite` and `geonames.sqlite.gz` must not exist physically; the SQLite is reconstructed from the pinned members.
Metadata must be a bounded regular file before opening, and native proof counts preserve integer types.
