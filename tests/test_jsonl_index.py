import json
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
import time
from unittest.mock import patch

import meury_app.indexer as indexer_module
from meury_app.indexer import build_index, image_key, load_index_payload, update_index_incremental
from meury_app.operational_store import sync_records
from meury_app.operational_store import load_quarantine_issues


class JsonlIndexTest(unittest.TestCase):
    def test_build_reload_and_incremental_scan_with_two_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            sources = [root / "primeira", root / "segunda"]
            images = [sources[0] / "100" / "100-A.jpg", sources[1] / "200" / "200-B.jpg"]
            for image in images:
                image.parent.mkdir(parents=True)
                image.write_bytes(b"image")
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                index, result = build_index(sources)
                self.assertEqual(result.source_dirs, 2)
                self.assertEqual(index[image_key("100", "100-A")], [str(images[0])])
                self.assertEqual(index[image_key("200", "200-B")], [str(images[1])])
                records = load_index_payload(sources)["records"]
                self.assertEqual({record["source"] for record in records}, {0, 1})
                self.assertEqual({record["path"] for record in records}, set(map(str, images)))
                _, incremental = update_index_incremental(sources)
                self.assertEqual(incremental.unchanged_files, 2)
                self.assertEqual(incremental.hashed_files, 0)

    def catalog_files(self, root):
        return (
            patch.object(indexer_module, "INDEX_FILE", root / "indice.jsonl"),
            patch.object(indexer_module, "LEGACY_INDEX_FILE", root / "indice.json"),
            patch.object(indexer_module, "DUPLICATES_LOG_FILE", root / "duplicidades.txt"),
            patch.object(indexer_module, "ensure_app_dir"),
        )

    def test_writes_jsonl_with_metadata_and_supports_jpeg(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            image = source / "12345 BRASIL" / "12345-A.jpeg"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"jpeg")
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                index, _ = build_index(source)
                lines = (root / "indice.jsonl").read_text(encoding="utf-8").splitlines()

            self.assertIn(image_key("12345", "12345-A"), index)
            self.assertEqual(json.loads(lines[0])["type"], "catalog")
            record = json.loads(lines[1])
            self.assertEqual(record["relative_path"], "12345 BRASIL/12345-A.jpeg")
            self.assertEqual(record["path"], "12345 BRASIL/12345-A.jpeg")
            self.assertNotIn(str(source), lines[0])
            self.assertEqual(record["keywords"], [])
            self.assertFalse(record["processed"])
            self.assertEqual(record["codigo"], "12345")
            self.assertEqual(record["variante"], "A")
            self.assertEqual(record["asset_id"], "12345 brasil/12345-a")
            self.assertTrue(record["indexed"])
            self.assertFalse(record["changed"])
            self.assertEqual(record["content_hash"], hashlib.sha256(b"jpeg").hexdigest())
            self.assertEqual(record["scan_status"], "new")
            self.assertEqual(record["processing_status"], "pending")
            self.assertEqual(record["preview_status"], "pending")
            self.assertEqual(record["cloud_status"], "pending")
            self.assertEqual(record["supabase_status"], "pending")
            self.assertFalse(record["missing_locally"])
            self.assertTrue(record["last_indexed_at"])

    def test_loads_version_7_and_supplies_new_state_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            image = source / "900 COLECAO" / "900-B.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            catalog = root / "indice.jsonl"
            header = {
                "type": "catalog", "version": 7,
                "source_dirs": [str(source.resolve())],
            }
            record = {
                "type": "image", "source": 0,
                "relative_path": "900 COLECAO/900-B.png",
                "path": str(image.resolve()), "filename": image.name,
                "design_id": "900", "key": image_key("900", "900-B"),
                "size": image.stat().st_size, "mtime_ns": image.stat().st_mtime_ns,
                "active": True,
            }
            catalog.write_text(
                json.dumps(header) + "\n" + json.dumps(record) + "\n",
                encoding="utf-8",
            )
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                payload = load_index_payload(source)

            migrated = payload["records"][0]
            self.assertEqual(migrated["codigo"], "900")
            self.assertEqual(migrated["variante"], "B")
            self.assertEqual(migrated["scan_status"], "indexed")
            self.assertEqual(migrated["preview_status"], "pending")
            self.assertEqual(migrated["cloud_status"], "pending")
            self.assertEqual(migrated["supabase_status"], "pending")

    def test_preserves_metadata_when_file_is_renamed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            original = source / "12345" / "12345-A.jpg"
            renamed = source / "12345" / "12345-B.jpg"
            original.parent.mkdir(parents=True)
            original.write_bytes(b"imagem-unica")
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                build_index(source)
                catalog = root / "indice.jsonl"
                lines = catalog.read_text(encoding="utf-8").splitlines()
                record = json.loads(lines[1])
                record.update({
                    "keywords": ["Brasil"], "description": "Bandeira do Brasil",
                    "processed": True, "embedding": [0.1, 0.2],
                    "preview_status": "ready", "preview_path": "/cache/preview.jpg",
                    "cloud_status": "uploaded", "storage_key": "artes/12345.jpg",
                    "supabase_status": "synced", "last_synced_at": "2026-08-27",
                })
                catalog.write_text(lines[0] + "\n" + json.dumps(record) + "\n", encoding="utf-8")
                sync_records(indexer_module._operational_db_path(), [record])
                original.rename(renamed)
                renamed_stat = renamed.stat()
                os.utime(
                    renamed,
                    ns=(renamed_stat.st_atime_ns, renamed_stat.st_mtime_ns + 1_000_000),
                )

                index, result = update_index_incremental(source)
                payload = load_index_payload(source)

            self.assertEqual(result.moved_files, 1)
            self.assertEqual(result.added_files, 0)
            self.assertIn(str(renamed.resolve()), index[image_key("12345", "12345-B")])
            active = [item for item in payload["records"] if item["active"]]
            self.assertEqual(active[0]["keywords"], ["Brasil"])
            self.assertEqual(active[0]["description"], "Bandeira do Brasil")
            self.assertTrue(active[0]["processed"])
            self.assertEqual(active[0]["embedding"], [0.1, 0.2])
            self.assertEqual(active[0]["preview_status"], "ready")
            self.assertEqual(active[0]["cloud_status"], "uploaded")
            self.assertEqual(active[0]["supabase_status"], "synced")
            self.assertEqual(active[0]["asset_id"], "12345/12345-a")

    def test_ambiguous_same_hash_requires_review_instead_of_merging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            first = source / "100" / "100-A.png"
            second = source / "200" / "200-A.png"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_bytes(b"conteudo-igual")
            second.write_bytes(b"conteudo-igual")
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                build_index(source)
                first.unlink()
                second.unlink()
                candidate = source / "NATAL" / "300" / "300-X.png"
                candidate.parent.mkdir(parents=True)
                candidate.write_bytes(b"conteudo-igual")
                _index, result = update_index_incremental(source)
                records = load_index_payload(source)["records"]

            self.assertEqual(result.moved_files, 0)
            self.assertEqual(result.added_files, 1)
            self.assertEqual(result.removed_files, 2)
            self.assertGreaterEqual(result.review_files, 3)
            reviewed = [record for record in records if record["review_required"]]
            self.assertEqual(len(reviewed), 3)

    def test_quarantines_problem_files_and_continues_scan(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow não instalado")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            valid = source / "100" / "100-A.png"
            corrupt = source / "200" / "200-B.png"
            invalid_name = source / "300" / "sem-variante.png"
            unsupported = source / "400" / "400-A.gif"
            for path in (valid, corrupt, invalid_name, unsupported):
                path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (4, 4), "red").save(valid)
            corrupt.write_bytes(b"nao-e-imagem")
            Image.new("RGB", (4, 4), "blue").save(invalid_name)
            unsupported.write_bytes(b"GIF89a")
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                index, _result = build_index(source)
                records = load_index_payload(source)["records"]
                issues = load_quarantine_issues(indexer_module._operational_db_path())

            self.assertIn(image_key("100", "100-A"), index)
            self.assertEqual(len([record for record in records if record["active"]]), 3)
            attention = {record["filename"]: record for record in records}
            self.assertEqual(attention["200-B.png"]["attention_status"], "REQUIRES_ATTENTION")
            self.assertEqual(
                attention["sem-variante.png"]["attention_status"], "REQUIRES_ATTENTION"
            )
            reasons = {issue["reason"] for issue in issues}
            self.assertIn("CORRUPTED_FILE", reasons)
            self.assertIn("VARIANT_NOT_IDENTIFIED", reasons)
            self.assertIn("UNSUPPORTED_FORMAT", reasons)

    def test_marks_removed_file_inactive_and_excludes_it_from_lookup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            image = source / "12345" / "12345-A.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"imagem")
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                build_index(source)
                image.unlink()
                index, result = update_index_incremental(source)
                payload = load_index_payload(source)

            self.assertEqual(result.removed_files, 1)
            self.assertNotIn(image_key("12345", "12345-A"), index)
            self.assertFalse(payload["records"][0]["active"])
            self.assertTrue(payload["records"][0]["missing_locally"])
            self.assertTrue(payload["records"][0]["missing_detected_at"])

    def test_reappearing_file_clears_missing_without_deleting_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            image = source / "12345" / "12345-A.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"imagem")
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                build_index(source)
                image.unlink()
                update_index_incremental(source)
                missing = load_index_payload(source)["records"][0]
                detected_at = missing["missing_detected_at"]
                image.write_bytes(b"imagem")
                _index, result = update_index_incremental(source)
                restored = load_index_payload(source)["records"][0]

            self.assertEqual(result.removed_files, 0)
            self.assertTrue(restored["active"])
            self.assertFalse(restored["missing_locally"])
            self.assertEqual(restored["scan_status"], "reappeared")
            self.assertEqual(restored["missing_detected_at"], detected_at)

    def test_hashes_only_candidate_and_detects_same_name_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            image = source / "6844" / "6844-A.tif"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"original")
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                build_index(source)
                previous_mtime = image.stat().st_mtime_ns
                image.write_bytes(b"corrigida")
                os.utime(image, ns=(previous_mtime + 1_000_000, previous_mtime + 1_000_000))
                _index, result = update_index_incremental(source)
                payload = load_index_payload(source)

            record = payload["records"][0]
            self.assertEqual(result.changed_files, 1)
            self.assertEqual(result.verification_files, 1)
            self.assertEqual(result.hashed_files, 1)
            self.assertTrue(record["changed"])
            self.assertEqual(record["scan_status"], "changed")
            self.assertEqual(record["content_hash"], hashlib.sha256(b"corrigida").hexdigest())
            self.assertEqual(record["preview_status"], "pending")
            self.assertEqual(record["cloud_status"], "pending")
            self.assertEqual(record["supabase_status"], "pending")

    def test_mtime_change_with_same_hash_keeps_derived_states(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            image = source / "7000" / "7000-C.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"same-content")
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                build_index(source)
                catalog = root / "indice.jsonl"
                lines = catalog.read_text(encoding="utf-8").splitlines()
                record = json.loads(lines[1])
                record.update({
                    "preview_status": "ready", "cloud_status": "uploaded",
                    "supabase_status": "synced",
                })
                catalog.write_text(lines[0] + "\n" + json.dumps(record) + "\n", encoding="utf-8")
                sync_records(indexer_module._operational_db_path(), [record])
                stat = image.stat()
                os.utime(image, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
                _index, result = update_index_incremental(source)
                payload = load_index_payload(source)

            current = payload["records"][0]
            self.assertEqual(result.changed_files, 0)
            self.assertEqual(result.hashed_files, 1)
            self.assertFalse(current["changed"])
            self.assertEqual(current["preview_status"], "ready")
            self.assertEqual(current["cloud_status"], "uploaded")
            self.assertEqual(current["supabase_status"], "synced")

    def test_fast_scan_does_not_hash_or_rewrite_unchanged_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            for number in range(300):
                image = source / str(number) / f"{number}-A.jpg"
                image.parent.mkdir(parents=True)
                image.write_bytes(f"imagem-{number}".encode())
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                build_index(source)
                catalog = root / "indice.jsonl"
                written_at = catalog.stat().st_mtime_ns
                started = time.monotonic()
                with patch(
                    "meury_app.indexer.calculate_content_hash",
                    side_effect=AssertionError("hash não deveria ser calculado"),
                ):
                    _index, result = update_index_incremental(source)
                elapsed = time.monotonic() - started

            self.assertEqual(result.unchanged_files, 300)
            self.assertEqual(result.hashed_files, 0)
            self.assertEqual(catalog.stat().st_mtime_ns, written_at)
            # Limite folgado para detectar regressões grosseiras sem tornar o
            # teste dependente da velocidade específica da máquina.
            self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
