#!/usr/bin/env python3
"""Reproduce candidate deployment wrappers from accepted local native artifacts.

No network, source import, semantic reconciliation, runtime preparation or
query-availability promotion occurs. The accepted native SQLite is streamed
into a temporary file solely for read-only schema/count introspection.
"""
import argparse
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
METADATA_LIMIT = 2 * 1024**2
FILE_LIMIT = 25 * 1024**2
CONFIG = {
    "geo-ingitdb": ("geonames", "GeoNames W1", "57e25689009047a557d35519831b8413b4abe838", "CC-BY-4.0", 8),
    "ror-ingitdb": ("ror", "Research Organization Registry", "24bcbcb5f0ba715d72d604e9d4d296766e5f4ca7", "CC0-1.0 AND CC-BY-4.0", 3),
}
OUTPUTS = ("manifest.json", "metadata/contract.json", "metadata/checksums.json",
           "metadata/artifact.json", "ovdb-database.json", "ovdb.yaml", "OVDB.md")
GEONAMES_DIAGNOSTICS = {"required_ror_places", "missing_ror_places", "missing_admin1_references",
                       "missing_country_references", "orphan_admin1"}
SCHEMA_PATH = "schemas/ovdb-database-draft-1.schema.json"
SCHEMA_SHA256 = "2424ef00acd462ab5a8abc546fe2d1fffbbb5397e312332aedc77b3e73109488"


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def decode(data):
    if len(data) > METADATA_LIMIT:
        raise ValueError("metadata exceeds 2MiB")
    def reject(value):
        raise ValueError("nonfinite JSON value")
    return json.loads(data, object_pairs_hook=unique_object, parse_constant=reject)


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode()


def safe_path(root, relative):
    if not isinstance(relative, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", relative):
        raise ValueError("invalid relative path")
    path = Path(root)
    for part in relative.split("/"):
        if part in ("", ".", ".."):
            raise ValueError("unsafe relative path")
        path /= part
        if path.is_symlink():
            raise ValueError("symbolic artifact path")
    return path


def file_pin(root, relative, ceiling=METADATA_LIMIT):
    path = safe_path(root, relative)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= ceiling:
        raise ValueError("expected bounded regular file")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"bytes": info.st_size, "sha256": digest}


def accepted_inputs(root, revision):
    """Rehash all accepted data/model/source/rights inputs against exact Git blobs."""
    output = subprocess.run(["git", "-C", str(root), "ls-tree", "-rz", revision],
                            check=True, capture_output=True).stdout
    selected = {}
    for entry in output.split(b"\0"):
        if not entry:
            continue
        header, path_bytes = entry.split(b"\t", 1)
        relative = path_bytes.decode()
        if relative.startswith(("source/", "model/", "bridges/", "artifacts/")) or relative in (
                "ATTRIBUTION.txt", "country-keys.json", "LICENSE", "DATA-LICENSE.md"):
            mode, kind, blob = header.split()
            if mode not in (b"100644", b"100755") or kind != b"blob":
                raise ValueError("accepted input is not a regular Git blob")
            path = safe_path(root, relative)
            pin = file_pin(root, relative, FILE_LIMIT if relative.startswith("artifacts/") else METADATA_LIMIT)
            actual = subprocess.run(["git", "hash-object", "--", str(path)], check=True,
                                    capture_output=True).stdout.strip()
            if actual != blob:
                raise ValueError(f"accepted input changed: {relative}")
            selected[relative] = pin
    if "source/artifact-snapshot.json" not in selected or "model/representations.json" not in selected:
        raise ValueError("missing accepted snapshot or representation attachment")
    attachment = decode(safe_path(root, "source/representation-attachment.json").read_bytes())
    if attachment != {"path": "model/representations.json", "sha256": selected["model/representations.json"]["sha256"]}:
        raise ValueError("representation attachment mismatch")
    return selected


