from pathlib import Path
import hashlib
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image, ImageCms
from PIL import features

import meury_app.indexer as indexer_module
from meury_app.indexer import build_index, load_index_payload
from meury_app.preview_generator import create_preview, generate_pending_previews


class PreviewGeneratorTest(unittest.TestCase):
    def test_keeps_exif_orientation_and_icc_after_resizing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "orientada.jpg"
            profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
            with Image.new("RGB", (2400, 1200), "red") as original:
                original.paste("blue", (1200, 0, 2400, 1200))
                exif = Image.Exif()
                exif[274] = 6
                original.save(source, exif=exif, icc_profile=profile)
            output = create_preview({"path": str(source)}, preview_dir=root / "previews")
            with Image.open(output) as preview:
                self.assertEqual(preview.size, (512, 1024))
                self.assertEqual(preview.info["icc_profile"], profile)
                top = preview.convert("RGB").getpixel((256, 100))
                bottom = preview.convert("RGB").getpixel((256, 900))
                self.assertGreater(top[0], top[2] + 100)
                self.assertGreater(bottom[2], bottom[0] + 100)

    def test_preserves_transparency_and_uses_white_in_jpeg_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "transparente.png"
            with Image.new("RGBA", (1200, 600), (255, 0, 0, 0)) as original:
                original.paste((0, 0, 255, 255), (400, 200, 800, 400))
                original.save(source)
            if features.check("webp"):
                output = create_preview({"path": str(source)}, preview_dir=root / "webp")
                with Image.open(output) as preview:
                    self.assertEqual(preview.mode, "RGBA")
                    self.assertEqual(preview.getpixel((0, 0))[3], 0)
                    self.assertEqual(preview.getpixel((512, 256))[3], 255)
            with patch("PIL.features.check", return_value=False):
                output = create_preview({"path": str(source)}, preview_dir=root / "jpeg")
            with Image.open(output) as preview:
                self.assertEqual(preview.format, "JPEG")
                self.assertTrue(all(channel > 240 for channel in preview.getpixel((0, 0))))

    def test_recovers_existing_preview_and_rebuilds_a_corrupt_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "imagem.png"
            Image.new("RGB", (1200, 600), "green").save(source)
            record = {
                "path": str(source), "content_hash": hashlib.sha256(source.read_bytes()).hexdigest(),
                "size": source.stat().st_size, "mtime_ns": source.stat().st_mtime_ns,
            }
            preview = create_preview(record, preview_dir=root / "previews")
            with patch("PIL.Image.Image.save", side_effect=AssertionError("preview deveria ser reutilizado")):
                self.assertEqual(create_preview(record, preview_dir=root / "previews"), preview)
            preview.write_bytes(b"preview interrompido")
            create_preview(record, preview_dir=root / "previews")
            with Image.open(preview) as image:
                image.load()
                self.assertEqual(image.size, (1024, 512))
            source.write_bytes(source.read_bytes() + b"changed")
            with self.assertRaisesRegex(ValueError, "original mudou"):
                create_preview(record, preview_dir=root / "previews")

    def test_invalid_content_hash_cannot_escape_preview_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "imagem.png"
            Image.new("RGB", (20, 20), "green").save(source)
            output = create_preview(
                {"path": str(source), "content_hash": "../../outside", "relative_path": "imagem.png"},
                preview_dir=root / "previews",
            )
            self.assertEqual(output.parent, (root / "previews").resolve())

    def test_rejects_original_changed_during_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "imagem.png"
            Image.new("RGB", (20, 20), "green").save(source)
            save = Image.Image.save

            def save_and_change(image, *args, **kwargs):
                save(image, *args, **kwargs)
                source.write_bytes(source.read_bytes() + b"alterado")

            with patch.object(Image.Image, "save", new=save_and_change):
                with self.assertRaisesRegex(ValueError, "mudou durante"):
                    create_preview({"path": str(source)}, preview_dir=root / "previews")
            self.assertEqual(list((root / "previews").iterdir()), [])

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

    def test_pauses_before_processing_and_resumes_safely(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            original = source / "101" / "101-A.png"
            original.parent.mkdir(parents=True)
            Image.new("RGB", (100, 100), "red").save(original)
            patches = self.catalog_files(root)
            pause = threading.Event()
            result = []
            with patches[0], patches[1], patches[2], patches[3]:
                build_index(source)
                worker = threading.Thread(
                    target=lambda: result.append(generate_pending_previews(
                        source, preview_dir=root / "previews", pause_event=pause,
                    )),
                )
                worker.start()
                time.sleep(0.05)
                self.assertTrue(worker.is_alive())
                self.assertEqual(list((root / "previews").glob("*")), [])
                pause.set()
                worker.join(timeout=2)

            self.assertFalse(worker.is_alive())
            self.assertEqual(result[0].completed, 1)

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
