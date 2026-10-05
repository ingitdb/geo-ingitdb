#!/usr/bin/env python3
"""Publish reviewed GeoNames bytes and bounded source evidence, without eligibility."""
import argparse
from contextlib import closing
import gzip
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import resource
import shutil
import sqlite3
import subprocess
import tempfile
import time

REPOSITORY = "https://github.com/ingitdb/geo-ingitdb"
SOURCE_REVISION = "fc53a3537304dd77a4e97162608454b2da2ae241"
SOURCE_SNAPSHOT_SHA256 = "d564721b80809537424d17b2bf2276697593a3cb380ea020f33c070e1ff0e7ab"
SCRIPT = "scripts/package_geonames_w1.py"
PREFIX = "artifacts/geonames-w1-2026-10-05/native/"
METADATA_LIMIT, FILE_LIMIT = 2 * 1024**2, 25 * 1024**2
SQLITE_LIMIT, RSS_LIMIT, DISK_LIMIT = 256 * 1024**2, 512 * 1024**2, 8 * 1024**3
SOURCE_METADATA = ["ATTRIBUTION.txt", "country-keys.json", "bridges/accepted-inputs.json",
                   *["bridges/" + name + ".json" for name in (
                       "geonames_chinook_customer_country", "geonames_northwind_customer_country",
                       "geonames_northwind_order_country", "geonames_pubs_publisher_country")],
                   "model/geonames.modelspec.hcl", "model/geonames.modelspec.json", "model/geonames.meaning.yaml"]
REQUIRED_METADATA = [*SOURCE_METADATA, "LICENSE", "DATA-LICENSE.md", "source/generation-snapshot.json",
                     "source/generation-validation.json", "source/native-key-evidence.json"]
KEYS = {"geonames_countries": "iso", "geonames_admin1": "code", "geonames_places": "geonameid",
        "geonames_alternate_names": "alternate_name_id"}
