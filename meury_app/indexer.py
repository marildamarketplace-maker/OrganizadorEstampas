from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
import json
import os
import tempfile
import time

from .config import (
    ANALYSIS_RESULTS_FILE,
    DUPLICATES_LOG_FILE,
    INDEX_FILE,
    LEGACY_INDEX_FILE,
    SUPPORTED_EXTENSIONS,
    ensure_app_dir,
)


INDEX_VERSION = 7
STRUCTURAL_FIELDS = {
    "type", "source", "relative_path", "path", "filename", "design_id", "key",
    "size", "mtime_ns", "active", "missing_since", "analysis_stale",
}


@dataclass
class IndexResult:
    total_files: int
    indexed_names: int
    duplicates: int
    source_dirs: int
    elapsed_seconds: float
    duplicates_log: str | None


@dataclass
class IncrementalIndexResult:
    scanned_files: int
    added_files: int
    indexed_names: int
    duplicates: int
    source_dirs: int
    elapsed_seconds: float
    duplicates_log: str | None
    removed_files: int = 0
    moved_files: int = 0
    changed_files: int = 0
    unchanged_files: int = 0


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
    seen: set[str] = set()
    for source in sources:
        resolved = Path(source).expanduser().resolve()
        identity = os.path.normcase(str(resolved)).casefold()
        if identity not in seen:
            normalized.append(resolved)
            seen.add(identity)
    return normalized


def validate_source_dirs(source_dirs: Path | list[Path]) -> list[Path]:
    sources = normalize_source_dirs(source_dirs)
    if not sources:
        raise ValueError("Selecione pelo menos uma pasta de entrada.")
    invalid_sources = [str(source) for source in sources if not source.is_dir()]
    if invalid_sources:
        raise ValueError(
            "Estas pastas de entrada não existem ou não são válidas: "
            + " | ".join(invalid_sources)
        )
    return sources


def _default_metadata() -> dict:
    return {
        "keywords": [], "description": "", "colors": [], "elements": [],
        "themes": [], "category": "", "processed": False,
    }


def _record_identity(record: dict) -> tuple[int, str]:
    return (
        int(record.get("source", 0)),
        str(record.get("relative_path", "")).replace("\\", "/").casefold(),
    )


def _signature(record: dict) -> tuple[int, int]:
    return int(record.get("size", -1)), int(record.get("mtime_ns", -1))


def _copy_metadata(previous: dict, current: dict) -> None:
    """Preserva campos atuais e futuros que não descrevem o arquivo físico."""
    defaults = _default_metadata()
    for field, value in previous.items():
        if field not in STRUCTURAL_FIELDS:
            current[field] = value
    for field, value in defaults.items():
        current.setdefault(field, value)


def _iter_source_files(source: Path) -> Iterator[Path]:
    """Percorre sem seguir links, evitando ciclos e com baixo uso de memória."""
    pending = [source]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif (
                            entry.is_file(follow_symlinks=False)
                            and Path(entry.name).suffix.casefold() in SUPPORTED_EXTENSIONS
                        ):
                            yield Path(entry.path)
                    except OSError:
                        continue
        except OSError as exc:
            raise OSError(f"Não foi possível ler a pasta de estampas: {directory}") from exc


def _make_record(path: Path, source: Path, source_number: int) -> dict | None:
    relative = path.relative_to(source)
    if len(relative.parts) < 2:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    design_id = design_id_from_folder(relative.parts[-2])
    record = {
        "type": "image", "source": source_number,
        "relative_path": relative.as_posix(), "path": str(path.resolve()),
        "filename": path.name, "design_id": design_id,
        "key": image_key(design_id, path.stem), "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns, "active": True,
    }
    record.update(_default_metadata())
    return record


def _read_jsonl(path: Path) -> tuple[dict, list[dict]] | None:
    try:
        with path.open(encoding="utf-8") as stream:
            header = json.loads(next(stream))
            if header.get("type") != "catalog" or header.get("version") != INDEX_VERSION:
                return None
            records = []
            for line in stream:
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("type") == "image":
                    for field, value in _default_metadata().items():
                        record.setdefault(field, value)
                    record.setdefault("active", True)
                    records.append(record)
            return header, records
    except (OSError, StopIteration, json.JSONDecodeError, TypeError, ValueError):
        return None


