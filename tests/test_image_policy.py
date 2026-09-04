import unittest
import warnings
from unittest.mock import patch

from PIL import Image

from meury_app.image_policy import MAX_IMAGE_PIXELS, configure_pillow_limits


class ImagePolicyTest(unittest.TestCase):
    def test_accepts_expected_print_art_size_without_warning(self):
        configure_pillow_limits()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            Image._decompression_bomb_check((10_500, 10_000))
        self.assertEqual(caught, [])

    def test_keeps_protection_for_exceptionally_large_images(self):
        configure_pillow_limits()
        with self.assertRaises(Image.DecompressionBombError):
            Image._decompression_bomb_check((MAX_IMAGE_PIXELS * 2 + 1, 1))

    def test_missing_optional_pillow_is_allowed(self):
        with patch.dict("sys.modules", {"PIL": None}):
            configure_pillow_limits()


if __name__ == "__main__":
    unittest.main()
