from __future__ import annotations

from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Iterator
import json
import hashlib
import logging
import os
import re
import tempfile
import time
import warnings
import uuid

from .config import (
    ANALYSIS_RESULTS_FILE,
    DUPLICATES_LOG_FILE,
    INDEX_FILE,
    IMAGE_LIKE_EXTENSIONS,
    LEGACY_INDEX_FILE,
    OPERATIONAL_DB_FILE,
    SUPPORTED_EXTENSIONS,
    ensure_app_dir,
    join_indexed_image_path,
)
from .asset_identity import relative_asset_identity
from .index_progress import IndexProgress
from .index_diagnostics import ACTIVE_METRICS, count, measured, new_metrics, stage
from .index_journal import CandidateJournal, catalog_lock, physical_signature
from .index_run_state import record_index_update, state_file_for
from .operational_store import (
    overlay_records, record_quarantine_issues, record_scan_summary, sync_records,
)


INDEX_VERSION = 9
COMPATIBLE_INDEX_VERSIONS = {7, 8, INDEX_VERSION}
STRUCTURAL_FIELDS = {
    "type", "source", "relative_path", "path", "filename", "design_id", "key",
    "asset_id",
    "size", "mtime_ns", "active", "missing_since", "analysis_stale",
    "codigo", "variante", "content_hash", "indexed", "changed",
    "preview_status", "preview_path", "cloud_status", "storage_key", "preview_url",
    "supabase_status", "missing_locally", "last_error", "last_indexed_at",
    "last_synced_at", "scan_status", "processing_status",
    "missing_detected_at",
    "review_required", "review_reason",
    "attention_status", "attention_reason", "attention_at",
    "preview_content_hash", "cloud_content_hash", "supabase_content_hash",
    "preview_attempts", "preview_last_error", "preview_last_attempt_at",
    "cloud_attempts", "cloud_last_error", "cloud_last_attempt_at",
    "supabase_attempts", "supabase_last_error", "supabase_last_attempt_at",
}
PERSISTED_STATE_FIELDS = {
    "asset_id", "content_hash", "preview_status", "preview_path", "cloud_status",
    "storage_key", "preview_url", "supabase_status", "last_error",
    "last_synced_at", "processing_status", "missing_detected_at",
    "review_required", "review_reason",
    "attention_status", "attention_reason", "attention_at",
    "preview_content_hash", "cloud_content_hash", "supabase_content_hash",
    "preview_attempts", "preview_last_error", "preview_last_attempt_at",
    "cloud_attempts", "cloud_last_error", "cloud_last_attempt_at",
    "supabase_attempts", "supabase_last_error", "supabase_last_attempt_at",
}


@dataclass
class IndexResult:
    total_files: int
    indexed_names: int
    duplicates: int
    source_dirs: int
    elapsed_seconds: float
    duplicates_log: str | None
    performance: dict = field(default_factory=dict)


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
    verification_files: int = 0
    hashed_files: int = 0
    errors: int = 0
    review_files: int = 0
    performance: dict = field(default_factory=dict)

    @property
    def total_found(self) -> int:
        return self.scanned_files

    @property
    def absent_files(self) -> int:
        return self.removed_files


def normalize_key(value: str) -> str:
    return value.strip().casefold()


def image_key(estampa: str, arquivo: str) -> str:
    """Cria a chave da estrutura Estampa/arquivo."""
    return "\u0000".join(normalize_key(part) for part in (estampa, arquivo))


def design_id_from_folder(folder_name: str) -> str:
    """Extrai o código inicial de uma pasta de estampa com descrição opcional."""
    return folder_name.strip().split(maxsplit=1)[0]


def design_id_from_filename(filename: str) -> str:
    """Extrai um código de estampa do começo do nome do arquivo.

    Alguns acervos de clientes usam pastas organizacionais (por exemplo,
    ``wellington``) em vez de uma pasta com o código da estampa.  Nesses casos,
    aceita códigos que tenham pelo menos um número, como ``MV1972`` ou ``20386``.
    """
    stem = Path(filename).stem.strip()
    match = re.match(r"^([A-Za-z]*\d+(?:-\d+)*)", stem)
    return match.group(1) if match else ""


def inferred_design_id(folder_name: str, filename: str) -> str:
    """Prefere o código da pasta, usando o nome quando a pasta é organizacional."""
    folder_id = design_id_from_folder(folder_name)
    if variant_from_filename(folder_id, filename):
        return folder_id
    return design_id_from_filename(filename) or folder_id


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


def _default_analysis_metadata() -> dict:
    return {
        "keywords": [], "description": "", "colors": [], "elements": [],
        "themes": [], "category": "", "processed": False,
    }


