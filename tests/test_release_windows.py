"""Windows publication must not depend on any macOS release artifact."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from meury_app import __version__
from meury_app.update_ui import UpdateController

ROOT = Path(__file__).resolve().parents[1]


class WindowsReleaseTests(unittest.TestCase):
    def prepare(self, root):
        notes = root / 'notes.md'
        notes.write_text('Atualização Windows', encoding='utf-8')
        output = root / 'manifest.json'
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_release.py'),
                                 str(root), 'example/releases', str(notes), str(output)],
                                capture_output=True, text=True)
        return result, output

    def test_windows_package_is_sufficient(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / f'MV-Image-{__version__}-win32-x86_64.zip').write_bytes(b'windows')
            result, output = self.prepare(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(set(json.loads(output.read_text())['packages']), {'win32-x86_64'})

    def test_mac_package_cannot_replace_missing_windows_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / f'MV-Image-{__version__}-darwin-arm64.zip').write_bytes(b'mac')
            result, output = self.prepare(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    def test_mac_never_queries_or_downloads_updates(self):
        controller = UpdateController.__new__(UpdateController)
        controller.root = Mock()
        with patch('meury_app.update_ui.sys.platform', 'darwin'), \
             patch('meury_app.update_ui.check') as request, \
             patch('meury_app.update_ui.messagebox.showinfo') as notice:
            controller.check()
            notice.assert_not_called()
            controller.check(True)
            notice.assert_called_once()
            request.assert_not_called()
