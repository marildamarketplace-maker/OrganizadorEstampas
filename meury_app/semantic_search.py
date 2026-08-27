"""Embeddings textuais OpenAI e índice FAISS persistente/incremental."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import hashlib
import json
import os
import time

from .config import (
    SEMANTIC_INDEX_FILE, SEMANTIC_LOG_FILE, SEMANTIC_METADATA_FILE, ensure_app_dir,
)
from .indexer import load_catalog_records, normalize_source_dirs


MODEL_ID = "text-embedding-3-small"
EMBEDDING_DIMENSION = 384
SEMANTIC_VERSION = 2


@dataclass
class SemanticUpdateResult:
    total_eligible: int
    added: int
    updated: int
    removed: int
    reused: int
    unchanged: int
    cancelled: bool
    elapsed_seconds: float


def record_identity(record: dict) -> str:
    relative = str(record.get("relative_path", "")).replace("\\", "/").casefold()
    return f"{int(record.get('source', 0))}:{relative}"


def semantic_document(record: dict) -> str:
    def text(name):
        value = record.get(name, "")
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value or "")

    parts = [
        f"nome: {text('filename')}", f"descrição: {text('description')}",
        f"palavras-chave: {text('keywords')}", f"cores: {text('colors')}",
        f"elementos: {text('elements')}", f"temas: {text('themes')}",
        f"categoria: {text('category')}",
    ]
    return ". ".join(part for part in parts if not part.endswith(": "))


def has_semantic_metadata(record: dict) -> bool:
    return any(record.get(field) for field in (
        "description", "keywords", "colors", "elements", "themes", "category"
    ))


def semantic_content_hash(record: dict) -> str:
    return hashlib.sha256(semantic_document(record).encode("utf-8")).hexdigest()


def _append_log(message: str) -> None:
    ensure_app_dir()
    with SEMANTIC_LOG_FILE.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


def _load_dependencies():
    try:
        import faiss
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "O índice vetorial não está instalado. Execute o instalador de recursos de IA."
        ) from exc
    return faiss, np


def _terminal_log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] Busca semântica: {message}", flush=True)


def _read_metadata(path: Path | None = None) -> tuple[dict, list[dict]] | None:
    path = path or SEMANTIC_METADATA_FILE
    try:
        with path.open(encoding="utf-8") as stream:
            header = json.loads(next(stream))
            if (
                header.get("type") != "semantic_index"
                or header.get("version") != SEMANTIC_VERSION
                or header.get("model") != MODEL_ID
                or header.get("dimension") != EMBEDDING_DIMENSION
            ):
                return None
            entries = [json.loads(line) for line in stream if line.strip()]
            return header, entries
    except (OSError, StopIteration, json.JSONDecodeError, TypeError, ValueError):
        return None


def semantic_index_status() -> tuple[bool, str, int]:
    loaded = _read_metadata()
    if loaded is None or not SEMANTIC_INDEX_FILE.exists():
        return False, "Índice semântico ainda não foi criado.", 0
    count = len(loaded[1])
    return True, f"Busca semântica disponível para {count:,} artes.", count


def semantic_index_identities() -> set[str]:
    """Lê apenas o mapa leve; não carrega FAISS nem pesos do modelo."""
    loaded = _read_metadata()
    return {str(entry.get("identity", "")) for entry in loaded[1]} if loaded else set()


class SemanticSearchIndex:
    def __init__(self, source_dirs):
        self.source_dirs = normalize_source_dirs(source_dirs)
        self.client = None
        self.index = None
        self.entries: list[dict] = []

    def _load_model(self):
        if self.client is not None:
            return
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("Informe a chave da API OpenAI para usar a busca semântica.")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("O cliente da OpenAI não está instalado.") from exc
        _terminal_log(f"preparando cliente para {MODEL_ID}...")
        self.client = OpenAI(api_key=api_key, timeout=300, max_retries=2)
        _terminal_log("cliente da API pronto.")

    def load(self) -> int:
        faiss, _np = _load_dependencies()
        metadata = _read_metadata()
        if metadata is None or not SEMANTIC_INDEX_FILE.exists():
            raise ValueError("Crie ou reconstrua o índice semântico primeiro.")
        index = faiss.read_index(str(SEMANTIC_INDEX_FILE))
        entries = metadata[1]
        if index.d != EMBEDDING_DIMENSION or index.ntotal != len(entries):
            raise ValueError("O índice semântico está inconsistente. Reconstrua-o.")
        self.index, self.entries = index, entries
        return len(entries)

    def _catalog_records(self) -> list[dict]:
        records = load_catalog_records(self.source_dirs)
        if records is None:
            raise ValueError("Atualize o catálogo antes do índice semântico.")
        return [
            record for record in records
            if record.get("active", True) and has_semantic_metadata(record)
        ]

    def _encode(self, texts: list[str], prefix: str, batch_size: int):
        self._load_model()
        _faiss, np = _load_dependencies()
        response = self.client.embeddings.create(
            model=MODEL_ID,
            input=[prefix + text for text in texts],
            dimensions=EMBEDDING_DIMENSION,
            encoding_format="float",
        )
        vectors = np.asarray([item.embedding for item in response.data], dtype="float32")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.maximum(norms, 1e-12)

    def release_model(self) -> None:
        """Libera o cliente HTTP sem descarregar o índice FAISS."""
        self.client = None

    def update(
        self,
        *,
        rebuild: bool = False,
        batch_size: int = 256,
        progress_callback: Callable[[int, int, str], None] | None = None,
        cancel_callback: Callable[[], bool] | None = None,
    ) -> SemanticUpdateResult:
        _terminal_log("iniciando atualização do índice.")
        faiss, np = _load_dependencies()
        started = time.monotonic()
        records = self._catalog_records()
        current = {record_identity(record): record for record in records}
        old_entries = []
        if not rebuild and SEMANTIC_INDEX_FILE.exists():
            try:
                self.load()
                old_entries = self.entries
            except (ValueError, RuntimeError):
                rebuild = True

        if rebuild:
            index = faiss.IndexIDMap2(faiss.IndexFlatIP(EMBEDDING_DIMENSION))
            old_entries = []
        else:
            index = self.index
        old_by_identity = {entry["identity"]: entry for entry in old_entries}
        next_id = max((int(entry["id"]) for entry in old_entries), default=-1) + 1
        removed_entries = [entry for identity, entry in old_by_identity.items() if identity not in current]
        new_identities = [identity for identity in current if identity not in old_by_identity]

        # Movimento/renomeação não recalcula o vetor se o conteúdo semântico é idêntico.
        removed_by_hash: dict[str, list[dict]] = {}
        for entry in removed_entries:
            removed_by_hash.setdefault(entry["content_hash"], []).append(entry)
        reused = 0
        for identity in list(new_identities):
            content_hash = semantic_content_hash(current[identity])
            candidates = removed_by_hash.get(content_hash, [])
            if len(candidates) == 1:
                entry = candidates.pop()
                old_by_identity.pop(entry["identity"], None)
                entry = {**entry, "identity": identity}
                old_by_identity[identity] = entry
                new_identities.remove(identity)
                reused += 1

        changed = [
            identity for identity, entry in old_by_identity.items()
            if identity in current and entry["content_hash"] != semantic_content_hash(current[identity])
        ]
        truly_removed = [
            entry for identity, entry in old_by_identity.items() if identity not in current
        ]
        ids_to_remove = [int(entry["id"]) for entry in truly_removed]
        ids_to_remove.extend(int(old_by_identity[identity]["id"]) for identity in changed)
        if ids_to_remove:
            index.remove_ids(np.asarray(ids_to_remove, dtype="int64"))

        work = [(identity, old_by_identity[identity]["id"], "updated") for identity in changed]
        for identity in new_identities:
            work.append((identity, next_id, "added"))
            next_id += 1
        entries_by_identity = {
            identity: entry for identity, entry in old_by_identity.items()
            if identity in current and identity not in changed
        }
        added = updated = processed = 0
        cancelled = False
        for offset in range(0, len(work), batch_size):
            if cancel_callback and cancel_callback():
                cancelled = True
                break
            batch = work[offset:offset + batch_size]
            vectors = self._encode(
                [semantic_document(current[identity]) for identity, _id, _kind in batch],
                "passage: ", batch_size,
            ).astype("float32")
            ids = np.asarray([item[1] for item in batch], dtype="int64")
            index.add_with_ids(vectors, ids)
            for identity, vector_id, kind in batch:
                entries_by_identity[identity] = {
                    "type": "semantic_record", "id": int(vector_id),
                    "identity": identity,
                    "content_hash": semantic_content_hash(current[identity]),
                    "source": int(current[identity].get("source", 0)),
                    "relative_path": str(current[identity].get("relative_path", "")),
                }
                added += kind == "added"
                updated += kind == "updated"
            processed += len(batch)
            if progress_callback:
                progress_callback(processed, len(work), f"Gerando embeddings: {processed:,} de {len(work):,}")
            _terminal_log(f"embeddings processados: {processed:,} de {len(work):,}.")

        if cancelled:
            # Não persiste um índice parcial; o próximo update recomeça apenas este trabalho.
            return SemanticUpdateResult(
                len(records), 0, 0, 0, reused, len(entries_by_identity), True,
                time.monotonic() - started,
            )
        entries = sorted(entries_by_identity.values(), key=lambda entry: int(entry["id"]))
        self._write(index, entries, faiss)
        self.index, self.entries = index, entries
        unchanged = len(entries) - added - updated - reused
        result = SemanticUpdateResult(
            len(records), added, updated, len(truly_removed), reused, unchanged,
            False, time.monotonic() - started,
        )
        _append_log(
            f"Índice atualizado | total={len(entries)} novos={added} alterados={updated} "
            f"removidos={len(truly_removed)} reutilizados={reused}"
        )
        return result

    def _write(self, index, entries: list[dict], faiss) -> None:
        ensure_app_dir()
        index_temp = str(SEMANTIC_INDEX_FILE) + ".tmp"
        metadata_temp = str(SEMANTIC_METADATA_FILE) + ".tmp"
        header = {
            "type": "semantic_index", "version": SEMANTIC_VERSION,
            "model": MODEL_ID, "dimension": EMBEDDING_DIMENSION,
            "source_dirs": [str(path) for path in self.source_dirs],
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            faiss.write_index(index, index_temp)
            with open(metadata_temp, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(header, ensure_ascii=False) + "\n")
                for entry in entries:
                    stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(index_temp, SEMANTIC_INDEX_FILE)
            os.replace(metadata_temp, SEMANTIC_METADATA_FILE)
        finally:
            Path(index_temp).unlink(missing_ok=True)
            Path(metadata_temp).unlink(missing_ok=True)

    def search(self, query: str, records_by_identity: dict[str, dict], limit: int = 300):
        return self.search_document(query, records_by_identity, limit=limit, prefix="consulta: ")

    def search_document(
        self, document: str, records_by_identity: dict[str, dict],
        limit: int = 300, prefix: str = "arte: ",
    ):
        if self.index is None:
            self.load()
        vector = self._encode([document], prefix, 1).astype("float32")
        scores, ids = self.index.search(vector, min(limit, len(self.entries)))
        entries_by_id = {int(entry["id"]): entry for entry in self.entries}
        results = []
        for score, vector_id in zip(scores[0], ids[0]):
            entry = entries_by_id.get(int(vector_id))
            if entry and entry["identity"] in records_by_identity:
                results.append((records_by_identity[entry["identity"]], float(score)))
        return results


def merge_hybrid_results(text_results, semantic_results, limit=200):
    """Fusão ponderada preservando candidatos exclusivos de ambas as buscas."""
    combined: dict[str, dict] = {}
    max_text = max((result.score for result in text_results), default=1.0) or 1.0
    for result in text_results:
        identity = record_identity(result.record)
        combined[identity] = {"record": result.record, "text": result.score / max_text, "semantic": 0.0}
    for record, score in semantic_results:
        identity = record_identity(record)
        item = combined.setdefault(identity, {"record": record, "text": 0.0, "semantic": 0.0})
        item["semantic"] = max(0.0, score)
    ranked = []
    for item in combined.values():
        score = item["text"] * 0.45 + item["semantic"] * 0.55
        ranked.append((item["record"], score))
    ranked.sort(key=lambda item: (-item[1], str(item[0].get("filename", "")).casefold()))
    return ranked[:limit]
