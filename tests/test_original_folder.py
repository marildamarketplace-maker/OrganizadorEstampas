import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meury_app.original_folder import (
    OriginalFolderError, open_original_directory, resolve_original_directory,
)


class OriginalFolderTest(unittest.TestCase):
    def test_resolves_directory_relative_to_configured_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            expected = root / "6844" / "A"
            expected.mkdir(parents=True)
            with patch.dict("os.environ", {"ORIGINAL_IMAGES_PATH": str(root)}):
                resolved = resolve_original_directory({"original_relative_path": "6844/A"})
            self.assertEqual(resolved, expected.resolve())

    def test_accepts_legacy_relative_file_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            expected = root / "6844" / "A"
            expected.mkdir(parents=True)
            record = {
                "relative_path": "6844\\A\\6844-A.tif", "filename": "6844-A.tif",
            }
            with patch.dict("os.environ", {"ORIGINAL_IMAGES_PATH": str(root)}):
                self.assertEqual(resolve_original_directory(record), expected.resolve())

    def test_rejects_path_outside_configured_root(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict("os.environ", {"ORIGINAL_IMAGES_PATH": folder}):
                with self.assertRaises(OriginalFolderError):
                    resolve_original_directory({"original_relative_path": "../segredo"})

    def test_reports_disconnected_root(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "volume-ausente"
            with patch.dict("os.environ", {"ORIGINAL_IMAGES_PATH": str(missing)}):
                with self.assertRaisesRegex(OriginalFolderError, "HD.*volume"):
                    resolve_original_directory({"original_relative_path": "6844/A"})

    def test_opens_resolved_folder_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "6844" / "A"
            target.mkdir(parents=True)
            with patch.dict("os.environ", {"ORIGINAL_IMAGES_PATH": str(root)}), \
                 patch("meury_app.original_folder.open_with_default_application") as opener:
                result = open_original_directory({"original_relative_path": "6844/A"})
            opener.assert_called_once_with(target.resolve())
            self.assertEqual(result, target.resolve())


if __name__ == "__main__":
    unittest.main()
