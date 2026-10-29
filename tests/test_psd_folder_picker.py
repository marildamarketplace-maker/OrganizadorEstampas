"""Multiple folder selection preserves the approved source list on cancel."""
from pathlib import Path
from unittest.mock import Mock, patch
import os
import tempfile
import unittest

from meury_app.psd_compression_tab import CompressionTab


class SourceSelectionTest(unittest.TestCase):
    def test_adds_multiple_folders_without_duplicates_and_invalidates_once(self):
        tab = CompressionTab.__new__(CompressionTab)
        tab.root = Mock()
        tab.source_list = Mock()
        tab.invalidate = Mock()
        first = str(Path.cwd() / "primeira")
        second = str(Path.cwd() / "segunda")
        tab.sources = [first]
        with patch("meury_app.psd_compression_tab.choose_directories", return_value=[first, second, second]):
            tab.add_source()
        self.assertEqual(tab.sources, [first, second])
        tab.source_list.insert.assert_called_once_with("end", second)
        tab.invalidate.assert_called_once_with()

    def test_cancel_preserves_analysis_and_sources(self):
        tab = CompressionTab.__new__(CompressionTab)
        tab.root = Mock()
        tab.sources = []
        tab.source_list = Mock()
        tab.invalidate = Mock()
        with patch("meury_app.psd_compression_tab.choose_directories", return_value=[]):
            tab.add_source()
        self.assertEqual(tab.sources, [])
        tab.invalidate.assert_not_called()
        tab.source_list.insert.assert_not_called()


@unittest.skipUnless(os.environ.get("MEURY_TEST_TK") == "1", "Requer Tk nativo")
class FolderPickerTest(unittest.TestCase):
    def test_multi_selection_navigation_and_cancel(self):
        import tkinter as tk
        from meury_app.folder_picker import FolderPicker
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            first, second = base / "Árvore", base / "Outra"
            first.mkdir()
            second.mkdir()
            (base / "arquivo.psd").write_bytes(b"PSD")
            root = tk.Tk()
            root.withdraw()
            try:
                picker = FolderPicker(root, base)
                self.assertEqual(set(picker.paths), {first, second})
                picker.folders.selection_set(0, "end")
                picker.accept_selected()
                self.assertEqual(set(picker.result), {str(first), str(second)})
                picker = FolderPicker(root, base)
                picker.navigate(first)
                self.assertEqual(picker.current, first)
                picker.navigate(base)
                self.assertEqual(set(picker.paths), {first, second})
                picker.destroy()
                self.assertEqual(picker.result, [])
            finally:
                root.destroy()
