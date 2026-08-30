from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PIL import features

import meury_app.indexer as indexer_module
from meury_app.indexer import build_index, load_index_payload
from meury_app.preview_generator import generate_pending_previews


class PreviewGeneratorTest(unittest.TestCase):
    def catalog_files(self, root):
        return (
            patch.object(indexer_module, "INDEX_FILE", root / "indice.jsonl"),
            patch.object(indexer_module, "LEGACY_INDEX_FILE", root / "indice.json"),
            patch.object(indexer_module, "DUPLICATES_LOG_FILE", root / "duplicidades.txt"),
            patch.object(indexer_module, "ensure_app_dir"),
        )

    def test_generates_derived_preview_without_changing_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            original = source / "100" / "100-A.png"
            original.parent.mkdir(parents=True)
            Image.new("RGB", (2000, 1000), "#d02070").save(original)
            original_bytes = original.read_bytes()
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                build_index(source)
                result = generate_pending_previews(source, preview_dir=root / "previews")
                record = load_index_payload(source)["records"][0]
                second = generate_pending_previews(source, preview_dir=root / "previews")

            self.assertEqual(result.completed, 1)
            self.assertEqual(result.failed, 0)
            self.assertEqual(second.pending, 0)
            self.assertEqual(original.read_bytes(), original_bytes)
            self.assertEqual(
                hashlib.sha256(original.read_bytes()).hexdigest(), record["content_hash"]
            )
            self.assertEqual(record["preview_status"], "completed")
            self.assertEqual(record["preview_content_hash"], record["content_hash"])
            preview = Path(record["preview_path"])
            self.assertTrue(preview.is_file())
            self.assertNotEqual(preview, original)
            with Image.open(preview) as image:
                self.assertEqual(image.size, (1024, 512))

    def test_marks_corrupt_pending_preview_as_failed_and_continues(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            corrupt = source / "200" / "200-B.png"
            valid = source / "300" / "300-C.jpg"
            corrupt.parent.mkdir(parents=True)
            valid.parent.mkdir(parents=True)
            corrupt.write_bytes(b"corrompida")
            Image.new("RGB", (20, 40), "blue").save(valid)
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                build_index(source)
                result = generate_pending_previews(source, preview_dir=root / "previews")
                records = load_index_payload(source)["records"]

            by_name = {record["filename"]: record for record in records}
            self.assertEqual(result.completed, 1)
            self.assertEqual(result.failed, 1)
            self.assertEqual(by_name["200-B.png"]["preview_status"], "failed")
            self.assertIn("Preview:", by_name["200-B.png"]["last_error"])
            self.assertEqual(by_name["300-C.jpg"]["preview_status"], "completed")

    def test_supports_cross_platform_raster_formats_and_large_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            formats = [
                ("PNG", ".png"), ("JPEG", ".jpg"), ("JPEG", ".jpeg"),
                ("TIFF", ".tif"), ("TIFF", ".tiff"),
            ]
            if features.check("webp"):
                formats.append(("WEBP", ".webp"))
            for position, (image_format, suffix) in enumerate(formats, start=1):
                path = source / str(position) / f"{position}-A{suffix}"
                path.parent.mkdir(parents=True)
                size = (4096, 2048) if suffix == ".jpg" else (320, 180)
                Image.new("RGB", size, (20, 80, 160)).save(path, image_format)
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                build_index(source)
                result = generate_pending_previews(source, preview_dir=root / "previews")
                records = load_index_payload(source)["records"]

            self.assertEqual(result.completed, len(formats))
            self.assertEqual(result.failed, 0)
            for record in records:
                with Image.open(record["preview_path"]) as preview:
                    self.assertLessEqual(max(preview.size), 1024)

    def test_removes_temporary_file_when_atomic_replace_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            original = source / "500" / "500-A.png"
            original.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), "green").save(original)
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                build_index(source)
                with patch("meury_app.preview_generator.os.replace", side_effect=OSError("falha")):
                    result = generate_pending_previews(source, preview_dir=root / "previews")

            self.assertEqual(result.failed, 1)
            self.assertEqual(list((root / "previews").glob("*.tmp.*")), [])


if __name__ == "__main__":
    unittest.main()
