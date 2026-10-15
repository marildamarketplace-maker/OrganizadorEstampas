import unittest

from meury_app.filename_filter import (
    DEFAULT_IGNORED_FILENAME_TERMS,
    is_ignored_filename,
    normalize_ignored_filename_terms,
)


class FilenameFilterTest(unittest.TestCase):
    def test_default_rules_match_expected_examples(self):
        cases = {
            "6245-A.jpg": False,
            "6245-.jpg": True,
            "6245.v2.jpg": True,
            "6245-MOCKUP.jpg": True,
            "6245-MOCLKUP.jpg": True,
            "6245-mockup.JPG": True,
        }
        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(
                    is_ignored_filename(
                        {"relative_path": f"pasta/{filename}"},
                        DEFAULT_IGNORED_FILENAME_TERMS,
                    ),
                    expected,
                )

    def test_normalization_accepts_comma_separated_text(self):
        self.assertEqual(
            normalize_ignored_filename_terms(" -, ., MOCKUP, mockup, MOCLKUP "),
            ("-", ".", "mockup", "moclkup"),
        )

    def test_empty_configuration_disables_filter(self):
        self.assertFalse(is_ignored_filename({"filename": "100-MOCKUP.jpg"}, ""))


if __name__ == "__main__":
    unittest.main()