def reconstruct(root, descriptor, output):
    """Verify ordered physical chunks, concatenated gzip stream and native bytes."""
    if descriptor.get("compression") != "gzip" or not 0 < descriptor["decodedBytes"] <= 256 * 1024**2:
        raise ValueError("unsupported native reconstruction or decoded limit")
    chunks = descriptor["chunks"]
    if not isinstance(chunks, list) or not chunks or len(chunks) > 10000:
        raise ValueError("invalid ordered chunk list")
    seen, encoded_hash, total = set(), hashlib.sha256(), 0
    with tempfile.TemporaryFile() as encoded:
        for chunk in chunks:
            relative = chunk["path"]
            if relative in seen or file_pin(root, relative, FILE_LIMIT) != {k: chunk[k] for k in ("bytes", "sha256")}:
                raise ValueError("duplicate or corrupt native chunk")
            seen.add(relative)
            with safe_path(root, relative).open("rb") as stream:
                while block := stream.read(1024**2):
                    total += len(block)
                    if total > descriptor["bytes"] or total > 512 * 1024**2:
                        raise ValueError("encoded native limit")
                    encoded_hash.update(block)
                    encoded.write(block)
        if total != descriptor["bytes"] or encoded_hash.hexdigest() != descriptor["sha256"]:
            raise ValueError("ordered encoded native hash mismatch")
        encoded.seek(0)
        decoded_hash, count = hashlib.sha256(), 0
        with gzip.GzipFile(fileobj=encoded) as stream, Path(output).open("wb") as native:
            while block := stream.read(1024**2):
                count += len(block)
                if count > descriptor["decodedBytes"]:
                    raise ValueError("decoded native limit")
                decoded_hash.update(block)
                native.write(block)
        if count != descriptor["decodedBytes"] or decoded_hash.hexdigest() != descriptor["decodedSha256"]:
            raise ValueError("decoded native hash mismatch")


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def native_schema(database):
    tables = []
    with closing(sqlite3.connect(f"file:{Path(database).resolve()}?mode=ro&immutable=1", uri=True)) as db:
        for name, kind in db.execute("SELECT name,type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            columns = [{"name": c[1], "type": c[2], "nullable": not bool(c[3] or c[5]),
                        "primaryKey": bool(c[5]), "primaryKeyPosition": c[5] or None,
                        "defaultValue": c[4]} for c in db.execute(f"PRAGMA table_info({quote(name)})")]
            primary = sorted([{"column": c["name"], "position": c["primaryKeyPosition"]}
                              for c in columns if c["primaryKey"]], key=lambda c: c["position"])
            foreign = [{"column": f[3], "table": f[2], "referencedColumn": f[4],
                        "constraint": f[0], "position": f[1]} for f in db.execute(f"PRAGMA foreign_key_list({quote(name)})")]
            tables.append({"name": name, "kind": kind, "description": "", "columns": columns,
                           "primaryKey": primary, "foreignKeys": foreign,
                           "rowCount": db.execute(f"SELECT count(*) FROM {quote(name)}").fetchone()[0]})
    return tables


