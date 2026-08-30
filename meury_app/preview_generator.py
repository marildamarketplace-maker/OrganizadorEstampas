"""Gera derivados web sem modificar os arquivos originais do catálogo."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import hashlib
import os
import tempfile
import time

from .config import PREVIEW_DIR, ensure_app_dir
from .indexer import _operational_db_path, load_catalog_records
from .operational_store import sync_records


MAX_PREVIEW_EDGE = 1024
PREVIEWABLE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


@dataclass(frozen=True)
class PreviewGenerationResult:
    pending: int
    completed: int
    failed: int
    elapsed_seconds: float


def _preview_identity(record: dict) -> str:
    content_hash = str(record.get("content_hash", "")).strip()
    if content_hash:
        return content_hash
    identity = (
        f"{int(record.get('source', 0))}:"
        f"{str(record.get('relative_path', '')).replace(chr(92), '/').casefold()}"
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def create_preview(
    record: dict, preview_dir: Path | None = None, max_edge: int = MAX_PREVIEW_EDGE,
) -> Path:
    """Cria um derivado atômico; o original é aberto exclusivamente para leitura."""
    source = Path(str(record.get("path", "")))
    if source.suffix.casefold() not in PREVIEWABLE_EXTENSIONS:
        raise ValueError(f"Formato sem preview local: {source.suffix or '(sem extensão)'}")
    if not source.is_file():
        raise FileNotFoundError(f"Arquivo original não encontrado: {source}")
    try:
        from PIL import Image, ImageOps, features
    except ImportError as exc:
        raise RuntimeError("A biblioteca Pillow não está instalada.") from exc

    if preview_dir is None:
        ensure_app_dir()
    destination_dir = preview_dir or PREVIEW_DIR
    destination_dir.mkdir(parents=True, exist_ok=True)
    use_webp = bool(features.check("webp"))
    suffix = ".webp" if use_webp else ".jpg"
    destination = destination_dir / f"{_preview_identity(record)}{suffix}"
    temporary_name = None
    derived = None
    try:
        with Image.open(source) as opened:
            icc_profile = opened.info.get("icc_profile")
            # JPEG permite que o decoder carregue uma resolução reduzida. Isso
            # diminui bastante o pico de RAM em fotos enormes sem afetar um preview
            # cujo maior lado será 1024 px.
            if source.suffix.casefold() in {".jpg", ".jpeg"}:
                opened.draft("RGB", (max_edge * 2, max_edge * 2))
            image = ImageOps.exif_transpose(opened)
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            # ``copy`` desacopla o derivado do arquivo aberto antes da gravação.
            derived = image.copy()
        if not use_webp and derived.mode != "RGB":
            if "A" in derived.getbands():
                background = Image.new("RGB", derived.size, "white")
                background.paste(derived, mask=derived.getchannel("A"))
                derived.close()
                derived = background
            else:
                converted = derived.convert("RGB")
                derived.close()
                derived = converted
        fd, temporary_name = tempfile.mkstemp(
            dir=destination_dir, prefix=destination.stem + ".", suffix=".tmp" + suffix,
        )
        os.close(fd)
        if use_webp:
            derived.save(
                temporary_name, "WEBP", quality=86, method=6,
                **({"icc_profile": icc_profile} if icc_profile else {}),
            )
        else:
            derived.save(
                temporary_name, "JPEG", quality=90, optimize=True,
                **({"icc_profile": icc_profile} if icc_profile else {}),
            )
        os.replace(temporary_name, destination)
        return destination.resolve()
    finally:
        if derived is not None:
            derived.close()
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def generate_pending_previews(
    source_dirs, *, preview_dir: Path | None = None, limit: int | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> PreviewGenerationResult:
    records = load_catalog_records(source_dirs)
    if records is None:
        raise ValueError("Atualize o índice antes de gerar previews.")
    pending = [
        record for record in records
        if record.get("active", True)
        and not record.get("missing_locally", False)
        and record.get("preview_status") in {"pending", "failed"}
    ]
    if limit is not None:
        pending = pending[:limit]
    started = time.monotonic()
    completed = failed = 0
    for position, record in enumerate(pending, start=1):
        attempted_at = time.strftime("%Y-%m-%d %H:%M:%S")
        record["preview_attempts"] = int(record.get("preview_attempts", 0)) + 1
        record["preview_last_attempt_at"] = attempted_at
        try:
            preview = create_preview(record, preview_dir=preview_dir)
            record["preview_status"] = "completed"
            record["preview_path"] = str(preview)
            record["preview_content_hash"] = str(record.get("content_hash", ""))
            record["last_error"] = ""
            record["preview_last_error"] = ""
            completed += 1
        except Exception as exc:
            record["preview_status"] = "failed"
            record["preview_path"] = ""
            record["preview_content_hash"] = ""
            record["last_error"] = f"Preview: {type(exc).__name__}: {exc}"
            record["preview_last_error"] = f"{type(exc).__name__}: {exc}"
            failed += 1
        sync_records(_operational_db_path(), [record])
        if progress_callback:
            progress_callback(
                position, len(pending),
                f"Gerando previews: {position:,} de {len(pending):,}",
            )
    return PreviewGenerationResult(
        pending=len(pending), completed=completed, failed=failed,
        elapsed_seconds=time.monotonic() - started,
    )
