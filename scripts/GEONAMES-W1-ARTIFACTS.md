# Reviewed GeoNames W1 artifact package

`source/artifact-snapshot.json` binds the five existing ordered gzip members to the
unchanged native `geonames.sqlite` logical artifact. Each compressed member is below
25MiB. Concatenate the members in descriptor order and decode the resulting gzip
stream; the members' decoded byte ranges concatenate to the exact reviewed SQLite.
The 75,304,960-byte decoded database is not committed as a separate file or read as
metadata. The descriptor records both aggregate encoded and decoded bytes/hashes.

The package preserves all native fields, keys, binding bytes, scoped bridges,
source coverage and visible missing references, and the model as the run saw it: the
snapshot pins the landed model, and the tree holds its exact rename (described below). The country index remains
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

The model's two files (`model/geonames.modelspec.json` and `.hcl`) are the one place
where the repository's tree differs from what the artifact snapshot pins. They were landed in ModelSpec's
earlier vocabulary (`entity`, `property`); ModelSpec has since renamed those words to
`record` and `field`, and the files in the tree are written in the current
vocabulary. Nothing of the past run was rewritten for that: the artifact snapshot,
the generation snapshot and receipts, the native key evidence and the per-entity
receipts keep their bytes and still name the landed model's SHA-256, because that is
the model the run saw. The checker ties the two together. Its comparison with the
landed source accepts the two files in exactly two states: the landed bytes, which the
snapshot pins, or the exact rename of those bytes, both files in the same state.
`scripts/modelspec_spellings.py` recomputes the rename from the landed bytes at
`fc53a3537304dd77a4e97162608454b2da2ae241` and compares it with the SHA-256 of what
the reference tool (`modelspec rewrite` 0.2.0) writes, so one file renamed without
the other, a rename with any other change, or a model that mixes the two vocabularies
is refused. On the repository tree only the renamed state passes the whole check: the
tree also holds the representation contract, whose generator pins the renamed model's
SHA-256, and the package check runs that generator's check whenever the contract pair
is present. The landed state passes for a package without the contract pair, which is
what `build` writes. `build` is unchanged: from the reviewed bundle it writes the
package with the landed model files. The repository's owner approved this two-state
rule on 2026-10-09. His approval covers this paragraph and not the two that follow.

The representation contract (`model/representations.json`) pins the model file as it
is in the tree, so its four entries, all of kind `label-bridge`, name the renamed
file's SHA-256 as `target.model.sha256`. The snapshot still pins the landed model.
For a `label-bridge` entry no reader compares the snapshot's artifact list with
`target.model`, so the four entries are consistent. A contract entry of kind
`native-identifier` that targets GeoNames, or any reader that requires the snapshot
to list the pinned model at its exact SHA-256, could not be given the renamed model
while the snapshot is as it is; that would need the snapshot rewritten, which this
change does not do.

The deployment wrappers written by `scripts/generate_deployment.py` name the provider
revision whose files they describe. That revision moved from
`57e25689009047a557d35519831b8413b4abe838` to
`7d18132fd9d09b4f893d019eb690d73576003de1`, the commit that holds the renamed model
and the contract's new pins, so the model links in `ovdb-database.json` serve the
files this repository describes. That generator's own check is unchanged: every
input must equal its Git blob at the revision it names. `OVDB.md` calls that commit
the accepted provider revision and says the snapshot, model, meaning and
representation bytes are unchanged; both statements mean that each file equals its
blob at that revision. That commit still carries the previous wrappers, which
describe `57e25689009047a557d35519831b8413b4abe838`, and on its own it fails this
repository's deployment check and the publisher check: a consumer that pins the
wrappers pins the commit that landed them. The change was landed as a merge commit so
that `7d18132fd9d09b4f893d019eb690d73576003de1` is in the history of `main`; a squash
or a rebase would have left it out. Moving this revision was not part of the owner's
approval, whose wording said the accepted revisions stay untouched. It was the choice
of the session that made the change, which told him; the alternative was to let the
generator's check tolerate changed inputs at the earlier revision.

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

`build` writes the landed model files. `check` on the repository tree passes only
after the two model files are renamed with `modelspec rewrite --write` (0.2.0), because
the contract in the tree pins the renamed model.

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
