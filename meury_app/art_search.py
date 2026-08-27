"""Busca local ranqueada e cache de miniaturas para o catálogo de artes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import hashlib
import heapq
import os
import re
import unicodedata

from .config import APP_DIR, ensure_app_dir
from .indexer import load_catalog_records


SEARCH_FIELDS = (
    ("filename", 5.0), ("path", 1.5), ("description", 4.0),
    ("keywords", 6.0), ("colors", 5.0), ("elements", 5.0),
    ("themes", 5.0), ("category", 6.0),
)
STOP_WORDS = {"a", "as", "o", "os", "e", "de", "da", "das", "do", "dos", "com"}


def normalize_search_text(value) -> str:
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def search_tokens(query: str) -> list[str]:
    result = []
    for token in normalize_search_text(query).split():
        if token not in STOP_WORDS and token not in result:
            result.append(token)
    return result


def _token_similarity(query: str, candidate: str) -> float:
    if query == candidate:
        return 1.0
    shortest = min(len(query), len(candidate))
    if shortest >= 4 and (query.startswith(candidate) or candidate.startswith(query)):
        return 0.78
    if len(query) >= 4 and query in candidate:
        return 0.58
    return 0.0


@dataclass
class SearchResult:
    record: dict
    score: float
    similarity: float | None = None


class ArtSearchEngine:
    """Carrega metadados uma vez e mantém imagens fora da memória."""

    def __init__(self, source_dirs):
        self.source_dirs = source_dirs
        self._documents: list[tuple[dict, tuple[tuple[list[str], float], ...]]] = []

    def load(self) -> int:
        records = load_catalog_records(self.source_dirs)
        if records is None:
            raise ValueError("Atualize o índice antes de pesquisar as artes.")
        documents = []
        for record in records:
            if not record.get("active", True):
                continue
            fields = []
            for name, weight in SEARCH_FIELDS:
                normalized = normalize_search_text(record.get(name, ""))
                if normalized:
                    fields.append((normalized.split(), weight))
            documents.append((record, tuple(fields)))
        self._documents = documents
        return len(documents)

    def search(self, query: str, limit: int = 200) -> list[SearchResult]:
        tokens = search_tokens(query)
        if not tokens:
            return []
        best_results = []
        required_matches = max(1, (len(tokens) + 1) // 2)
        for position, (record, fields) in enumerate(self._documents):
            total_score = 0.0
            matched = 0
            for query_token in tokens:
                best = 0.0
                for candidates, weight in fields:
                    similarity = max(
                        (_token_similarity(query_token, candidate) for candidate in candidates),
                        default=0.0,
                    )
                    best = max(best, similarity * weight)
                if best:
                    matched += 1
                    total_score += best
            if matched < required_matches:
                continue
            total_score *= matched / len(tokens)
            item = (total_score, -position, SearchResult(record, total_score))
            if len(best_results) < limit:
                heapq.heappush(best_results, item)
            elif item[:2] > best_results[0][:2]:
                heapq.heapreplace(best_results, item)
        return [item[2] for item in sorted(best_results, reverse=True)]


class ThumbnailCache:
    def __init__(self, cache_dir: Path | None = None):
        if cache_dir is None:
            ensure_app_dir()
        self.cache_dir = cache_dir or APP_DIR / "thumbnails"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def thumbnail_path(self, image_path: Path | str, size=(240, 180)) -> Path:
        path = Path(image_path)
        try:
            stat = path.stat()
            identity = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{size}"
        except OSError:
            identity = f"{path}|missing|{size}"
        digest = hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()
        return self.cache_dir / f"{digest}.jpg"

    def get_or_create(self, image_path: Path | str, size=(240, 180)) -> Path:
        destination = self.thumbnail_path(image_path, size)
        if destination.exists():
            return destination
        try:
            from PIL import Image, ImageOps
        except ImportError as exc:
            raise RuntimeError("Instale as dependências com: pip install -r requirements.txt") from exc
        source = Path(image_path)
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail(size)
            canvas = Image.new("RGB", size, "white")
            position = ((size[0] - image.width) // 2, (size[1] - image.height) // 2)
            canvas.paste(image, position)
            temporary = destination.with_suffix(".tmp.jpg")
            canvas.save(temporary, "JPEG", quality=82, optimize=True)
            os.replace(temporary, destination)
        return destination


def principal_keywords(record: dict, limit: int = 5) -> str:
    values: Iterable = record.get("keywords") or []
    if isinstance(values, str):
        values = re.split(r"[,;]", values)
    return ", ".join(str(value) for value in list(values)[:limit])
