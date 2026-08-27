from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import meury_app.analysis_batch as batch_module
import meury_app.indexer as indexer_module
from meury_app.analysis_batch import run_analysis_batch
from meury_app.image_analyzer import ImageAnalysis
from meury_app.indexer import build_index, pending_analysis_records


class FakeAnalyzer:
    def __init__(self, failing_name=""):
        self.failing_name = failing_name
        self.loaded = False

    def load(self):
        self.loaded = True

    def analyze(self, path):
        if path.name == self.failing_name:
            raise ValueError("imagem inválida")
        return ImageAnalysis(
            "Estampa floral", ["floral", "rosas"], ["vermelho"],
            ["rosas"], ["natureza"], "floral", model="modelo-teste",
        )


class AnalysisBatchTest(unittest.TestCase):
    def test_saves_each_success_and_error_and_resumes_only_pending(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            for name in ("100-A.jpg", "200-A.png", "300-A.jpeg"):
                image = source / name.split("-")[0] / name
                image.parent.mkdir(parents=True, exist_ok=True)
                image.write_bytes(name.encode())
            catalog = root / "indice.jsonl"
            journal = root / "resultados.jsonl"
            log = root / "analise.log"
            with (
                patch.object(indexer_module, "INDEX_FILE", catalog),
                patch.object(indexer_module, "LEGACY_INDEX_FILE", root / "legado.json"),
                patch.object(indexer_module, "DUPLICATES_LOG_FILE", root / "duplicados.txt"),
                patch.object(indexer_module, "ANALYSIS_RESULTS_FILE", journal),
                patch.object(indexer_module, "ensure_app_dir"),
                patch.object(batch_module, "ANALYSIS_LOG_FILE", log),
                patch.object(batch_module, "ensure_app_dir"),
            ):
                build_index(source)
                records, total = pending_analysis_records(source)
                updates = []
                analyzer = FakeAnalyzer("200-A.png")
                result = run_analysis_batch(
                    records, analyzer=analyzer,
                    progress_callback=lambda *values: updates.append(values),
                )
                remaining, remaining_total = pending_analysis_records(source)

            self.assertTrue(analyzer.loaded)
            self.assertEqual(total, 3)
            self.assertEqual(result.succeeded, 2)
            self.assertEqual(result.errors, 1)
            self.assertEqual(len(journal.read_text(encoding="utf-8").splitlines()), 3)
            self.assertEqual(remaining_total, 1)
            self.assertEqual(remaining[0]["filename"], "200-A.png")
            self.assertIn("imagem inválida", remaining[0]["analysis_error"])
            self.assertEqual([update[0] for update in updates], [1, 2, 3])
            self.assertIn("SUCESSO", log.read_text(encoding="utf-8"))
            self.assertIn("ERRO", log.read_text(encoding="utf-8"))

    def test_cancelled_paused_batch_does_not_process_an_image(self):
        run_event = threading.Event()
        cancel_event = threading.Event()
        cancel_event.set()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch.object(batch_module, "ANALYSIS_LOG_FILE", root / "analise.log"),
                patch.object(batch_module, "ensure_app_dir"),
            ):
                result = run_analysis_batch(
                    [{"path": "nao-deve-ser-lida.jpg"}],
                    analyzer=FakeAnalyzer(), run_event=run_event,
                    cancel_event=cancel_event,
                )
        self.assertTrue(result.cancelled)
        self.assertEqual(result.completed, 0)


if __name__ == "__main__":
    unittest.main()
