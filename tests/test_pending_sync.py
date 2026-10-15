import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from meury_app.cloud_preview import CloudUploadResult, preview_storage_key
from meury_app.pending_sync import synchronize_pending
from meury_app.preview_generator import PreviewGenerationResult
from meury_app.supabase_sync import SupabaseSyncResult


class PendingSyncTest(unittest.TestCase):
    def completed_record(self, record):
        completed = {
            **record,
            "preview_path": "preview.webp",
            "content_hash": "a" * 64,
            "preview_status": "completed", "preview_content_hash": "a" * 64,
            "cloud_status": "completed", "cloud_content_hash": "a" * 64,
            "supabase_status": "completed", "supabase_content_hash": "a" * 64,
        }
        completed["storage_key"] = preview_storage_key(completed, Path("preview.webp"))
        return completed

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

        completed_record = self.completed_record(record)
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
        self.assertEqual(result.remaining, 0)
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
        self.assertEqual(result.remaining, 1)

    def test_empty_pipeline_is_a_no_op(self):
        complete = self.completed_record({
            "active": True, "missing_locally": False,
            "preview_status": "completed", "cloud_status": "completed",
            "supabase_status": "completed",
        })
        preview = PreviewGenerationResult(0, 0, 0, 0.0)
        cloud = CloudUploadResult(0, 0, 0, 0.0)
        supabase = SupabaseSyncResult(0, 0, 0, 0.0)
        with patch("meury_app.pending_sync.load_catalog_records", side_effect=[[complete], [complete]]), \
             patch("meury_app.pending_sync.generate_pending_previews", return_value=preview), \
             patch("meury_app.pending_sync.upload_pending_previews", return_value=cloud), \
             patch("meury_app.pending_sync.sync_pending_records", return_value=supabase):
            result = synchronize_pending([Path("/tmp")])
        self.assertEqual((result.total_work, result.completed, result.pending), (0, 0, 0))
        self.assertEqual(result.remaining, 0)

    def test_remaining_counts_each_file_once_across_the_full_filtered_catalog(self):
        def ready(number):
            return self.completed_record({
                "asset_id": f"asset-{number}", "codigo": str(number),
                "filename": f"{number}-A.jpg",
            })

        initial = [ready(number) for number in range(8)]
        initial[0].update(
            preview_status="pending", cloud_status="pending", supabase_status="pending"
        )
        initial[1].update(
            preview_status="failed", cloud_status="pending", supabase_status="failed"
        )
        initial[2]["supabase_content_hash"] = "b" * 64
        initial[3].update(filename="6245-B..jpg", supabase_status="pending")
        initial[4].update(active=False, supabase_status="pending")
        initial[5].update(missing_locally=True, supabase_status="pending")
        initial[6]["cloud_content_hash"] = "b" * 64
        initial[7]["preview_status"] = "pending"
        current = [ready(0), *initial[1:]]
        with patch(
            "meury_app.pending_sync.load_catalog_records", side_effect=[initial, current]
        ), patch(
            "meury_app.pending_sync.generate_pending_previews",
            return_value=PreviewGenerationResult(1, 1, 0, 0),
        ) as generate, patch(
            "meury_app.pending_sync.upload_pending_previews",
            return_value=CloudUploadResult(1, 1, 0, 0),
        ) as upload, patch(
            "meury_app.pending_sync.sync_pending_records",
            return_value=SupabaseSyncResult(1, 1, 0, 0),
        ) as sync:
            result = synchronize_pending(
                [], limit=1, ignored_filename_terms="-, ., MOCKUP, MOCLKUP"
            )
        self.assertEqual((result.total_work, result.completed, result.remaining), (1, 1, 4))
        self.assertEqual(result.errors, 1)
        for operation in (generate, upload, sync):
            self.assertEqual(operation.call_args.kwargs["allowed_asset_ids"], {"asset-0"})

    def test_limit_selects_same_items_for_every_stage_and_passes_pause(self):
        records = [
            {
                "asset_id": f"asset-{number}", "active": True,
                "missing_locally": False, "preview_status": "pending",
                "cloud_status": "pending", "supabase_status": "pending",
            }
            for number in range(3)
        ]
        pause = Mock()
        preview = PreviewGenerationResult(1, 1, 0, 0.0)
        cloud = CloudUploadResult(1, 1, 0, 0.0)
        supabase = SupabaseSyncResult(1, 1, 0, 0.0)
        with patch(
            "meury_app.pending_sync.load_catalog_records",
            side_effect=[records, records],
        ), patch(
            "meury_app.pending_sync.generate_pending_previews", return_value=preview,
        ) as generate, patch(
            "meury_app.pending_sync.upload_pending_previews", return_value=cloud,
        ) as upload, patch(
            "meury_app.pending_sync.sync_pending_records", return_value=supabase,
        ) as sync:
            result = synchronize_pending([Path("/tmp")], limit=1, pause_event=pause)

        self.assertEqual(result.total_work, 1)
        expected = {"asset-0"}
        for call in (generate, upload, sync):
            self.assertEqual(call.call_args.kwargs["allowed_asset_ids"], expected)
            self.assertIs(call.call_args.kwargs["pause_event"], pause)


if __name__ == "__main__":
    unittest.main()
