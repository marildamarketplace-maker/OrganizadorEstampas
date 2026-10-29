"""Opt-in native Tk automation; use a real desktop, isolated data and a real 7z."""
from pathlib import Path
from unittest.mock import patch
import os
import tempfile
import time
import unittest


@unittest.skipUnless(os.environ.get("MEURY_TEST_TK") == "1", "Tk nativo requer MEURY_TEST_TK=1 e sessão gráfica")
class NativeTkTest(unittest.TestCase):
    def setUp(self):
        from meury_app.ui import App
        from meury_app.config import DEFAULT_CONFIG
        from meury_app.sevenzip import locate
        locate()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root_path = Path(self.temporary.name).resolve()
        from meury_app.compression_store import CompressionStore
        isolated_store = CompressionStore(self.root_path / "dados" / "state.sqlite3")
        self.patches = [patch("meury_app.ui.load_config", return_value=dict(DEFAULT_CONFIG))]
        self.patches += [patch("meury_app.psd_compression_tab.CompressionStore", return_value=isolated_store),
                         patch("meury_app.psd_compression_tab.load_config", return_value=dict(DEFAULT_CONFIG)),
                         patch("meury_app.psd_compression_tab.save_config")]
        self.patches += [patch.object(App, name) for name in ("_try_load_saved_index", "_refresh_semantic_status", "_refresh_visual_status", "_refresh_catalog_statistics")]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.app = App(usuario_atual={'id': 1, 'usuario': 'admin', 'permissoes': ['admin']})
        self.app.root.withdraw()
        self.tab = self.app.compression_tab
        from meury_app.compression_store import CompressionStore
        self.settle()
        self.tab.store = CompressionStore(self.root_path / "dados" / "state.sqlite3")
        self.addCleanup(self.close)

    def close(self):
        try:
            self.tab.cancel.set()
            self.settle()
            for callback in self.app.root.tk.call("after", "info"):
                self.app.root.after_cancel(callback)
            self.app.root.destroy()
        except Exception:
            pass

    def settle(self):
        deadline = time.monotonic() + 10
        while True:
            self.app.root.update()
            active = self.tab.external_busy or not self.tab.events.empty() or any(
                worker is not None and worker.is_alive() for worker in [self.tab.worker, *self.tab.aux_workers]
            )
            if not active:
                break
            if time.monotonic() >= deadline:
                self.fail("Operação Tk não terminou no prazo")
            time.sleep(.005)

    def prepare(self):
        source = self.root_path / "origem Unicode Ω"
        source.mkdir()
        (source / "árvore.PSD").write_bytes(b"8BPS" * 2048)
        self.tab.sources = [str(source)]
        self.tab.output.set(str(self.root_path / "saida"))
        self.app.root.update()

    def test_integrated_analysis_invalidation_compression_and_history(self):
        self.prepare()
        self.tab.start_analysis()
        self.settle()
        self.assertEqual(len(self.tab.analysis.files), 1)
        self.assertEqual(len(self.tab.table.get_children()), 1)
        self.tab.names.insert("end", "ausente")
        self.app.root.update()
        self.assertIsNone(self.tab.analysis)
        self.assertIn("disabled", self.tab.compress_button.state())
        self.tab.names.delete("1.0", "end")
        self.app.root.update()
        self.tab.start_analysis()
        self.settle()
        with patch("meury_app.psd_compression_tab.messagebox.askyesno", return_value=True):
            self.tab.start_compression()
        self.settle()
        self.assertIn("concluída", self.tab.status.get())
        self.assertEqual(len(self.tab.history.get_children()), 1)
        self.assertEqual(len(list((self.root_path / "saida").rglob("*.7z"))), 1)
        self.assertEqual(len(list((self.root_path / "saida").rglob("*.txt"))), 2)
        identity = self.tab.history.get_children()[0]
        self.tab.history.selection_set(identity)
        self.tab.history_details()
        self.settle()
        from tkinter import ttk
        notebooks = [widget for dialog in self.app.root.winfo_children()
                     for widget in dialog.winfo_children() if isinstance(widget, ttk.Notebook)]
        self.assertTrue(any([notebook.tab(tab, "text") for tab in notebook.tabs()] ==
                            ["PSDs efetivamente incluídos", "Análise e decisões"] for notebook in notebooks))
        self.tab.history_query.set("ÁRVORE")
        self.tab.refresh_history()
        self.settle()
        self.assertEqual(len(self.tab.history.get_children()), 1)
        self.tab.history_query.set("ausente")
        self.tab.refresh_history()
        self.settle()
        self.assertEqual(len(self.tab.history.get_children()), 0)
        self.tab.history_query.set("")
        self.tab.refresh_history()
        self.settle()
        self.tab.history.selection_set(identity)
        with patch("meury_app.psd_compression_tab.open_with_default_application") as opener:
            self.tab.open_report()
            self.settle()
            opener.assert_called_once()

    def test_slow_history_does_not_block_event_loop_or_enable_controls(self):
        original = self.tab.store.list
        def slow_read(*args, **kwargs):
            time.sleep(.25)
            return original(*args, **kwargs)
        ticks = []
        self.app.root.after(30, lambda: ticks.append(time.monotonic()))
        with patch.object(self.tab.store, "list", side_effect=slow_read):
            self.app._set_busy(True)
            self.tab.refresh_history()
            deadline = time.monotonic() + .12
            while time.monotonic() < deadline:
                self.app.root.update()
                time.sleep(.005)
            self.assertTrue(ticks, "O histórico bloqueou o loop gráfico")
            self.assertTrue(self.tab.external_busy)
            self.app._set_busy(False)
            self.settle()
