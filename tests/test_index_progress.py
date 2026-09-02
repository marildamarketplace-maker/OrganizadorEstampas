from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import warnings

from PIL import Image

from meury_app import indexer
from meury_app.index_progress import IndexProgress
from meury_app.ui import App


class IndexProgressTest(unittest.TestCase):
    def test_throttles_updates_but_delivers_phase_changes_and_final_counts(self):
        callback = Mock()
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output), patch("meury_app.index_progress.time.monotonic", return_value=10):
            progress = IndexProgress(callback)
            progress.report("Scan", 0)
            for current in range(1, 195001):
                progress.report("Scan", current)
            progress.report("Hash", 0, 195000)
            progress.report("Hash", 195000, 195000, force=True)
        self.assertEqual(callback.call_count, 3)
        self.assertIn("100.0% da etapa", callback.call_args.args[1])
        self.assertIn("195,000/195,000", callback.call_args.args[1])
        self.assertEqual(output.getvalue(), "")

    def test_reports_again_after_one_second_without_a_known_total(self):
        callback = Mock()
        with redirect_stdout(io.StringIO()), patch(
            "meury_app.index_progress.time.monotonic", side_effect=[10, 10.5, 11]
        ):
            progress = IndexProgress(callback)
            progress.report("Scan", 1)
            progress.report("Scan", 2)
            progress.report("Scan", 3)
        self.assertEqual(callback.call_count, 2)
        self.assertEqual(callback.call_args.args[0], 3)
        self.assertNotIn("%", callback.call_args.args[1])

    def test_windowed_windows_executable_without_stdout(self):
        callback = Mock()
        with patch("sys.stdout", None):
            IndexProgress(callback).report("Scan", 1)
        callback.assert_called_once()

    def test_updates_the_visible_index_label(self):
        app = App.__new__(App)
        app.index_status_var = Mock()
        app.status_var = Mock()
        app._log = Mock()
        app._index_progress(12, "SHA-256: 12/20 (60%)")
        app.index_status_var.set.assert_called_once_with("SHA-256: 12/20 (60%)")

    def test_small_catalog_reports_hash_persistence_and_completion(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary).resolve()
            source = root / "Coleção com espaços"
            image = source / "123 FLOR" / "123-A.png"
            image.parent.mkdir(parents=True)
            Image.new("RGB", (10, 10)).save(image)
            for name, filename in {
                "INDEX_FILE": "catalog.jsonl", "LEGACY_INDEX_FILE": "legacy.json",
                "ANALYSIS_RESULTS_FILE": "analysis.jsonl", "DUPLICATES_LOG_FILE": "duplicates.txt",
            }.items():
                stack.enter_context(patch.object(indexer, name, root / filename))
            stack.enter_context(patch.object(indexer, "ensure_app_dir"))
            stack.enter_context(redirect_stdout(io.StringIO()))
            messages = []
            indexer.build_index(source, lambda count, message: messages.append((count, message)))
            self.assertTrue(any("SHA-256" in message for _, message in messages))
            self.assertTrue(any("Persistindo estado local" in message for _, message in messages))
            self.assertEqual(messages[-1][0], 1)
            self.assertIn("Índice concluído", messages[-1][1])
            with patch.object(indexer, "_validate_file_content", side_effect=AssertionError("Fast Scan abriu imagem")), patch.object(
                indexer, "calculate_content_hash", side_effect=AssertionError("Fast Scan calculou hash")
            ), patch.object(indexer, "_write_catalog", side_effect=AssertionError("Fast Scan regravou catálogo")):
                _, result = indexer.update_index_incremental(source)
            self.assertEqual(result.unchanged_files, 1)

            original = (root / "catalog.jsonl").read_bytes()
            with patch.object(indexer.os, "replace", side_effect=PermissionError("arquivo bloqueado")):
                with self.assertRaises(PermissionError):
                    indexer.build_index(source)
            self.assertEqual((root / "catalog.jsonl").read_bytes(), original)
            self.assertEqual(list(root.glob("*.tmp")), [])

            image.write_bytes(b"corrompido")
            with self.assertLogs("meury_app.indexer", level="ERROR") as errors:
                indexer.build_index(source)
            self.assertIn(str(image), errors.output[0])
            self.assertIn("CORRUPTED_FILE", errors.output[0])


class ImageSizeWarningTest(unittest.TestCase):
    def test_suppresses_only_size_warning_and_keeps_security_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "image.png"
            Image.new("RGB", (15, 10)).save(path)
            with patch.object(Image, "MAX_IMAGE_PIXELS", 100), warnings.catch_warnings(record=True) as emitted:
                warnings.simplefilter("always")
                self.assertIsNone(indexer._validate_file_content(path))
                self.assertEqual(emitted, [])
            Image.new("RGB", (21, 10)).save(path)
            with patch.object(Image, "MAX_IMAGE_PIXELS", 100):
                error = indexer._validate_file_content(path)
            self.assertEqual(error[0], "CORRUPTED_FILE")
            self.assertIn("DecompressionBombError", error[1])

    def test_other_warnings_and_corruption_are_not_silenced(self):
        def open_image(path):
            warnings.warn("outro aviso", UserWarning)
            raise ValueError("conteúdo inválido")

        with patch.object(Image, "open", side_effect=open_image), warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always")
            error = indexer._validate_file_content(Path("image.png"))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(error[0], "CORRUPTED_FILE")


if __name__ == "__main__":
    unittest.main()
