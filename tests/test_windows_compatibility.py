from pathlib import Path
import re
import unittest
from build_app import build_command


ROOT = Path(__file__).parents[1]


class WindowsCompatibilityTest(unittest.TestCase):
    def test_windows_launchers_use_project_relative_paths(self):
        for filename in ("executar_windows.bat", "build_windows.bat"):
            text = (ROOT / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                self.assertIn('cd /d "%~dp0"', text)
                self.assertRegex(text, re.escape(r".venv\Scripts"))
                self.assertIsNone(re.search(r"(?im)^\s*[A-Z]:\\", text))

    def test_windows_build_packages_gcs_dynamic_imports(self):
        text = ' '.join(build_command('python', 'win32'))
        self.assertIn("--hidden-import google.cloud.storage", text)
        self.assertIn("--hidden-import google.oauth2.service_account", text)
        self.assertIn("--hidden-import faiss", text)
        self.assertIn("--collect-all pypdfium2", text)
        self.assertIn("--collect-all pypdfium2_raw", text)
        self.assertIn("--exclude-module torch", text)
        self.assertIn("--exclude-module transformers", text)
        self.assertIn("build_app.py", (ROOT / "build_windows.bat").read_text())
        self.assertNotIn("pip install --upgrade pip", text)

    def test_build_selects_native_icon_and_data_separator(self):
        for platform, extension, separator in [('win32', 'ico', ';'), ('darwin', 'icns', ':')]:
            with self.subTest(platform=platform):
                command = build_command('python', platform)
                self.assertEqual(command[command.index('--icon') + 1], f'assets/meury-logo.{extension}')
                self.assertIn(f'assets{separator}assets', command)
                self.assertIn(f'meury_app/batch_order_extraction.schema.json{separator}meury_app', command)
        with self.assertRaises(ValueError):
            build_command('python', 'linux')

    def test_source_never_hardcodes_local_windows_drive(self):
        source = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in (ROOT / "meury_app").glob("*.py")
        )
        self.assertIsNone(re.search(r"(?<![A-Za-z0-9_])[A-Z]:\\\\", source))


if __name__ == "__main__":
    unittest.main()
