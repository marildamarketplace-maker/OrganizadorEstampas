from pathlib import Path
import unittest
from unittest.mock import Mock, patch

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


if __name__ == "__main__":
    unittest.main()
