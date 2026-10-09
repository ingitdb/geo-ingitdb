import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import geonames_w1 as w1
import modelspec_reader as reader


def place(key, country="US", admin="01", name="A place"):
    return [key, name, name, "", "0", "-1", "T", "MT", country, "", admin, "", "", "", "0", "", "-2", "", "2026-09-22"]


class W1Test(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.country = ["US", "USA", "840", "US", "United States", "", "0", "0", "NA", ".us", "USD", "Dollar", "1", "", "", "en", "6252001", "", ""]
        self.sources = {
            "countryInfo.txt": [self.country],
            "admin1CodesASCII.txt": [["US.01", "Admin", "Admin", "11"], ["XX.07", "Orphan", "Orphan", "12"]],
            "cities5000.zip": [place("1", admin="00", name=" Zero ")],
            "allCountries.zip": [place("1", admin="00", name=" Zero "), place("2", country="XX", admin="07")],
            "alternateNamesV2.zip": [["10", "1", "", " Alias ", "", "1", "0", "1"],
                                     ["11", "2", "wkdt", "Q2", "1", "", "", "", "", ""],
                                     ["12", "999", "en", "Not retained", "", "", "", "", "", ""]],
        }
        self.write_inputs()
        (self.root / "ids.txt").write_text("1\n2\n3\n", encoding="utf-8")
        archive = self.root / "ror.zip"
        records = [{"locations": [{"geonames_id": key, "geonames_details": {"name": "A location"}} for key in keys]}
                   for keys in ([1, 2], [3], [1], [None])]
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("ror.json", json.dumps(records))
        self.ror = {"complete": True, "archive_url": "https://zenodo.org/records/22902037/files/v2.13-2026-09-22-ror-data.zip",
                    "release": "v2.13", "release_date": "2026-09-22", "archive_sha256": w1.digest(archive), "archive_bytes": archive.stat().st_size,
                    "archive_file": "ror.zip", "json_member": "ror.json",
                    "record_count": 4, "location_count": 5, "ids_file": "ids.txt", "ids_sha256": w1.digest(self.root / "ids.txt"), "id_count": 3}
        self.write_ror()

    def tearDown(self):
        self.temporary.cleanup()

    def write_inputs(self):
        manifest = {"format": "geonames-w1-inputs-1", "sources": {}}
        for name, member in w1.FILES.items():
            content = "".join("\t".join(fields) + "\n" for fields in self.sources[name]).encode("utf-8")
            path = self.inputs / name
            if member:
                with zipfile.ZipFile(path, "w") as archive:
                    info = zipfile.ZipInfo(member, date_time=(2026, 9, 22, 0, 0, 0))
                    archive.writestr(info, content)
            else:
                path.write_bytes(content)
            manifest["sources"][name] = {"url": w1.BASE + name, "member": member, "file": name,
                                         "bytes": path.stat().st_size, "sha256": w1.digest(path),
                                         "licence": "CC-BY-4.0", "fetched_at": "2026-09-22T00:00:00Z"}
        w1.write_json(self.inputs / "inputs.json", manifest)

    def write_ror(self):
        w1.write_json(self.root / "ror.json", self.ror)

    def build(self, name="out"):
        return w1.build(self.inputs / "inputs.json", self.root / "ror.json", self.root / name, "a" * 40, self.root / (name + "-receipt.json"))

    def test_full_union_native_keys_raw_fields_and_visible_exceptions(self):
        snapshot = self.build()
        self.assertEqual(snapshot["counts"]["geonames_places"], 2)
        self.assertEqual(snapshot["counts"]["geonames_alternate_names"], 2)
        self.assertEqual(snapshot["counts"]["missing_ror_places"], 1)
        self.assertEqual(snapshot["counts"]["missing_admin1_references"], 1)
        self.assertEqual(snapshot["counts"]["missing_country_references"], 1)
        self.assertEqual(snapshot["counts"]["orphan_admin1"], 1)
        with sqlite3.connect(self.root / "out" / "geonames.sqlite") as db:
            self.assertEqual(db.execute("SELECT name,feature_class,feature_code,country_code,admin1_code,latitude,population,elevation FROM geonames_places WHERE geonameid='1'").fetchone(), (" Zero ", "T", "MT", "US", "00", "0", "0", ""))
            self.assertEqual(db.execute("SELECT alternate_name,is_short_name,is_historic,from_period,to_period FROM geonames_alternate_names WHERE alternate_name_id='10'").fetchone(), (" Alias ", "1", "1", None, None))
            self.assertEqual(db.execute("SELECT from_period,to_period FROM geonames_alternate_names WHERE alternate_name_id='11'").fetchone(), ("", ""))
            self.assertEqual(db.execute("SELECT raw_reference FROM missing_admin1_references").fetchone()[0], "US.00")
        self.assertEqual(snapshot["source_scan_counts"]["alternateNamesV2.zip"], 3)

    def test_same_pinned_inputs_reproduce_every_export_byte(self):
        first = self.build("first")
        second = self.build("second")
        self.assertEqual(first, second)
        for path in (self.root / "first").iterdir():
            self.assertEqual(path.read_bytes(), (self.root / "second" / path.name).read_bytes(), path.name)
        import gzip
        reconstructed = b"".join(gzip.decompress((self.root / "first" / item["file"]).read_bytes()) for item in first["outputs"]["chunks"])
        self.assertEqual(reconstructed, (self.root / "first" / "geonames.sqlite").read_bytes())

    def test_source_checksums_and_incomplete_ror_fail(self):
        (self.inputs / "countryInfo.txt").write_bytes(b"tampered")
        with self.assertRaisesRegex(w1.ProjectionError, "checksum/size"):
            self.build()
        self.assertFalse((self.root / "out").exists())
        self.write_inputs()
        self.ror["complete"] = False
        self.write_ror()
        with self.assertRaisesRegex(w1.ProjectionError, "complete pinned ROR"):
            self.build()

    def test_duplicate_keys_and_disagreeing_source_union_fail(self):
        self.sources["admin1CodesASCII.txt"].append(self.sources["admin1CodesASCII.txt"][0])
        self.write_inputs()
        with self.assertRaisesRegex(w1.ProjectionError, "duplicate native"):
            self.build()
        self.sources["admin1CodesASCII.txt"].pop()
        self.sources["allCountries.zip"][0][1] = "Different"
        self.write_inputs()
        with self.assertRaisesRegex(w1.ProjectionError, "source collision"):
            self.build()
        self.sources["allCountries.zip"][0][1] = " Zero "
        self.sources["alternateNamesV2.zip"].append(self.sources["alternateNamesV2.zip"][0])
        self.write_inputs()
        with self.assertRaisesRegex(w1.ProjectionError, "duplicate native"):
            self.build()

    def test_unretained_source_key_collisions_are_not_hidden_by_filtering(self):
        self.sources["allCountries.zip"].extend([place("999"), place("999")])
        self.write_inputs()
        with self.assertRaisesRegex(w1.ProjectionError, "duplicate native allCountries"):
            self.build()
        self.sources["allCountries.zip"] = self.sources["allCountries.zip"][:-2]
        self.sources["alternateNamesV2.zip"].append(self.sources["alternateNamesV2.zip"][-1])
        self.write_inputs()
        with self.assertRaisesRegex(w1.ProjectionError, "duplicate native alternateNamesV2"):
            self.build()

    def test_non_ror_city_overlap_is_checked_and_identical_row_retained_once(self):
        self.sources["cities5000.zip"].append(place("99", name="city original"))
        self.sources["allCountries.zip"].append(place("99", name="global conflict"))
        self.write_inputs()
        with self.assertRaisesRegex(w1.ProjectionError, "source collision: 99"):
            self.build()
        self.assertFalse((self.root / "out").exists())
        self.assertFalse(list(self.root.glob(".geonames-w1-*")))
        self.sources["allCountries.zip"][-1] = place("99", name="city original")
        self.write_inputs()
        self.assertEqual(self.build()["counts"]["geonames_places"], 3)

    def test_strict_ror_json_rejections_leave_no_failed_output(self):
        valid = '{"locations":[{"geonames_id":1}]}'
        invalid = [
            '[{"locations":[{"geonames_id":999}],"locations":[{"geonames_id":1}]}]',
            '[{"locations":[{"geonames_id":999,"geonames_id":1}]}]',
            '[{"locations":[{"geonames_id":1}],"nested":{"x":0,"x":1}}]',
            *['[{"locations":[{"geonames_id":1}],"x":' + x + '}]'
              for x in ('NaN', 'Infinity', '-Infinity', '1e999', '-1e999')],
            *[ws + '[' + valid + ']' for ws in ('\v', '\f', '\u00a0')],
            '[' + valid + ']\u00a0', '[' + valid + ',\v' + valid + ']',
            *['[{"locations":[{"geonames_id":' + key + '}]}]'
              for key in ('true', '1.0', '"01"')],
        ]
        record = '{"locations":[{"geonames_id":1}],"padding":"' + 'x' * (1 << 20) + '"}'
        invalid.append('[' + record + ']')
        (self.root / "ids.txt").write_text("1\n", encoding="utf-8")
        for index, data in enumerate(invalid):
            with self.subTest(data=data[:90]):
                archive = self.root / "ror.zip"
                with zipfile.ZipFile(archive, "w") as zipped:
                    zipped.writestr("ror.json", data)
                self.ror.update(archive_sha256=w1.digest(archive), archive_bytes=archive.stat().st_size,
                                record_count=1, location_count=1, id_count=1,
                                ids_sha256=w1.digest(self.root / "ids.txt"))
                self.write_ror()
                name = f"bad-json-{index}"
                with self.assertRaises(w1.ProjectionError):
                    self.build(name)
                self.assertFalse((self.root / name).exists())
                self.assertFalse(list(self.root.glob(".geonames-w1-*")))
                self.assertEqual(json.loads((self.root / (name + "-receipt.json")).read_text())["status"], "blocked")

    def test_ror_json_exact_utf8_record_bound_and_chunk_boundaries(self):
        class Chunked(io.StringIO):
            def read(self, size=-1):
                return super().read(min(size, 7))

        prefix, suffix = '{"locations":[],"padding":"', '"}'
        padding_size = (1 << 20) - len(prefix) - len(suffix)
        exact = prefix + 'é' * (padding_size // 2) + 'x' * (padding_size % 2) + suffix
        self.assertEqual(len(exact.encode('utf-8')), 1 << 20)
        self.assertEqual(len(list(w1.json_array(io.StringIO('[' + exact + ']')))), 1)
        with self.assertRaisesRegex(w1.ProjectionError, "overlong"):
            list(w1.json_array(io.StringIO('[' + exact[:-2] + 'x' + suffix + ']')))
        data = ' \r\n[ {"locations":[{"geonames_id":"1"}],"nested":{"a":2}}, {} ]\t'
        self.assertEqual(list(w1.json_array(Chunked(data))), json.loads(data))
        for data in ('[{"a":0,"a":1}]', '[{"nested":{"a":0,"a":1}}]', '[{"x":1e999}]', '[{}]\v'):
            with self.subTest(data=data), self.assertRaises(w1.ProjectionError):
                list(w1.json_array(Chunked(data)))

        prefix = '{"locations":[{"geonames_id":"1"}],"padding":"'
        exact = prefix + 'x' * ((1 << 20) - len(prefix) - len(suffix)) + suffix
        (self.root / "ids.txt").write_text("1\n", encoding="utf-8")
        for label, record in (("exact", exact), ("over", exact[:-2] + 'x' + suffix)):
            archive = self.root / "ror.zip"
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("ror.json", '[' + record + ']')
            self.ror.update(archive_sha256=w1.digest(archive), archive_bytes=archive.stat().st_size,
                            record_count=1, location_count=1, id_count=1,
                            ids_sha256=w1.digest(self.root / "ids.txt"))
            self.write_ror()
            if label == "exact":
                self.assertEqual(self.build(label)["counts"]["required_ror_places"], 1)
            else:
                with self.assertRaisesRegex(w1.ProjectionError, "overlong"):
                    self.build(label)
                self.assertFalse((self.root / label).exists())
                self.assertFalse(list(self.root.glob(".geonames-w1-*")))
                self.assertEqual(json.loads((self.root / (label + "-receipt.json")).read_text())["status"], "blocked")

    def test_budget_failure_is_reported_without_output_or_scope_narrowing(self):
        with patch.dict(w1.LIMITS, {"sqlite_bytes": 1}):
            with self.assertRaisesRegex(w1.ProjectionError, "exceeds 256MiB"):
                self.build()
        self.assertFalse((self.root / "out").exists())
        self.assertFalse(list(self.root.glob(".geonames-w1-*")))
        receipt = json.loads((self.root / "out-receipt.json").read_text())
        self.assertEqual(receipt["status"], "blocked")
        with patch.dict(w1.LIMITS, {"download_bytes": 1}):
            with self.assertRaisesRegex(w1.ProjectionError, "combined W1 download"):
                self.build()

    def test_invalid_native_ids_field_count_flags_and_zip_member_fail(self):
        for value in ("01", " 1", "1 ", "0", "-1", "١"):
            with self.assertRaises(w1.ProjectionError):
                w1.positive_id(value)
        with self.assertRaisesRegex(w1.ProjectionError, "fields"):
            list(w1.rows(io.BytesIO(b"short\trow\n"), w1.PLACE))
        with self.assertRaisesRegex(w1.ProjectionError, "1MiB"):
            list(w1.rows(io.BytesIO(b"x" * ((1 << 20) + 1)), w1.PLACE))
        with zipfile.ZipFile(self.inputs / "cities5000.zip", "w") as archive:
            archive.writestr("wrong.txt", b"test")
        with self.assertRaisesRegex(w1.ProjectionError, "exactly one ZIP"):
            with w1.source_stream(self.inputs / "cities5000.zip", "cities5000.txt"):
                pass
        self.sources["alternateNamesV2.zip"][0][4] = "true"
        self.write_inputs()
        with self.assertRaisesRegex(w1.ProjectionError, "flag"):
            self.build()

    def test_existing_snapshot_is_never_overwritten(self):
        self.build()
        before = w1.digest(self.root / "out" / "snapshot.json")
        with self.assertRaisesRegex(w1.ProjectionError, "already exists"):
            self.build()
        self.assertEqual(before, w1.digest(self.root / "out" / "snapshot.json"))

    def test_falsely_attested_ror_sample_is_rejected_against_complete_archive(self):
        (self.root / "ids.txt").write_text("1\n", encoding="utf-8")
        self.ror.update(ids_sha256=w1.digest(self.root / "ids.txt"), id_count=1)
        self.write_ror()
        with self.assertRaisesRegex(w1.ProjectionError, "full ROR archive"):
            self.build()
        for data in ('[{"locations": []}', '[{},]', '[] junk', '{}', '[2]'):
            with self.assertRaises(w1.ProjectionError):
                list(w1.json_array(io.StringIO(data)))

    def test_scoped_bridges_preserve_bytes_and_fail_collisions_or_missing_target(self):
        inputs = json.loads((Path(w1.__file__).parent.parent / "bridges" / "accepted-country-bridges.json").read_text())
        for bridge in inputs["bridges"]:
            bridge["rows"] = [{"raw_label": " USA ", "target_key": "US"}]
        bridge_path = self.root / "bridges.json"
        w1.write_json(bridge_path, inputs)
        snapshot = w1.build(self.inputs / "inputs.json", self.root / "ror.json", self.root / "with-bridges", "a" * 40, self.root / "bridge-receipt.json", bridge_path)
        with sqlite3.connect(self.root / "with-bridges" / "geonames.sqlite") as db:
            for table in w1.BRIDGE_TABLES:
                self.assertEqual(snapshot["counts"][table], 1)
                self.assertEqual(db.execute(f'SELECT raw_label,target_key FROM "{table}"').fetchone(), (" USA ", "US"))
                self.assertEqual(db.execute(f'SELECT count(*) FROM "{table}" WHERE raw_label=?', ("USA",)).fetchone()[0], 0)
        artifacts = {item["path"]: item["sha256"] for item in snapshot["artifacts"]}
        self.assertIn("country-keys.json", artifacts)
        keys = json.loads((self.root / "with-bridges" / "country-keys.json").read_text())
        self.assertEqual(keys["namespace"], "GeoNames:countryInfoISO2")
        self.assertIn("model/geonames.meaning.yaml", artifacts)
        inputs["bridges"][0]["rows"].append(inputs["bridges"][0]["rows"][0])
        w1.write_json(bridge_path, inputs)
        with self.assertRaisesRegex(w1.ProjectionError, "raw-label bridge collision"):
            w1.build(self.inputs / "inputs.json", self.root / "ror.json", self.root / "bad-bridge", "a" * 40, self.root / "bad-bridge-receipt.json", bridge_path)
        inputs["bridges"][0]["rows"] = [{"raw_label": "USA", "target_key": "us"}]
        w1.write_json(bridge_path, inputs)
        with self.assertRaisesRegex(w1.ProjectionError, "target country is missing"):
            w1.build(self.inputs / "inputs.json", self.root / "ror.json", self.root / "bad-bridge", "a" * 40, self.root / "bad-bridge-receipt.json", bridge_path)

    def test_published_model_matches_physical_source_and_bridge_columns(self):
        model = json.loads((Path(w1.__file__).parent.parent / "model" / "geonames.modelspec.json").read_text())
        records = reader.record_types(model)
        for table, (columns, key) in w1.TABLES.items():
            entity = records[table]
            self.assertEqual(set(reader.members(model, entity)), set(columns))
            self.assertEqual(entity["key"], [key])
            self.assertTrue(all(prop["type"] == "string" for prop in reader.members(model, entity).values()))
        for table in w1.BRIDGE_TABLES:
            self.assertEqual(set(reader.members(model, records[table])), {"serving_id", "raw_label", "target_key"})

    def test_capture_attests_fully_flushed_small_file_bytes(self):
        class Response(io.BytesIO):
            status = 200
            headers = {"Content-Length": "2"}

        class Opener:
            def open(self, request, timeout):
                self.request = request
                return Response(b"x\n")

        captured = self.root / "captured"
        with patch.object(w1.urllib.request, "build_opener", return_value=Opener()):
            manifest = json.loads(w1.capture(captured).read_text())
        for name in w1.FILES:
            self.assertEqual(manifest["sources"][name]["sha256"], w1.digest(captured / name))
        with self.assertRaisesRegex(w1.ProjectionError, "already exists"):
            w1.capture(captured)
        with self.assertRaisesRegex(w1.ProjectionError, "redirect refused"):
            w1.NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example/")


if __name__ == "__main__":
    unittest.main()
