"""Contadores do catálogo calculáveis fora da thread da interface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .indexer import load_catalog_records
from .indexer import _operational_db_path
from .operational_store import load_latest_scan_summary
from .semantic_search import record_identity, semantic_index_identities

RASTER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


@dataclass(frozen=True)
class CatalogStatistics:
    total: int = 0
    with_keywords: int = 0
    without_keywords: int = 0
    with_embedding: int = 0
    pending: int = 0
    errors: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    pending_preview: int = 0
    pending_cloud: int = 0
    pending_supabase: int = 0
    synced: int = 0
    missing: int = 0


def calculate_statistics(
    records: list[dict], embedded: set[str], scan_summary: dict | None = None,
) -> CatalogStatistics:
    total = raster_total = with_keywords = with_embedding = pending = errors = 0
    pending_preview = pending_cloud = pending_supabase = synced = missing = 0
    for record in records:
        if record.get("missing_locally", False) or not record.get("active", True):
            missing += 1
            continue
        total += 1
        if Path(str(record.get("filename", ""))).suffix.casefold() in RASTER_EXTENSIONS:
            raster_total += 1
            with_keywords += bool(record.get("keywords"))
            with_embedding += record_identity(record) in embedded
            pending += not bool(record.get("processed", False))
        pending_preview += record.get("preview_status") == "pending"
        pending_cloud += record.get("cloud_status") == "pending"
        pending_supabase += record.get("supabase_status") == "pending"
        synced += record.get("supabase_status") in {"synced", "completed"}
        errors += bool(
            record.get("analysis_error") or record.get("last_error")
            or record.get("attention_status") == "REQUIRES_ATTENTION"
        )
    scan = scan_summary or {}
    return CatalogStatistics(
        total=total, with_keywords=with_keywords,
        without_keywords=max(0, raster_total - with_keywords), with_embedding=with_embedding,
        pending=pending, errors=errors, new=int(scan.get("added_files", 0)),
        changed=int(scan.get("changed_files", 0)),
        unchanged=int(scan.get("unchanged_files", 0)),
        pending_preview=pending_preview, pending_cloud=pending_cloud,
        pending_supabase=pending_supabase, synced=synced, missing=missing,
    )


def load_catalog_statistics(source_dirs) -> CatalogStatistics:
    return calculate_statistics(
        load_catalog_records(source_dirs) or [], semantic_index_identities(),
        load_latest_scan_summary(_operational_db_path()),
    )


def load_category_records(source_dirs, category: str, limit: int = 500) -> list[dict]:
    records = load_catalog_records(source_dirs) or []
    filters = {
        "total": lambda r: r.get("active", True) and not r.get("missing_locally", False),
        "new": lambda r: r.get("scan_status") == "new" and r.get("active", True),
        "changed": lambda r: bool(r.get("changed")) and r.get("active", True),
        "unchanged": lambda r: r.get("scan_status") == "unchanged" and r.get("active", True),
        "preview": lambda r: r.get("preview_status") == "pending" and r.get("active", True),
        "cloud": lambda r: r.get("cloud_status") == "pending" and r.get("active", True),
        "supabase": lambda r: r.get("supabase_status") == "pending" and r.get("active", True),
        "synced": lambda r: r.get("supabase_status") in {"synced", "completed"} and r.get("active", True),
        "missing": lambda r: r.get("missing_locally", False) or not r.get("active", True),
        "errors": lambda r: bool(r.get("analysis_error") or r.get("last_error")
                                  or r.get("attention_status") == "REQUIRES_ATTENTION"),
    }
    predicate = filters.get(category, lambda _r: False)
    selected = [record for record in records if predicate(record)]
    selected.sort(key=lambda record: str(record.get("path", "")).casefold())
    return selected[:limit]
