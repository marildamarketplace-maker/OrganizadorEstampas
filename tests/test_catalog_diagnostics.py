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
        result = calculate_statistics(
            records, {"0:a.jpg"},
            {"added_files": 4, "changed_files": 2, "unchanged_files": 10},
        )
        self.assertEqual(result.total, 3)
        self.assertEqual(result.with_keywords, 1)
        self.assertEqual(result.without_keywords, 1)
        self.assertEqual(result.with_embedding, 1)
        self.assertEqual(result.pending, 1)
        self.assertEqual(result.errors, 1)
        self.assertEqual(result.missing, 1)
        self.assertEqual(result.new, 4)
        self.assertEqual(result.changed, 2)
        self.assertEqual(result.unchanged, 10)

    def test_counts_operational_processing_states(self):
        records = [
            {"filename": "a.png", "active": True, "preview_status": "pending",
             "cloud_status": "pending", "supabase_status": "pending"},
            {"filename": "b.tif", "active": True, "preview_status": "ready",
             "cloud_status": "uploaded", "supabase_status": "synced"},
            {"filename": "c.jpg", "active": True,
             "attention_status": "REQUIRES_ATTENTION"},
        ]
        result = calculate_statistics(records, set())
        self.assertEqual(result.pending_preview, 1)
        self.assertEqual(result.pending_cloud, 1)
        self.assertEqual(result.pending_supabase, 1)
        self.assertEqual(result.synced, 1)
        self.assertEqual(result.errors, 1)
