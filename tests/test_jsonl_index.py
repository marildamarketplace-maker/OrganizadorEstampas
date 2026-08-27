import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import meury_app.indexer as indexer_module
from meury_app.indexer import build_index, image_key, load_index_payload, update_index_incremental


class JsonlIndexTest(unittest.TestCase):
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
            self.assertEqual(record["keywords"], [])
            self.assertFalse(record["processed"])

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
                })
                catalog.write_text(lines[0] + "\n" + json.dumps(record) + "\n", encoding="utf-8")
                original.rename(renamed)

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


if __name__ == "__main__":
    unittest.main()