def build(root=ROOT):
    root = Path(root)
    local_id, title, revision, licence, expected_tables = CONFIG[root.name]
    repository = "https://github.com/ingitdb/" + root.name
    raw_base = f"https://raw.githubusercontent.com/ingitdb/{root.name}/{revision}/"
    inputs = accepted_inputs(root, revision)
    schema_pin = file_pin(root, SCHEMA_PATH)
    if schema_pin["sha256"] != SCHEMA_SHA256:
        raise ValueError("frozen public database schema changed")
    inputs[SCHEMA_PATH] = schema_pin
    snapshot = decode(safe_path(root, "source/artifact-snapshot.json").read_bytes())
    artifact = snapshot["sqlite"]
    with tempfile.TemporaryDirectory(prefix="w1-wrapper-") as scratch:
        native = Path(scratch) / artifact["path"]
        reconstruct(root, artifact, native)
        tables = native_schema(native)
    model = decode(safe_path(root, f"model/{local_id}.modelspec.json").read_bytes())
    diagnostics = GEONAMES_DIAGNOSTICS if local_id == "geonames" else set()
    if any(t["kind"] != "table" for t in tables) or set(t["name"] for t in tables) != set(model["entities"]) | diagnostics:
        raise ValueError("native table/model entity inventory differs")
    public_tables = [t for t in tables if t["name"] in model["entities"]]
    if len(public_tables) != expected_tables:
        raise ValueError("accepted public physical table inventory changed")
    for table in tables:
        if table["rowCount"] != snapshot["counts"][table["name"]]:
            raise ValueError("native count differs from accepted snapshot")
    profile = f"https://cloud.openvaultdb.com/ovdb/dbs/{local_id}"
    api = f"https://cloud.openvaultdb.com/v1/databases/{local_id}"
    description = ("Accepted pinned native source projection. Query availability awaits reviewed live runtime proof; "
                   "native identifiers, source values, exceptions and multiplicity are preserved.")
    notes = ("Unchanged generated native SQLite, reconstructed only from the accepted ordered chunks. "
             "The logical SQLite path is not a hosted download file. Original upstream inputs, generator, "
             "hashes and coverage remain in source/artifact-snapshot.json and its referenced source receipts. "
             "Serving artifacts and public query proof have separate future receipts.")
    source = {"repository": repository, "revision": revision, "path": artifact["path"],
              "sha256": artifact["decodedSha256"], "license": licence, "licenseFile": "DATA-LICENSE.md", "notes": notes}
    manifest = {"contractVersion": 1, "id": local_id, "name": title, "description": description,
                "source": source, "dataFile": artifact["path"], "capabilities": {"exports": ["sqlite"],
                "ovdb": {"available": False, "canonicalUrl": profile, "serverId": "https://cloud.openvaultdb.com/ovdb/",
                         "serverDbBaseUrl": profile, "connection": api, "query": False,
                         "readOnly": True, "deploymentVerified": False}}}
    descriptor = {"format": "ovdb-database/draft-1", "id": profile, "localId": local_id,
                  "serverId": "https://cloud.openvaultdb.com/ovdb/", "serverDbBaseUrl": profile,
                  "title": title, "description": description, "homepage": repository, "apiUrl": api,
                  "capabilities": {"read": True, "query": False, "write": False},
                  "deployment": {"engine": "sqlite", "url": profile,
                                 "discovery": "https://cloud.openvaultdb.com/.well-known/openvaultdb"},
                  "model": {"id": f"modelspec://github.com/ingitdb/{root.name}/{local_id}",
                            "url": raw_base + f"model/{local_id}.modelspec.json", "hclUrl": raw_base + f"model/{local_id}.modelspec.hcl"},
                  "meaning": {"id": f"meaning://github.com/ingitdb/{root.name}", "url": raw_base + f"model/{local_id}.meaning.yaml"},
                  "publisher": {"name": "inGitDB", "url": "https://github.com/ingitdb", "repository": repository},
                  "provenance": {k: v for k, v in source.items() if k != "licenseFile"},
                  "licences": {"data": licence, "model": "CC0-1.0", "meaning": "CC0-1.0"}, "recordsets": public_tables}
    contract = {"contractVersion": 1, "manifest": manifest,
                "schema": {"contractVersion": 1, "database": {"id": local_id, "name": title}, "source": source, "tables": tables},
                "exports": [artifact]}
    rights_paths = ["DATA-LICENSE.md"] + (["ATTRIBUTION.txt"] if "ATTRIBUTION.txt" in inputs else [])
    downloads = {"repository": repository, "revision": revision,
                 "snapshot": {"path": "source/artifact-snapshot.json", **inputs["source/artifact-snapshot.json"]},
                 "sqlite": artifact, "reconstruction": "Concatenate chunks in listed order, then decode the complete gzip stream; verify every chunk, encoded stream and decoded SQLite size and SHA256.",
                 "downloads": [{**chunk, "url": raw_base + chunk["path"]} for chunk in artifact["chunks"]],
                 "attribution": [{"path": path, **inputs[path], "url": raw_base + path} for path in rights_paths]}
    attachment = inputs["model/representations.json"]["sha256"]
    yaml = f'''# Reproducible candidate provider input; public query proof remains pending.
format: ovdb-manifest/draft-1
id: {local_id}
title: {title}
description: {description}
homepage: {repository}
url: {profile}
deployment:
  url: {profile}
  engine: sqlite
  discovery: https://cloud.openvaultdb.com/.well-known/openvaultdb
  recordset_page: {profile}/collections/{{name}}
model:
  address: modelspec://github.com/ingitdb/{root.name}/{local_id}
  modelspec: model/{local_id}.modelspec.json
  hcl: model/{local_id}.modelspec.hcl
meaning:
  file: model/{local_id}.meaning.yaml
  graph:
    id: {local_id}
    address: meaning://github.com/ingitdb/{root.name}
publisher:
  name: inGitDB
  url: https://github.com/ingitdb
  repository: {repository}
licences:
  data: {licence}
  model: CC0-1.0
  meaning: CC0-1.0
representation_contract:
  path: model/representations.json
  sha256: {attachment}
recordsets:
''' + ''.join(f'  - {table["name"]}\n' for table in public_tables)
    documentation = f'''---
ovdb: 1
publish: [./ovdb.yaml]
---
# {title} candidate deployment metadata

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
instructions and attribution downloads at accepted provider revision `{revision}`.
There is no physical download URL for `{artifact["path"]}`. Keep DATA-LICENSE.md
{'and ATTRIBUTION.txt ' if len(rights_paths) > 1 else ''}with reconstructed/downloaded data. Data licence: `{licence}`;
code/model/meaning rights remain separate. The snapshot and model/meaning/representation
bytes are unchanged; structural validation grants no new semantic acceptance.

The full native schema has {len(tables)} physical tables; {expected_tables} reviewed
model-backed tables are listed in publisher/public descriptor metadata.
{'Four native GeoNames logical recordsets and four accepted country bridge tables retain their original scope. Five unchanged diagnostic tables remain fully recorded in metadata/contract.json and the original native SQLite. Runtime integration must select the eight reviewed collections with an independently reviewed generic serving seam, preserving the five diagnostic tables without mounting them as public collections.' if local_id == 'geonames' else 'The three physical ROR tables retain one logical organizations recordset, all statuses and original location/relationship ordinals.'}
The accepted five-logical-recordset W1 scope is shared across the two providers.
Native `id`/keys remain source fields; future generated serving keys are separate.
`deployment.recordset_page` exists only in publisher YAML; JSON deployment stays
closed to engine/url/discovery. No new provenance roles are assigned.

Reproduce offline with `python3 scripts/generate_deployment.py`; verify without
writing with `python3 scripts/generate_deployment.py --check`. This streams local
accepted chunks into a temporary SQLite, reads its native schema/counts in read-only
mode, explicitly rehashes unchanged inputs and deletes temporary reconstruction.
No upstream download, source rebuild or runtime deployment occurs.
'''
    outputs = {"manifest.json": encode(manifest), "metadata/contract.json": encode(contract),
               "ovdb-database.json": encode(descriptor), "metadata/artifact.json": encode(downloads),
               "ovdb.yaml": yaml.encode(), "OVDB.md": documentation.encode()}
    checksums = dict(inputs)
    checksums.update({path: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for path, data in outputs.items()})
    outputs["metadata/checksums.json"] = encode({"contractVersion": 1, "files": checksums})
    if sum(len(data) for data in outputs.values()) + sum(pin["bytes"] for path, pin in inputs.items()
            if not path.startswith("artifacts/")) > METADATA_LIMIT:
        raise ValueError("combined provider metadata exceeds 2MiB")
    return outputs


def generate(check=False, root=ROOT):
    outputs = build(root)
    for relative, data in outputs.items():
        path = safe_path(root, relative)
        if check:
            if not path.is_file() or path.read_bytes() != data:
                raise ValueError(f"deployment wrapper differs: {relative}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return outputs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    try:
        generate(arguments.check)
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")
    print("Candidate wrappers reproduced; runtime, semantic and publication admission remain separate.")
