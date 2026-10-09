import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import package_geonames_w1 as package
from modelspec_spellings import both

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
        self.assertTrue((self.root / "DATA-LICENSE.md").read_bytes().startswith(
            package.git_blob(ROOT, package.SOURCE_REVISION, "DATA-LICENSE.md")))

    def test_later_representation_pair_requires_exact_bytes_and_regular_closure(self):
        required = {pin["path"] for pin in self.snapshot["artifacts"]}
        pair = ("model/representations.json", "source/representation-attachment.json")
        for name in pair:
            shutil.copyfile(ROOT / name, self.root / name)
        package.physical_closure(self.root, required, ROOT)
        # These are later metadata, not additional historical snapshot artifacts.
        self.assertFalse(any(pin["path"] in pair for pin in self.snapshot["artifacts"]))
        for name in pair:
            file = self.root / name
            original = file.read_bytes()
            file.write_bytes(original + b" ")
            with self.assertRaises(ValueError):
                package.physical_closure(self.root, required, ROOT)
            file.unlink()
            with self.assertRaises((ValueError, OSError)):
                package.physical_closure(self.root, required, ROOT)
            file.symlink_to(ROOT / name)
            with self.assertRaises((ValueError, OSError)):
                package.physical_closure(self.root, required, ROOT)
            file.unlink()
            file.write_bytes(original)
        extra = self.root / "source/untracked-representation.json"
        extra.write_text("{}")
        with self.assertRaises(ValueError):
            package.physical_closure(self.root, required, ROOT)

    def test_model_is_the_landed_file_or_its_exact_rename_and_nothing_else(self):
        spellings = package.modelspec_spellings
        json_file, hcl_file = spellings.MODEL_PATHS
        landed = {name: package.git_blob(ROOT, package.SOURCE_REVISION, name) for name in spellings.MODEL_PATHS}
        renamed = {name: spellings.renamed(name, data) for name, data in landed.items()}
        for name in spellings.MODEL_PATHS:
            # The rename is recomputed, differs from the landed bytes, and is what the reference tool writes.
            self.assertNotEqual(renamed[name], landed[name])
            self.assertEqual(hashlib.sha256(renamed[name]).hexdigest(), spellings.RENAMED_SHA256[name])
        pins = {pin["path"]: pin for pin in self.snapshot["artifacts"]}
        for name in spellings.MODEL_PATHS:
            # In both states the snapshot pins the landed bytes; it is not rewritten for the rename.
            self.assertEqual((pins[name]["bytes"], pins[name]["sha256"]),
                             (len(landed[name]), hashlib.sha256(landed[name]).hexdigest()))

        def place(files):
            for name, data in files.items():
                (self.root / name).write_bytes(data)

        for label, files in {"landed": landed, "renamed": renamed}.items():
            with self.subTest(state=label):
                place(files)
                self.assertEqual(spellings.model_state(landed, files), "accepted" if label == "landed" else "renamed")
                self.assertEqual(package.verify_bundle(self.root, ROOT), self.snapshot)
        mixed = json.loads(renamed[json_file])
        mixed["entities"] = mixed.pop("records")
        refused = {
            "only the JSON renamed": {json_file: renamed[json_file], hcl_file: landed[hcl_file]},
            "only the HCL renamed": {json_file: landed[json_file], hcl_file: renamed[hcl_file]},
            "rename and one more byte": {json_file: renamed[json_file] + b" ", hcl_file: renamed[hcl_file]},
            "rename and a comment": {json_file: renamed[json_file], hcl_file: renamed[hcl_file] + b"# note\n"},
            "rename and a changed type": {json_file: renamed[json_file].replace(b'"string"', b'"int"', 1), hcl_file: renamed[hcl_file]},
            "mixed vocabulary": {json_file: (json.dumps(mixed, indent=2, ensure_ascii=False) + "\n").encode(), hcl_file: renamed[hcl_file]},
            "landed and one more byte": {json_file: landed[json_file], hcl_file: landed[hcl_file] + b"\n"},
        }
        for label, files in refused.items():
            with self.subTest(refused=label):
                place(files)
                with self.assertRaisesRegex(ValueError, "model/binding differs from landed source"):
                    package.verify_bundle(self.root, ROOT)
                with self.assertRaises(ValueError):
                    spellings.model_state(landed, files)
        # The rename is recomputed, not looked up: with the pinned digest moved to another file's,
        # that file is still refused, because the recomputed rename no longer matches the digest.
        other_type = renamed[json_file].replace(b'"string"', b'"int"', 1)
        with patch.dict(spellings.RENAMED_SHA256, {json_file: hashlib.sha256(other_type).hexdigest()}):
            with self.assertRaisesRegex(ValueError, "does not reproduce the reference tool's output"):
                spellings.model_state(landed, {json_file: other_type, hcl_file: renamed[hcl_file]})
        # A snapshot pin of a model file must name the landed size as well as the landed SHA-256.
        place(renamed)
        for name in spellings.MODEL_PATHS:
            with self.subTest(wrong_bytes=name):
                pins[name]["bytes"] += 1
                self.save_snapshot()
                with self.assertRaisesRegex(ValueError, "required artifact bytes/hash mismatch"):
                    package.verify_bundle(self.root, ROOT)
                pins[name]["bytes"] -= 1
        self.save_snapshot()
        self.assertEqual(package.verify_bundle(self.root, ROOT), self.snapshot)
        # A snapshot whose pin is moved to follow the renamed file is refused: the pin names the landed bytes.
        for pin in self.snapshot["artifacts"]:
            if pin["path"] == json_file:
                pin.update(bytes=len(renamed[json_file]), sha256=hashlib.sha256(renamed[json_file]).hexdigest())
        self.save_snapshot()
        self.rejected()
        # A rename the reference tool would not write is refused, whatever it is compared with.
        other = landed[json_file].replace(b'"geonames"', b'"other"', 1)
        with self.assertRaisesRegex(ValueError, "does not reproduce the reference tool's output"):
            spellings.renamed(json_file, other)
        with self.assertRaisesRegex(ValueError, "not a renamed model file"):
            spellings.renamed("model/geonames.meaning.yaml", b"")

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

    def test_model_native_keys_read_either_vocabulary_and_refuse_a_mixed_document(self):
        earlier, current = both(package.read_json(ROOT / "model/geonames.modelspec.json"))
        package.check_model_keys(earlier)
        package.check_model_keys(current)
        for label, model, message in [
                ("mixed", dict(current, modelspec="1.0-draft"), '"records" belongs to format 1.0-draft-2'),
                ("removed", dict(earlier, collections={}), '"collections" was removed'),
                ("reserved", dict(current, migrations={}), '"migrations" is a reserved word')]:
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, message):
                package.check_model_keys(model)
        for label, change in [("key", lambda m: m["records"]["geonames_places"].update(key=["name"])),
                              ("required", lambda m: m["records"]["geonames_places"]["fields"]["geonameid"].update(required=False)),
                              ("type", lambda m: m["records"]["geonames_places"]["fields"]["geonameid"].update(type="int")),
                              ("module", lambda m: m["module"].update(name="other"))]:
            model = copy.deepcopy(current)
            change(model)
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "model native key mismatch"):
                package.check_model_keys(model)
        with self.assertRaises(KeyError):
            package.check_model_keys({"modelspec": "1.0-draft-2", "module": current["module"]})

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

    def test_per_entity_original_association_key_and_proofs_are_checked(self):
        name = "source/native/geonames_places.json"
        original = package.read_json(self.root / name)
        mutants = []
        for target, field, value in [("native_key", "property", "name"), ("native_key", "namespace", "generic-city"),
                                     ("snapshot_association", "output_key", "chunks"), ("native_key", "records", 1)]:
            changed = copy.deepcopy(original)
            changed[target][field] = value
            mutants.append(changed)
        changed = copy.deepcopy(original)
        changed["snapshot_association"]["source"]["sha256"] = "0" * 64
        mutants.append(changed)
        changed = copy.deepcopy(original)
        changed["snapshot"]["counts"]["geonames_places"] -= 1
        mutants.append(changed)
        for value in mutants:
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

    def test_unlisted_physical_metadata_chunks_and_logical_dataset_fail(self):
        for name, data in [("source/native/unreviewed.json", b"{}"),
                           ("source/extra.json", b" " * (3 * 1024**2)),
                           (package.PREFIX + "extra.gz", b"junk"),
                           ("geonames.sqlite", b"counterfeit"),
                           ("geonames.sqlite.gz", b"counterfeit"),
                           ("bridges/unlisted.json", b"{}"), ("model/unlisted.json", b"{}"),
                           ("artifacts/geonames-w1-2026-10-05/extra.json", b"{}")]:
            with self.subTest(name=name):
                path = self.root / name
                path.write_bytes(data)
                self.rejected()
                path.unlink()
        directory = self.root / "source/unlisted"
        directory.mkdir()
        self.rejected()
        directory.rmdir()
        link = self.root / "source/unlisted"
        link.symlink_to(ROOT / "LICENSE")
        self.rejected()
        link.unlink()
        os.mkfifo(link)
        self.rejected()

    def test_metadata_guards_reject_fifo_directory_symlink_and_oversize_before_open(self):
        for name in ("source/artifact-snapshot.json", "source/generation-validation.json",
                     "source/generation-snapshot.json"):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.unlink()
                os.mkfifo(path)
                # Timeout is a backstop: a regression fails without hanging the suite.
                result = subprocess.run([sys.executable, str(ROOT / package.SCRIPT), "check", "--root", str(self.root)],
                                        capture_output=True, timeout=3, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"regular file", result.stderr)
                path.unlink()
                for kind in ("directory", "symlink", "oversize"):
                    with self.subTest(kind=kind):
                        if kind == "directory":
                            path.mkdir()
                        elif kind == "symlink":
                            path.symlink_to(ROOT / name)
                        else:
                            with path.open("wb") as output:
                                output.truncate(package.METADATA_LIMIT + 1)
                        # Demonstrate the guard runs before even a bounded open.
                        with patch.object(package.os, "open", side_effect=AssertionError("opened unsafe metadata")):
                            with self.assertRaises(ValueError):
                                package.read_json(path)
                        self.rejected()
                        if kind == "directory":
                            path.rmdir()
                        else:
                            path.unlink()
                path.write_bytes(original)

    def test_input_snapshot_is_bounded_regular_before_open(self):
        bundle = self.root / "input"
        bundle.mkdir()
        path = bundle / "snapshot.json"
        for kind in ("fifo", "directory", "symlink", "oversize"):
            with self.subTest(kind=kind):
                if kind == "fifo":
                    os.mkfifo(path)
                elif kind == "directory":
                    path.mkdir()
                elif kind == "symlink":
                    path.symlink_to(self.root / "source/generation-snapshot.json")
                else:
                    with path.open("wb") as output:
                        output.truncate(package.METADATA_LIMIT + 1)
                with self.assertRaises(ValueError):
                    package.package(ROOT, bundle, self.root / "candidate", self.snapshot["generator"]["revision"],
                                    self.root / "measure.json")
                self.assertFalse((self.root / "candidate").exists())
                self.assertFalse((self.root / "measure.json").exists())
                if kind == "directory":
                    path.rmdir()
                else:
                    path.unlink()

    def test_repinned_evidence_and_native_receipt_counts_require_exact_integer_types(self):
        for name in ("source/native-key-evidence.json", "source/native/geonames_places.json"):
            original = package.read_json(self.root / name)
            paths = [("keys", 2, field) for field in ("records", "duplicates", "missing", "invalid_native_values")] if "evidence" in name else [
                ("native_key", "records"), ("native_key", "duplicates"),
                ("native_key_checks", "missing"), ("native_key_checks", "invalid_native_values"),
                ("snapshot", "counts", "geonames_places")]
            for keys in paths:
                for kind in ("float", "bool"):
                    with self.subTest(name=name, keys=keys, kind=kind):
                        changed = copy.deepcopy(original)
                        target = changed
                        for key in keys[:-1]:
                            target = target[key]
                        value = target[keys[-1]]
                        target[keys[-1]] = float(value) if kind == "float" else bool(value)
                        self.alter_metadata(name, changed)
                        self.rejected()
            self.alter_metadata(name, original)

    def test_existing_output_and_wrong_generator_leave_no_candidate(self):
        output = self.root / "already"
        output.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            package.package(ROOT, self.root, output, package.SOURCE_REVISION, self.root / "measure.json")
        with self.assertRaises((ValueError, __import__('subprocess').CalledProcessError)):
            package.package(ROOT, self.root, self.root / "candidate", package.SOURCE_REVISION, self.root / "measure.json")
        self.assertFalse((self.root / "candidate").exists())
        self.assertFalse(list(self.root.glob(".geonames-package-*")))

    def test_package_budget_failure_cleans_completed_stage_without_narrowing(self):
        bundle = self.root / "input-bundle"
        bundle.mkdir()
        for name in package.SOURCE_METADATA:
            destination = bundle / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if name in package.modelspec_spellings.MODEL_PATHS:
                # The reviewed input bundle holds the model as it was landed, whichever state the tree is in.
                destination.write_bytes(package.git_blob(ROOT, package.SOURCE_REVISION, name))
            else:
                shutil.copyfile(self.root / name, destination)
        shutil.copyfile(self.root / "source/generation-snapshot.json", bundle / "snapshot.json")
        for chunk in self.snapshot["sqlite"]["chunks"]:
            shutil.copyfile(self.root / chunk["path"], bundle / Path(chunk["path"]).name)
        with (bundle / "geonames.sqlite").open("wb") as stream:
            package.reconstruct(self.root, self.snapshot["sqlite"], stream)
        output = self.root / "over-budget"
        revision = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        with patch.object(package, "DISK_LIMIT", 1), self.assertRaisesRegex(ValueError, "resource budget"):
            package.package(ROOT, bundle, output, revision, self.root / "measurement.json")
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob(".geonames-package-*")))
        self.assertFalse((self.root / "measurement.json").exists())


if __name__ == "__main__":
    unittest.main()
