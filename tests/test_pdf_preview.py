from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image
import pypdfium2 as pdfium

from meury_app import indexer
from meury_app.pdf_preview import render_first_page
from meury_app.preview_generator import create_preview, generate_pending_previews


def sample_pdf(path):
    with Image.new("RGB", (200, 100), "red") as first, Image.new("RGB", (100, 200), "blue") as second:
        first.save(path, "PDF", save_all=True, append_images=[second])


class PdfPreviewTest(unittest.TestCase):
    def test_renders_only_first_page_with_limited_dimensions_and_reuses_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Coleção com espaços.PDF"
            sample_pdf(source)
            original = source.read_bytes()
            record = {"path": str(source), "content_hash": hashlib.sha256(original).hexdigest()}
            destination = create_preview(record, preview_dir=root / "previews")
            with Image.open(destination) as preview:
                self.assertEqual(preview.size, (1024, 512))
                pixel = preview.convert("RGB").getpixel((512, 256))
                self.assertGreater(pixel[0], pixel[2] + 150)
            with patch("meury_app.pdf_preview.render_first_page", side_effect=AssertionError("PDF reaberto")):
                self.assertEqual(create_preview(record, preview_dir=root / "previews"), destination)
            with render_first_page(source, 128) as preview:
                self.assertEqual(preview.size, (128, 64))
            self.assertEqual(source.read_bytes(), original)

    def test_pdfium_rendering_is_serialized_between_workers(self):
        active = peak = 0
        counter_lock = threading.Lock()
        original_render = pdfium.PdfPage.render

        def tracked_render(page, *args, **kwargs):
            nonlocal active, peak
            with counter_lock:
                active += 1
                peak = max(active, peak)
            try:
                time.sleep(0.01)
                return original_render(page, *args, **kwargs)
            finally:
                with counter_lock:
                    active -= 1

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "sample.pdf"
            sample_pdf(source)
            with patch.object(pdfium.PdfPage, "render", new=tracked_render), ThreadPoolExecutor(max_workers=4) as executor:
                images = list(executor.map(lambda _: render_first_page(source, 128), range(8)))
            try:
                self.assertEqual(peak, 1)
                self.assertTrue(all(image.size == (128, 64) for image in images))
            finally:
                for image in images:
                    image.close()

    def test_retries_previously_failed_pdf_and_persists_completed_status(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary).resolve()
            source = root / "artes"
            pdf = source / "123" / "123-A.pdf"
            pdf.parent.mkdir(parents=True)
            sample_pdf(pdf)
            for name, filename in {
                "INDEX_FILE": "index.jsonl", "LEGACY_INDEX_FILE": "legacy.json",
                "DUPLICATES_LOG_FILE": "duplicates.txt", "ANALYSIS_RESULTS_FILE": "analysis.jsonl",
            }.items():
                stack.enter_context(patch.object(indexer, name, root / filename))
            stack.enter_context(patch.object(indexer, "ensure_app_dir"))
            indexer.build_index(source)
            records = indexer.load_catalog_records(source)
            records[0].update(preview_status="failed", preview_last_error="Formato sem preview local: .pdf")
            indexer.sync_records(indexer._operational_db_path(), records)
            result = generate_pending_previews(source, preview_dir=root / "previews")
            self.assertEqual((result.completed, result.failed), (1, 0))
            record = indexer.load_catalog_records(source)[0]
            self.assertEqual(record["preview_status"], "completed")
            self.assertEqual(record["preview_last_error"], "")
            self.assertTrue(Path(record["preview_path"]).is_file())

    def test_corrupt_pdf_has_a_clear_error_and_leaves_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "corrompido.pdf"
            source.write_bytes(b"%PDF-1.4\ninvalid")
            with self.assertRaisesRegex(ValueError, "Não foi possível abrir ou renderizar o PDF"):
                create_preview({"path": str(source)}, preview_dir=root / "previews")
            self.assertEqual(list((root / "previews").iterdir()), [])

    def test_missing_dependency_explains_how_to_enable_pdf_support(self):
        with patch.dict("sys.modules", {"pypdfium2": None}):
            with self.assertRaisesRegex(RuntimeError, "Reinicie pelo inicializador"):
                render_first_page(Path("sample.pdf"), 128)


if __name__ == "__main__":
    unittest.main()
