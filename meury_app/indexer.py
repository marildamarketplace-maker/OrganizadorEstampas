from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import json
import time

from .config import (
    DUPLICATES_LOG_FILE,
    INDEX_FILE,
    SUPPORTED_EXTENSIONS,
    ensure_app_dir,
)


INDEX_VERSION = 6


@dataclass
class IndexResult:
    total_files: int
    indexed_names: int
    duplicates: int
    source_dirs: int
    elapsed_seconds: float
    duplicates_log: str | None


def normalize_key(value: str) -> str:
    return value.strip().casefold()


def image_key(estampa: str, arquivo: str) -> str:
    """Cria a chave da estrutura Estampa/arquivo."""
    return "\u0000".join(normalize_key(part) for part in (estampa, arquivo))


def design_id_from_folder(folder_name: str) -> str:
    """Extrai o código inicial de uma pasta de estampa com descrição opcional."""
    return folder_name.strip().split(maxsplit=1)[0]


def normalize_source_dirs(source_dirs: Path | list[Path]) -> list[Path]:
    sources = [source_dirs] if isinstance(source_dirs, Path) else source_dirs
    normalized: list[Path] = []
    seen: set[Path] = set()
    for source in sources:
        resolved = Path(source).expanduser().resolve()
        if resolved not in seen:
            normalized.append(resolved)
            seen.add(resolved)
    return normalized


def build_index(
    source_dirs: Path | list[Path],
    progress_callback: Callable[[int, str], None] | None = None,
) -> tuple[dict[str, list[str]], IndexResult]:
    sources = normalize_source_dirs(source_dirs)
    if not sources:
        raise ValueError("Selecione pelo menos uma pasta de entrada.")
    invalid_sources = [
        str(source) for source in sources
        if not source.exists() or not source.is_dir()
    ]
    if invalid_sources:
        raise ValueError(
            "Estas pastas de entrada não existem ou não são válidas: "
            + " | ".join(invalid_sources)
        )

    started = time.time()
    index: dict[str, list[str]] = {}
    total_files = 0

    for source_dir in sources:
        # rglob permite localizar imagens em qualquer nível de subpasta.
        for path in source_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.casefold() not in SUPPORTED_EXTENSIONS:
                continue

            total_files += 1
            # As pastas anteriores são livres; só Estampa/arquivo é relevante.
            relative = path.relative_to(source_dir)
            if len(relative.parts) < 2:
                continue

            estampa = design_id_from_folder(relative.parts[-2])
            key = image_key(estampa, path.stem)
            index.setdefault(key, []).append(str(path.resolve()))

            if progress_callback and total_files % 250 == 0:
                progress_callback(
                    total_files,
                    f"Indexando: {total_files:,} imagens encontradas",
                )

    duplicates = sum(1 for paths in index.values() if len(paths) > 1)
    duplicate_items = [
        (key, paths) for key, paths in index.items() if len(paths) > 1
    ]
    payload = {
        "version": INDEX_VERSION,
        "source_dirs": [str(source) for source in sources],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "index": index,
    }

    ensure_app_dir()
    INDEX_FILE.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8"
    )
    duplicates_log: str | None = None
    if duplicate_items:
        log_lines = [
            "DUPLICIDADES ENCONTRADAS NA CRIAÇÃO DO ÍNDICE",
            f"Data: {payload['created_at']}",
            f"Grupos duplicados: {duplicates}",
            "",
        ]
        for key, paths in sorted(duplicate_items):
            estampa, arquivo = key.split("\u0000", maxsplit=1)
            log_lines.append(f"Estampa: {estampa} | Arquivo: {arquivo}")
            log_lines.extend(f"  - {path}" for path in paths)
            log_lines.append("")
        DUPLICATES_LOG_FILE.write_text(
            "\n".join(log_lines),
            encoding="utf-8",
        )
        duplicates_log = str(DUPLICATES_LOG_FILE.resolve())
    elif DUPLICATES_LOG_FILE.exists():
        DUPLICATES_LOG_FILE.unlink()

    result = IndexResult(
        total_files=total_files,
        indexed_names=len(index),
        duplicates=duplicates,
        source_dirs=len(sources),
        elapsed_seconds=time.time() - started,
        duplicates_log=duplicates_log,
    )
    return index, result


def load_index(source_dirs: Path | list[Path] | None = None) -> dict[str, list[str]]:
    if not INDEX_FILE.exists():
        return {}

    try:
        payload = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        if payload.get("version") != INDEX_VERSION:
            return {}
        if source_dirs is not None:
            requested_sources = normalize_source_dirs(source_dirs)
            saved_sources = [
                Path(source).resolve()
                for source in payload.get("source_dirs", [])
            ]
            if saved_sources != requested_sources:
                return {}
        return payload.get("index", {})
    except Exception:
        return {}
