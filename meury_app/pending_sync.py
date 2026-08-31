"""Pipeline explícito para enviar somente o trabalho pendente do computador."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import threading
import time

from .cloud_preview import PreviewUploader, upload_pending_previews
from .indexer import load_catalog_records
from .preview_generator import generate_pending_previews
from .supabase_sync import SupabaseClient, sync_pending_records


@dataclass(frozen=True)
class PendingSyncResult:
    total_work: int
    completed: int
    pending: int
    errors: int
    previews_completed: int
    uploads_completed: int
    supabase_completed: int
    elapsed_seconds: float


def _needs_preview(record: dict) -> bool:
    return record.get("preview_status") in {"pending", "failed"}


def _needs_cloud(record: dict) -> bool:
    return _needs_preview(record) or record.get("cloud_status") in {"pending", "failed"}


def _needs_supabase(record: dict) -> bool:
    return _needs_cloud(record) or record.get("supabase_status") in {"pending", "failed"}


def _active(record: dict) -> bool:
    return bool(record.get("active", True)) and not record.get("missing_locally", False)


def _estimate_work(records: list[dict]) -> int:
    """Conta arquivos, não etapas, para o resumo não triplicar o total."""
    return sum(
        1 for record in records
        if _active(record) and _needs_supabase(record)
    )


def synchronize_pending(
    source_dirs, *, preview_dir: Path | None = None,
    uploader: PreviewUploader | None = None, supabase_client: SupabaseClient | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    limit: int | None = None, pause_event: threading.Event | None = None,
) -> PendingSyncResult:
    """Gera derivados, envia-os e publica metadados, sempre nessa ordem."""
    initial = load_catalog_records(source_dirs)
    if initial is None:
        raise ValueError("Atualize o índice antes de sincronizar os pendentes.")
    selected = [
        record for record in initial
        if _active(record) and _needs_supabase(record)
    ]
    if limit is not None:
        selected = selected[:limit]
    allowed_asset_ids = {str(record.get("asset_id", "")) for record in selected}
    total_work = len(selected)
    started = time.monotonic()
    processed_units = 0
    stage_work = sum(
        int(_needs_preview(record)) + int(_needs_cloud(record)) + int(_needs_supabase(record))
        for record in selected
    )

    def stage_progress(stage: str):
        def report(current: int, total: int, _message: str) -> None:
            overall_total = max(stage_work, processed_units + total)
            if progress_callback:
                progress_callback(
                    processed_units + current, overall_total,
                    f"Processando {processed_units + current:,} de {overall_total:,} — {stage}",
                )
        return report

    preview = generate_pending_previews(
        source_dirs, preview_dir=preview_dir,
        progress_callback=stage_progress("previews"),
        pause_event=pause_event, allowed_asset_ids=allowed_asset_ids,
    )
    processed_units += preview.pending
    cloud = upload_pending_previews(
        source_dirs, uploader=uploader, preview_dir=preview_dir,
        progress_callback=stage_progress("Cloud"),
        pause_event=pause_event, allowed_asset_ids=allowed_asset_ids,
    )
    processed_units += cloud.pending
    supabase = sync_pending_records(
        source_dirs, client=supabase_client,
        progress_callback=stage_progress("Supabase"),
        pause_event=pause_event, allowed_asset_ids=allowed_asset_ids,
    )
    processed_units += supabase.pending

    current = load_catalog_records(source_dirs) or []
    pending = sum(
        1 for record in current
        if _active(record) and (
            record.get("preview_status") == "pending"
            or record.get("cloud_status") == "pending"
            or record.get("supabase_status") == "pending"
        )
    )
    errors = sum(
        1 for record in current
        if _active(record) and (
            record.get("preview_status") == "failed"
            or record.get("cloud_status") == "failed"
            or record.get("supabase_status") == "failed"
        )
    )
    return PendingSyncResult(
        total_work=total_work,
        completed=supabase.completed,
        pending=pending,
        errors=errors,
        previews_completed=preview.completed,
        uploads_completed=cloud.completed,
        supabase_completed=supabase.completed,
        elapsed_seconds=time.monotonic() - started,
    )