DATA_LICENSE = """# GeoNames data licence and attribution

GeoNames geographic data is licensed under [Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).
Source and attribution: [GeoNames](https://www.geonames.org/), [official dump readme](https://download.geonames.org/export/dump/readme.txt).
Keep this file and ATTRIBUTION.txt with downloaded/reconstructed data and display attribution in consumers.

Changes: the pinned source files are projected into relational SQLite, with complete countryInfo/admin1,
global cities5000 plus full pinned ROR location references, and every alias for retained features.
Native values, feature classes/codes and unmatched references remain intact. Chunk packaging changes no data.
No endorsement, warranty, atomic upstream snapshot or semantic eligibility is asserted.

The ROR v2.13 metadata used for the place ID union is CC0; GeoNames attribution remains applicable.
Code uses the repository LICENSE (CC0-1.0). Models and MeaningGraph files retain their declared CC0-1.0
licence separately; that code/model licence does not relicense GeoNames data.
"""


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_path(root, relative):
    if not isinstance(relative, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", relative):
        raise ValueError("invalid relative artifact path")
    if any(part in ("", ".", "..") for part in relative.split("/")):
        raise ValueError("unsafe artifact path")
    cursor = Path(root)
    for part in relative.split("/"):
        cursor /= part
        if cursor.is_symlink():
            raise ValueError("artifact path uses symlink")
    if not cursor.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("artifact path escapes root")
    return cursor


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("nonfinite JSON number")
    return value


def reject_constant(text):
    raise ValueError("nonstandard JSON constant: " + text)


def decode_json(data):
    if len(data) > METADATA_LIMIT:
        raise ValueError("metadata exceeds 2MiB")
    return json.loads(data, object_pairs_hook=unique_object, parse_float=finite_float, parse_constant=reject_constant)


def read_json(path):
    with Path(path).open("rb") as stream:
        return decode_json(stream.read(METADATA_LIMIT + 1))


def write_json(path, value):
    data = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()
    if len(data) > METADATA_LIMIT:
        raise ValueError("metadata exceeds 2MiB")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def git_blob(root, revision, path):
    safe_path(root, path)
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("immutable Git commit required")
    kind = subprocess.run(["git", "-C", str(root), "cat-file", "-t", revision], check=True, capture_output=True).stdout.strip()
    if kind != b"commit":
        raise ValueError("revision is not a Git commit")
    return subprocess.run(["git", "-C", str(root), "show", f"{revision}:{path}"], check=True, capture_output=True).stdout


def generator(root, revision):
    data = git_blob(root, revision, SCRIPT)
    if data != safe_path(root, SCRIPT).read_bytes():
        raise ValueError("packager differs from committed generator")
    return {"repository": REPOSITORY, "revision": revision, "script": SCRIPT,
            "sha256": hashlib.sha256(data).hexdigest()}


def source_association(root, original, source):
    original_bytes = git_blob(root, SOURCE_REVISION, "w1/validation.json")
    if original_bytes != original:
        raise ValueError("original source generation receipt differs from landed Git pin")
    validation = decode_json(original)
    if hashlib.sha256(source).hexdigest() != SOURCE_SNAPSHOT_SHA256:
        raise ValueError("original generation snapshot hash mismatch")
    snapshot = decode_json(source)
    pins = {item["path"]: item for item in validation["artifacts"]}
    if pins.get("snapshot.json", {}).get("sha256") != SOURCE_SNAPSHOT_SHA256:
        raise ValueError("original snapshot not bound by landed receipt")
    if snapshot["generator"] != validation["generator"] or snapshot["counts"] != validation["counts"]:
        raise ValueError("original generator/count association mismatch")
    if snapshot["importer_revision"] != snapshot["generator"]["revision"]:
        raise ValueError("source importer revision mismatch")
    importer = git_blob(root, snapshot["importer_revision"], "scripts/geonames_w1.py")
    if hashlib.sha256(importer).hexdigest() != snapshot["importer_sha256"] or snapshot["importer_sha256"] != validation["importer_sha256"]:
        raise ValueError("original importer Git SHA mismatch")
    for field in ("sources", "ror_closure", "download_bytes", "source_scan_counts"):
        if snapshot[field] != validation[field]:
            raise ValueError("original source input association mismatch")
    for item in snapshot["artifacts"]:
        if pins.get(item["path"], {}).get("sha256") != item["sha256"]:
            raise ValueError("original snapshot artifact disagrees with generation receipt")
    return validation, snapshot


def native_evidence(database, snapshot, source_ref):
    expected = snapshot["outputs"]["sqlite"]
    if database.stat().st_size != expected["bytes"] or database.stat().st_size > SQLITE_LIMIT or digest(database) != expected["sha256"]:
        raise ValueError("SQLite differs from reviewed original dataset")
    proofs = []
    with closing(sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity failure")
        for table, key in KEYS.items():
            columns = {row[1]: row for row in db.execute(f'PRAGMA table_info("{table}")')}
            if columns.get(key, (None,) * 6)[2:6] != ("TEXT", 1, None, 1):
                raise ValueError("native key must be TEXT NOT NULL PRIMARY KEY")
            count, missing = db.execute(f'SELECT count(*),sum("{key}" IS NULL OR "{key}"=\'\') FROM "{table}"').fetchone()
            duplicates = db.execute(f'SELECT count(*) FROM (SELECT "{key}" FROM "{table}" GROUP BY "{key}" HAVING count(*)>1)').fetchone()[0]
            if count != snapshot["counts"][table] or duplicates or (missing or 0):
                raise ValueError("native key count/uniqueness association mismatch")
            invalid = 0
            for (value,) in db.execute(f'SELECT "{key}" FROM "{table}"'):
                pattern = r"[A-Z]{2}" if table == "geonames_countries" else r"[A-Z]{2}\.[^.]+" if table == "geonames_admin1" else r"[1-9][0-9]*"
                invalid += type(value) is not str or re.fullmatch(pattern, value) is None
            if invalid:
                raise ValueError("non-native source key")
            proofs.append({"module": "geonames", "entity": table, "property": key, "records": count,
                           "duplicates": duplicates, "missing": missing or 0, "invalid_native_values": invalid,
                           "constraint": "TEXT NOT NULL PRIMARY KEY"})
        countries = [row[0] for row in db.execute("SELECT iso FROM geonames_countries ORDER BY iso COLLATE BINARY")]
    return {"format": "geonames-native-key-evidence-1", "generation_provider": {"repository": REPOSITORY, "revision": SOURCE_REVISION},
            "original_snapshot": source_ref, "original_generator": snapshot["generator"],
            "original_importer_sha256": snapshot["importer_sha256"], "dataset": {"path": "geonames.sqlite", "sha256": expected["sha256"]},
            "model": next(item for item in snapshot["artifacts"] if item["path"] == "model/geonames.modelspec.json"),
            "binding": next(item for item in snapshot["artifacts"] if item["path"] == "model/geonames.meaning.yaml"),
            "sqlite_integrity": "ok", "keys": proofs, "country_keys": {"namespace": "GeoNames:countryInfoISO2", "keys": countries},
            "eligibility": "measurement evidence only; not a native representation contract or helper-compatible original snapshot"}


def chunk_paths(source):
    chunks = source["outputs"]["chunks"]
    expected = [f"geonames.sqlite.{i:04d}.gz" for i in range(len(chunks))]
    if not 0 < len(chunks) <= 10000 or [item["file"] for item in chunks] != expected:
        raise ValueError("original chunks missing/duplicate/out-of-order")
    return [{"path": PREFIX + item["file"], "sha256": item["sha256"], "bytes": item["bytes"]} for item in chunks]


def reconstruct(root, descriptor, destination=None):
    encoded_hash, decoded_hash, encoded_count, decoded_count = hashlib.sha256(), hashlib.sha256(), 0, 0
    with tempfile.TemporaryFile() as encoded:
        for item in descriptor["chunks"]:
            path = safe_path(root, item["path"])
            if type(item["bytes"]) is not int or not 0 < item["bytes"] <= FILE_LIMIT or path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
                raise ValueError("chunk hash/size/per-file guard mismatch")
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024**2), b""):
                    encoded.write(block)
                    encoded_hash.update(block)
                    encoded_count += len(block)
                    if encoded_count > RSS_LIMIT:
                        raise ValueError("encoded byte budget exceeded")
        encoded.seek(0)
        with gzip.GzipFile(fileobj=encoded, mode="rb") as stream:
            for block in iter(lambda: stream.read(1024**2), b""):
                decoded_count += len(block)
                if decoded_count > SQLITE_LIMIT or decoded_count > descriptor["decodedBytes"]:
                    raise ValueError("decoded byte budget exceeded")
                decoded_hash.update(block)
                if destination is not None:
                    destination.write(block)
    if (encoded_count, encoded_hash.hexdigest(), decoded_count, decoded_hash.hexdigest()) != (
            descriptor["bytes"], descriptor["sha256"], descriptor["decodedBytes"], descriptor["decodedSha256"]):
        raise ValueError("encoded aggregate or decoded SQLite mismatch")


