import unittest
from unittest.mock import patch

from meury_app.dependency_setup import _module_available


class DependencySetupTest(unittest.TestCase):
    def test_nested_missing_package_is_reported_without_crashing(self):
        with patch("meury_app.dependency_setup.importlib.util.find_spec", side_effect=ModuleNotFoundError):
            self.assertFalse(_module_available("google.cloud.storage"))


if __name__ == "__main__":
    unittest.main()