def _apply_analysis_results(records: list[dict]) -> None:
    """Aplica o diário incremental sem exigir a regravação do catálogo principal."""
    if not ANALYSIS_RESULTS_FILE.exists():
        return
    by_identity = {_record_identity(record): record for record in records}
    try:
        with ANALYSIS_RESULTS_FILE.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                    identity = (
                        int(event["source"]),
                        str(event["relative_path"]).replace("\\", "/").casefold(),
                    )
                    record = by_identity.get(identity)
                    if record is None:
                        continue
                    for field, value in event.get("metadata", {}).items():
                        if field not in STRUCTURAL_FIELDS:
                            record[field] = value
                    if event.get("status") == "success":
                        record.pop("analysis_error", None)
                    elif event.get("error"):
                        record["analysis_error"] = event["error"]
                        record["analysis_error_at"] = event.get("created_at", "")
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
    except OSError:
        return


def _read_legacy_index() -> tuple[dict, list[dict]] | None:
    if not LEGACY_INDEX_FILE.exists():
        return None
    try:
        payload = json.loads(LEGACY_INDEX_FILE.read_text(encoding="utf-8"))
        records = []
        sources = [Path(value).resolve() for value in payload.get("source_dirs", [])]
        for key, paths in payload.get("index", {}).items():
            design_id = key.split("\u0000", 1)[0]
            for raw_path in paths:
                path = Path(raw_path)
                source_number = next(
                    (number for number, source in enumerate(sources)
                     if path == source or source in path.parents),
                    0,
                )
                source = sources[source_number] if sources else path.parent
                try:
                    relative = path.relative_to(source).as_posix()
                    stat = path.stat()
                    size, mtime_ns = stat.st_size, stat.st_mtime_ns
                except (OSError, ValueError):
                    relative, size, mtime_ns = path.name, -1, -1
                record = {
                    "type": "image", "source": source_number,
                    "relative_path": relative, "path": str(path),
                    "filename": path.name, "design_id": design_id, "key": key,
                    "size": size, "mtime_ns": mtime_ns, "active": path.exists(),
                }
                record.update(_default_metadata())
                records.append(record)
        return {"source_dirs": [str(value) for value in sources]}, records
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _load_catalog(source_dirs: list[Path] | None = None) -> tuple[dict, list[dict]] | None:
    loaded = _read_jsonl(INDEX_FILE) if INDEX_FILE.exists() else None
    if loaded is None and INDEX_FILE.parent == LEGACY_INDEX_FILE.parent:
        loaded = _read_legacy_index()
    if loaded is None:
        return None
    header, records = loaded
    _apply_analysis_results(records)
    if source_dirs is not None:
        saved_sources = header.get("source_dirs", [])
        # Caminhos relativos permitem troca da letra/unidade do HD. A ordem das
        # raízes configuradas funciona como identidade local simples.
        if len(saved_sources) != len(source_dirs):
            return None
        for record in records:
            source_number = int(record.get("source", 0))
            if source_number >= len(source_dirs):
                return None
            relative = str(record.get("relative_path", "")).replace("/", os.sep)
            record["path"] = str((source_dirs[source_number] / relative).resolve())
    return header, records


def _index_from_records(records: list[dict]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for record in records:
        if record.get("active", True):
            index.setdefault(record["key"], []).append(record["path"])
    for paths in index.values():
        paths.sort(key=str.casefold)
    return index


def _write_catalog(records: list[dict], sources: list[Path]) -> None:
    ensure_app_dir()
    header = {
        "type": "catalog", "version": INDEX_VERSION,
        "source_dirs": [str(source) for source in sources],
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", delete=False,
            dir=INDEX_FILE.parent, prefix=INDEX_FILE.name + ".", suffix=".tmp",
        ) as stream:
            temporary_name = stream.name
            stream.write(json.dumps(header, ensure_ascii=False) + "\n")
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, INDEX_FILE)
        # Os eventos já foram incorporados aos registros escritos acima.
        ANALYSIS_RESULTS_FILE.unlink(missing_ok=True)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def _write_duplicates(index: dict[str, list[str]]) -> str | None:
    duplicate_items = [(key, paths) for key, paths in index.items() if len(paths) > 1]
    if not duplicate_items:
        DUPLICATES_LOG_FILE.unlink(missing_ok=True)
        return None
    lines = [
        "DUPLICIDADES ENCONTRADAS NO ÍNDICE",
        f"Grupos duplicados: {len(duplicate_items)}", "",
    ]
    for key, paths in sorted(duplicate_items):
        design, filename = key.split("\u0000", 1)
        lines.append(f"Estampa: {design} | Arquivo: {filename}")
        lines.extend(f"  - {path}" for path in paths)
        lines.append("")
    DUPLICATES_LOG_FILE.write_text("\n".join(lines), encoding="utf-8")
    return str(DUPLICATES_LOG_FILE.resolve())


