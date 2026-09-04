import unittest

from meury_app.asset_identity import relative_asset_identity, storage_asset_segment


class AssetIdentityTest(unittest.TestCase):
    def test_persisted_identity_survives_path_change(self):
        record = {
            "asset_id": "colecao/6844/6844-a",
            "filename": "renomeada.jpg",
            "relative_path": "outra-pasta/renomeada.jpg",
        }
        self.assertEqual(relative_asset_identity(record), "colecao/6844/6844-a")

    def test_relative_directory_is_part_of_identity(self):
        common = {"filename": "pa444-a-0.jpg"}
        first = {**common, "relative_path": "pa444/pa444-a-0.jpg"}
        second = {**common, "relative_path": "bandeira/pa444/pa444-a-0.jpg"}
        self.assertNotEqual(relative_asset_identity(first), relative_asset_identity(second))
        self.assertNotEqual(storage_asset_segment(first), storage_asset_segment(second))

    def test_invalid_dot_name_does_not_abort_identity_creation(self):
        record = {"filename": ".", "relative_path": "."}
        self.assertEqual(relative_asset_identity(record), ".")


if __name__ == "__main__":
    unittest.main()
