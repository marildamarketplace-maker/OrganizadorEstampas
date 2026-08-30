from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest

from meury_app.operational_store import overlay_records, sync_records


class OperationalStoreTest(unittest.TestCase):
    def record(self):
        return {
            "source": 0, "relative_path": "6844/6844-A.tif",
            "path": "/catalogo/6844/6844-A.tif", "design_id": "6844",
            "codigo": "6844", "variante": "A", "filename": "6844-A.tif",
            "asset_id": "6844/6844-a",
            "size": 123, "mtime_ns": 456, "content_hash": "abc",
            "indexed": True, "changed": False, "scan_status": "new",
            "processing_status": "pending", "preview_status": "pending",
            "preview_path": "", "cloud_status": "pending", "storage_key": "",
            "preview_url": "", "supabase_status": "pending", "active": True,
            "missing_locally": False, "last_error": "",
            "last_indexed_at": "2026-08-27 10:00:00", "last_synced_at": "",
        }

    def test_migrates_and_overlays_operational_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "estado.sqlite3"
            record = self.record()
            sync_records(database, [record])
            with closing(sqlite3.connect(database)) as connection, connection:
                stored_path = connection.execute("SELECT path FROM image_state").fetchone()[0]
                connection.execute(
                    "UPDATE image_state SET cloud_status='uploaded', storage_key='artes/6844.tif'"
                )
                connection.commit()
            self.assertEqual(stored_path, "6844/6844-A.tif")
            loaded = [dict(record, cloud_status="pending", storage_key="")]
            overlay_records(database, loaded)

            self.assertEqual(loaded[0]["cloud_status"], "uploaded")
            self.assertEqual(loaded[0]["storage_key"], "artes/6844.tif")
            self.assertTrue(loaded[0]["indexed"])
            self.assertEqual(loaded[0]["asset_id"], "6844/6844-a")

    def test_upsert_preserves_created_at(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "estado.sqlite3"
            record = self.record()
            sync_records(database, [record])
            with closing(sqlite3.connect(database)) as connection, connection:
                created = connection.execute("SELECT created_at FROM image_state").fetchone()[0]
            record["preview_status"] = "ready"
            sync_records(database, [record])
            with closing(sqlite3.connect(database)) as connection, connection:
                row = connection.execute(
                    "SELECT created_at, preview_status FROM image_state"
                ).fetchone()
            self.assertEqual(row[0], created)
            self.assertEqual(row[1], "ready")


if __name__ == "__main__":
    unittest.main()