def save_index(index: dict[str, list[str]], sources: list[Path]) -> str | None:
    """Compatibilidade para consumidores antigos que fornecem um dicionário."""
    records = []
    for key, paths in index.items():
        for raw_path in paths:
            path = Path(raw_path)
            source_number = next(
                (i for i, root in enumerate(sources) if root in path.parents), 0
            )
            record = _make_record(path, sources[source_number], source_number)
            if record:
                record["key"] = key
                records.append(record)
    _write_catalog(records, sources)
    return _write_duplicates(index)


def load_index_payload(source_dirs: Path | list[Path] | None = None) -> dict | None:
    sources = normalize_source_dirs(source_dirs) if source_dirs is not None else None
    loaded = _load_catalog(sources)
    if loaded is None:
        return None
    header, records = loaded
    return {
        **header, "version": INDEX_VERSION, "index": _index_from_records(records),
        "records": records,
    }


def load_catalog_records(
    source_dirs: Path | list[Path] | None = None,
) -> list[dict] | None:
    """Carrega registros sem construir o dicionário de busca usado por pedidos."""
    sources = normalize_source_dirs(source_dirs) if source_dirs is not None else None
    loaded = _load_catalog(sources)
    return loaded[1] if loaded is not None else None


def index_catalog_available(source_dirs: Path | list[Path]) -> bool:
    """Validação leve para inicialização da GUI; lê apenas o cabeçalho JSONL."""
    sources = normalize_source_dirs(source_dirs)
    if not INDEX_FILE.exists():
        return LEGACY_INDEX_FILE.exists()
    try:
        with INDEX_FILE.open(encoding="utf-8") as stream:
            header = json.loads(next(stream))
        return (
            header.get("type") == "catalog"
            and header.get("version") == INDEX_VERSION
            and len(header.get("source_dirs", [])) == len(sources)
        )
    except (OSError, StopIteration, json.JSONDecodeError, TypeError):
        return False


def _scan_and_merge(
    sources: list[Path], old_records: list[dict], progress_callback=None,
) -> tuple[list[dict], dict, int]:
    """Varre o disco e mescla em fluxo, sem manter dois catálogos completos.

    Registros existentes são atualizados no próprio objeto. Assim, uma
    atualização de 195 mil arquivos não cria simultaneamente as listas
    ``anterior``, ``atual`` e ``mesclada`` com dicionários independentes.
    """
    old_by_path = {_record_identity(record): record for record in old_records}
    matched, new_records, removed = [], [], []
    seen_paths: set[str] = set()
    scanned = 0
    stats = {"added": 0, "removed": 0, "moved": 0, "changed": 0, "unchanged": 0}
    for source_number, source in enumerate(sources):
        for path in _iter_source_files(source):
            path_identity = os.path.normcase(str(path.resolve())).casefold()
            if path_identity in seen_paths:
                continue
            seen_paths.add(path_identity)
            record = _make_record(path, source, source_number)
            if record is None:
                continue
            scanned += 1
            old = old_by_path.pop(_record_identity(record), None)
            if old is None:
                new_records.append(record)
            else:
                old_signature = _signature(old)
                metadata = {
                    key: value for key, value in old.items()
                    if key not in STRUCTURAL_FIELDS
                }
                old.clear()
                old.update(record)
                old.update(metadata)
                if old_signature == _signature(record):
                    stats["unchanged"] += 1
                else:
                    old["processed"] = False
                    old["analysis_stale"] = True
                    stats["changed"] += 1
                matched.append(old)
            if progress_callback and scanned % 250 == 0:
                progress_callback(scanned, f"Verificando: {scanned:,} imagens")

    for old in old_by_path.values():
        if old.get("active", True):
            removed.append(old)
        else:
            matched.append(old)

    removed_by_signature: dict[tuple[int, int], list[dict]] = {}
    new_by_signature: dict[tuple[int, int], list[dict]] = {}
    for record in removed:
        removed_by_signature.setdefault(_signature(record), []).append(record)
    for record in new_records:
        new_by_signature.setdefault(_signature(record), []).append(record)

    moved_new_ids, moved_old_ids = set(), set()
    for signature, candidates in new_by_signature.items():
        previous = removed_by_signature.get(signature, [])
        if signature != (-1, -1) and len(candidates) == len(previous) == 1:
            new, old = candidates[0], previous[0]
            _copy_metadata(old, new)
            matched.append(new)
            moved_new_ids.add(id(new))
            moved_old_ids.add(id(old))
            stats["moved"] += 1

    for record in new_records:
        if id(record) not in moved_new_ids:
            matched.append(record)
            stats["added"] += 1
    for record in removed:
        if id(record) not in moved_old_ids:
            record["active"] = False
            record["missing_since"] = time.strftime("%Y-%m-%d %H:%M:%S")
            matched.append(record)
            stats["removed"] += 1
    return matched, stats, scanned


