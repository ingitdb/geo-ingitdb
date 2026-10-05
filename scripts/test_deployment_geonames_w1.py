"""Candidate metadata preserves native bytes and fails closed on corrupt inputs."""
import copy
from contextlib import closing
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("deployment", Path(__file__).resolve().parents[1] / "scripts/generate_deployment.py")
deployment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deployment)


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.outputs = deployment.build()

    def test_reproduced_metadata_matches_checked_files(self):
        for path, data in self.outputs.items():
            self.assertEqual((deployment.ROOT / path).read_bytes(), data, path)

    def test_candidate_identity_rights_and_closed_deployment(self):
        manifest = json.loads(self.outputs["manifest.json"])
        public = json.loads(self.outputs["ovdb-database.json"])
        _, _, revision, licence, _ = deployment.CONFIG[deployment.ROOT.name]
        self.assertFalse(manifest["capabilities"]["ovdb"]["available"])
        self.assertFalse(manifest["capabilities"]["ovdb"]["query"])
        self.assertFalse(manifest["capabilities"]["ovdb"]["deploymentVerified"])
        self.assertEqual(public["capabilities"], dict(read=True, query=False, write=False))
        self.assertEqual(set(public["deployment"]), {"engine", "url", "discovery"})
        self.assertEqual(public["provenance"]["revision"], revision)
        self.assertNotIn("role", public["provenance"])
        self.assertEqual(public["licences"]["data"], licence)
        self.assertEqual(public["provenance"]["license"], licence)
        self.assertEqual(public["id"], public["deployment"]["url"])
        self.assertEqual(public["homepage"], public["publisher"]["repository"])

    def test_native_export_and_representation_inputs_remain_bound(self):
        snapshot = json.loads((deployment.ROOT / "source/artifact-snapshot.json").read_bytes())
        contract = json.loads(self.outputs["metadata/contract.json"])
        public = json.loads(self.outputs["ovdb-database.json"])
        checksums = json.loads(self.outputs["metadata/checksums.json"])["files"]
        self.assertEqual(contract["exports"], [snapshot["sqlite"]])
        self.assertEqual(public["provenance"]["sha256"], snapshot["sqlite"]["decodedSha256"])
        modeled = json.loads((deployment.ROOT / ("model/" + public["localId"] + ".modelspec.json")).read_bytes())["entities"]
        self.assertEqual({t["name"] for t in public["recordsets"]}, set(modeled))
        full_names = {t["name"] for t in contract["schema"]["tables"]}
        expected_diagnostics = deployment.GEONAMES_DIAGNOSTICS if public["localId"] == "geonames" else set()
        self.assertEqual(full_names, set(modeled) | expected_diagnostics)
        self.assertEqual(checksums[deployment.SCHEMA_PATH]["sha256"], deployment.SCHEMA_SHA256)
        for chunk in snapshot["sqlite"]["chunks"]:
            self.assertEqual(checksums[chunk["path"]], {k: chunk[k] for k in ("bytes", "sha256")})
        attachment = json.loads((deployment.ROOT / "source/representation-attachment.json").read_bytes())
        self.assertEqual(checksums[attachment["path"]]["sha256"], attachment["sha256"])
        self.assertIn(attachment["sha256"].encode(), self.outputs["ovdb.yaml"])

    def test_downloads_are_actual_immutable_chunks_with_attribution(self):
        artifact = json.loads(self.outputs["metadata/artifact.json"])
        chunks = artifact["sqlite"]["chunks"]
        self.assertEqual(len(artifact["downloads"]), len(chunks))
        for download, chunk in zip(artifact["downloads"], chunks):
            self.assertEqual({k: download[k] for k in chunk}, chunk)
            self.assertIn("/" + artifact["revision"] + "/", download["url"])
            self.assertTrue(download["url"].endswith(chunk["path"]))
            self.assertNotEqual(download["url"].rsplit("/", 1)[-1], artifact["sqlite"]["path"])
        self.assertIn("DATA-LICENSE.md", [a["path"] for a in artifact["attribution"]])

    def test_acceptance_input_drift_is_refused(self):
        original = deployment.file_pin
        def drift(root, relative, ceiling=deployment.METADATA_LIMIT):
            if relative == "model/representations.json":
                raise ValueError("accepted input changed")
            return original(root, relative, ceiling)
        with patch.object(deployment, "file_pin", side_effect=drift):
            with self.assertRaisesRegex(ValueError, "accepted input changed"):
                deployment.build()

    def test_strict_json_and_paths(self):
        for data in (b'{"query":false,"query":true}', b'{"x":NaN}'):
            with self.assertRaises(ValueError):
                deployment.decode(data)
        with tempfile.TemporaryDirectory() as scratch:
            for name in ("../source.json", "/source.json", "a//b", "a/./b", "https://evil/x"):
                with self.assertRaises(ValueError):
                    deployment.safe_path(Path(scratch), name)
            (Path(scratch) / "link").symlink_to(Path(scratch))
            with self.assertRaises(ValueError):
                deployment.safe_path(Path(scratch), "link/data")

    def test_native_column_types_keys_and_nullability_are_introspected(self):
        with tempfile.TemporaryDirectory() as scratch:
            file = Path(scratch) / "native.sqlite"
            with closing(sqlite3.connect(file)) as db:
                db.execute('CREATE TABLE "native table" ("id" TEXT NOT NULL, "ordinal" INTEGER NOT NULL, "empty" TEXT, "raw" BLOB, PRIMARY KEY("id","ordinal"))')
                db.execute('INSERT INTO "native table" VALUES (?,?,?,?)', ("https://ror.org/raw", 0, None, b"\x00"))
                db.commit()
            table = deployment.native_schema(file)[0]
            self.assertEqual(table["primaryKey"], [{"column": "id", "position": 1}, {"column": "ordinal", "position": 2}])
            self.assertEqual(table["rowCount"], 1)
            self.assertEqual([c["name"] for c in table["columns"]], ["id", "ordinal", "empty", "raw"])
            self.assertEqual([c["type"] for c in table["columns"]], ["TEXT", "INTEGER", "TEXT", "BLOB"])
            self.assertTrue(table["columns"][2]["nullable"])

    def test_reconstruction_refuses_corruption_order_duplicate_and_expansion(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            decoded = b"SQLite format 3\x00" + b"native data" * 100
            encoded = gzip.compress(decoded, mtime=0)
            chunks = []
            for n, data in enumerate((encoded[:len(encoded)//2], encoded[len(encoded)//2:])):
                path = f"part-{n}"
                (root / path).write_bytes(data)
                chunks.append({"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            descriptor = {"compression": "gzip", "chunks": chunks, "bytes": len(encoded),
                          "sha256": hashlib.sha256(encoded).hexdigest(), "decodedBytes": len(decoded),
                          "decodedSha256": hashlib.sha256(decoded).hexdigest()}
            target = root / "native.sqlite"
            deployment.reconstruct(root, descriptor, target)
            self.assertEqual(target.read_bytes(), decoded)
            for mutation in ("order", "duplicate", "encoded", "decoded", "expansion"):
                bad = copy.deepcopy(descriptor)
                if mutation == "order":
                    bad["chunks"].reverse()
                elif mutation == "duplicate":
                    bad["chunks"][1] = bad["chunks"][0]
                elif mutation == "encoded":
                    bad["sha256"] = "0" * 64
                elif mutation == "decoded":
                    bad["decodedSha256"] = "0" * 64
                else:
                    bad["decodedBytes"] -= 1
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    deployment.reconstruct(root, bad, target)


if __name__ == "__main__":
    unittest.main()