def verify_bundle(root, code_root=None):
    root = Path(root)
    code_root = Path(code_root) if code_root else Path(__file__).resolve().parents[1]
    snapshot = read_json(safe_path(root, "source/artifact-snapshot.json"))
    if snapshot.get("generator") != generator(code_root, snapshot["generator"].get("revision", "")):
        raise ValueError("packaging generator Git association mismatch")
    original = safe_path(root, "source/generation-validation.json").read_bytes()
    original_source = safe_path(root, "source/generation-snapshot.json").read_bytes()
    validation, source = source_association(code_root, original, original_source)
    association = {"repository": REPOSITORY, "revision": SOURCE_REVISION,
                   "original_validation_sha256": hashlib.sha256(original).hexdigest(),
                   "original_snapshot_sha256": SOURCE_SNAPSHOT_SHA256}
    if snapshot.get("source_generation") != association or snapshot.get("counts") != source["counts"]:
        raise ValueError("source generation repository/revision/receipt association mismatch")
    chunks = chunk_paths(source)
    required = set(REQUIRED_METADATA) | {item["path"] for item in chunks} | {"geonames.sqlite"}
    pins = {}
    for item in snapshot["artifacts"]:
        name = item["path"]
        safe_path(root, name)
        if name in pins or not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")) or type(item.get("bytes")) is not int:
            raise ValueError("duplicate/invalid artifact pin")
        pins[name] = item
        if name == "geonames.sqlite":
            if item != {"path": name, "kind": "reconstructed", "sha256": source["outputs"]["sqlite"]["sha256"], "bytes": source["outputs"]["sqlite"]["bytes"]}:
                raise ValueError("native dataset reconstructed association mismatch")
        else:
            path = safe_path(root, name)
            ceiling = FILE_LIMIT if name.startswith(PREFIX) else METADATA_LIMIT
            if not 0 < item["bytes"] <= ceiling or path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
                raise ValueError("required artifact bytes/hash mismatch")
    if set(pins) != required:
        raise ValueError("complete required artifact closure missing or widened")
    if safe_path(root, "DATA-LICENSE.md").read_text() != DATA_LICENSE or safe_path(root, "LICENSE").read_bytes() != git_blob(code_root, SOURCE_REVISION, "LICENSE"):
        raise ValueError("data/code licence association mismatch")
    source_pins = {item["path"]: item["sha256"] for item in validation["artifacts"]}
    for name in SOURCE_METADATA:
        if pins[name]["sha256"] != source_pins[name]:
            raise ValueError("source model/binding/key/bridge/attribution changed")
    for name in ["model/geonames.modelspec.hcl", "model/geonames.modelspec.json", "model/geonames.meaning.yaml"]:
        if safe_path(root, name).read_bytes() != git_blob(code_root, SOURCE_REVISION, name):
            raise ValueError("model/binding differs from landed source")
    descriptor = snapshot["sqlite"]
    if descriptor.get("chunks") != chunks or descriptor.get("path") != "geonames.sqlite" or descriptor.get("encodedPath") != "geonames.sqlite.gz" or descriptor.get("compression") != "gzip":
        raise ValueError("invalid gzip reconstruction/chunk order descriptor")
    if (descriptor.get("decodedBytes"), descriptor.get("decodedSha256")) != (source["outputs"]["sqlite"]["bytes"], source["outputs"]["sqlite"]["sha256"]):
        raise ValueError("decoded artifact differs from original generation")
    # The gzip stream contains the five original members. No native dataset is read as metadata.
    with tempfile.TemporaryDirectory(prefix="geonames-check-") as temporary:
        database = Path(temporary) / "native.sqlite"
        with database.open("wb") as output:
            reconstruct(root, descriptor, output)
        proof = native_evidence(database, source, {"path": "source/generation-snapshot.json", "sha256": SOURCE_SNAPSHOT_SHA256})
    if read_json(safe_path(root, "source/native-key-evidence.json")) != proof:
        raise ValueError("native key evidence/generation provider association mismatch")
    if read_json(safe_path(root, "country-keys.json")) != proof["country_keys"]:
        raise ValueError("native country index association mismatch")
    model = read_json(safe_path(root, "model/geonames.modelspec.json"))
    for table, key in KEYS.items():
        entity = model["entities"][table]
        if model["module"]["name"] != "geonames" or entity["key"] != [key] or entity["properties"][key].get("required") is not True or entity["properties"][key]["type"] != "string":
            raise ValueError("model native key mismatch")
    if sum(path.stat().st_size for path in root.rglob("*") if path.is_file() and path.name != "geonames.sqlite" and not path.name.endswith(".gz") and path.relative_to(root).as_posix() in (set(REQUIRED_METADATA) | {"source/artifact-snapshot.json"})) > METADATA_LIMIT:
        raise ValueError("combined metadata exceeds 2MiB")
    return snapshot


