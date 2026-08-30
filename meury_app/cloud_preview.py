"""Upload independente de previews para armazenamento S3 compatível."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import quote
import mimetypes
import os
import re
import time

from .asset_identity import storage_asset_segment
from .config import PREVIEW_DIR
from .indexer import _operational_db_path, load_catalog_records
from .operational_store import sync_records
from .retry_policy import RetryFailure, run_with_retry


@dataclass(frozen=True)
class CloudStorageConfig:
    bucket: str
    access_key_id: str
    secret_access_key: str
    public_base_url: str
    endpoint_url: str = ""
    region: str = "us-east-1"

    @classmethod
    def from_environment(cls) -> "CloudStorageConfig":
        config = cls(
            bucket=os.environ.get("CLOUD_BUCKET", "").strip(),
            access_key_id=os.environ.get("CLOUD_ACCESS_KEY_ID", "").strip(),
            secret_access_key=os.environ.get("CLOUD_SECRET_ACCESS_KEY", "").strip(),
            public_base_url=os.environ.get("CLOUD_PUBLIC_BASE_URL", "").strip(),
            endpoint_url=os.environ.get("CLOUD_ENDPOINT_URL", "").strip(),
            region=os.environ.get("CLOUD_REGION", "us-east-1").strip() or "us-east-1",
        )
        missing = [
            name for name, value in (
                ("CLOUD_BUCKET", config.bucket),
                ("CLOUD_ACCESS_KEY_ID", config.access_key_id),
                ("CLOUD_SECRET_ACCESS_KEY", config.secret_access_key),
                ("CLOUD_PUBLIC_BASE_URL", config.public_base_url),
            ) if not value
        ]
        if missing:
            raise ValueError("Configuração Cloud incompleta: " + ", ".join(missing))
        return config


@dataclass(frozen=True)
class GoogleCloudStorageConfig:
    bucket: str
    client_email: str
    private_key: str
    project_id: str
    public_base_url: str

    @classmethod
    def from_environment(cls) -> "GoogleCloudStorageConfig":
        email = os.environ.get("GOOGLE_CLOUD_CLIENT_EMAIL", "").strip()
        inferred_project = ""
        suffix = ".iam.gserviceaccount.com"
        if "@" in email and email.endswith(suffix):
            inferred_project = email.split("@", 1)[1][:-len(suffix)]
        bucket = os.environ.get("GOOGLE_CLOUD_STORAGE_BUCKET", "").strip()
        config = cls(
            bucket=bucket,
            client_email=email,
            private_key=os.environ.get("GOOGLE_CLOUD_PRIVATE_KEY", "").strip().replace("\\n", "\n"),
            project_id=(os.environ.get("GOOGLE_CLOUD_PROJECT_ID", "").strip()
                        or inferred_project),
            public_base_url=(
                os.environ.get("GOOGLE_CLOUD_PUBLIC_BASE_URL", "").strip().rstrip("/")
                or (f"https://storage.googleapis.com/{bucket}" if bucket else "")
            ),
        )
        missing = [
            name for name, value in (
                ("GOOGLE_CLOUD_STORAGE_BUCKET", config.bucket),
                ("GOOGLE_CLOUD_CLIENT_EMAIL", config.client_email),
                ("GOOGLE_CLOUD_PRIVATE_KEY", config.private_key),
                ("GOOGLE_CLOUD_PROJECT_ID", config.project_id),
            ) if not value
        ]
        if missing:
            raise ValueError("Configuração Google Cloud incompleta: " + ", ".join(missing))
        if "BEGIN PRIVATE KEY" not in config.private_key:
            raise ValueError("GOOGLE_CLOUD_PRIVATE_KEY não possui uma chave privada válida.")
        return config


class PreviewUploader(Protocol):
    def upload_preview(self, preview_path: Path, storage_key: str) -> str: ...


class S3PreviewUploader:
    def __init__(self, config: CloudStorageConfig):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("Instale as dependências Cloud com requirements.txt.") from exc
        kwargs = {
            "service_name": "s3", "region_name": config.region,
            "aws_access_key_id": config.access_key_id,
            "aws_secret_access_key": config.secret_access_key,
        }
        if config.endpoint_url:
            kwargs["endpoint_url"] = config.endpoint_url
        self.client = boto3.client(**kwargs)
        self.bucket = config.bucket
        self.public_base_url = config.public_base_url.rstrip("/")

    def upload_preview(self, preview_path: Path, storage_key: str) -> str:
        content_type = mimetypes.guess_type(preview_path.name)[0] or "application/octet-stream"
        self.client.upload_file(
            str(preview_path), self.bucket, storage_key,
            ExtraArgs={
                "ContentType": content_type,
                # A URL recebe ?v=HASH; versões antigas podem ser cacheadas por
                # longo prazo sem esconder uma alteração do original.
                "CacheControl": "public, max-age=31536000, immutable",
            },
        )
        encoded_key = "/".join(quote(part, safe="") for part in storage_key.split("/"))
        return f"{self.public_base_url}/{encoded_key}"


class GCSPreviewUploader:
    """Envia derivados ao Google Cloud Storage usando uma service account."""

    def __init__(self, config: GoogleCloudStorageConfig):
        try:
            from google.cloud import storage
            from google.oauth2 import service_account
        except ImportError as exc:
            raise RuntimeError(
                "Instale as dependências Google Cloud com requirements.txt."
            ) from exc
        credentials = service_account.Credentials.from_service_account_info({
            "type": "service_account",
            "project_id": config.project_id,
            "private_key": config.private_key,
            "client_email": config.client_email,
            "token_uri": "https://oauth2.googleapis.com/token",
        })
        client = storage.Client(project=config.project_id, credentials=credentials)
        self.bucket = client.bucket(config.bucket)
        self.public_base_url = config.public_base_url

    def upload_preview(self, preview_path: Path, storage_key: str) -> str:
        content_type = mimetypes.guess_type(preview_path.name)[0] or "application/octet-stream"
        blob = self.bucket.blob(storage_key)
        blob.cache_control = "public, max-age=31536000, immutable"
        blob.upload_from_filename(
            str(preview_path), content_type=content_type, timeout=60,
        )
        encoded_key = "/".join(quote(part, safe="") for part in storage_key.split("/"))
        return f"{self.public_base_url}/{encoded_key}"


def preview_uploader_from_environment() -> PreviewUploader:
    google_names = (
        "GOOGLE_CLOUD_STORAGE_BUCKET", "GOOGLE_CLOUD_CLIENT_EMAIL",
        "GOOGLE_CLOUD_PRIVATE_KEY",
    )
    if any(os.environ.get(name, "").strip() for name in google_names):
        return GCSPreviewUploader(GoogleCloudStorageConfig.from_environment())
    return S3PreviewUploader(CloudStorageConfig.from_environment())


@dataclass(frozen=True)
class CloudUploadResult:
    pending: int
    completed: int
    failed: int
    elapsed_seconds: float


def _resolve_derived_preview(record: dict, allowed_dir: Path) -> Path:
    """Relocaliza previews após a pasta operacional ter sido migrada."""
    recorded = Path(str(record.get("preview_path", ""))).expanduser().resolve()
    if allowed_dir in recorded.parents and recorded.is_file():
        return recorded

    relocated = (allowed_dir / recorded.name).resolve()
    expected_hash = str(
        record.get("preview_content_hash") or record.get("content_hash") or ""
    ).strip().casefold()
    if (
        allowed_dir in relocated.parents
        and relocated.is_file()
        and expected_hash
        and relocated.stem.casefold() == expected_hash
    ):
        record["preview_path"] = str(relocated)
        return relocated
    return recorded


def _safe_segment(value: object, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip(".-")
    return cleaned or fallback


def preview_storage_key(record: dict, preview_path: Path) -> str:
    codigo = _safe_segment(record.get("codigo", record.get("design_id")), "SEM-CODIGO")
    variante = _safe_segment(record.get("variante"), "SEM-VARIANTE")
    asset_id = _safe_segment(storage_asset_segment(record), "sem-arquivo")
    suffix = ".webp" if preview_path.suffix.casefold() == ".webp" else ".jpg"
    return f"estampas/{codigo}/{variante}/{asset_id}/preview{suffix}"


def upload_pending_previews(
    source_dirs, *, uploader: PreviewUploader | None = None,
    preview_dir: Path | None = None, limit: int | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> CloudUploadResult:
    records = load_catalog_records(source_dirs)
    if records is None:
        raise ValueError("Atualize o índice antes do envio para Cloud.")
    allowed_dir = (preview_dir or PREVIEW_DIR).expanduser().resolve()
    pending = [
        record for record in records
        if record.get("active", True)
        and not record.get("missing_locally", False)
        and record.get("preview_status") == "completed"
        and bool(record.get("content_hash"))
        and record.get("preview_content_hash") == record.get("content_hash")
        and (
            record.get("cloud_status") in {"pending", "failed"}
            or (
                record.get("cloud_status") == "completed"
                and (
                    record.get("cloud_content_hash") != record.get("content_hash")
                    or record.get("storage_key") != preview_storage_key(
                        record, Path(str(record.get("preview_path", "")))
                    )
                )
            )
        )
    ]
    if limit is not None:
        pending = pending[:limit]
    if pending and uploader is None:
        uploader = preview_uploader_from_environment()
    started = time.monotonic()
    completed = failed = 0
    for position, record in enumerate(pending, start=1):
        try:
            preview = _resolve_derived_preview(record, allowed_dir)
            original = Path(str(record.get("path", ""))).expanduser().resolve()
            if preview == original:
                raise ValueError("O preview aponta para a imagem original; upload bloqueado.")
            if allowed_dir not in preview.parents:
                raise ValueError("O arquivo não pertence à pasta de previews derivados.")
            if not preview.is_file():
                raise FileNotFoundError(f"Preview não encontrado: {preview}")
            key = preview_storage_key(record, preview)
            previous_key = str(record.get("storage_key", ""))
            if uploader is None:  # Garantido pela inicialização sob demanda acima.
                raise RuntimeError("Cliente Cloud indisponível.")
            url, attempts = run_with_retry(lambda: uploader.upload_preview(preview, key))
            record["cloud_attempts"] = int(record.get("cloud_attempts", 0)) + attempts
            record["cloud_last_attempt_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if not str(url).strip():
                raise ValueError("O provedor não retornou a URL pública do preview.")
            record["storage_key"] = key
            version = str(record.get("content_hash", ""))[:16]
            separator = "&" if "?" in str(url) else "?"
            record["preview_url"] = f"{url}{separator}v={version}"
            record["cloud_content_hash"] = str(record.get("preview_content_hash", ""))
            record["cloud_status"] = "completed"
            if previous_key != key:
                record["supabase_status"] = "pending"
                record["supabase_content_hash"] = ""
            record["last_error"] = ""
            record["cloud_last_error"] = ""
            completed += 1
        except RetryFailure as failure:
            exc = failure.cause
            record["cloud_attempts"] = int(record.get("cloud_attempts", 0)) + failure.attempts
            record["cloud_last_attempt_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            record["cloud_status"] = "failed"
            record["last_error"] = f"Cloud: {type(exc).__name__}: {exc}"
            record["cloud_last_error"] = f"{type(exc).__name__}: {exc}"
            failed += 1
        except Exception as exc:
            # Validações locais não entram no retry externo.
            record["cloud_attempts"] = int(record.get("cloud_attempts", 0)) + 1
            record["cloud_last_attempt_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            record["cloud_status"] = "failed"
            record["last_error"] = f"Cloud: {type(exc).__name__}: {exc}"
            record["cloud_last_error"] = f"{type(exc).__name__}: {exc}"
            failed += 1
        sync_records(_operational_db_path(), [record])
        if progress_callback:
            progress_callback(
                position, len(pending),
                f"Enviando previews: {position:,} de {len(pending):,}",
            )
    return CloudUploadResult(
        pending=len(pending), completed=completed, failed=failed,
        elapsed_seconds=time.monotonic() - started,
    )
