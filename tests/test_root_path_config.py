import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meury_app.config import (
    join_indexed_image_path, original_images_path, resolve_record_path,
    resolve_relative_image_path,
    validate_original_images_path,
)


class RootPathConfigTest(unittest.TestCase):
    def test_interface_configuration_overrides_environment_default(self):
        with tempfile.TemporaryDirectory() as folder:
            selected = Path(folder) / "selecionada"
            with patch.dict(
                "os.environ", {"ORIGINAL_IMAGES_PATH": "/volume/antigo"}
            ):
                result = original_images_path({"original_images_path": str(selected)})
            self.assertEqual(result, selected)

    def test_resolves_portable_relative_path_from_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            expected = root / "6844" / "A" / "6844-A.tif"
            expected.parent.mkdir(parents=True)
            expected.touch()
            with patch.dict("os.environ", {"ORIGINAL_IMAGES_PATH": str(root)}):
                result = resolve_record_path({
                    "relative_path": "6844\\A\\6844-A.tif", "source": 0,
                })
            self.assertEqual(result, expected.resolve())

    def test_rejects_absolute_and_parent_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for invalid in ("../fora.tif", "/fora.tif"):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    resolve_relative_image_path(invalid, root=root)

    def test_joins_indexed_path_without_resolving_filesystem(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(Path, "resolve") as resolve:
                result = join_indexed_image_path(
                    "6844\\A\\6844-A.tif", root=root,
                )

            resolve.assert_not_called()
            self.assertEqual(result, root / "6844" / "A" / "6844-A.tif")

    def test_indexed_path_rejects_cross_platform_absolute_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            invalid_paths = (
                "../fora.tif",
                "/fora.tif",
                "C:\\fora\\arquivo.tif",
                "\\\\servidor\\pasta\\arquivo.tif",
            )
            for invalid in invalid_paths:
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    join_indexed_image_path(invalid, root=root)

    def test_startup_validation_reports_unavailable_volume(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "volume"
            with patch.dict("os.environ", {"ORIGINAL_IMAGES_PATH": str(missing)}):
                with self.assertRaisesRegex(ValueError, "HD.*volume"):
                    validate_original_images_path()


if __name__ == "__main__":
    unittest.main()