def build_index(source_dirs, progress_callback=None) -> tuple[dict[str, list[str]], IndexResult]:
    sources = validate_source_dirs(source_dirs)
    started = time.time()
    previous = _load_catalog(sources)
    records, _stats, scanned = _scan_and_merge(
        sources, previous[1] if previous else [], progress_callback
    )
    index = _index_from_records(records)
    _write_catalog(records, sources)
    duplicates_log = _write_duplicates(index)
    return index, IndexResult(
        scanned, len(index), sum(len(value) > 1 for value in index.values()),
        len(sources), time.time() - started, duplicates_log,
    )


def update_index_incremental(source_dirs, progress_callback=None):
    sources = validate_source_dirs(source_dirs)
    previous = _load_catalog(sources)
    if previous is None:
        raise ValueError(
            "Ainda não existe um índice completo para estas pastas. "
            "Clique primeiro em Atualizar índice completo."
        )
    started = time.time()
    records, stats, scanned = _scan_and_merge(sources, previous[1], progress_callback)
    index = _index_from_records(records)
    _write_catalog(records, sources)
    duplicates_log = _write_duplicates(index)
    result = IncrementalIndexResult(
        scanned_files=scanned, added_files=stats["added"],
        indexed_names=len(index), duplicates=sum(len(value) > 1 for value in index.values()),
        source_dirs=len(sources), elapsed_seconds=time.time() - started,
        duplicates_log=duplicates_log, removed_files=stats["removed"],
        moved_files=stats["moved"], changed_files=stats["changed"],
        unchanged_files=stats["unchanged"],
    )
    return index, result


def load_index(source_dirs: Path | list[Path] | None = None) -> dict[str, list[str]]:
    payload = load_index_payload(source_dirs)
    return payload.get("index", {}) if payload is not None else {}


def pending_analysis_records(
    source_dirs: Path | list[Path], limit: int | None = None,
) -> tuple[list[dict], int]:
    """Retorna artes raster ativas ainda não analisadas e o total pendente."""
    sources = validate_source_dirs(source_dirs)
    records = load_catalog_records(sources)
    if records is None:
        raise ValueError("Atualize o índice antes de iniciar a análise com IA.")
    pending = [
        record for record in records
        if record.get("active", True)
        and Path(record.get("filename", "")).suffix.casefold() in {".jpg", ".jpeg", ".png"}
        and not record.get("processed", False)
    ]
    pending.sort(key=lambda record: (
        int(record.get("source", 0)), str(record.get("relative_path", "")).casefold()
    ))
    total = len(pending)
    return (pending[:limit] if limit is not None else pending), total


def append_analysis_result(
    record: dict,
    *,
    metadata: dict | None = None,
    error: str | None = None,
) -> None:
    """Persiste imediatamente um resultado individual em um diário append-only."""
    ensure_app_dir()
    clean_metadata = {
        key: value for key, value in (metadata or {}).items()
        if key not in STRUCTURAL_FIELDS
    }
    event = {
        "type": "analysis_result", "version": 1,
        "source": int(record.get("source", 0)),
        "relative_path": str(record.get("relative_path", "")),
        "status": "error" if error else "success",
        "metadata": clean_metadata,
        "error": str(error or ""),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with ANALYSIS_RESULTS_FILE.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