def package(root, bundle, output, revision, receipt):
    root, bundle, output, receipt = map(Path, (root, bundle, output, receipt))
    if output.exists() or receipt.exists():
        raise ValueError("output or measurement receipt already exists")
    if receipt.resolve().is_relative_to(output.resolve()):
        raise ValueError("measurement receipt must remain outside deterministic package")
    started = time.monotonic()
    tool = generator(root, revision)
    original = git_blob(root, SOURCE_REVISION, "w1/validation.json")
    source_bytes = safe_path(bundle, "snapshot.json").read_bytes()
    validation, source = source_association(root, original, source_bytes)
    for pin in validation["artifacts"]:
        path = safe_path(bundle, pin["path"])
        if path.stat().st_size != pin["bytes"] or digest(path) != pin["sha256"]:
            raise ValueError("reviewed input bundle artifact differs")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".geonames-package-", dir=output.parent))
    try:
        for name in SOURCE_METADATA:
            destination = safe_path(stage, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_path(bundle, name), destination)
        (stage / "LICENSE").write_bytes(git_blob(root, SOURCE_REVISION, "LICENSE"))
        (stage / "DATA-LICENSE.md").write_text(DATA_LICENSE)
        (stage / "source").mkdir()
        (stage / "source/generation-validation.json").write_bytes(original)
        (stage / "source/generation-snapshot.json").write_bytes(source_bytes)
        proof = native_evidence(safe_path(bundle, "geonames.sqlite"), source,
                                {"path": "source/generation-snapshot.json", "sha256": SOURCE_SNAPSHOT_SHA256})
        write_json(stage / "source/native-key-evidence.json", proof)
        chunks = chunk_paths(source)
        total, encoded = 0, hashlib.sha256()
        for item, original_chunk in zip(chunks, source["outputs"]["chunks"]):
            destination = safe_path(stage, item["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_path(bundle, original_chunk["file"]), destination)
            with destination.open("rb") as stream:
                for block in iter(lambda: stream.read(1024**2), b""):
                    total += len(block)
                    encoded.update(block)
        sqlite = {"path": "geonames.sqlite", "encodedPath": "geonames.sqlite.gz", "compression": "gzip",
                  "bytes": total, "sha256": encoded.hexdigest(), "chunks": chunks,
                  "decodedBytes": source["outputs"]["sqlite"]["bytes"], "decodedSha256": source["outputs"]["sqlite"]["sha256"]}
        artifacts = [{"path": "geonames.sqlite", "kind": "reconstructed", "bytes": sqlite["decodedBytes"], "sha256": sqlite["decodedSha256"]}]
        artifacts += [{"path": name, "bytes": safe_path(stage, name).stat().st_size, "sha256": digest(safe_path(stage, name))} for name in REQUIRED_METADATA]
        artifacts += chunks
        snapshot = {"generator": tool, "source_generation": {"repository": REPOSITORY, "revision": SOURCE_REVISION,
                    "original_validation_sha256": hashlib.sha256(original).hexdigest(), "original_snapshot_sha256": SOURCE_SNAPSHOT_SHA256},
                    "artifacts": artifacts, "sqlite": sqlite, "counts": source["counts"],
                    "dataset_role": "unchanged native source projection; runtime serving/eligibility remains separate"}
        write_json(stage / "source/artifact-snapshot.json", snapshot)
        verify_bundle(stage, root)
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if platform.system() == "Darwin" else 1024)
        generated_bytes = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file())
        elapsed = time.monotonic() - started
        peak_logical = generated_bytes + sqlite["decodedBytes"] + total + sum(path.stat().st_size for path in bundle.rglob("*") if path.is_file())
        if elapsed > 1800 or rss > RSS_LIMIT or peak_logical > DISK_LIMIT:
            raise ValueError("packaging resource budget exceeded")
        measured = {"status": "packaged", "generator": tool, "artifact_snapshot_sha256": digest(stage / "source/artifact-snapshot.json"),
                    "elapsed_seconds": elapsed, "peak_rss_bytes": rss, "new_source_download_bytes": 0,
                    "generated_bytes": generated_bytes, "peak_logical_working_bound_bytes": peak_logical,
                    "scope": "packaging only; conservative input+output+encoded+decoded logical working bound, not whole-wave disk peak",
                    "python": platform.python_version(), "sqlite": sqlite3.sqlite_version, "platform": platform.platform()}
        write_json(receipt, measured)
        stage.rename(output)
        return snapshot
    except BaseException:
        shutil.rmtree(stage)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--bundle", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--revision", required=True)
    build.add_argument("--receipt", type=Path, required=True)
    check = commands.add_parser("check")
    check.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    if args.command == "build":
        print(json.dumps(package(Path(__file__).resolve().parents[1], args.bundle, args.out, args.revision, args.receipt)["sqlite"], indent=2))
    else:
        print(json.dumps(verify_bundle(args.root)["sqlite"], indent=2))
