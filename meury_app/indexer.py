from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import json
import time

from .config import INDEX_FILE, SUPPORTED_EXTENSIONS, ensure_app_dir


INDEX_VERSION = 2


@dataclass
class IndexResult:
    total_files: int
    indexed_names: int
    duplicates: int
    elapsed_seconds: float


def normalize_key(value: str) -> str:
    return value.strip().casefold()


def image_key(cliente: str, estampa: str, arquivo: str) -> str:
    """Cria a chave da estrutura Cliente/Estampa/arquivo."""
    return "\u0000".join(normalize_key(part) for part in (cliente, estampa, arquivo))


def build_index(
    source_dir: Path,
    progress_callback: Callable[[int, str], None] | None = None,
) -> tuple[dict[str, list[str]], IndexResult]:
    if not source_dir.exists() or not source_dir.is_dir():
        raise ValueError("A pasta de entrada não existe ou não é uma pasta válida.")

    started = time.time()
    index: dict[str, list[str]] = {}
    total_files = 0

    # rglob permite localizar imagens em qualquer nível de subpasta.
    for path in source_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.casefold() not in SUPPORTED_EXTENSIONS:
            continue

        total_files += 1
        # Só indexa imagens na estrutura Cliente/Estampa/Estampa-Variante.ext.
        try:
            relative = path.relative_to(source_dir)
        except ValueError:
            continue
        if len(relative.parts) < 3:
            continue

        cliente = relative.parts[-3]
        estampa = relative.parts[-2]
        key = image_key(cliente, estampa, path.stem)
        index.setdefault(key, []).append(str(path.resolve()))

        if progress_callback and total_files % 250 == 0:
            progress_callback(total_files, f"Indexando: {total_files:,} imagens encontradas")

    duplicates = sum(1 for paths in index.values() if len(paths) > 1)
    payload = {
        "version": INDEX_VERSION,
        "source_dir": str(source_dir.resolve()),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "index": index,
    }

    ensure_app_dir()
    INDEX_FILE.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8"
    )

    result = IndexResult(
        total_files=total_files,
        indexed_names=len(index),
        duplicates=duplicates,
        elapsed_seconds=time.time() - started,
    )
    return index, result


def load_index(source_dir: Path | None = None) -> dict[str, list[str]]:
    if not INDEX_FILE.exists():
        return {}

    try:
        payload = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        if payload.get("version") != INDEX_VERSION:
            return {}
        if source_dir is not None:
            saved_source = Path(payload.get("source_dir", "")).resolve()
            if saved_source != source_dir.resolve():
                return {}
        return payload.get("index", {})
    except Exception:
        return {}
