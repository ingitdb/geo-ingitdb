import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import package_geonames_w1 as package

ROOT = Path(__file__).resolve().parents[1]


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.snapshot = package.read_json(ROOT / "source/artifact-snapshot.json")
        for pin in self.snapshot["artifacts"]:
            if pin.get("kind") == "reconstructed":
                continue
            destination = self.root / pin["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / pin["path"], destination)
        self.save_snapshot()

    def tearDown(self):
        self.temporary.cleanup()

    def save_snapshot(self):
        package.write_json(self.root / "source/artifact-snapshot.json", self.snapshot)

    def rejected(self):
        with self.assertRaises((ValueError, OSError)):
            package.verify_bundle(self.root, ROOT)

    def alter_metadata(self, name, value):
        package.write_json(self.root / name, value)
        for pin in self.snapshot["artifacts"]:
            if pin["path"] == name:
                pin.update(bytes=(self.root / name).stat().st_size, sha256=package.digest(self.root / name))
        self.save_snapshot()

    def test_full_public_package_reconstructs_native_keys_and_originals(self):
        self.assertEqual(package.verify_bundle(self.root, ROOT), self.snapshot)
        self.assertEqual(self.snapshot["sqlite"]["decodedBytes"], 75304960)
        self.assertFalse((self.root / "geonames.sqlite").exists())
        self.assertEqual((self.root / "source/generation-validation.json").read_bytes(),
                         package.git_blob(ROOT, package.SOURCE_REVISION, "w1/validation.json"))
        self.assertEqual(package.digest(self.root / "source/generation-snapshot.json"), package.SOURCE_SNAPSHOT_SHA256)

    def test_source_generation_repo_revision_and_original_hash_are_checked(self):
        original = copy.deepcopy(self.snapshot)
        for field, value in [("repository", "https://github.com/example/other"),
                             ("revision", "0" * 40), ("original_validation_sha256", "0" * 64),
                             ("original_snapshot_sha256", "0" * 64)]:
            with self.subTest(field=field):
                self.snapshot = copy.deepcopy(original)
                self.snapshot["source_generation"][field] = value
                self.save_snapshot()
                self.rejected()

    def test_generator_sha_and_actual_commit_are_checked(self):
        for field, value in [("sha256", "0" * 64), ("repository", "https://github.com/example/other"),
                             ("revision", package.SOURCE_REVISION), ("script", "scripts/geonames_w1.py")]:
            with self.subTest(field=field):
                original = self.snapshot["generator"][field]
                self.snapshot["generator"][field] = value
                self.save_snapshot()
                self.rejected()
                self.snapshot["generator"][field] = original

    def test_required_data_licence_and_provenance_cannot_be_unpinned_or_removed(self):
        original = copy.deepcopy(self.snapshot)
        for name in package.REQUIRED_METADATA:
            with self.subTest(name=name):
                self.snapshot = copy.deepcopy(original)
                self.snapshot["artifacts"] = [pin for pin in self.snapshot["artifacts"] if pin["path"] != name]
                self.save_snapshot()
                self.rejected()
        self.snapshot = original
        self.save_snapshot()
        licence = self.root / "DATA-LICENSE.md"
        licence.unlink()
        self.rejected()

    def test_forged_native_generation_provider_or_counts_fail_even_if_repinned(self):
        name = "source/native-key-evidence.json"
        original = package.read_json(self.root / name)
        mutants = []
        for field, value in [("revision", "0" * 40), ("repository", "https://github.com/example/other")]:
            value_copy = copy.deepcopy(original)
            value_copy["generation_provider"][field] = value
            mutants.append(value_copy)
        value_copy = copy.deepcopy(original)
        value_copy["keys"][0]["records"] += 1
        mutants.append(value_copy)
        value_copy = copy.deepcopy(original)
        value_copy["keys"][0]["duplicates"] = 1
        mutants.append(value_copy)
        value_copy = copy.deepcopy(original)
        value_copy["original_importer_sha256"] = "0" * 64
        mutants.append(value_copy)
        for value in mutants:
            with self.subTest(value=value["generation_provider"]):
                self.alter_metadata(name, value)
                self.rejected()

    def test_original_receipt_or_snapshot_rewrite_cannot_be_repinned(self):
        for name in ("source/generation-validation.json", "source/generation-snapshot.json"):
            with self.subTest(name=name):
                original = (self.root / name).read_bytes()
                value = package.read_json(self.root / name)
                value["counts"]["geonames_places"] -= 1
                self.alter_metadata(name, value)
                self.rejected()
                (self.root / name).write_bytes(original)
                for pin in self.snapshot["artifacts"]:
                    if pin["path"] == name:
                        pin.update(bytes=len(original), sha256=package.digest(self.root / name))

    def test_chunk_order_duplicate_omission_corruption_and_resource_guards(self):
        original = copy.deepcopy(self.snapshot)
        for chunks in (list(reversed(original["sqlite"]["chunks"])), original["sqlite"]["chunks"][:-1],
                       original["sqlite"]["chunks"] + [original["sqlite"]["chunks"][0]]):
            with self.subTest(chunks=[item["path"] for item in chunks]):
                self.snapshot = copy.deepcopy(original)
                self.snapshot["sqlite"]["chunks"] = chunks
                self.save_snapshot()
                self.rejected()
        self.snapshot = original
        self.save_snapshot()
        with patch.object(package, "FILE_LIMIT", 1):
            self.rejected()
        with patch.object(package, "SQLITE_LIMIT", 1):
            self.rejected()
        with patch.object(package, "METADATA_LIMIT", 1):
            self.rejected()
        chunk = self.root / original["sqlite"]["chunks"][0]["path"]
        with chunk.open("r+b") as stream:
            stream.write(b"bad")
        self.rejected()

    def test_traversal_symlink_duplicate_artifact_and_json_members_fail(self):
        for path in ("../escape", "/absolute", "a/./b", "a//b", "a/../b", "a\\b"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                package.safe_path(self.root, path)
        (self.root / "link").symlink_to(self.root / "source", target_is_directory=True)
        with self.assertRaises(ValueError):
            package.safe_path(self.root, "link/generation-snapshot.json")
        self.snapshot["artifacts"].append(self.snapshot["artifacts"][0])
        self.save_snapshot()
        self.rejected()
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                package.decode_json(raw)

    def test_existing_output_and_wrong_generator_leave_no_candidate(self):
        output = self.root / "already"
        output.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            package.package(ROOT, self.root, output, package.SOURCE_REVISION, self.root / "measure.json")
        with self.assertRaises((ValueError, __import__('subprocess').CalledProcessError)):
            package.package(ROOT, self.root, self.root / "candidate", package.SOURCE_REVISION, self.root / "measure.json")
        self.assertFalse((self.root / "candidate").exists())
        self.assertFalse(list(self.root.glob(".geonames-package-*")))


if __name__ == "__main__":
    unittest.main()
