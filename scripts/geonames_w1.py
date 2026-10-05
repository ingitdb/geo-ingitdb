#!/usr/bin/env python3
"""Offline, source-faithful W1 projection. No changes to the legacy importer.

Only `capture` accesses the network. `build` consumes checked, pinned inputs.
SQLite is a physical projection, not a semantic acceptance registry.
"""
import argparse
import contextlib
import datetime
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import resource
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.request
import zipfile

BASE = "https://download.geonames.org/export/dump/"
FILES = {
    "countryInfo.txt": None,
    "admin1CodesASCII.txt": None,
    "cities5000.zip": "cities5000.txt",
    "allCountries.zip": "allCountries.txt",
    "alternateNamesV2.zip": "alternateNamesV2.txt",
}
COUNTRY = ("iso", "iso3", "iso_numeric", "fips", "country", "capital", "area_sq_km",
           "population", "continent", "tld", "currency_code", "currency_name", "phone",
           "postal_code_format", "postal_code_regex", "languages", "geonameid", "neighbours",
           "equivalent_fips_code")
ADMIN1 = ("code", "name", "asciiname", "geonameid")
PLACE = ("geonameid", "name", "asciiname", "alternatenames", "latitude", "longitude",
         "feature_class", "feature_code", "country_code", "cc2", "admin1_code", "admin2_code",
         "admin3_code", "admin4_code", "population", "elevation", "dem", "timezone",
         "modification_date")
ALIAS = ("alternate_name_id", "geonameid", "isolanguage", "alternate_name", "is_preferred_name",
         "is_short_name", "is_colloquial", "is_historic", "from_period", "to_period")
TABLES = {"geonames_countries": (COUNTRY, "iso"), "geonames_admin1": (ADMIN1, "code"),
          "geonames_places": (PLACE, "geonameid"), "geonames_alternate_names": (ALIAS, "alternate_name_id")}
LIMITS = {"download_bytes": 1 << 30, "disk_bytes": 8 << 30, "rss_bytes": 512 << 20,
          "elapsed_seconds": 1800, "sqlite_bytes": 256 << 20, "metadata_bytes": 2 << 20}
BRIDGE_TABLES = ("geonames_chinook_customer_country", "geonames_northwind_customer_country",
                 "geonames_northwind_order_country", "geonames_pubs_publisher_country")
ATTRIBUTION = """GeoNames W1 geographic projection
Source: GeoNames https://www.geonames.org/
Data: Creative Commons Attribution 4.0 https://creativecommons.org/licenses/by/4.0/
Changes: selected global countries/admin1, cities5000 union complete pinned ROR GeoNames references,
and every alternateNamesV2 row belonging to retained places; relational SQLite projection.
Native source values and unresolved references are preserved. No GeoNames endorsement or warranty.
Importer code: CC0-1.0, repository LICENSE. Models and meanings have their separate declared licences.
ROR references: CC0 metadata; embedded GeoNames locations retain GeoNames CC-BY-4.0 attribution.
"""


class ProjectionError(ValueError):
    """A source, collision, provenance or budget control failed."""


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def positive_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*", value):
        raise ProjectionError(f"non-native decimal GeoNames ID: {value!r}")
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProjectionError("source redirect refused")


