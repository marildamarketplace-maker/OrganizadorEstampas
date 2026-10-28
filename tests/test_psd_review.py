"""Regression and real-engine acceptance checks for the PSD tab review."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
import hashlib
import json
import os
import platform
import queue
import runpy
import sys
import subprocess
import tempfile
import threading
import unittest

from meury_app.compression_store import CompressionStore
from meury_app.platform_utils import open_existing_location, publish_without_overwrite
from meury_app.psd_compression import compress
from meury_app.psd_search import Cancelled, SearchSettings, analyze
from meury_app.sevenzip import SevenZip, locate
from test_psd_compression import FakeEngine


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "origem"
        self.source.mkdir()
        self.file = self.source / "arte.psd"
        self.file.write_bytes(b"PSD original")
        self.settings = SearchSettings((str(self.source),), str(self.root / "saida"))
        self.store = CompressionStore(self.root / "dados" / "state.sqlite3")

    def test_output_ancestor_does_not_hide_selected_origin(self):
        result = analyze(replace(self.settings, output=str(self.root)))
        self.assertEqual([item.path for item in result.files], [str(self.file)])

    def test_nested_output_is_pruned(self):
        nested = self.source / "saida"
        nested.mkdir()
        (nested / "old.psd").write_bytes(b"old")
        result = analyze(replace(self.settings, output=str(nested)))
        self.assertEqual([item.path for item in result.files], [str(self.file)])

    def test_dates_reject_alternate_iso_forms(self):
        for value in ("20261027", "2026-W44-2"):
            with self.assertRaises(ValueError):
                analyze(replace(self.settings, start=value))

    def test_failed_manifest_distinguishes_staged_and_published(self):
        engine = FakeEngine()
        engine.validate = Mock(side_effect=RuntimeError("bad CRC"))
        result = compress(analyze(self.settings), store=self.store, engine=engine)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["included"], 0)
        self.assertFalse(result["published"])
        self.assertEqual(result["included_paths"], [])
        saved = self.store.get(result["id"])["payload"]
        self.assertEqual(saved["included"], 0)
        self.assertIn("ainda não publicado", Path(result["log"]).read_text())

    def test_manifest_frozen_against_changes_by_caller(self):
        analysis = analyze(self.settings)
        engine = FakeEngine()
        def mutate(cancel):
            analysis.files[0].internal = "../unapproved.psd"
            analysis.files[0].selected = False
        engine.probe = mutate
        result = compress(analysis, store=self.store, engine=engine)
        self.assertEqual(result["state"], "concluída")
        self.assertTrue(result["analysis"]["files"][0]["selected"])
        self.assertNotIn("..", result["analysis"]["files"][0]["internal"])

    def test_backend_rejects_tampered_portable_collisions(self):
        for first, second in (("A/x.psd", "a/y.psd"), ("x.psd", "X.PSD"), ("x.psd", "x.psd/y.psd")):
            analysis = analyze(self.settings)
            analysis.files[0].internal = first
            analysis.files.append(replace(analysis.files[0], internal=second))
            with self.subTest(first=first), self.assertRaises(ValueError):
                compress(analysis, store=self.store, engine=FakeEngine())
        self.assertFalse(Path(self.settings.output).exists())

    def test_publication_never_overwrites_existing_file(self):
        temporary = self.root / "tmp.7z"
        target = self.root / "final.7z"
        temporary.write_bytes(b"new")
        target.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            publish_without_overwrite(temporary, target)
        self.assertEqual(target.read_bytes(), b"existing")
        self.assertEqual(temporary.read_bytes(), b"new")
        target.unlink()
        publish_without_overwrite(temporary, target)
        self.assertEqual(target.read_bytes(), b"new")
        self.assertFalse(temporary.exists())

    def test_publication_failure_does_not_report_savings_or_included_files(self):
        with patch("meury_app.psd_compression.publish_without_overwrite", side_effect=OSError("publication denied")):
            result = compress(analyze(self.settings), store=self.store, engine=FakeEngine())
        self.assertEqual(result["state"], "falhou")
        self.assertEqual(result["included"], 0)
        self.assertNotIn("difference_bytes", result)
        self.assertNotIn("archive_bytes", result)

    def test_recovery_handles_corrupt_row_and_offline_log(self):
        self.store.save("broken", "em andamento", {})
        self.store.save("offline", "em andamento", {"log": str(self.root / "offline.txt")})
        with self.store.connect() as connection:
            connection.execute("UPDATE compression_run SET payload='not-json' WHERE id='broken'")
        with patch.object(Path, "is_file", side_effect=OSError("share unavailable")):
            self.store.recover()
        self.assertTrue(all(row["state"] == "interrompida" for row in self.store.list()))

    def test_exit_race_still_reaps_child(self):
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired("7z", .15), 0]
        process.poll.return_value = None
        process.terminate.side_effect = ProcessLookupError()
        with patch("meury_app.sevenzip.subprocess.Popen", return_value=process):
            with self.assertRaises(Cancelled):
                SevenZip("/trusted/7z").run(["i"], lambda: bool(process.wait.call_count))
        self.assertEqual(process.wait.call_count, 2)

    def test_forced_cancel_waits_after_kill(self):
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired("7z", .15), subprocess.TimeoutExpired("7z", 3), 0]
        process.poll.return_value = None
        with patch("meury_app.sevenzip.subprocess.Popen", return_value=process):
            with self.assertRaises(Cancelled):
                SevenZip("/trusted/7z").run(["i"], lambda: bool(process.wait.call_count))
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 3)

    def test_location_opener_waits_without_shell(self):
        with patch("meury_app.platform_utils.platform.system", return_value="Darwin"), patch("meury_app.platform_utils.subprocess.run") as run:
            open_existing_location(self.file)
        self.assertEqual(run.call_args.args[0], ["open", str(self.source)])
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_history_worker_does_not_access_widgets_or_change_busy_state(self):
        from meury_app.psd_compression_tab import CompressionTab
        tab = CompressionTab.__new__(CompressionTab)
        tab.closing = False
        tab.aux_workers = []
        tab.events = queue.Queue()
        tab.history_generation = 0
        tab.store = self.store
        tab.app = Mock()
        tab.root = Mock()
        tab.history = Mock()
        tab.refresh_history(recover=True)
        for worker in tab.aux_workers:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(tab.events.get_nowait(), ("history", (1, [])))
        tab.history.delete.assert_not_called()
        tab.app._set_busy.assert_not_called()
        tab.root.after.assert_not_called()

    def test_stale_analysis_result_is_discarded(self):
        from meury_app.psd_compression_tab import CompressionTab
        tab = CompressionTab.__new__(CompressionTab)
        tab.closing = False
        tab.events = queue.Queue()
        tab.events.put(("analysis", analyze(self.settings)))
        tab.settings = Mock(return_value=replace(self.settings, names="changed"))
        tab.app = Mock()
        tab.stop_button = Mock()
        tab.root = Mock()
        tab.invalidate = Mock()
        tab.render = Mock()
        tab.drain()
        tab.invalidate.assert_called_once()
        tab.render.assert_not_called()

    def test_window_close_cancels_and_waits_for_all_workers(self):
        from types import SimpleNamespace
        from meury_app.ui import App
        app = App.__new__(App)
        app.root = Mock()
        worker = Mock()
        worker.is_alive.return_value = True
        auxiliary = Mock()
        auxiliary.is_alive.return_value = True
        tab = SimpleNamespace(worker=worker, aux_workers=[auxiliary], closing=False, cancel=threading.Event(), status=Mock())
        app.compression_tab = tab
        app._close_window()
        self.assertTrue(tab.closing)
        self.assertTrue(tab.cancel.is_set())
        app.root.destroy.assert_not_called()
        worker.is_alive.return_value = False
        app._close_window()
        app.root.destroy.assert_not_called()
        auxiliary.is_alive.return_value = False
        app._close_window()
        app.root.destroy.assert_called_once()

    def test_bundle_hook_includes_only_provisioned_runtime_and_licenses(self):
        hook = Path(__file__).resolve().parents[1] / "hooks" / "hook-meury_app.sevenzip.py"
        isolated_hook = self.root / "hooks" / hook.name
        isolated_hook.parent.mkdir()
        isolated_hook.write_text(hook.read_text())
        self.assertEqual(runpy.run_path(str(isolated_hook))["binaries"], [])
        vendor = self.root / "vendor" / "7zip"
        vendor.mkdir(parents=True)
        executable = "7za.exe" if sys.platform == "win32" else "7zz"
        (vendor / executable).write_bytes(b"synthetic executable")
        with self.assertRaises(RuntimeError):
            runpy.run_path(str(isolated_hook))
        (vendor / "License.txt").write_text("synthetic license")
        result = runpy.run_path(str(isolated_hook))
        self.assertIn((str(vendor / executable), "vendor/7zip"), result["binaries"])
        self.assertIn((str(vendor / "License.txt"), "vendor/7zip"), result["datas"])

    def real_engine(self):
        try:
            return SevenZip(locate())
        except RuntimeError:
            self.skipTest("7-Zip indisponível: integração real não executada")

    def test_real_multi_origin_sha256_and_archive_properties(self):
        engine = self.real_engine()
        source_a = self.root / "origem com espaços"
        source_b = self.root / "segunda origem Ω"
        files = {}
        for root, relative, content in (
            (source_a, "sub pasta/árvore.PSD", b"8BPS" + bytes(range(256)) * 1024),
            (source_a, "outra pasta/arte.psd", b"primeira arte" * 4096),
            (source_b, "sub Unicode/ARTE.PSD", b"outra arte diferente" * 2048),
            (source_b, "sub/não incluir.psd", b"not selected"),
            (source_a, "sub pasta/vazio.psd", b""),
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            info = path.stat()
            files[str(path)] = {"sha256": sha256(path), "size": info.st_size, "mtime_ns": info.st_mtime_ns, "mode": info.st_mode}
        output = source_a / "saida de compactação"
        settings = SearchSettings((str(source_a), str(source_b), str(source_a / "sub pasta")), str(output))
        analysis = analyze(settings)
        self.assertEqual(len(analysis.files), 5)
        self.assertTrue(any("Homônimo" in item.situation for item in analysis.files))
        for item in analysis.files:
            if Path(item.path).name == "não incluir.psd":
                item.selected = False
                item.decision = "ignorar ocorrência"
        commands = []
        children = []
        popen = subprocess.Popen
        def spawn(args, **kwargs):
            commands.append(args)
            child = popen(args, **kwargs)
            children.append(child)
            return child
        with patch("meury_app.sevenzip.subprocess.Popen", side_effect=spawn):
            result = compress(analysis, store=self.store, engine=engine)
        self.assertEqual(result["state"], "concluída com avisos", result["errors"])
        self.assertEqual(result["included"], 4)
        self.assertEqual(result["inspection"]["Type"], "7z")
        self.assertEqual(result["inspection"]["Solid"], "-")
        self.assertIn("LZMA2", result["inspection"]["Method"])
        self.assertTrue(all(child.poll() is not None for child in children))
        self.assertTrue(all("-mx=5" in args and "-m0=LZMA2" in args and "-ms=off" in args for args in commands if args[1] == "a"))
        saved = self.store.get(result["id"])
        self.assertEqual(saved["payload"]["included_paths"], result["included_paths"])
        self.assertEqual(saved["payload"]["inspection"], result["inspection"])
        log = Path(result["log"]).read_text(encoding="utf-8")
        final_log = json.loads(log.splitlines()[-1].split(" ", 1)[1])
        self.assertEqual(final_log["included_paths"], result["included_paths"])
        for path in files:
            self.assertIn(path, log)
        extraction = self.root / "extração somente teste"
        self.assertTrue(extraction.is_relative_to(self.root))
        engine.run(["x", result["archive"], f"-o{extraction}", "-y"])
        evidence = []
        for item in result["analysis"]["files"]:
            original = Path(item["path"])
            self.assertEqual(sha256(original), files[item["path"]]["sha256"])
            self.assertEqual(original.stat().st_mtime_ns, files[item["path"]]["mtime_ns"])
            self.assertEqual(original.stat().st_size, files[item["path"]]["size"])
            self.assertEqual(original.stat().st_mode, files[item["path"]]["mode"])
            if item["selected"]:
                extracted_hash = sha256(extraction / item["internal"])
                self.assertEqual(extracted_hash, files[item["path"]]["sha256"])
                evidence.append({"original": str(original.relative_to(self.root)), "internal": item["internal"], "original_sha256": files[item["path"]]["sha256"], "extracted_sha256": extracted_hash})
        archive_folder = Path(result["archive"]).parent
        self.assertEqual({path.name for path in archive_folder.iterdir()}, {Path(result["archive"]).name, Path(result["log"]).name})
        self.assertFalse(any(path.suffix.casefold() == ".psd" for path in output.rglob("*")))
        self.assertEqual(len(list(extraction.rglob("*.*"))), 4)
        report = {"platform": platform.platform(), "mechanism": result["mechanism"], "inspection": result["inspection"], "originals_checked": 5, "included": 4, "sha256_comparisons": evidence, "originals_unchanged": True, "manifest_log_history_consistent": True, "no_loose_psds": True, "all_children_reaped": True, "test_data": "synthetic files, temporary directories removed after test", "compression_argv": [args[1:] for args in commands if args[1] == "a"]}
        target = os.environ.get("MEURY_PSD_VALIDATION_REPORT")
        if target:
            Path(target).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_real_cancel_reaps_7zip(self):
        engine = self.real_engine()
        # Enough incompressible data to keep a real child alive until cancellation.
        self.file.write_bytes(os.urandom(8 * 1024 * 1024))
        analysis = analyze(self.settings)
        event = threading.Event()
        children = []
        popen = subprocess.Popen
        def spawn(args, **kwargs):
            child = popen(args, **kwargs)
            children.append(child)
            # Skip the capability probe; cancel the first real PSD input process.
            if any(arg.startswith("-siorigem-") and "item-" in arg for arg in args):
                event.set()
            return child
        before = sha256(self.file)
        with patch("meury_app.sevenzip.subprocess.Popen", side_effect=spawn):
            result = compress(analysis, event.is_set, store=self.store, engine=engine)
        self.assertEqual(result["state"], "cancelada")
        self.assertTrue(all(child.poll() is not None for child in children))
        self.assertEqual(sha256(self.file), before)
        self.assertFalse(Path(result["archive"]).exists())
        self.assertEqual(result["included"], 0)
        self.assertEqual(len(list(Path(result["log"]).parent.iterdir())), 1)
