import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from meury_app import indexer
from meury_app.retry_policy import is_retryable_external_error
from meury_app.supabase_sync import (
    SupabaseConfig, SupabaseHttpError, SupabaseRestClient, _payload,
    sync_pending_records,
)


class FakeClient:
    def __init__(self, error=None):
        self.rows = []
        self.calls = []
        self.error = error

    def upsert(self, rows):
        self.calls.append(rows)
        if self.error:
            raise self.error
        self.rows.extend(rows)


class SupabaseSyncTest(unittest.TestCase):
    def record(self, path):
        return {
            "source": 0, "relative_path": "6844/A/6844-A.tif",
            "path": str(path), "filename": "6844-A.tif", "design_id": "6844",
            "codigo": "6844", "variante": "A", "size": 10, "mtime_ns": 1,
            "content_hash": "abc", "active": True, "missing_locally": False,
            "cloud_status": "completed", "preview_url": "https://cdn/p.webp?v=abc",
            "storage_key": "estampas/6844/A/preview.webp",
            "supabase_status": "pending", "processing_status": "pending",
            "processed": False, "keywords": ["não enviar"],
        }

    def test_payload_contains_only_owned_fields(self):
        row = _payload(self.record("/tmp/6844-A.tif"))
        self.assertEqual(set(row), {
            "codigo", "variante", "arquivo_id", "preview_url", "storage_key",
            "original_relative_path", "original_filename", "original_extension",
            "content_hash", "processing_status",
        })
        self.assertEqual(row["processing_status"], "PENDING")
        self.assertEqual(row["arquivo_id"], "6844/a/6844-a")
        self.assertEqual(row["original_extension"], ".tif")
        self.assertEqual(row["original_relative_path"], "6844/A")

    def test_legacy_local_ai_state_never_skips_remote_processing(self):
        record = self.record("/tmp/6844-A.tif")
        record.update({"processed": True, "processing_status": "completed"})
        self.assertEqual(_payload(record)["processing_status"], "PENDING")

    def test_missing_variant_is_sent_with_explicit_fallback(self):
        record = self.record("/tmp/mockup-1.jpg")
        record.update({
            "codigo": "67-45", "variante": "", "filename": "mockup-1.jpg",
            "relative_path": "67-45/mockup-1.jpg",
        })
        self.assertEqual(_payload(record)["variante"], "SEM-VARIANTE")
        with patch("meury_app.supabase_sync.load_catalog_records", return_value=[record]), \
             patch("meury_app.supabase_sync.sync_records"):
            client = FakeClient()
            result = sync_pending_records([Path("/tmp")], client=client)
        self.assertEqual(result.completed, 1)
        self.assertEqual(client.rows[0]["variante"], "SEM-VARIANTE")

    def test_sync_is_idempotent_for_same_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "artes"
            source.mkdir()
            record = self.record(source / "6844-A.tif")
            catalog = root / "catalog.jsonl"
            database = root / "state.sqlite3"
            with patch.object(indexer, "INDEX_FILE", catalog), \
                 patch("meury_app.supabase_sync._operational_db_path", return_value=database), \
                 patch("meury_app.supabase_sync.load_catalog_records", return_value=[record]):
                client = FakeClient()
                first = sync_pending_records([source], client=client)
                self.assertEqual(first.completed, 1)
                self.assertEqual(record["supabase_status"], "completed")
                self.assertEqual(record["supabase_content_hash"], "abc")
                self.assertEqual(record["supabase_attempts"], 1)
                self.assertTrue(record["supabase_last_attempt_at"])
                second = sync_pending_records([source], client=client)
                self.assertEqual(second.pending, 0)
                self.assertEqual(len(client.rows), 1)

    def test_failure_is_quarantined_to_record(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "artes"
            source.mkdir()
            record = self.record(source / "6844-A.tif")
            database = root / "state.sqlite3"
            with patch("meury_app.supabase_sync._operational_db_path", return_value=database), \
                 patch("meury_app.supabase_sync.load_catalog_records", return_value=[record]):
                result = sync_pending_records(
                    [source], client=FakeClient(RuntimeError("indisponível"))
                )
            self.assertEqual(result.failed, 1)
            self.assertEqual(record["supabase_status"], "failed")
            self.assertIn("indisponível", record["last_error"])
            self.assertEqual(record["supabase_attempts"], 3)
            self.assertTrue(record["supabase_last_error"])

    def test_large_sync_uses_batches_of_one_hundred(self):
        records = []
        for number in range(205):
            record = self.record(f"/tmp/{number}.tif")
            record.update({
                "relative_path": f"{number}/{number}-A.tif",
                "filename": f"{number}-A.tif", "design_id": str(number),
                "codigo": str(number), "content_hash": f"hash-{number}",
            })
            records.append(record)
        client = FakeClient()
        with patch("meury_app.supabase_sync.load_catalog_records", return_value=records), \
             patch("meury_app.supabase_sync.sync_records") as persist:
            result = sync_pending_records([Path("/tmp")], client=client)
        self.assertEqual([len(call) for call in client.calls], [100, 100, 5])
        self.assertEqual(result.completed, 205)
        self.assertEqual(persist.call_count, 3)

    def test_failed_batch_isolated_and_failed_record_can_retry(self):
        records = [self.record(f"/tmp/{code}.tif") for code in ("100", "BAD", "300")]
        for record, code in zip(records, ("100", "BAD", "300")):
            record.update({
                "relative_path": f"{code}/{code}-A.tif", "filename": f"{code}-A.tif",
                "design_id": code, "codigo": code, "content_hash": f"hash-{code}",
            })

        class SelectiveClient(FakeClient):
            def upsert(self, rows):
                self.calls.append(rows)
                if len(rows) > 1 or rows[0]["codigo"] == "BAD":
                    raise ValueError("registro inválido")
                self.rows.extend(rows)

        with patch("meury_app.supabase_sync.load_catalog_records", return_value=records), \
             patch("meury_app.supabase_sync.sync_records"):
            first = sync_pending_records([Path("/tmp")], client=SelectiveClient())
            self.assertEqual((first.completed, first.failed), (2, 1))
            self.assertEqual(records[1]["supabase_status"], "failed")

            retry_client = FakeClient()
            second = sync_pending_records([Path("/tmp")], client=retry_client)
        self.assertEqual((second.pending, second.completed, second.failed), (1, 1, 0))
        self.assertEqual(records[1]["supabase_status"], "completed")

    def test_rest_error_preserves_database_message_and_marks_sql_as_permanent(self):
        body = BytesIO(b'{"code":"21000","message":"row affected a second time"}')
        error = HTTPError("https://example.test", 500, "Internal", {}, body)
        client = SupabaseRestClient(SupabaseConfig("https://example.test", "secret"))
        with patch("meury_app.supabase_sync.urlopen", side_effect=error), \
             self.assertRaisesRegex(SupabaseHttpError, "row affected a second time") as raised:
            client.upsert([{"codigo": "1"}])
        self.assertFalse(is_retryable_external_error(raised.exception))

    def test_rest_transient_server_error_remains_retryable(self):
        body = BytesIO(b'{"message":"temporarily unavailable"}')
        error = HTTPError("https://example.test", 503, "Unavailable", {}, body)
        client = SupabaseRestClient(SupabaseConfig("https://example.test", "secret"))
        with patch("meury_app.supabase_sync.urlopen", side_effect=error), \
             self.assertRaises(SupabaseHttpError) as raised:
            client.upsert([{"codigo": "1"}])
        self.assertTrue(is_retryable_external_error(raised.exception))


if __name__ == "__main__":
    unittest.main()
