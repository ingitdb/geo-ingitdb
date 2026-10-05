---
ovdb: 1
publish: [./ovdb.yaml]
---
# GeoNames W1 candidate deployment metadata

The explicit list opts [publisher YAML](ovdb.yaml) into publisher ingestion.
The separate [public database descriptor](ovdb-database.json) declares discovery
metadata and candidate query status; it is not a publisher manifest. These
wrappers allocate the existing Cloud identities; they do not prove live hosting,
Directory admission or query availability. The candidate JSON declares query=false
and manifest.json declares available/query/deploymentVerified=false. Future runtime
handoff must explicitly use requirePublishedQuery:false for candidate smoke only.
The runtime owner must supply released dependencies, route/homepage checks,
native serving-key preservation, immutable pin guards, read-only/CORS and measured
capacity proof before live publication. Final admission also requires independent
carry-forward review and the released default OVDB publisher validator.

`metadata/artifact.json` lists actual immutable chunk URLs, hashes, reconstruction
instructions and attribution downloads at accepted provider revision `57e25689009047a557d35519831b8413b4abe838`.
There is no physical download URL for `geonames.sqlite`. Keep DATA-LICENSE.md
and ATTRIBUTION.txt with reconstructed/downloaded data. Data licence: `CC-BY-4.0`;
code/model/meaning rights remain separate. The snapshot and model/meaning/representation
bytes are unchanged; structural validation grants no new semantic acceptance.

The full native schema has 13 physical tables; 8 reviewed
model-backed tables are listed in publisher/public descriptor metadata.
Four native GeoNames logical recordsets and four accepted country bridge tables retain their original scope. Five unchanged diagnostic tables remain fully recorded in metadata/contract.json and the original native SQLite. Runtime integration must select the eight reviewed collections with an independently reviewed generic serving seam, preserving the five diagnostic tables without mounting them as public collections.
The accepted five-logical-recordset W1 scope is shared across the two providers.
Native `id`/keys remain source fields; future generated serving keys are separate.
`deployment.recordset_page` exists only in publisher YAML; JSON deployment stays
closed to engine/url/discovery. No new provenance roles are assigned.

Reproduce offline with `python3 scripts/generate_deployment.py`; verify without
writing with `python3 scripts/generate_deployment.py --check`. This streams local
accepted chunks into a temporary SQLite, reads its native schema/counts in read-only
mode, explicitly rehashes unchanged inputs and deletes temporary reconstruction.
No upstream download, source rebuild or runtime deployment occurs.
