"""Contadores do catálogo calculáveis fora da thread da interface."""

from dataclasses import dataclass
from pathlib import Path

from .indexer import load_catalog_records
from .semantic_search import record_identity, semantic_index_identities

RASTER_EXTENSIONS = {".jpg", ".jpeg", ".png"}


@dataclass(frozen=True)
class CatalogStatistics:
    total: int = 0
    with_keywords: int = 0
    without_keywords: int = 0
    with_embedding: int = 0
    pending: int = 0
    errors: int = 0


def calculate_statistics(records: list[dict], embedded: set[str]) -> CatalogStatistics:
    total = with_keywords = with_embedding = pending = errors = 0
    for record in records:
        if not record.get("active", True):
            continue
        if Path(str(record.get("filename", ""))).suffix.casefold() not in RASTER_EXTENSIONS:
            continue
        total += 1
        with_keywords += bool(record.get("keywords"))
        with_embedding += record_identity(record) in embedded
        pending += not bool(record.get("processed", False))
        errors += bool(record.get("analysis_error"))
    return CatalogStatistics(
        total, with_keywords, total - with_keywords, with_embedding, pending, errors
    )


def load_catalog_statistics(source_dirs) -> CatalogStatistics:
    return calculate_statistics(
        load_catalog_records(source_dirs) or [], semantic_index_identities()
    )