def variant_from_filename(design_id: str, filename: str) -> str:
    """Infere a variante sem alterar a chave legada usada pelos pedidos."""
    stem = Path(filename).stem.strip()
    if stem.casefold() == design_id.casefold():
        return "A"
    # Medidas e descrições após o código não representam uma variante. Exemplo:
    # ``MV25560 200x145.jpg`` é a variante A de MV25560.
    if (
        stem.casefold().startswith(design_id.casefold())
        and len(stem) > len(design_id)
        and stem[len(design_id)].isspace()
    ):
        return "A"
    prefix = design_id + "-"
    if not stem.casefold().startswith(prefix.casefold()):
        return ""
    remainder = stem[len(prefix):].strip()
    return remainder.split(maxsplit=1)[0].split("-", 1)[0].upper() if remainder else ""


def _state_metadata(
    *, design_id: str = "", filename: str = "", indexed_at: str = "",
    scan_status: str = "new",
) -> dict:
    return {
        "codigo": design_id,
        "variante": variant_from_filename(design_id, filename) if design_id else "",
        # Reservado para uma etapa futura de hashing; vazio significa "não calculado".
        "content_hash": "",
        "indexed": True,
        "changed": False,
        "scan_status": scan_status,
        "processing_status": "pending" if scan_status == "new" else "indexed",
        "preview_status": "pending",
        "preview_path": "",
        "preview_content_hash": "",
        "cloud_status": "pending",
        "storage_key": "",
        "preview_url": "",
        "cloud_content_hash": "",
        "supabase_status": "pending",
        "supabase_content_hash": "",
        "preview_attempts": 0, "preview_last_error": "", "preview_last_attempt_at": "",
        "cloud_attempts": 0, "cloud_last_error": "", "cloud_last_attempt_at": "",
        "supabase_attempts": 0, "supabase_last_error": "", "supabase_last_attempt_at": "",
        "missing_locally": False,
        "missing_detected_at": "",
        "review_required": False,
        "review_reason": "",
        "attention_status": "",
        "attention_reason": "",
        "attention_at": "",
        "last_error": "",
        "last_indexed_at": indexed_at,
        "last_synced_at": "",
    }


def _ensure_record_defaults(record: dict) -> None:
    for field, value in _default_analysis_metadata().items():
        record.setdefault(field, value)
    state = _state_metadata(
        design_id=str(record.get("design_id", "")),
        filename=str(record.get("filename", "")),
        scan_status="indexed",
    )
    for field, value in state.items():
        record.setdefault(field, value)
    record.setdefault("active", True)
    record["missing_locally"] = not bool(record.get("active", True))
    record.setdefault("asset_id", relative_asset_identity(record))


def _record_identity(record: dict) -> tuple[int, str]:
    return (
        int(record.get("source", 0)),
        str(record.get("relative_path", "")).replace("\\", "/").casefold(),
    )


def _signature(record: dict) -> tuple[int, int]:
    return int(record.get("size", -1)), int(record.get("mtime_ns", -1))


