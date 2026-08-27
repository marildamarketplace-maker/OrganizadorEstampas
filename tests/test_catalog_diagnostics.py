import unittest

from meury_app.catalog_diagnostics import calculate_statistics


class CatalogDiagnosticsTest(unittest.TestCase):
    def test_counts_only_active_raster_images(self):
        records = [
            {"source": 0, "relative_path": "a.jpg", "filename": "a.jpg",
             "active": True, "keywords": ["flor"], "processed": True},
            {"source": 0, "relative_path": "b.png", "filename": "b.png",
             "active": True, "keywords": [], "processed": False,
             "analysis_error": "corrompida"},
            {"source": 0, "relative_path": "c.jpg", "filename": "c.jpg",
             "active": False, "keywords": ["ignorar"], "processed": True},
            {"source": 0, "relative_path": "manual.pdf", "filename": "manual.pdf",
             "active": True, "processed": False},
        ]
        result = calculate_statistics(records, {"0:a.jpg"})
        self.assertEqual(result.total, 2)
        self.assertEqual(result.with_keywords, 1)
        self.assertEqual(result.without_keywords, 1)
        self.assertEqual(result.with_embedding, 1)
        self.assertEqual(result.pending, 1)
        self.assertEqual(result.errors, 1)
