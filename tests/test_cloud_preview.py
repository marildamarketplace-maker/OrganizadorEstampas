from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import meury_app.indexer as indexer_module
from meury_app.cloud_preview import (
    GCSPreviewUploader, GoogleCloudStorageConfig, preview_storage_key,
    preview_uploader_from_environment, upload_pending_previews,
)
from meury_app.indexer import build_index, load_index_payload
from meury_app.operational_store import sync_records


class FakeUploader:
    def __init__(self, fail_code=""):
        self.calls = []
        self.fail_code = fail_code

    def upload_preview(self, preview_path, storage_key):
        self.calls.append((Path(preview_path), storage_key))
        if self.fail_code and f"/{self.fail_code}/" in storage_key:
            raise RuntimeError("falha simulada")
        return "https://cdn.example.test/" + storage_key


class CloudPreviewTest(unittest.TestCase):
    def catalog_files(self, root):
        return (
            patch.object(indexer_module, "INDEX_FILE", root / "indice.jsonl"),
            patch.object(indexer_module, "LEGACY_INDEX_FILE", root / "indice.json"),
            patch.object(indexer_module, "DUPLICATES_LOG_FILE", root / "duplicidades.txt"),
            patch.object(indexer_module, "ensure_app_dir"),
        )

    def prepare(self, root, codes=("6844",)):
        source = root / "artes"
        preview_dir = root / "previews"
        preview_dir.mkdir()
        for code in codes:
            original = source / code / f"{code}-A.png"
            original.parent.mkdir(parents=True)
            Image.new("RGB", (20, 20), "red").save(original)
        build_index(source)
        records = load_index_payload(source)["records"]
        for record in records:
            preview = preview_dir / f"{record['codigo']}.webp"
            Image.new("RGB", (10, 10), "blue").save(preview, "WEBP")
            record["preview_status"] = "completed"
            record["preview_path"] = str(preview)
            record["preview_content_hash"] = record["content_hash"]
            sync_records(indexer_module._operational_db_path(), [record])
        return source, preview_dir

    def test_uploads_only_derived_preview_and_persists_cloud_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                source, preview_dir = self.prepare(root)
                uploader = FakeUploader()
                result = upload_pending_previews(
                    source, uploader=uploader, preview_dir=preview_dir
                )
                record = load_index_payload(source)["records"][0]

            self.assertEqual(result.completed, 1)
            self.assertEqual(result.failed, 0)
            uploaded_path, key = uploader.calls[0]
            self.assertEqual(uploaded_path, Path(record["preview_path"]).resolve())
            self.assertNotEqual(uploaded_path, Path(record["path"]).resolve())
            self.assertRegex(key, r"^estampas/6844/A/6844-a-[0-9a-f]{12}/preview\.webp$")
            self.assertEqual(record["cloud_status"], "completed")
            self.assertEqual(record["storage_key"], key)
            self.assertEqual(
                record["preview_url"],
                "https://cdn.example.test/" + key + "?v=" + record["content_hash"][:16],
            )
            self.assertEqual(record["cloud_content_hash"], record["content_hash"])

    def test_completed_same_content_is_idempotent_but_new_version_uploads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                source, preview_dir = self.prepare(root)
                uploader = FakeUploader()
                first = upload_pending_previews(
                    source, uploader=uploader, preview_dir=preview_dir
                )
                second = upload_pending_previews(
                    source, uploader=uploader, preview_dir=preview_dir
                )
                record = load_index_payload(source)["records"][0]
                old_cloud_hash = record["cloud_content_hash"]
                record["content_hash"] = "f" * 64
                record["preview_content_hash"] = record["content_hash"]
                record["preview_status"] = "completed"
                record["cloud_status"] = "completed"
                sync_records(indexer_module._operational_db_path(), [record])
                third = upload_pending_previews(
                    source, uploader=uploader, preview_dir=preview_dir
                )
                updated = load_index_payload(source)["records"][0]

            self.assertEqual(first.completed, 1)
            self.assertEqual(second.pending, 0)
            self.assertEqual(third.completed, 1)
            self.assertEqual(len(uploader.calls), 2)
            self.assertNotEqual(old_cloud_hash, updated["cloud_content_hash"])
            self.assertEqual(updated["cloud_content_hash"], "f" * 64)

    def test_isolates_upload_failure_and_continues(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                source, preview_dir = self.prepare(root, ("100", "200"))
                uploader = FakeUploader(fail_code="200")
                result = upload_pending_previews(
                    source, uploader=uploader, preview_dir=preview_dir
                )
                records = load_index_payload(source)["records"]

            by_code = {record["codigo"]: record for record in records}
            self.assertEqual(result.completed, 1)
            self.assertEqual(result.failed, 1)
            self.assertEqual(by_code["100"]["cloud_status"], "completed")
            self.assertEqual(by_code["200"]["cloud_status"], "failed")
            self.assertIn("Cloud: RuntimeError", by_code["200"]["last_error"])

    def test_blocks_original_even_if_marked_as_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                source, _preview_dir = self.prepare(root)
                record = load_index_payload(source)["records"][0]
                record["preview_path"] = record["path"]
                record["cloud_status"] = "pending"
                sync_records(indexer_module._operational_db_path(), [record])
                uploader = FakeUploader()
                result = upload_pending_previews(
                    source, uploader=uploader, preview_dir=source
                )
                failed = load_index_payload(source)["records"][0]

            self.assertEqual(result.failed, 1)
            self.assertEqual(uploader.calls, [])
            self.assertEqual(failed["cloud_status"], "failed")
            self.assertIn("upload bloqueado", failed["last_error"])

    def test_relocates_existing_preview_after_app_data_directory_move(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            patches = self.catalog_files(root)
            with patches[0], patches[1], patches[2], patches[3]:
                source, old_preview_dir = self.prepare(root)
                record = load_index_payload(source)["records"][0]
                old_preview = Path(record["preview_path"])
                new_preview_dir = root / "dados-novos" / "previews"
                new_preview_dir.mkdir(parents=True)
                relocated = new_preview_dir / f"{record['content_hash']}.webp"
                relocated.write_bytes(old_preview.read_bytes())
                record["preview_path"] = str(
                    root / "dados-antigos" / "previews" / relocated.name
                )
                record["cloud_status"] = "failed"
                sync_records(indexer_module._operational_db_path(), [record])

                uploader = FakeUploader()
                result = upload_pending_previews(
                    source, uploader=uploader, preview_dir=new_preview_dir
                )
                updated = load_index_payload(source)["records"][0]

            self.assertEqual(result.completed, 1)
            self.assertEqual(uploader.calls[0][0], relocated.resolve())
            self.assertEqual(updated["preview_path"], str(relocated.resolve()))

    def test_storage_key_sanitizes_segments(self):
        key = preview_storage_key(
            {"codigo": "68 44", "variante": "A/B"}, Path("preview.jpg")
        )
        self.assertRegex(
            key, r"^estampas/68-44/A-B/arquivo-[0-9a-f]{12}/preview\.jpg$"
        )

    def test_storage_key_distinguishes_all_files_of_same_variant(self):
        first = preview_storage_key(
            {"codigo": "6236", "variante": "A", "filename": "6236-a-1.png"},
            Path("preview.webp"),
        )
        mockup = preview_storage_key(
            {"codigo": "6236", "variante": "A", "filename": "6236-a-mockup.png"},
            Path("preview.webp"),
        )
        self.assertNotEqual(first, mockup)
        self.assertIn("/6236-a-1-", first)
        self.assertIn("/6236-a-mockup-", mockup)

    def test_storage_key_distinguishes_same_filename_in_different_folders(self):
        common = {"codigo": "PA444", "variante": "A", "filename": "pa444-a-0.jpg"}
        first = preview_storage_key(
            {**common, "relative_path": "pa444/pa444-a-0.jpg"}, Path("p.webp")
        )
        second = preview_storage_key(
            {**common, "relative_path": "bandeira/pa444/pa444-a-0.jpg"}, Path("p.webp")
        )
        self.assertNotEqual(first, second)

    def test_google_config_normalizes_private_key_and_infers_project(self):
        environment = {
            "GOOGLE_CLOUD_STORAGE_BUCKET": "previews-meury",
            "GOOGLE_CLOUD_CLIENT_EMAIL": "indexador@meury-prod.iam.gserviceaccount.com",
            "GOOGLE_CLOUD_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----\\n",
        }
        with patch.dict("os.environ", environment, clear=True):
            config = GoogleCloudStorageConfig.from_environment()
        self.assertEqual(config.project_id, "meury-prod")
        self.assertIn("\nabc\n", config.private_key)
        self.assertEqual(
            config.public_base_url,
            "https://storage.googleapis.com/previews-meury",
        )

    def test_google_variables_select_gcs_adapter(self):
        environment = {
            "GOOGLE_CLOUD_STORAGE_BUCKET": "previews-meury",
            "GOOGLE_CLOUD_CLIENT_EMAIL": "indexador@meury-prod.iam.gserviceaccount.com",
            "GOOGLE_CLOUD_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----",
        }
        sentinel = object()
        with patch.dict("os.environ", environment, clear=True), \
             patch("meury_app.cloud_preview.GCSPreviewUploader", return_value=sentinel):
            self.assertIs(preview_uploader_from_environment(), sentinel)

    def test_gcs_uploads_file_and_returns_encoded_url(self):
        class Blob:
            cache_control = ""

            def __init__(self):
                self.calls = []

            def upload_from_filename(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        class Bucket:
            def __init__(self):
                self.key = ""
                self.object = Blob()

            def blob(self, key):
                self.key = key
                return self.object

        uploader = object.__new__(GCSPreviewUploader)
        uploader.bucket = Bucket()
        uploader.public_base_url = "https://storage.googleapis.com/previews"
        url = uploader.upload_preview(
            Path("preview teste.webp"), "estampas/68 44/A/preview.webp"
        )
        self.assertEqual(uploader.bucket.key, "estampas/68 44/A/preview.webp")
        self.assertEqual(
            uploader.bucket.object.cache_control,
            "public, max-age=31536000, immutable",
        )
        self.assertEqual(
            url,
            "https://storage.googleapis.com/previews/estampas/68%2044/A/preview.webp",
        )


if __name__ == "__main__":
    unittest.main()