@measured("hashing")
def calculate_content_hash(path: Path, block_size: int = 1024 * 1024) -> str:
    """Calcula SHA-256 em fluxo somente para candidatos novos ou alterados."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(block_size), b""):
            digest.update(block)
            count("hash_bytes_read", len(block))
    count("hash_files")
    return digest.hexdigest()


def _copy_metadata(previous: dict, current: dict) -> None:
    """Preserva campos atuais e futuros que não descrevem o arquivo físico."""
    defaults = _default_analysis_metadata()
    for field, value in previous.items():
        if field not in STRUCTURAL_FIELDS or field in PERSISTED_STATE_FIELDS:
            current[field] = value
    for field, value in defaults.items():
        current.setdefault(field, value)


def _iter_source_files(source: Path, issue_callback=None, *, with_stat=False) -> Iterator:
    """Percorre sem seguir links, evitando ciclos e com baixo uso de memória."""
    pending = [source]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                count("directories_read")
                for entry in entries:
                    count("entries_read")
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            suffix = Path(entry.name).suffix.casefold()
                            if suffix in SUPPORTED_EXTENSIONS:
                                if with_stat:
                                    count("entry_stat_calls")
                                    yield Path(entry.path), entry.stat(follow_symlinks=False)
                                else:
                                    yield Path(entry.path)
                            elif suffix in IMAGE_LIKE_EXTENSIONS and issue_callback:
                                issue_callback(
                                    Path(entry.path), "UNSUPPORTED_FORMAT",
                                    f"Extensão não suportada: {suffix or '(sem extensão)'}",
                                )
                    except ValueError:
                        # Alguns compartilhamentos de rede podem expor uma entrada
                        # com nome inválido para pathlib (por exemplo, "."). Ela
                        # não representa uma imagem e não deve interromper o scan.
                        continue
                    except OSError as exc:
                        if issue_callback:
                            issue_callback(Path(entry.path), "INACCESSIBLE_FILE", str(exc))
                        raise OSError(f"Varredura incompleta: {entry.path}") from exc
        except ValueError:
            raise ValueError(f"Diretório inválido durante a varredura: {directory}")
        except OSError as exc:
            raise OSError(f"Não foi possível ler a pasta de estampas: {directory}") from exc


@measured("validation")
def _validate_file_content(path: Path) -> tuple[str, str] | None:
    """Valida somente candidatos novos/alterados, preservando o Fast Scan."""
    count("validation_files")
    try:
        if path.suffix.casefold() == ".pdf":
            with path.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    return "CORRUPTED_FILE", "O arquivo não possui cabeçalho PDF válido."
        else:
            from PIL import Image
            # Silencia apenas o aviso de dimensões nesta operação. O limite de
            # erro e a detecção de arquivos corrompidos continuam ativos.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    image.verify()
    except Exception as exc:
        return "CORRUPTED_FILE", f"{type(exc).__name__}: {exc}"
    return None


def _make_record(
    path: Path, source: Path, source_number: int, *, stat_result=None,
) -> dict | None:
    relative = path.relative_to(source)
    if len(relative.parts) < 2:
        return None
    if stat_result is None:
        try:
            stat_result = path.stat()
        except OSError:
            return None
    design_id = inferred_design_id(relative.parts[-2], path.name)
    record = {
        "type": "image", "source": source_number,
        "relative_path": relative.as_posix(), "path": os.path.abspath(os.fspath(path)),
        "filename": path.name, "design_id": design_id,
        "key": image_key(design_id, path.stem), "size": stat_result.st_size,
        "mtime_ns": stat_result.st_mtime_ns, "active": True,
    }
    record.update(_default_analysis_metadata())
    record.update(_state_metadata(
        design_id=design_id,
        filename=path.name,
        indexed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    ))
    record["asset_id"] = relative_asset_identity(record)
    return record


@measured("catalog_read")
def _read_jsonl(path: Path, progress: IndexProgress | None = None) -> tuple[dict, list[dict]] | None:
    try:
        with path.open(encoding="utf-8") as stream:
            count("catalog_bytes_read", path.stat().st_size)
            header = json.loads(next(stream))
            if (
                header.get("type") != "catalog"
                or header.get("version") not in COMPATIBLE_INDEX_VERSIONS
            ):
                return None
            records = []
            for line in stream:
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("type") == "image":
                    _ensure_record_defaults(record)
                    records.append(record)
                    count("catalog_records_read")
                    if progress:
                        progress.report("Carregando catálogo", len(records), header.get("record_count"))
            return header, records
    except (OSError, StopIteration, json.JSONDecodeError, TypeError, ValueError):
        return None


@measured("analysis_overlay")
def _apply_analysis_results(records: list[dict]) -> None:
    """Aplica o diário incremental sem exigir a regravação do catálogo principal."""
    analysis_file = _analysis_results_path()
    if not analysis_file.exists():
        return
    by_identity = {_record_identity(record): record for record in records}
    try:
        with analysis_file.open(encoding="utf-8") as stream:
            for line in stream:
                count("analysis_events_read")
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
                record.update(_default_analysis_metadata())
                record.update(_state_metadata(
                    design_id=design_id,
                    filename=path.name,
                    indexed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                ))
                records.append(record)
        return {"source_dirs": [str(value) for value in sources]}, records
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


@measured("load_total")
def _load_catalog(source_dirs: list[Path] | None = None,
                  progress: IndexProgress | None = None, *, for_scan=False) -> tuple[dict, list[dict]] | None:
    if progress:
        progress.report("Carregando catálogo")
    loaded = _read_jsonl(INDEX_FILE, progress) if INDEX_FILE.exists() else None
    if loaded is None and INDEX_FILE.parent == LEGACY_INDEX_FILE.parent:
        loaded = _read_legacy_index()
    if loaded is None:
        return None
    header, records = loaded
    _recover_catalog_commit(header, records)
    _apply_analysis_results(records)
    with stage("operational_overlay"):
        overlay_records(_operational_db_path(), records, progress=progress)
    if source_dirs is not None:
        saved_sources = header.get("source_dirs", [])
        # Caminhos relativos permitem troca da letra/unidade do HD. A ordem das
        # raízes configuradas funciona como identidade local simples.
        if len(saved_sources) != len(source_dirs):
            return None
        with stage("path_resolution"):
            for current, record in enumerate(records, 1):
                source_number = int(record.get("source", 0))
                if not 0 <= source_number < len(source_dirs):
                    return None
                record["path"] = str(join_indexed_image_path(
                    record.get("relative_path", ""),
                    root=source_dirs[source_number],
                ))
                if progress:
                    progress.report("Montando caminhos", current, len(records))
    return header, records


def _operational_db_path() -> Path:
    """Mantém testes/catálogos alternativos isolados do banco real do usuário."""
    if INDEX_FILE.parent == OPERATIONAL_DB_FILE.parent:
        return OPERATIONAL_DB_FILE
    return INDEX_FILE.with_name(OPERATIONAL_DB_FILE.name)


def _analysis_results_path() -> Path:
    """Catálogos alternativos nunca leem/apagam o diário do catálogo real."""
    return INDEX_FILE.with_name(ANALYSIS_RESULTS_FILE.name)


class ImageIndex(dict[str, list[str]]):
    """Índice de busca que preserva as raízes usadas para validar cópias."""

    def __init__(self, *args, source_dirs: list[Path] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_dirs = tuple(source_dirs or ())


@measured("lookup_build")
def _index_from_records(
    records: list[dict], source_dirs: list[Path] | None = None,
) -> ImageIndex:
    index = ImageIndex(source_dirs=source_dirs)
    for record in records:
        if record.get("active", True):
            index.setdefault(record["key"], []).append(record["path"])
    for paths in index.values():
        paths.sort(key=str.casefold)
    return index


@measured("catalog_commit")
def _write_catalog(
    records: list[dict], sources: list[Path], *, operational_records: list[dict] | None = None,
    progress: IndexProgress | None = None,
) -> None:
    ensure_app_dir()
    header = {
        "type": "catalog", "version": INDEX_VERSION,
        "commit_id": uuid.uuid4().hex,
        # A raiz física pertence à configuração, nunca à identidade persistida.
        "source_dirs": [str(number) for number, _source in enumerate(sources)],
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "record_count": len(records),
    }
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", delete=False,
            dir=INDEX_FILE.parent, prefix=INDEX_FILE.name + ".", suffix=".tmp",
        ) as stream:
            temporary_name = stream.name
            stream.write(json.dumps(header, ensure_ascii=False) + "\n")
            for current, record in enumerate(records, 1):
                persisted = dict(record)
                persisted["path"] = str(record.get("relative_path", "")).replace("\\", "/")
                stream.write(json.dumps(persisted, ensure_ascii=False) + "\n")
                if progress:
                    progress.report("Gravando catálogo", current, len(records))
            stream.flush()
            os.fsync(stream.fileno())
            count("catalog_bytes_written", stream.tell())
            count("catalog_records_written", len(records))
        # WAL de publicação: se o processo cair após o replace e antes do
        # SQLite, o próximo carregamento conclui a sincronização antes do overlay.
        _write_commit_intent(header["commit_id"], operational_records)
        os.replace(temporary_name, INDEX_FILE)
        count("catalog_writes")
        with stage("operational_sync"):
            sync_records(
                _operational_db_path(),
                records if operational_records is None else operational_records,
                progress=progress,
            )
        # Os eventos já foram incorporados aos registros escritos acima.
        _analysis_results_path().unlink(missing_ok=True)
        INDEX_FILE.with_suffix(".commit.json").unlink(missing_ok=True)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def _write_commit_intent(commit_id, records):
    path = INDEX_FILE.with_suffix(".commit.json")
    payload = json.dumps({"commit_id": commit_id, "records": records}, ensure_ascii=False).encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        count("commit_intent_bytes_written", len(payload))
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


@measured("commit_recovery")
def _recover_catalog_commit(header, records):
    path = INDEX_FILE.with_suffix(".commit.json")
    if not path.exists():
        return
    intent = json.loads(path.read_text(encoding="utf-8"))
    if intent.get("commit_id") != header.get("commit_id"):
        return  # Catálogo anterior ainda publicado; nenhuma mudança a aplicar.
    pending = intent.get("records")
    sync_records(_operational_db_path(), records if pending is None else pending)
    _analysis_results_path().unlink(missing_ok=True)
    path.unlink()
    count("recovered_commits")


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
        **header, "version": INDEX_VERSION,
        "index": _index_from_records(records, sources),
        "records": records,
    }


def load_catalog_records(
    source_dirs: Path | list[Path] | None = None,
    *, progress: IndexProgress | None = None,
) -> list[dict] | None:
    """Carrega registros sem construir o dicionário de busca usado por pedidos."""
    sources = normalize_source_dirs(source_dirs) if source_dirs is not None else None
    loaded = _load_catalog(sources, progress)
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
            and header.get("version") in COMPATIBLE_INDEX_VERSIONS
            and len(header.get("source_dirs", [])) == len(sources)
        )
    except (OSError, StopIteration, json.JSONDecodeError, TypeError):
        return False


def _entries_with_metrics(source, issue_callback):
    iterator = iter(_iter_source_files(source, issue_callback, with_stat=True))
    while True:
        with stage("enumeration_metadata"):
            try:
                item = next(iterator)
            except StopIteration:
                return
        yield item


def _verify_candidate(path, stat_result, journal):
    # DirEntry no Windows não fornece dev/ino. Consulta completa só para
    # candidatos, preservando o fast path dos arquivos inalterados.
    initial = path.stat()
    count("candidate_stat_calls")
    if (initial.st_size, initial.st_mtime_ns) != (stat_result.st_size, stat_result.st_mtime_ns):
        raise OSError(f"Arquivo mudou durante a varredura; tente novamente: {path}")
    stat_result = initial
    key = os.path.normcase(os.path.abspath(path)).casefold()
    recovered = journal.lookup(key, stat_result) if journal else None
    if recovered:
        return recovered["validation"], recovered["hash"], False
    validation = _validate_file_content(path)
    content_hash = calculate_content_hash(path)
    count("candidate_stat_calls")
    if physical_signature(path.stat()) != physical_signature(stat_result):
        raise OSError(f"Arquivo mudou durante a verificação; tente novamente: {path}")
    if journal:
        journal.add(key, stat_result, validation, content_hash)
    return validation, content_hash, True


@measured("scan_total")
def _scan_and_merge(
    sources: list[Path], old_records: list[dict], progress_callback=None,
    *, progress: IndexProgress | None = None, journal=None,
) -> tuple[list[dict], dict, int, list[dict], list[dict]]:
    """Varre o disco e mescla em fluxo, sem manter dois catálogos completos.

    Registros existentes são atualizados no próprio objeto. Assim, uma
    atualização de 195 mil arquivos não cria simultaneamente as listas
    ``anterior``, ``atual`` e ``mesclada`` com dicionários independentes.
    """
    progress = progress or IndexProgress(progress_callback)
    progress.report("Verificando imagens", detail="total conhecido ao terminar a varredura")
    old_by_path = {_record_identity(record): record for record in old_records}
    matched, new_records, removed = [], [], []
    dirty_records: list[dict] = []
    quarantine_issues: list[dict] = []
    seen_paths: set[str] = set()
    scanned = 0
    stats = {
        "added": 0, "removed": 0, "moved": 0, "changed": 0,
        "unchanged": 0, "hashed": 0, "errors": 0, "review": 0,
    }

    def add_issue(source_number: int, source: Path, path: Path, reason: str, message: str):
        # Formatos conhecidos, mas fora do catálogo (por exemplo, PSD), são
        # apenas ignorados. Mantemos o registro para a interface sem poluir o
        # terminal durante uma varredura de pastas de criação.
        if reason != "UNSUPPORTED_FORMAT":
            logging.getLogger(__name__).error(
                "Falha na indexação | arquivo: %s | motivo: %s: %s", path, reason, message,
            )
        try:
            relative = path.relative_to(source).as_posix()
        except ValueError:
            relative = path.name
        quarantine_issues.append({
            "source": source_number, "relative_path": relative,
            "filename": path.name, "path": os.path.abspath(os.fspath(path)),
            "reason": reason, "technical_message": message,
            "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        stats["errors"] += 1

    def mark_attention(record: dict, reason: str, message: str):
        if (
            record.get("attention_status") == "REQUIRES_ATTENTION"
            and record.get("attention_reason") == reason
        ):
            return False
        record["attention_status"] = "REQUIRES_ATTENTION"
        record["attention_reason"] = reason
        record["attention_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        add_issue(
            int(record.get("source", 0)), sources[int(record.get("source", 0))],
            Path(record.get("path", "")), reason, message,
        )
        return True

    for source_number, source in enumerate(sources):
        issue_callback = lambda path, reason, message, sn=source_number, root=source: add_issue(
            sn, root, path, reason, message
        )
        for path, stat_result in _entries_with_metrics(source, issue_callback):
            # ``abspath`` é puramente textual. Evita o custo de ``resolve`` e de
            # consultas extras ao filesystem para cada item de catálogos grandes.
            absolute_path = os.path.abspath(os.fspath(path))
            path_identity = os.path.normcase(absolute_path).casefold()
            if path_identity in seen_paths:
                continue
            seen_paths.add(path_identity)
            try:
                relative = path.relative_to(source)
                if len(relative.parts) < 2:
                    add_issue(
                        source_number, source, path, "INVALID_PATH",
                        "O arquivo precisa estar dentro de uma pasta de estampa.",
                    )
                    continue
            except (OSError, ValueError) as exc:
                add_issue(source_number, source, path, "INACCESSIBLE_FILE", str(exc))
                raise
            scanned += 1
            identity = (source_number, relative.as_posix().casefold())
            old = old_by_path.pop(identity, None)
            was_missing = bool(old and (
                old.get("missing_locally", False) or not old.get("active", True)
            ))
            current_signature = (stat_result.st_size, stat_result.st_mtime_ns)
            if old is not None and _signature(old) == current_signature:
                # Fast path: não cria outro registro nem toca em hash, preview ou
                # estados de sincronização quando caminho, tamanho e mtime coincidem.
                old["path"] = absolute_path
                old["active"] = True
                old["missing_locally"] = False
                old.pop("missing_since", None)
                inferred_id = inferred_design_id(relative.parts[-2], path.name)
                inferred_variant = variant_from_filename(inferred_id, path.name)
                if (
                    old.get("design_id") != inferred_id
                    or old.get("codigo") != inferred_id
                    or old.get("variante") != inferred_variant
                ):
                    old["design_id"] = inferred_id
                    old["codigo"] = inferred_id
                    old["variante"] = inferred_variant
                    old["key"] = image_key(inferred_id, path.stem)
                    if old.get("attention_reason") == "VARIANT_NOT_IDENTIFIED":
                        old["attention_status"] = ""
                        old["attention_reason"] = ""
                        old["attention_at"] = ""
                    dirty_records.append(old)
                if was_missing:
                    old["scan_status"] = "reappeared"
                    old["last_indexed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    if old not in dirty_records:
                        dirty_records.append(old)
                matched.append(old)
                stats["unchanged"] += 1
                progress.report("Verificando imagens", scanned,
                                detail=f"Novas: {len(new_records):,}; inalteradas: {stats['unchanged']:,}; alteradas: {stats['changed']:,}")
                if journal:
                    journal.flush_if_needed()
                continue

            record = _make_record(
                path, source, source_number, stat_result=stat_result,
            )
            if record is None:
                continue
            if old is None and not record.get("design_id"):
                mark_attention(record, "CODE_NOT_IDENTIFIED", "Código da estampa vazio.")
            if old is None and not record.get("variante"):
                mark_attention(
                    record, "VARIANT_NOT_IDENTIFIED",
                    "A variante não pôde ser inferida pelo nome do arquivo.",
                )
            validation_error, current_hash, hashed = _verify_candidate(path, stat_result, journal)
            stats["hashed"] += int(hashed)
            if old is None and validation_error:
                mark_attention(record, validation_error[0], validation_error[1])
            if old is None:
                record["content_hash"] = current_hash
                new_records.append(record)
            else:
                previous_hash = str(old.get("content_hash", ""))
                metadata = {
                    key: value for key, value in old.items()
                    if key not in STRUCTURAL_FIELDS or key in PERSISTED_STATE_FIELDS
                }
                record.update(metadata)
                if not record.get("design_id"):
                    mark_attention(record, "CODE_NOT_IDENTIFIED", "Código da estampa vazio.")
                if not record.get("variante"):
                    mark_attention(
                        record, "VARIANT_NOT_IDENTIFIED",
                        "A variante não pôde ser inferida pelo nome do arquivo.",
                    )
                if validation_error:
                    mark_attention(record, validation_error[0], validation_error[1])
                if previous_hash and current_hash == previous_hash:
                    # Tamanho ou mtime mudou, mas os bytes continuam idênticos.
                    record["content_hash"] = current_hash
                    record["changed"] = False
                    record["scan_status"] = "unchanged"
                    record["last_error"] = ""
                    stats["unchanged"] += 1
                else:
                    # Sem hash anterior, a decisão segura é tratar o candidato
                    # legado como alterado e guardar a assinatura para o próximo scan.
                    record["processed"] = False
                    record["analysis_stale"] = True
                    record["changed"] = True
                    record["scan_status"] = "changed"
                    record["processing_status"] = "pending"
                    record.update({
                        "content_hash": current_hash,
                        "preview_status": "pending", "preview_path": "",
                        "preview_content_hash": "",
                        "cloud_status": "pending", "storage_key": "",
                        "preview_url": "", "cloud_content_hash": "",
                        "supabase_status": "pending",
                        "supabase_content_hash": "",
                        "preview_attempts": 0, "preview_last_error": "",
                        "preview_last_attempt_at": "", "cloud_attempts": 0,
                        "cloud_last_error": "", "cloud_last_attempt_at": "",
                        "supabase_attempts": 0, "supabase_last_error": "",
                        "supabase_last_attempt_at": "",
                        "last_synced_at": "",
                    })
                    if current_hash:
                        record["last_error"] = ""
                    stats["changed"] += 1
                if was_missing:
                    record["missing_locally"] = False
                    record["active"] = True
                    record["scan_status"] = "reappeared"
                matched.append(record)
                dirty_records.append(record)
            progress.report("Verificando imagens", scanned,
                            detail=f"Novas: {len(new_records):,}; inalteradas: {stats['unchanged']:,}; alteradas: {stats['changed']:,}")

    progress.report("Verificando imagens", scanned, scanned, force=True,
                    detail=f"Novas: {len(new_records):,}; inalteradas: {stats['unchanged']:,}; alteradas: {stats['changed']:,}")

    for old in old_by_path.values():
        if old.get("active", True):
            removed.append(old)
        else:
            matched.append(old)

    progress.report("SHA-256 dos candidatos concluído", stats["hashed"], force=True)
    progress.report("Conciliando movimentos e ausências", scanned)
    reconcile_started = time.perf_counter()
    for source in sources:
        with os.scandir(source) as entries:
            next(entries, None)

    removed_by_hash: dict[str, list[dict]] = {}
    new_by_hash: dict[str, list[dict]] = {}
    for record in removed:
        content_hash = str(record.get("content_hash", ""))
        if content_hash:
            removed_by_hash.setdefault(content_hash, []).append(record)
    for record in new_records:
        content_hash = str(record.get("content_hash", ""))
        if content_hash:
            new_by_hash.setdefault(content_hash, []).append(record)

    moved_new_ids, moved_old_ids = set(), set()
    for content_hash, candidates in new_by_hash.items():
        previous = removed_by_hash.get(content_hash, [])
        if len(candidates) == len(previous) == 1:
            new, old = candidates[0], previous[0]
            attention = (
                new.get("attention_status", ""), new.get("attention_reason", ""),
                new.get("attention_at", ""), new.get("last_error", ""),
            )
            _copy_metadata(old, new)
            if attention[0]:
                new["attention_status"], new["attention_reason"] = attention[:2]
                new["attention_at"], new["last_error"] = attention[2:]
            new["content_hash"] = content_hash
            new["scan_status"] = "moved"
            new["review_required"] = False
            new["review_reason"] = ""
            old["active"] = False
            old["missing_locally"] = True
            old["scan_status"] = "moved_from"
            dirty_records.append(old)
            matched.append(new)
            dirty_records.append(new)
            moved_new_ids.add(id(new))
            moved_old_ids.add(id(old))
            stats["moved"] += 1
        elif previous:
            reason = (
                "Mais de um arquivo novo ou ausente possui o mesmo SHA-256; "
                "o movimento não foi decidido automaticamente."
            )
            for record in candidates + previous:
                record["review_required"] = True
                record["review_reason"] = reason
                dirty_records.append(record)
            stats["review"] += len(candidates) + len(previous)

    # Catálogos legados podem ainda não possuir hash. Uma assinatura física
    # semelhante é apenas indício e nunca autoriza mesclagem automática.
    legacy_removed_by_signature: dict[tuple[int, int], list[dict]] = {}
    for record in removed:
        if not record.get("content_hash"):
            legacy_removed_by_signature.setdefault(_signature(record), []).append(record)
    for record in new_records:
        if id(record) in moved_new_ids:
            continue
        candidates = legacy_removed_by_signature.get(_signature(record), [])
        if candidates:
            reason = "Possível movimento de registro legado sem SHA-256; revisão necessária."
            record["review_required"] = True
            record["review_reason"] = reason
            for previous in candidates:
                previous["review_required"] = True
                previous["review_reason"] = reason
                dirty_records.append(previous)
            stats["review"] += 1 + len(candidates)

    for record in new_records:
        if id(record) not in moved_new_ids:
            matched.append(record)
            dirty_records.append(record)
            stats["added"] += 1
    for record in removed:
        if id(record) not in moved_old_ids:
            record["active"] = False
            record["missing_locally"] = True
            record["scan_status"] = "missing"
            detected_at = time.strftime("%Y-%m-%d %H:%M:%S")
            record["missing_since"] = detected_at
            record["missing_detected_at"] = detected_at
            matched.append(record)
            dirty_records.append(record)
            stats["removed"] += 1

    by_key: dict[str, list[dict]] = {}
    for record in matched:
        if record.get("active", True):
            by_key.setdefault(str(record.get("key", "")), []).append(record)
    dirty_ids = {id(record) for record in dirty_records}
    progress.report("Verificando duplicidades", 0, len(by_key))
    for current, duplicates in enumerate(by_key.values(), 1):
        progress.report("Verificando duplicidades", current, len(by_key))
        if len(duplicates) < 2:
            continue
        paths = " | ".join(str(record.get("path", "")) for record in duplicates)
        for record in duplicates:
            changed_attention = mark_attention(
                record, "UNEXPECTED_DUPLICATE",
                f"Mais de um arquivo ativo possui a mesma chave: {paths}",
            )
            if changed_attention and id(record) not in dirty_ids:
                dirty_records.append(record)
                dirty_ids.add(id(record))
    metrics = ACTIVE_METRICS.get()
    if metrics is not None:
        metrics["seconds"]["reconciliation_duplicates"] = time.perf_counter() - reconcile_started
    return matched, stats, scanned, dirty_records, quarantine_issues


def _index_operation(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        metrics = new_metrics()
        token = ACTIVE_METRICS.set(metrics)
        started = time.perf_counter()
        metrics["storage"] = {
            "catalog": str(INDEX_FILE), "operational_db": str(_operational_db_path()),
            "journal": str(INDEX_FILE.with_suffix(".scan.jsonl")),
            "catalog_temporary_directory": str(INDEX_FILE.parent),
            "report": str(INDEX_FILE.with_suffix(".performance.json")),
        }
        locked = False
        try:
            with catalog_lock(INDEX_FILE.with_suffix(".lock")):
                locked = True
                index, result = function(*args, **kwargs)
                metrics["seconds"]["total"] = time.perf_counter() - started
                result.elapsed_seconds = metrics["seconds"]["total"]
                metrics["summary"] = {key: value for key, value in vars(result).items()
                                      if key != "performance"}
                result.performance = metrics
                metrics["status"] = "completed"
                logging.getLogger(__name__).info("Desempenho do índice: %s", json.dumps(metrics, ensure_ascii=False))
                _save_performance(metrics)
                return index, result
        except BaseException as exc:
            metrics["seconds"]["total"] = time.perf_counter() - started
            metrics.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            if locked:
                _save_performance(metrics)
            raise
        finally:
            ACTIVE_METRICS.reset(token)
    return wrapped


def _save_performance(metrics):
    """Diagnóstico auxiliar; falhar aqui não invalida o catálogo publicado."""
    path = INDEX_FILE.with_suffix(".performance.json")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(metrics, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    except OSError:
        logging.getLogger(__name__).warning("Não foi possível salvar diagnóstico do índice", exc_info=True)
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def _scan_with_journal(sources, previous, progress):
    journal = CandidateJournal(INDEX_FILE.with_suffix(".scan.jsonl"), sources, INDEX_VERSION)
    try:
        result = _scan_and_merge(sources, previous, progress=progress, journal=journal)
        for source, initial in zip(sources, journal.header["sources"]):
            stat = source.stat()
            if [str(source), stat.st_dev, stat.st_ino] != initial:
                raise OSError(f"A origem foi substituída durante a varredura: {source}")
    except BaseException:
        # Falha da gravação não deve esconder a causa original da interrupção.
        try:
            journal.flush()
        except OSError:
            logging.getLogger(__name__).exception("Falha ao salvar diário de recuperação")
        raise
    journal.flush()
    return result, journal


@_index_operation
def build_index(source_dirs, progress_callback=None) -> tuple[dict[str, list[str]], IndexResult]:
    sources = validate_source_dirs(source_dirs)
    started = time.monotonic()
    progress = IndexProgress(progress_callback)
    previous = _load_catalog(sources, progress, for_scan=True)
    (records, _stats, scanned, _dirty_records, quarantine_issues), journal = _scan_with_journal(
        sources, previous[1] if previous else [], progress,
    )
    index = _index_from_records(records, sources)
    _write_catalog(records, sources, progress=progress)
    record_quarantine_issues(_operational_db_path(), quarantine_issues)
    duplicates_log = _write_duplicates(index)
    journal.complete()
    record_index_update(sources, state_file=state_file_for(INDEX_FILE))
    progress.report("Índice concluído", scanned, scanned, force=True)
    return index, IndexResult(
        scanned, len(index), sum(len(value) > 1 for value in index.values()),
        len(sources), time.monotonic() - started, duplicates_log,
    )


@_index_operation
def update_index_incremental(source_dirs, progress_callback=None):
    sources = validate_source_dirs(source_dirs)
    started = time.monotonic()
    progress = IndexProgress(progress_callback)
    previous = _load_catalog(sources, progress, for_scan=True)
    if previous is None:
        raise ValueError(
            "Ainda não existe um índice completo para estas pastas. "
            "Clique primeiro em Atualizar índice completo."
        )
    (records, stats, scanned, dirty_records, quarantine_issues), journal = _scan_with_journal(
        sources, previous[1], progress,
    )
    index = _index_from_records(records, sources)
    if dirty_records:
        _write_catalog(records, sources, operational_records=dirty_records, progress=progress)
        duplicates_log = _write_duplicates(index)
    else:
        duplicates_log = str(DUPLICATES_LOG_FILE.resolve()) if DUPLICATES_LOG_FILE.exists() else None
    record_quarantine_issues(_operational_db_path(), quarantine_issues)
    result = IncrementalIndexResult(
        scanned_files=scanned, added_files=stats["added"],
        indexed_names=len(index), duplicates=sum(len(value) > 1 for value in index.values()),
        source_dirs=len(sources), elapsed_seconds=time.monotonic() - started,
        duplicates_log=duplicates_log, removed_files=stats["removed"],
        moved_files=stats["moved"], changed_files=stats["changed"],
        unchanged_files=stats["unchanged"],
        verification_files=stats["changed"],
        hashed_files=stats["hashed"],
        errors=stats["errors"],
        review_files=stats["review"],
    )
    with stage("scan_summary"):
        record_scan_summary(_operational_db_path(), result)
    journal.complete()
    record_index_update(sources, state_file=state_file_for(INDEX_FILE))
    progress.report("Índice concluído", scanned, scanned, force=True,
                    detail=f"Novos: {stats['added']:,}; inalterados: {stats['unchanged']:,}; erros: {stats['errors']:,}")
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
    with _analysis_results_path().open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
