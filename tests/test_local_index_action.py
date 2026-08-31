from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import inspect

from meury_app.ui import App


class LocalIndexActionTest(unittest.TestCase):
    def app(self):
        app = App.__new__(App)
        app.source_dirs = [str(Path("catalogo"))]
        app.start_incremental_indexing = Mock()
        app.start_indexing = Mock()
        return app

    def test_uses_incremental_scan_when_catalog_exists(self):
        app = self.app()
        with patch("meury_app.ui.index_catalog_available", return_value=True):
            app.start_local_indexing()
        app.start_incremental_indexing.assert_called_once_with()
        app.start_indexing.assert_not_called()

    def test_creates_local_catalog_on_first_scan(self):
        app = self.app()
        with patch("meury_app.ui.index_catalog_available", return_value=False):
            app.start_local_indexing()
        app.start_indexing.assert_called_once_with()
        app.start_incremental_indexing.assert_not_called()

    def test_ui_does_not_offer_local_ai_keyword_batch(self):
        source = inspect.getsource(App)
        self.assertIn('text="Mapear estampas"', source)
        self.assertNotIn('text="Organizar pedidos"', source)
        self.assertNotIn('notebook.add(search_tab, text="Pesquisar Artes")', source)
        self.assertNotIn("Gerar Palavras-chave com IA", source)
        self.assertIn("legacy_order_fields = ttk.Frame(form)", source)
        self.assertNotIn("mode_frame = ttk.Frame(form)", source)
        self.assertNotIn('process_frame.pack(fill="both", expand=True)', source)
        self.assertNotIn("choose_analysis_batch", source)
        self.assertNotIn("run_analysis_batch", source)

    def test_pause_and_continue_control_the_current_batch(self):
        app = App.__new__(App)
        app.operation_pause_event = Mock()
        app.operation_pause_button = Mock()
        app.operation_continue_button = Mock()
        app.status_var = Mock()

        app.pause_current_operation()
        app.operation_pause_event.clear.assert_called_once_with()
        app.operation_continue_button.configure.assert_called_with(state="normal")

        app.continue_current_operation()
        app.operation_pause_event.set.assert_called_once_with()
        app.operation_pause_button.configure.assert_called_with(state="normal")


if __name__ == "__main__":
    unittest.main()