def capture(directory):
    """Capture official GeoNames bytes once; input manifest is never refreshed implicitly."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "inputs.json"
    if manifest_path.exists():
        raise ProjectionError("capture already exists; choose a new snapshot directory")
    opener = urllib.request.build_opener(NoRedirect)
    manifest = {"format": "geonames-w1-inputs-1", "sources": {}}
    total = 0
    for name, member in FILES.items():
        path = directory / name
        if path.exists():
            raise ProjectionError(f"unattested pre-existing input: {name}")
        request = urllib.request.Request(BASE + name, headers={"User-Agent": "geo-ingitdb W1 snapshot importer"})
        with opener.open(request, timeout=120) as response, path.open("xb") as target:
            if response.status != 200:
                raise ProjectionError(f"source status {response.status}")
            length = response.headers.get("Content-Length")
            if length and total + int(length) > LIMITS["download_bytes"]:
                raise ProjectionError("combined GeoNames source download budget exceeded")
            size = 0
            for block in iter(lambda: response.read(1 << 20), b""):
                size += len(block)
                if total + size > LIMITS["download_bytes"]:
                    raise ProjectionError("download budget exceeded")
                target.write(block)
            if length and size != int(length):
                raise ProjectionError("truncated source download")
            target.flush()
            manifest["sources"][name] = {
                "url": BASE + name, "file": name, "member": member, "bytes": size,
                "sha256": digest(path), "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "last_modified": response.headers.get("Last-Modified"), "etag": response.headers.get("ETag"),
                "licence": "CC-BY-4.0",
            }
        total += size
        print(f"captured {name}: {size} bytes", flush=True)
    write_json(manifest_path, manifest)
    return manifest_path


@contextlib.contextmanager
def source_stream(path, member):
    if member:
        with zipfile.ZipFile(path) as archive:
            matches = [entry for entry in archive.infolist() if entry.filename == member]
            if len(matches) != 1:
                raise ProjectionError(f"expected exactly one ZIP member {member}")
            with archive.open(matches[0]) as stream:
                yield stream
    else:
        with Path(path).open("rb") as stream:
            yield stream


def rows(stream, columns, minimum=None):
    """Keep empty strings distinct from missing trailing fields (SQL NULL)."""
    minimum = len(columns) if minimum is None else minimum
    for line_number, raw in enumerate(iter(lambda: stream.readline((1 << 20) + 1), b""), 1):
        if len(raw) > 1 << 20:
            raise ProjectionError(f"source line {line_number} exceeds 1MiB")
        line = raw.decode("utf-8").removesuffix("\n").removesuffix("\r")
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if not minimum <= len(fields) <= len(columns):
            raise ProjectionError(f"source line {line_number}: {len(fields)} fields, expected {minimum}..{len(columns)}")
        yield fields + [None] * (len(columns) - len(fields))


class Budget:
    def __init__(self, roots):
        self.roots = []
        self.include_roots(roots)
        self.started = time.monotonic()
        self.peak_disk = 0
        self.peak_rss = 0

    def include_roots(self, roots):
        roots = list(self.roots) + list(roots)
        paths = sorted({Path(root).resolve() for root in roots}, key=lambda path: len(path.parts))
        self.roots = []
        for path in paths:
            if not any(path.is_relative_to(root) for root in self.roots):
                self.roots.append(path)

    def check(self):
        usage = resource.getrusage(resource.RUSAGE_SELF)
        self.peak_rss = max(self.peak_rss, usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024))
        disk = sum(p.stat().st_size for root in self.roots for p in Path(root).rglob("*") if p.is_file())
        self.peak_disk = max(self.peak_disk, disk)
        elapsed = time.monotonic() - self.started
        for name, actual in (("disk_bytes", disk), ("rss_bytes", self.peak_rss), ("elapsed_seconds", elapsed)):
            if actual > LIMITS[name]:
                raise ProjectionError(f"{name} budget exceeded: {actual} > {LIMITS[name]}")

    def receipt(self):
        return {"elapsed_seconds": round(time.monotonic() - self.started, 6), "peak_rss_bytes": self.peak_rss,
                "peak_observed_disk_bytes": self.peak_disk, "limits": LIMITS,
                "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                "platform": platform.platform(), "rss_source": "getrusage(RUSAGE_SELF).ru_maxrss",
                "disk_measurement": "sampled logical files under complete input and staging roots"}


def checked_inputs(manifest_path):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "geonames-w1-inputs-1" or set(manifest.get("sources", {})) != set(FILES):
        raise ProjectionError("complete five-file GeoNames input manifest required")
    for name, member in FILES.items():
        source = manifest["sources"][name]
        path = manifest_path.parent / name
        if (source.get("file") != name or source.get("url") != BASE + name or source.get("member") != member
                or source.get("licence") != "CC-BY-4.0" or not source.get("fetched_at")):
            raise ProjectionError(f"invalid source provenance: {name}")
        if path.stat().st_size != source["bytes"] or digest(path) != source["sha256"]:
            raise ProjectionError(f"source checksum/size mismatch: {name}")
    return manifest


def checked_ror(path):
    path = Path(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    required = ("archive_url", "release", "release_date", "archive_sha256", "archive_bytes", "record_count",
                "location_count", "ids_file", "ids_sha256", "id_count", "archive_file", "json_member")
    if value.get("complete") is not True or any(key not in value for key in required):
        raise ProjectionError("complete pinned ROR archive and GeoNames ID projection receipt required")
    if not re.fullmatch(r"[0-9a-f]{64}", value["archive_sha256"]) or value["record_count"] <= 0:
        raise ProjectionError("invalid ROR archive receipt")
    ids_path = path.parent / value["ids_file"]
    if digest(ids_path) != value["ids_sha256"]:
        raise ProjectionError("ROR ID projection checksum mismatch")
    archive_path = path.parent / value["archive_file"]
    if archive_path.stat().st_size != value["archive_bytes"] or digest(archive_path) != value["archive_sha256"]:
        raise ProjectionError("ROR archive checksum/size mismatch")
    with source_stream(archive_path, value["json_member"]) as binary:
        with io.TextIOWrapper(binary, encoding="utf-8") as stream:
            ids, records, locations = set(), 0, 0
            for record in json_array(stream):
                records += 1
                for location in record["locations"]:
                    locations += 1
                    key = location.get("geonames_id")
                    if key is not None:
                        if type(key) not in (int, str):
                            raise ProjectionError("non-native ROR location GeoNames identifier")
                        ids.add(positive_id(str(key)))
    with ids_path.open(encoding="utf-8") as stream:
        projected = {positive_id(line.removesuffix("\n")) for line in stream}
    if (ids != projected or records != value["record_count"] or locations != value["location_count"]
            or len(ids) != value["id_count"]):
        raise ProjectionError("full ROR archive location/ID closure does not match receipt")
    return value, ids_path


def json_array(stream):
    """Incrementally decode the complete top-level ROR array, bounded per record."""
    decoder = json.JSONDecoder()
    buffer = ""
    state = "start"
    eof = False
    while True:
        buffer = buffer.lstrip()
        if not buffer and not eof:
            block = stream.read(65536)
            eof = not block
            buffer += block
            continue
        if state == "start":
            if not buffer.startswith("["):
                raise ProjectionError("ROR JSON must be a complete top-level array")
            buffer, state = buffer[1:], "value-or-end"
        elif state in ("value-or-end", "value"):
            if state == "value-or-end" and buffer.startswith("]"):
                buffer, state = buffer[1:], "finished"
                continue
            try:
                value, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError as error:
                if eof or len(buffer) > 1 << 20:
                    raise ProjectionError("truncated or overlong ROR JSON record") from error
                block = stream.read(65536)
                eof = not block
                buffer += block
                continue
            if not isinstance(value, dict):
                raise ProjectionError("ROR array members must be objects")
            yield value
            buffer, state = buffer[end:], "delimiter"
        elif state == "delimiter":
            if buffer.startswith(","):
                buffer, state = buffer[1:], "value"
            elif buffer.startswith("]"):
                buffer, state = buffer[1:], "finished"
            else:
                raise ProjectionError("malformed ROR array delimiter")
        else:
            if buffer.strip() or any(block.strip() for block in iter(lambda: stream.read(65536), "")):
                raise ProjectionError("trailing ROR JSON content")
            return


def insert(db, table, fields):
    columns, _ = TABLES[table]
    try:
        db.execute(f'INSERT INTO "{table}" VALUES ({",".join("?" for _ in columns)})', fields)
    except sqlite3.IntegrityError as error:
        raise ProjectionError(f"duplicate native key in {table}: {fields[0]!r}") from error


def export_bridges(db, stage, input_path):
    """Materialize reviewed dictionaries; never synthesize a label or infer scope."""
    input_path = Path(input_path)
    if input_path.stat().st_size > 65536:
        raise ProjectionError("accepted bridge input exceeds 64KiB")
    value = json.loads(input_path.read_text(encoding="utf-8"))
    if value.get("format") != "geonames-w1-accepted-bridge-inputs-1":
        raise ProjectionError("unknown accepted bridge input format")
    tables = set()
    counts = {}
    (stage / "bridges").mkdir()
    for bridge in value["bridges"]:
        table = bridge["table"]
        if table not in BRIDGE_TABLES or table in tables:
            raise ProjectionError("unknown or duplicate scoped bridge table")
        tables.add(table)
        for document in (bridge["source"]["schema"], bridge["decision"]["document"], bridge["accepted_values"]["document"]):
            if not (re.fullmatch(r"https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", document["repository"])
                    and re.fullmatch(r"[0-9a-f]{40}", document["revision"])
                    and re.fullmatch(r"[0-9a-f]{64}", document["sha256"])):
                raise ProjectionError("bridge requires immutable source/decision references")
        db.execute(f'CREATE TABLE "{table}" (serving_id TEXT PRIMARY KEY NOT NULL,raw_label TEXT UNIQUE NOT NULL,target_key TEXT NOT NULL) WITHOUT ROWID')
        rows_to_write = sorted(bridge["rows"], key=lambda row: row["raw_label"].encode("utf-8"))
        if not 0 < len(rows_to_write) <= 256:
            raise ProjectionError("scoped bridge row count outside bounds")
        for row in rows_to_write:
            if not isinstance(row["raw_label"], str) or not row["raw_label"] or not isinstance(row["target_key"], str):
                raise ProjectionError("raw bridge labels and targets must be exact non-empty strings")
            if not db.execute("SELECT 1 FROM geonames_countries WHERE iso=?", (row["target_key"],)).fetchone():
                raise ProjectionError("bridge target country is missing")
            serving_id = hashlib.sha256(row["raw_label"].encode("utf-8")).hexdigest()
            try:
                db.execute(f'INSERT INTO "{table}" VALUES (?,?,?)', (serving_id, row["raw_label"], row["target_key"]))
            except sqlite3.IntegrityError as error:
                raise ProjectionError("duplicate raw-label bridge collision") from error
        write_json(stage / "bridges" / (table + ".json"), {"table": table, "rows": rows_to_write})
        counts[table] = len(rows_to_write)
    if tables != set(BRIDGE_TABLES):
        raise ProjectionError("all four accepted source-property bridge scopes required")
    shutil.copyfile(input_path, stage / "bridges" / "accepted-inputs.json")
    return counts


def build(manifest_path, ror_path, output, tool_revision, receipt_path, bridge_inputs=None):
    output = Path(output)
    if output.exists():
        raise ProjectionError("output already exists; use a new snapshot directory")
    if not re.fullmatch(r"[0-9a-f]{40}", tool_revision):
        raise ProjectionError("immutable importer Git revision required")
    output.parent.mkdir(parents=True, exist_ok=True)
    source_root = Path(manifest_path).parent
    with tempfile.TemporaryDirectory(prefix=".geonames-w1-", dir=output.parent) as temporary:
        stage = Path(temporary)
        budget = Budget([source_root, Path(ror_path).parent, stage])
        try:
            inputs = checked_inputs(manifest_path)
            ror, ids_path = checked_ror(ror_path)
            budget.include_roots([ids_path.parent, (Path(ror_path).parent / ror["archive_file"]).parent])
            download_bytes = sum(x["bytes"] for x in inputs["sources"].values()) + ror["archive_bytes"]
            if download_bytes > LIMITS["download_bytes"]:
                raise ProjectionError("full combined W1 download budget exceeded")
            budget.check()
            db_path = stage / "geonames.sqlite"
            scan_keys_path = stage / "scan-keys.sqlite"
            scan_keys = sqlite3.connect(scan_keys_path)
            scan_keys.executescript("PRAGMA journal_mode=OFF; PRAGMA cache_size=-4096; CREATE TABLE place_keys (id TEXT PRIMARY KEY) WITHOUT ROWID; CREATE TABLE alias_keys (id TEXT PRIMARY KEY) WITHOUT ROWID;")
            with contextlib.closing(sqlite3.connect(db_path)) as db:
                db.executescript("PRAGMA page_size=4096; PRAGMA journal_mode=DELETE; PRAGMA temp_store=FILE; PRAGMA cache_size=-8192;")
                for table, (columns, key) in TABLES.items():
                    definitions = ",".join(f'"{column}" TEXT' + (" PRIMARY KEY NOT NULL" if column == key else "") for column in columns)
                    db.execute(f'CREATE TABLE "{table}" ({definitions}) WITHOUT ROWID')
                db.execute("CREATE TABLE required_ror_places (geonameid TEXT PRIMARY KEY NOT NULL) WITHOUT ROWID")
                with ids_path.open(encoding="utf-8") as stream:
                    for raw in stream:
                        key = positive_id(raw.removesuffix("\n"))
                        try:
                            db.execute("INSERT INTO required_ror_places VALUES (?)", (key,))
                        except sqlite3.IntegrityError as error:
                            raise ProjectionError("duplicate ROR projection ID") from error
                actual_ids = db.execute("SELECT count(*) FROM required_ror_places").fetchone()[0]
                if actual_ids != ror["id_count"]:
                    raise ProjectionError("ROR ID count mismatch")
                source_counts = {}
                for name, table, columns, minimum in (("countryInfo.txt", "geonames_countries", COUNTRY, 17),
                                                       ("admin1CodesASCII.txt", "geonames_admin1", ADMIN1, 4),
                                                       ("cities5000.zip", "geonames_places", PLACE, 19)):
                    count = 0
                    with source_stream(source_root / name, FILES[name]) as stream:
                        for fields in rows(stream, columns, minimum):
                            if table == "geonames_places":
                                positive_id(fields[0])
                            if table == "geonames_countries" and not re.fullmatch(r"[A-Z]{2}", fields[0]):
                                raise ProjectionError("country native key is not ISO alpha2")
                            if table == "geonames_admin1" and not re.fullmatch(r"[A-Z]{2}\.[^\.]+", fields[0]):
                                raise ProjectionError("invalid native admin1 composite")
                            insert(db, table, fields)
                            count += 1
                    source_counts[name] = count
                bridge_counts = export_bridges(db, stage, bridge_inputs) if bridge_inputs else {}
                db.commit()
                # allCountries is scanned once. Only IDs required by ROR are loaded.
                scan_count = 0
                found = set()
                with source_stream(source_root / "allCountries.zip", "allCountries.txt") as stream:
                    for fields in rows(stream, PLACE):
                        key = positive_id(fields[0])
                        try:
                            scan_keys.execute("INSERT INTO place_keys VALUES (?)", (key,))
                        except sqlite3.IntegrityError as error:
                            raise ProjectionError(f"duplicate native allCountries key: {key}") from error
                        scan_count += 1
                        if db.execute("SELECT 1 FROM required_ror_places WHERE geonameid=?", (key,)).fetchone():
                            if key in found:
                                raise ProjectionError(f"duplicate required native key in allCountries: {key}")
                            found.add(key)
                            existing = db.execute("SELECT * FROM geonames_places WHERE geonameid=?", (key,)).fetchone()
                            if existing and tuple(fields) != existing:
                                raise ProjectionError(f"cities5000/allCountries source collision: {key}")
                            if not existing:
                                insert(db, "geonames_places", fields)
                        if scan_count % 10000 == 0:
                            budget.check()
                source_counts["allCountries.zip"] = scan_count
                db.commit()
                # Membership only is cached (~50k keys), not the global alias/source corpus.
                retained = {row[0] for row in db.execute("SELECT geonameid FROM geonames_places")}
                alias_count = 0
                with source_stream(source_root / "alternateNamesV2.zip", "alternateNamesV2.txt") as stream:
                    for fields in rows(stream, ALIAS, 8):
                        positive_id(fields[0])
                        positive_id(fields[1])
                        try:
                            scan_keys.execute("INSERT INTO alias_keys VALUES (?)", (fields[0],))
                        except sqlite3.IntegrityError as error:
                            raise ProjectionError(f"duplicate native alternateNamesV2 key: {fields[0]}") from error
                        alias_count += 1
                        if fields[1] in retained:
                            if any(flag not in ("", "0", "1") for flag in fields[4:8]):
                                raise ProjectionError("unexpected GeoNames alternate-name flag")
                            insert(db, "geonames_alternate_names", fields)
                        if alias_count % 10000 == 0:
                            budget.check()
                source_counts["alternateNamesV2.zip"] = alias_count
                db.commit()
                db.execute("CREATE INDEX geonames_alias_place ON geonames_alternate_names(geonameid)")
                db.execute("CREATE TABLE missing_ror_places AS SELECT geonameid FROM required_ror_places WHERE geonameid NOT IN (SELECT geonameid FROM geonames_places)")
                db.execute("CREATE TABLE missing_admin1_references AS SELECT geonameid,country_code,admin1_code,country_code||'.'||admin1_code AS raw_reference FROM geonames_places WHERE admin1_code<>'' AND country_code||'.'||admin1_code NOT IN (SELECT code FROM geonames_admin1)")
                db.execute("CREATE TABLE missing_country_references AS SELECT geonameid,country_code FROM geonames_places WHERE country_code<>'' AND country_code NOT IN (SELECT iso FROM geonames_countries)")
                db.execute("CREATE TABLE orphan_admin1 AS SELECT code FROM geonames_admin1 WHERE substr(code,1,2) NOT IN (SELECT iso FROM geonames_countries)")
                db.commit()
                db.execute("VACUUM")
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ProjectionError("generated SQLite integrity failed")
                counts = {table: db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                          for table in (*TABLES, "required_ror_places", "missing_ror_places", "missing_admin1_references", "missing_country_references", "orphan_admin1")}
                counts.update(bridge_counts)
                write_json(stage / "country-keys.json", {"namespace": "iso-3166-1-alpha-2",
                           "keys": [row[0] for row in db.execute("SELECT iso FROM geonames_countries ORDER BY iso")]})
                if db.execute("SELECT count(*) FROM geonames_alternate_names WHERE geonameid NOT IN (SELECT geonameid FROM geonames_places)").fetchone()[0]:
                    raise ProjectionError("retained alias closure failed")
            if db_path.stat().st_size > LIMITS["sqlite_bytes"]:
                raise ProjectionError(f"provider SQLite exceeds 256MiB: {db_path.stat().st_size}")
            scan_keys.close()
            scan_keys_path.unlink()
            (stage / "ATTRIBUTION.txt").write_text(ATTRIBUTION, encoding="utf-8")
            if bridge_inputs:
                (stage / "model").mkdir()
                for name in ("geonames.modelspec.hcl", "geonames.modelspec.json", "geonames.meaning.yaml"):
                    shutil.copyfile(Path(__file__).parent.parent / "model" / name, stage / "model" / name)
            # Ordered deterministic gzip chunks fit the existing 25MiB per-file runtime policy.
            chunks = []
            with db_path.open("rb") as source:
                while block := source.read(16 << 20):
                    name = f"geonames.sqlite.{len(chunks):04d}.gz"
                    with (stage / name).open("wb") as target:
                        with gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as compressed:
                            compressed.write(block)
                    chunks.append({"file": name, "bytes": (stage / name).stat().st_size, "sha256": digest(stage / name)})
            public_ror = {key: value for key, value in ror.items() if key not in ("ids_file", "archive_file")}
            snapshot = {"format": "geonames-w1-snapshot-1", "sources": inputs["sources"], "ror_closure": public_ror,
                        "generator": {"repository": "https://github.com/ingitdb/geo-ingitdb", "revision": tool_revision},
                        "artifacts": [{"path": str(path.relative_to(stage)), "sha256": digest(path)}
                                      for path in sorted(stage.rglob("*")) if path.is_file()],
                        "importer_revision": tool_revision, "importer_sha256": digest(__file__),
                        "projection": {"countries": "complete countryInfo", "admin1": "complete admin1CodesASCII",
                                       "places": "global cities5000 union complete ROR location GeoNames IDs",
                                       "alternate_names": "all alternateNamesV2 rows for retained place IDs",
                                       "native_columns": "UTF-8 source strings; missing trailing field is NULL; empty is empty",
                                       "refresh": "monthly reviewed snapshots; failed refresh retains previous snapshot"},
                        "counts": counts, "source_scan_counts": source_counts, "download_bytes": download_bytes,
                        "outputs": {"sqlite": {"file": db_path.name, "bytes": db_path.stat().st_size, "sha256": digest(db_path)},
                                    "chunks": chunks, "attribution": {"file": "ATTRIBUTION.txt", "sha256": digest(stage / "ATTRIBUTION.txt")}}}
            write_json(stage / "snapshot.json", snapshot)
            metadata_bytes = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file()
                                 and path.name != "geonames.sqlite" and not path.name.endswith(".gz"))
            if metadata_bytes > LIMITS["metadata_bytes"]:
                raise ProjectionError("snapshot metadata exceeds 2MiB")
            budget.check()
            os.rename(stage, output)
            receipt = {"status": "generated", "measurement": budget.receipt(), "snapshot_sha256": digest(output / "snapshot.json")}
            write_json(receipt_path, receipt)
            return snapshot
        except Exception as error:
            if "scan_keys" in locals():
                scan_keys.close()
            write_json(receipt_path, {"status": "blocked", "error": str(error), "measurement": budget.receipt()})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    get = commands.add_parser("capture")
    get.add_argument("--directory", type=Path, required=True)
    project = commands.add_parser("build")
    project.add_argument("--inputs", type=Path, required=True)
    project.add_argument("--ror-closure", type=Path, required=True)
    project.add_argument("--out", type=Path, required=True)
    project.add_argument("--tool-revision", required=True)
    project.add_argument("--receipt", type=Path, required=True)
    project.add_argument("--bridges", type=Path, default=Path(__file__).parent.parent / "bridges" / "accepted-country-bridges.json",
                         help="accepted scoped bridge generation input; no label inference")
    args = parser.parse_args()
    try:
        if args.command == "capture":
            print(capture(args.directory))
        else:
            result = build(args.inputs, args.ror_closure, args.out, args.tool_revision, args.receipt, args.bridges)
            print(json.dumps(result["counts"], sort_keys=True))
    except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as error:
        parser.exit(1, f"geonames-w1: {error}\n")


if __name__ == "__main__":
    main()
