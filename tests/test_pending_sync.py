import unittest
from pathlib import Path
from unittest.mock import patch

from meury_app.cloud_preview import CloudUploadResult
from meury_app.pending_sync import synchronize_pending
from meury_app.preview_generator import PreviewGenerationResult
from meury_app.supabase_sync import SupabaseSyncResult


class PendingSyncTest(unittest.TestCase):
    def test_pipeline_runs_in_order_and_reports_aggregate(self):
        record = {
            "active": True, "missing_locally": False,
            "preview_status": "pending", "cloud_status": "pending",
            "supabase_status": "pending",
        }
        order = []

        def preview(*args, **kwargs):
            order.append("preview")
            kwargs["progress_callback"](1, 1, "preview")
            return PreviewGenerationResult(1, 1, 0, 0.1)

        def cloud(*args, **kwargs):
            order.append("cloud")
            kwargs["progress_callback"](1, 1, "cloud")
            return CloudUploadResult(1, 1, 0, 0.1)

        def supabase(*args, **kwargs):
            order.append("supabase")
            kwargs["progress_callback"](1, 1, "supabase")
            return SupabaseSyncResult(1, 1, 0, 0.1)

        completed_record = {
            **record, "preview_status": "completed", "cloud_status": "completed",
            "supabase_status": "completed",
        }
        progress = []
        with patch("meury_app.pending_sync.load_catalog_records", side_effect=[[record], [completed_record]]), \
             patch("meury_app.pending_sync.generate_pending_previews", side_effect=preview), \
             patch("meury_app.pending_sync.upload_pending_previews", side_effect=cloud), \
             patch("meury_app.pending_sync.sync_pending_records", side_effect=supabase):
            result = synchronize_pending(
                [Path("/tmp")], progress_callback=lambda *values: progress.append(values)
            )
        self.assertEqual(order, ["preview", "cloud", "supabase"])
        self.assertEqual((result.total_work, result.completed, result.pending, result.errors), (1, 1, 0, 0))
        self.assertEqual([item[0] for item in progress], [1, 2, 3])
        self.assertTrue(all(item[1] == 3 for item in progress))

    def test_errors_count_records_instead_of_failed_stages(self):
        failed = {
            "active": True, "missing_locally": False,
            "preview_status": "failed", "cloud_status": "failed",
            "supabase_status": "failed",
        }
        preview = PreviewGenerationResult(1, 0, 1, 0.0)
        cloud = CloudUploadResult(0, 0, 0, 0.0)
        supabase = SupabaseSyncResult(0, 0, 0, 0.0)
        with patch("meury_app.pending_sync.load_catalog_records", side_effect=[[failed], [failed]]), \
             patch("meury_app.pending_sync.generate_pending_previews", return_value=preview), \
             patch("meury_app.pending_sync.upload_pending_previews", return_value=cloud), \
             patch("meury_app.pending_sync.sync_pending_records", return_value=supabase):
            result = synchronize_pending([Path("/tmp")])
        self.assertEqual((result.completed, result.pending, result.errors), (0, 0, 1))

    def test_empty_pipeline_is_a_no_op(self):
        complete = {
            "active": True, "missing_locally": False,
            "preview_status": "completed", "cloud_status": "completed",
            "supabase_status": "completed",
        }
        preview = PreviewGenerationResult(0, 0, 0, 0.0)
        cloud = CloudUploadResult(0, 0, 0, 0.0)
        supabase = SupabaseSyncResult(0, 0, 0, 0.0)
        with patch("meury_app.pending_sync.load_catalog_records", side_effect=[[complete], [complete]]), \
             patch("meury_app.pending_sync.generate_pending_previews", return_value=preview), \
             patch("meury_app.pending_sync.upload_pending_previews", return_value=cloud), \
             patch("meury_app.pending_sync.sync_pending_records", return_value=supabase):
            result = synchronize_pending([Path("/tmp")])
        self.assertEqual((result.total_work, result.completed, result.pending), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
