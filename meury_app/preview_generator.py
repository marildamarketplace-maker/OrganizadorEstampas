"""Gera derivados web sem modificar os arquivos originais do catálogo."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from itertools import islice
from pathlib import Path
from typing import Callable
import hashlib
import logging
import os
import tempfile
import threading
import time

from .config import PREVIEW_DIR, ensure_app_dir
from .indexer import _operational_db_path, load_catalog_records
from .operational_store import sync_records
from .index_progress import IndexProgress
from .preview_progress import PreviewProgress


MAX_PREVIEW_EDGE = 1024
WEBP_METHOD = 4
STATE_BATCH_SIZE = 32
STATE_FLUSH_SECONDS = 1.0
PREVIEWABLE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".pdf"}


@dataclass(frozen=True)
class PreviewGenerationResult:
    pending: int
    completed: int
    failed: int
    elapsed_seconds: float


def _preview_identity(record: dict) -> str:
    content_hash = str(record.get("content_hash", "")).strip()
    if len(content_hash) == 64 and all(character in "0123456789abcdefABCDEF" for character in content_hash):
        return content_hash.lower()
    identity = (
        f"{int(record.get('source', 0))}:"
        f"{str(record.get('relative_path', '')).replace(chr(92), '/').casefold()}"
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


@contextmanager
def _open_preview_source(source: Path, max_edge: int):
    if source.suffix.casefold() == ".pdf":
        from .pdf_preview import render_first_page
        with render_first_page(source, max_edge) as image:
            yield image
    else:
        from PIL import Image
        with Image.open(source) as image:
            yield image


def _valid_cached_preview(destination: Path, image_format: str, expected_edge: int) -> bool:
    from PIL import Image
    try:
        with Image.open(destination) as cached:
            if cached.format == image_format and max(cached.size) == expected_edge:
                cached.load()
                return True
    except (OSError, ValueError):
        pass
    return False


def create_preview(
    record: dict, preview_dir: Path | None = None, max_edge: int = MAX_PREVIEW_EDGE,
) -> Path:
    """Cria um derivado atômico; o original é aberto exclusivamente para leitura."""
    source = Path(str(record.get("path", "")))
    if source.suffix.casefold() not in PREVIEWABLE_EXTENSIONS:
        raise ValueError(f"Formato sem preview local: {source.suffix or '(sem extensão)'}")
    if max_edge < 1:
        raise ValueError("A dimensão do preview precisa ser positiva.")
    stat = source.stat()
    for field, actual in (("size", stat.st_size), ("mtime_ns", stat.st_mtime_ns)):
        if record.get(field, -1) != -1 and int(record[field]) != actual:
            raise ValueError("O original mudou desde a indexação. Atualize o índice antes de gerar o preview.")
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
        # Um PDF já concluído não precisa ser rasterizado novamente na retomada.
        if source.suffix.casefold() == ".pdf" and record.get("content_hash") and _valid_cached_preview(
            destination, "WEBP" if use_webp else "JPEG", max_edge,
        ):
            return destination.resolve()
        with _open_preview_source(source, max_edge) as opened:
            # Recupera derivados de uma execução interrompida após a gravação do
            # arquivo e antes do commit SQLite. Só reutiliza conteúdo identificado.
            if record.get("content_hash") and _valid_cached_preview(
                destination, "WEBP" if use_webp else "JPEG", min(max_edge, max(opened.size)),
            ):
                return destination.resolve()
            icc_profile = opened.info.get("icc_profile")
            # JPEG permite que o decoder carregue uma resolução reduzida. Isso
            # diminui bastante o pico de RAM em fotos enormes sem afetar um preview
            # cujo maior lado será 1024 px.
            if source.suffix.casefold() in {".jpg", ".jpeg"}:
                scale = min(1.0, max_edge / max(opened.size))
                opened.draft("RGB", tuple(max(1, int(edge * scale)) for edge in opened.size))
            # Reduz antes de transpor: evita copiar a imagem inteira (centenas de
            # MB nos originais grandes). EXIF é aplicado aos pixels já reduzidos.
            if opened.mode in {"P", "1"}:
                derived = opened.convert("RGBA" if "transparency" in opened.info else "RGB")
            else:
                derived = opened
            derived.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            ImageOps.exif_transpose(derived, in_place=True)
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
                    temporary_name, "WEBP", quality=86, method=WEBP_METHOD,
                    **({"icc_profile": icc_profile} if icc_profile else {}),
                )
            else:
                derived.save(
                    temporary_name, "JPEG", quality=90, optimize=True,
                    **({"icc_profile": icc_profile} if icc_profile else {}),
                )
        current_stat = source.stat()
        if (current_stat.st_size, current_stat.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            raise ValueError("O original mudou durante a geração. Atualize o índice antes de gerar o preview.")
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
    pause_event: threading.Event | None = None,
    allowed_asset_ids: set[str] | None = None,
    max_workers: int | None = None,
) -> PreviewGenerationResult:
    """Fila limitada, imagens em paralelo e persistência apenas no coordenador."""
    workers = max_workers if max_workers is not None else int(os.environ.get("MEURY_PREVIEW_WORKERS", "2"))
    if not 1 <= workers <= 4:
        raise ValueError("Use de 1 a 4 workers para gerar previews.")
    if limit is not None and limit < 1:
        raise ValueError("O limite de previews precisa ser positivo.")
    started = time.monotonic()

    def loading_progress(_count, message):
        if progress_callback:
            progress_callback(0, 0, message)

    records = load_catalog_records(source_dirs, progress=IndexProgress(loading_progress))
    if records is None:
        raise ValueError("Atualize o índice antes de gerar previews.")
    already_ready = sum(
        1 for record in records
        if record.get("active", True) and not record.get("missing_locally", False)
        and record.get("preview_status") in {"completed", "ready"}
    )
    candidates = (
        record for record in records
        if record.get("active", True)
        and not record.get("missing_locally", False)
        and record.get("preview_status") in {"pending", "failed"}
        and (
            allowed_asset_ids is None
            or str(record.get("asset_id", "")) in allowed_asset_ids
        )
    )
    pending = list(islice(candidates, limit)) if limit is not None else list(candidates)
    del candidates, records
    completed = failed = 0
    reporter = PreviewProgress(len(pending), already_ready, progress_callback)
    reporter.report(0, 0, force=True)
    dirty: list[dict] = []
    last_flush = time.monotonic()

    def flush():
        nonlocal last_flush
        if dirty:
            sync_records(_operational_db_path(), dirty)
            dirty.clear()
        last_flush = time.monotonic()

    remaining = iter(pending)
    scheduled = 0
    exhausted = not pending
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="meury-preview") as executor:
            running = {}
            while running or not exhausted:
                # Não envia novos trabalhos durante a pausa. Drena os que já
                # começaram e salva seus resultados antes de ficar aguardando.
                if pause_event is not None and not pause_event.is_set() and not running:
                    flush()
                    reporter.pause()
                    reporter.report(completed, failed, force=True)
                    pause_event.wait()
                    reporter.resume()
                    reporter.report(completed, failed, force=True)
                while len(running) < workers and not exhausted:
                    if pause_event is not None and not pause_event.is_set():
                        break
                    record = next(remaining)
                    scheduled += 1
                    exhausted = scheduled == len(pending)
                    record["preview_attempts"] = int(record.get("preview_attempts", 0)) + 1
                    record["preview_last_attempt_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    running[executor.submit(create_preview, record, preview_dir=preview_dir)] = record
                if not running:
                    continue
                finished, _ = wait(running, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in finished:
                    record = running.pop(future)
                    try:
                        preview = future.result()
                        record["preview_status"] = "completed"
                        record["preview_path"] = str(preview)
                        record["preview_content_hash"] = str(record.get("content_hash", ""))
                        record["last_error"] = record["preview_last_error"] = ""
                        completed += 1
                    except Exception as exc:
                        logging.getLogger(__name__).error(
                            "Falha ao gerar preview | arquivo: %s | motivo: %s: %s",
                            record.get("path", ""), type(exc).__name__, exc,
                        )
                        record["preview_status"] = "failed"
                        record["preview_path"] = record["preview_content_hash"] = ""
                        record["last_error"] = f"Preview: {type(exc).__name__}: {exc}"
                        record["preview_last_error"] = f"{type(exc).__name__}: {exc}"
                        failed += 1
                    dirty.append(record)
                if len(dirty) >= STATE_BATCH_SIZE or time.monotonic() - last_flush >= STATE_FLUSH_SECONDS:
                    flush()
                reporter.report(completed, failed)
    finally:
        flush()
    reporter.report(completed, failed, force=True, finishing=True)
    return PreviewGenerationResult(
        pending=len(pending), completed=completed, failed=failed,
        elapsed_seconds=time.monotonic() - started,
    )
