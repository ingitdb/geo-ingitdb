import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import geonames_w1 as w1


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
        self.ror = {"complete": True, "archive_url": "https://zenodo.org/records/22902037/files/v2.13-2026-09-22-ror-data.zip",
                    "release": "v2.13", "release_date": "2026-09-22", "archive_sha256": "a" * 64, "archive_bytes": 100,
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
        with self.assertRaisesRegex(w1.ProjectionError, "duplicate native key"):
            self.build()
        self.sources["admin1CodesASCII.txt"].pop()
        self.sources["allCountries.zip"][0][1] = "Different"
        self.write_inputs()
        with self.assertRaisesRegex(w1.ProjectionError, "source collision"):
            self.build()
        self.sources["allCountries.zip"][0][1] = " Zero "
        self.sources["alternateNamesV2.zip"].append(self.sources["alternateNamesV2.zip"][0])
        self.write_inputs()
        with self.assertRaisesRegex(w1.ProjectionError, "duplicate native key"):
            self.build()

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
