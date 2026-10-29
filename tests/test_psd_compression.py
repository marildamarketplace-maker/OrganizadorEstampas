from dataclasses import replace
from datetime import datetime
from pathlib import Path
import json
import os
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from meury_app.compression_store import CompressionStore
from meury_app.psd_search import (Cancelled, SearchSettings, analyze, parse_names,
                                  portable_key, validate_internal)
from meury_app.psd_compression import compress, sanitized, size_summary
from meury_app.sevenzip import SevenZip, locate
from meury_app.platform_utils import open_existing_location


class FakeEngine:
    def probe(self, cancel):
        if cancel():
            raise Cancelled()

    def add(self, archive, item, stream, cancel):
        with archive.open("ab") as target:
            target.write(stream.read(1024))

    def validate(self, archive, items, cancel):
        return "integridade simulada OK"


class PsdTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "entrada"
        self.source.mkdir()
        self.output = self.root / "saida"
        self.store = CompressionStore(self.root / "dados" / "state.sqlite3")
        self.settings = SearchSettings((str(self.source),), str(self.output))

    def file(self, name="arte.psd", content=b"PSD bytes"):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_names_exact_accents_codes_and_separators(self):
        self.assertEqual(parse_names(" 001.PSD;001\nÁrvore,árvore.psd,\n"), ("001", "Árvore"))
        for name in ("001.PSD", "001-extra.psd", "árvore.psd", "arvore.psd", "foo.jpg"):
            self.file(name)
        result = analyze(replace(self.settings, names="001;ÁRVORE.psd;ausente"))
        self.assertEqual({Path(item.path).name for item in result.files}, {"001.PSD", "árvore.psd"})
        self.assertEqual(result.missing, ["ausente"])

    def test_dates_include_final_day(self):
        path = self.file()
        timestamp = datetime(2026, 10, 27, 23, 59, 59).timestamp()
        os.utime(path, (timestamp, timestamp))
        self.assertEqual(len(analyze(replace(self.settings, start="2026-10-27", end="2026-10-27")).files), 1)
        self.assertFalse(analyze(replace(self.settings, end="2026-10-26")).files)
        for first, last in (("2026-10-28", "2026-10-27"), ("invalid", "")):
            with self.assertRaises(ValueError):
                analyze(replace(self.settings, start=first, end=last))

    def test_overlap_output_and_no_analysis_writes(self):
        path = self.file("sub/a.PsD")
        self.output = self.source / "saida"
        self.output.mkdir()
        (self.output / "excluded.psd").write_bytes(b"x")
        result = analyze(replace(self.settings, sources=(str(self.source), str(path.parent), str(self.source)), output=str(self.output)))
        self.assertEqual([item.path for item in result.files], [str(path)])
        self.assertEqual(list(self.output.iterdir()), [self.output / "excluded.psd"])
        with self.assertRaises(ValueError):
            analyze(replace(self.settings, output=str(self.source)))

    def test_symlinks_ignored_and_missing_root_incomplete(self):
        self.file()
        try:
            (self.source / "loop").symlink_to(self.source, target_is_directory=True)
            (self.source / "link.psd").symlink_to(self.source / "arte.psd")
        except OSError:
            self.skipTest("Symlink permission unavailable")
        result = analyze(self.settings)
        self.assertEqual(len(result.files), 1)
        self.assertEqual(len(result.issues), 2)
        result = analyze(replace(self.settings, sources=(str(self.root / "gone"),)))
        self.assertTrue(result.incomplete)

    def test_access_error_reported_as_incomplete(self):
        with patch("meury_app.psd_search.os.scandir", side_effect=PermissionError("acesso negado")):
            result = analyze(self.settings)
        self.assertTrue(result.incomplete)
        self.assertIn("acesso negado", result.issues[0])

    def test_homonyms_and_portable_internal_names(self):
        self.file("one/a.psd", b"a")
        self.file("two/A.PSD", b"different")
        result = analyze(self.settings)
        self.assertEqual(len(result.files), 2)
        self.assertTrue(all("Homônimo" in item.situation for item in result.files))
        self.assertEqual(len({portable_key(item.internal) for item in result.files}), 2)

    def test_internal_security(self):
        for name in ("../a.psd", "/a.psd", "C:/a.psd", "a/../b", "a\\b", "a\nx", "a/CON.psd", "a/x?.psd", "a/trailing. /x"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_internal(name)
        self.assertEqual(validate_internal("origem-01/-@á.psd"), "origem-01/-@á.psd")

    def test_cancel_search(self):
        with self.assertRaises(Cancelled):
            analyze(self.settings, lambda: True)

    def run_fake(self, engine=None, cancel=lambda: False):
        return compress(analyze(self.settings), cancel, store=self.store, engine=engine or FakeEngine())

    def test_success_selection_log_and_originals(self):
        path = self.file()
        self.file("ignore.psd")
        analysis = analyze(self.settings)
        analysis.files[1].selected = False
        result = compress(analysis, store=self.store, engine=FakeEngine())
        self.assertEqual(result["state"], "concluída")
        self.assertEqual(result["included"], 1)
        self.assertEqual(path.read_bytes(), b"PSD bytes")
        folder = Path(result["archive"]).parent
        self.assertEqual({p.suffix for p in folder.iterdir()}, {".txt", ".7z"})
        log = Path(result["log"]).read_text(encoding="utf-8")
        self.assertIn(json.dumps(str(path), ensure_ascii=False)[1:-1], log)
        self.assertEqual(self.store.get(result["id"])["state"], "concluída")

    def test_warning_and_new_unique_result(self):
        self.file()
        analysis = analyze(replace(self.settings, names="arte;missing"))
        first = compress(analysis, store=self.store, engine=FakeEngine())
        second = compress(analysis, store=self.store, engine=FakeEngine())
        self.assertEqual(first["state"], "concluída com avisos")
        self.assertNotEqual(first["archive"], second["archive"])

    def test_failure_no_public_archive_and_log_retained(self):
        self.file()
        engine = FakeEngine()
        engine.validate = Mock(side_effect=RuntimeError("corrompido"))
        result = self.run_fake(engine)
        self.assertEqual(result["state"], "falhou")
        self.assertFalse(Path(result["archive"]).exists())
        self.assertTrue(Path(result["log"]).exists())
        self.assertEqual(len(list(Path(result["log"]).parent.iterdir())), 1)

    def test_mutation_before_and_during_compression(self):
        path = self.file()
        analysis = analyze(self.settings)
        path.write_bytes(b"changed")
        result = compress(analysis, store=self.store, engine=FakeEngine())
        self.assertEqual(result["state"], "falhou")
        engine = FakeEngine()
        original_add = engine.add
        def mutate(*args):
            original_add(*args)
            path.write_bytes(b"changed again")
        engine.add = mutate
        result = self.run_fake(engine)
        self.assertEqual(result["state"], "falhou")
        self.assertFalse(Path(result["archive"]).exists())

    def test_simulated_disk_full_network_loss_and_cancel(self):
        self.file()
        for error in (OSError(28, "No space left"), OSError("network gone"), Cancelled("cancelada")):
            engine = FakeEngine()
            engine.add = Mock(side_effect=error)
            result = self.run_fake(engine)
            self.assertEqual(result["state"], "cancelada" if isinstance(error, Cancelled) else "falhou")
            self.assertTrue(Path(result["log"]).exists())
            self.assertFalse(Path(result["archive"]).exists())

    def test_cleanup_failure_is_reported(self):
        self.file()
        engine = FakeEngine()
        engine.validate = Mock(side_effect=RuntimeError("bad"))
        original = Path.unlink
        def fail_cleanup(path, *args, **kwargs):
            if path.name.startswith("incompleto-") and path.stat().st_size:
                raise PermissionError("locked")
            return original(path, *args, **kwargs)
        with patch.object(Path, "unlink", fail_cleanup):
            result = self.run_fake(engine)
        self.assertTrue(any("Temporário não removido" in error for error in result["errors"]))

    def test_log_disk_failure_still_finishes_local_history(self):
        self.file()
        original_open = Path.open
        class FullLog:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def write(self, value):
                raise OSError(28, "simulated output disk full")
        def open_file(path, *args, **kwargs):
            if path.suffix == ".txt":
                return FullLog()
            return original_open(path, *args, **kwargs)
        with patch.object(Path, "open", open_file):
            result = self.run_fake()
        self.assertEqual(result["state"], "falhou")
        self.assertEqual(self.store.get(result["id"])["state"], "falhou")

    def test_history_recovery_and_size_math(self):
        self.store.save("test", "em andamento", {"value": "á"})
        self.store.recover()
        self.assertEqual(self.store.get("test")["state"], "interrompida")
        self.assertEqual(self.store.list()[0]["id"], "test")
        self.assertIsNone(size_summary(0, 32)["difference_percent"])
        self.assertEqual(size_summary(10, 20)["difference_bytes"], -10)

    def test_concurrent_run_is_blocked_without_overwriting(self):
        from meury_app.index_journal import catalog_lock
        from meury_app.psd_compression import lock_path
        self.file()
        with catalog_lock(lock_path(self.store)):
            with self.assertRaises(RuntimeError):
                self.run_fake()
        self.assertFalse(self.output.exists())

    def test_history_recovery_marks_log_and_preserves_finished(self):
        log = self.root / "attempt.txt"
        log.write_text("Início\n", encoding="utf-8")
        self.store.save("old", "em andamento", {"log": str(log)})
        self.store.save("done", "concluída", {})
        self.store.recover()
        self.assertIn("interrompida", log.read_text())
        self.assertEqual(self.store.get("done")["state"], "concluída")

    def test_unavailable_engine_is_a_failed_logged_attempt(self):
        self.file()
        with patch("meury_app.psd_compression.SevenZip", side_effect=RuntimeError("7z indisponível")):
            result = compress(analyze(self.settings), store=self.store)
        self.assertEqual(result["state"], "falhou")
        self.assertIn("7z indisponível", Path(result["log"]).read_text(encoding="utf-8"))

    def test_output_permission_and_redirect_reparse(self):
        with patch("meury_app.psd_search.os.access", return_value=False):
            with self.assertRaises(PermissionError):
                analyze(self.settings)
        from meury_app.psd_search import redirected
        path = Mock()
        path.lstat.return_value = type("Stat", (), {"st_mode": 0o040755, "st_file_attributes": 0x400})()
        self.assertTrue(redirected(path))

    def test_ui_selection_and_invalidation_contract(self):
        from meury_app.psd_compression_tab import CompressionTab
        self.file("one/a.psd")
        self.file("two/a.psd")
        tab = CompressionTab.__new__(CompressionTab)
        tab.analysis = analyze(self.settings)
        tab.external_busy = False
        tab.table = Mock()
        tab.table.selection.return_value = ("0",)
        tab.render = Mock()
        tab.select_group(False)
        self.assertTrue(all(not item.selected for item in tab.analysis.files))
        tab.toggle()
        self.assertTrue(tab.analysis.files[0].selected)
        self.assertFalse(tab.analysis.files[1].selected)
        tab.names = Mock()
        tab.names.get.return_value = ""
        tab.scope = Mock()
        tab.status = Mock()
        tab.compress_button = Mock()
        tab.invalidate()
        self.assertIsNone(tab.analysis)
        tab.compress_button.configure.assert_called_with(state="disabled")
        self.assertIn("Busca ampla", tab.scope.set.call_args.args[0])

    def test_redaction_and_missing_location(self):
        self.assertNotIn("hunter", sanitized("smb://user:hunter@server/x?token=hunter"))
        with self.assertRaises(FileNotFoundError):
            open_existing_location(self.root / "missing")

    def test_subprocess_cancel_terminates_and_waits(self):
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired("7z", .15), 0]
        process.poll.return_value = None
        event = threading.Event()
        def cancelled():
            if process.wait.call_count:
                event.set()
            return event.is_set()
        with patch("meury_app.sevenzip.subprocess.Popen", return_value=process) as popen:
            with self.assertRaises(Cancelled):
                SevenZip("/trusted/7z").run(["t", "archive"], cancelled)
        process.terminate.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)
        self.assertIsInstance(popen.call_args.args[0], list)
        self.assertNotIn("shell", popen.call_args.kwargs)

    def test_real_7zip_roundtrip(self):
        try:
            executable = locate()
        except RuntimeError:
            self.skipTest("7-Zip indisponível: integração real não executada")
        path = self.file("pasta/-@Árvore.PSD", b"8BPS\0\1" + bytes(range(256)) * 20)
        self.file("outra/-@árvore.psd", b"second PSD")
        before = path.read_bytes()
        result = compress(analyze(self.settings), store=self.store, engine=SevenZip(executable))
        self.assertEqual(result["state"], "concluída", result["errors"])
        extracted = self.root / "extraido"
        subprocess.run([executable, "x", result["archive"], f"-o{extracted}", "-y"], check=True, stdout=subprocess.DEVNULL)
        item = next(item for item in result["analysis"]["files"] if item["path"] == str(path))
        self.assertEqual((extracted / item["internal"]).read_bytes(), before)
        self.assertEqual(path.read_bytes(), before)

    def test_real_zero_byte_archive_and_corruption(self):
        try:
            executable = locate()
        except RuntimeError:
            self.skipTest("7-Zip indisponível")
        self.file(content=b"")
        analysis = analyze(self.settings)
        engine = SevenZip(executable)
        result = compress(analysis, store=self.store, engine=engine)
        self.assertEqual(result["state"], "concluída", result["errors"])
        self.assertIsNone(result["difference_percent"])
        archive = Path(result["archive"])
        archive.write_bytes(archive.read_bytes()[:12])
        with self.assertRaises(RuntimeError):
            engine.validate(archive, analysis.files, lambda: False)
