from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

from meury_app.preview_generator import generate_pending_previews
from meury_app.preview_progress import PreviewProgress


def records(count):
    return [
        {"asset_id": str(number), "preview_status": "pending", "path": f"{number}.jpg"}
        for number in range(count)
    ]


class PreviewScalingTest(unittest.TestCase):
    def test_parallel_generation_has_bounded_work_and_batches_database_writes(self):
        entered = threading.Barrier(2)
        persisted = []
        active = peak = 0
        lock = threading.Lock()
        calls = []

        def create(record, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                calls.append(record["asset_id"])
            entered.wait(timeout=5)
            with lock:
                active -= 1
            return Path("preview.webp")

        def persist(path, batch):
            persisted.append([record.copy() for record in batch])

        with patch("meury_app.preview_generator.load_catalog_records", return_value=records(80)), patch(
            "meury_app.preview_generator.create_preview", side_effect=create
        ), patch("meury_app.preview_generator.sync_records", side_effect=persist), redirect_stdout(io.StringIO()):
            result = generate_pending_previews([], max_workers=2)
        self.assertEqual(peak, 2)
        self.assertEqual(len(set(calls)), 80)
        self.assertEqual(result.completed, 80)
        self.assertEqual(sum(map(len, persisted)), 80)
        self.assertLess(len(persisted), 10)
        self.assertTrue(all(record["preview_status"] == "completed" for batch in persisted for record in batch))

    def test_pause_drains_active_images_and_persists_before_waiting(self):
        pause = threading.Event()
        pause.set()
        release = threading.Event()
        both_started = threading.Event()
        paused = threading.Event()
        lock = threading.Lock()
        calls, saved, results, errors = [], [], [], []

        def create(record, **kwargs):
            with lock:
                calls.append(record["asset_id"])
                if len(calls) == 2:
                    both_started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("teste não liberou workers")
            return Path("preview.webp")

        def progress(current, total, message):
            if "Pausado" in message:
                paused.set()

        def work():
            try:
                results.append(generate_pending_previews([], max_workers=2, pause_event=pause, progress_callback=progress))
            except Exception as exc:
                errors.append(exc)

        with patch("meury_app.preview_generator.load_catalog_records", return_value=records(6)), patch(
            "meury_app.preview_generator.create_preview", side_effect=create
        ), patch("meury_app.preview_generator.sync_records", side_effect=lambda path, batch: saved.extend(record.copy() for record in batch)), redirect_stdout(io.StringIO()):
            worker = threading.Thread(target=work)
            worker.start()
            try:
                self.assertTrue(both_started.wait(timeout=5))
                pause.clear()
                release.set()
                self.assertTrue(paused.wait(timeout=5))
                self.assertEqual(len(calls), 2)
                self.assertEqual(len(saved), 2)
            finally:
                release.set()
                pause.set()
                worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results[0].completed, 6)

    def test_limit_allowed_assets_and_failures_are_counted_separately(self):
        items = records(8)
        items[0]["preview_status"] = "completed"
        items[1]["active"] = False
        items[2]["missing_locally"] = True
        callback = Mock()

        def create(record, **kwargs):
            if record["asset_id"] == "4":
                raise OSError("arquivo corrompido")
            return Path("preview.webp")

        with patch("meury_app.preview_generator.load_catalog_records", return_value=items), patch(
            "meury_app.preview_generator.create_preview", side_effect=create
        ) as generate, patch("meury_app.preview_generator.sync_records"), redirect_stdout(io.StringIO()), self.assertLogs(
            "meury_app.preview_generator", level="ERROR"
        ) as errors:
            result = generate_pending_previews([], allowed_asset_ids={"3", "4", "5"}, limit=2, progress_callback=callback)
        self.assertEqual(len(errors.output), 1)
        self.assertIn("4.jpg", errors.output[0])
        self.assertIn("arquivo corrompido", errors.output[0])
        self.assertEqual((result.pending, result.completed, result.failed), (2, 1, 1))
        self.assertEqual({call.args[0]["asset_id"] for call in generate.call_args_list}, {"3", "4"})
        self.assertEqual(callback.call_args.args[:2], (2, 2))
        self.assertIn("Prontos no catálogo: 2", callback.call_args.args[2])
        self.assertIn("falhas: 1", callback.call_args.args[2])

    def test_empty_batch_does_not_wait_on_pause(self):
        pause = Mock()
        pause.is_set.return_value = False
        with patch("meury_app.preview_generator.load_catalog_records", return_value=[]), redirect_stdout(io.StringIO()):
            result = generate_pending_previews([], pause_event=pause)
        pause.wait.assert_not_called()
        self.assertEqual(result.pending, 0)

    def test_database_error_does_not_report_success(self):
        callback = Mock()
        with patch("meury_app.preview_generator.load_catalog_records", return_value=records(1)), patch(
            "meury_app.preview_generator.create_preview", return_value=Path("preview.webp")
        ), patch("meury_app.preview_generator.sync_records", side_effect=OSError("disco cheio")), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(OSError, "disco cheio"):
                generate_pending_previews([], progress_callback=callback)
        self.assertFalse(any("Lote finalizado" in call.args[2] for call in callback.call_args_list))

    def test_worker_count_and_limit_validation(self):
        for options in ({"max_workers": 0}, {"max_workers": 5}, {"limit": 0}, {"limit": -1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                generate_pending_previews([], **options)


class PreviewProgressTest(unittest.TestCase):
    def test_eta_excludes_pause_and_messages_are_throttled(self):
        callback = Mock()
        terminal = io.StringIO()
        with patch("meury_app.preview_progress.time.monotonic") as clock, redirect_stdout(terminal), redirect_stderr(terminal):
            clock.return_value = 10
            reporter = PreviewProgress(100, 195000, callback)
            reporter.report(0, 0)
            clock.return_value = 20
            reporter.report(10, 0)
            self.assertIn("00:01:30", callback.call_args.args[2])
            reporter.report(11, 0)
            self.assertEqual(callback.call_count, 2)
            reporter.pause()
            clock.return_value = 80
            reporter.report(10, 0, force=True)
            self.assertIn("Pausado", callback.call_args.args[2])
            self.assertNotIn("Término estimado", callback.call_args.args[2])
            reporter.resume()
            clock.return_value = 90
            reporter.report(20, 0)
            self.assertIn("00:01:20", callback.call_args.args[2])
            self.assertIn("Término estimado:", callback.call_args.args[2])
            self.assertIn("Prontos no catálogo: 195,020", callback.call_args.args[2])
        self.assertEqual(terminal.getvalue(), "")

    def test_windowed_executable_reports_without_a_terminal(self):
        callback = Mock()
        with patch("sys.stdout", None):
            PreviewProgress(0, 50, callback).report(0, 0, force=True, finishing=True)
        self.assertIn("100.0%", callback.call_args.args[2])
        self.assertIn("Prontos no catálogo: 50", callback.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
