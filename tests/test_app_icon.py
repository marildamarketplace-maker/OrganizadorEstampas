from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from meury_app.app_icon import apply_app_icon


class AppIconTests(unittest.TestCase):
    def test_icon_retained_and_shared_with_new_windows(self):
        root = Mock()
        with patch('meury_app.app_icon._apply_macos_dock_icon'), patch('meury_app.app_icon.tk.PhotoImage') as photo, patch.object(Path, 'is_file', return_value=True):
            apply_app_icon(root)
        self.assertIs(root.meury_icon, photo.return_value)
        root.iconphoto.assert_called_once_with(True, photo.return_value)
        self.assertIs(photo.call_args.kwargs['master'], root)

    def test_missing_asset_does_not_prevent_startup(self):
        root = Mock()
        with patch.object(Path, 'is_file', return_value=False):
            apply_app_icon(root)
        root.iconphoto.assert_not_called()

    def test_macos_sets_process_dock_icon(self):
        root = Mock()
        with patch('meury_app.app_icon.sys.platform', 'darwin'), \
             patch('meury_app.app_icon.tk.PhotoImage'), \
             patch.object(Path, 'is_file', return_value=True), \
             patch('meury_app.app_icon._apply_macos_dock_icon') as dock:
            apply_app_icon(root)
        dock.assert_called_once()
        self.assertEqual(dock.call_args.args[0].name, 'meury-logo.png')
