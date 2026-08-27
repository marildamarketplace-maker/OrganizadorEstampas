"""Índice persistente de embeddings visuais SigLIP 2 para imagens semelhantes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import json
import os
import time

from .config import VISUAL_INDEX_FILE, VISUAL_LOG_FILE, VISUAL_METADATA_FILE, ensure_app_dir
from .indexer import load_catalog_records, normalize_source_dirs


MODEL_ID = "google/siglip2-base-patch16-224"
VISUAL_DIMENSION = 768
VISUAL_VERSION = 1
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MAX_MODEL_INPUT_EDGE = 512


@dataclass
class VisualUpdateResult:
    total_eligible: int
    added: int
    updated: int
    removed: int
    reused: int
    unchanged: int
    errors: int
    cancelled: bool
    elapsed_seconds: float


def visual_record_identity(record: dict) -> str:
    relative = str(record.get("relative_path", "")).replace("\\", "/").casefold()
    return f"{int(record.get('source', 0))}:{relative}"


def visual_file_signature(record: dict) -> str:
    return f"{int(record.get('size', -1))}:{int(record.get('mtime_ns', -1))}"


def _dependencies():
    try:
        import faiss
        import numpy as np
        import torch
        from PIL import Image, ImageOps
        from transformers import AutoModel, AutoProcessor
    except ImportError as exc:
        raise RuntimeError(
            "A busca visual não está instalada. Execute: "
            "pip install -r requirements-visual.txt"
        ) from exc
    return faiss, np, torch, Image, ImageOps, AutoModel, AutoProcessor


def _terminal_log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] Busca visual: {message}", flush=True)


def _read_metadata(path: Path | None = None):
    path = path or VISUAL_METADATA_FILE
    try:
        with path.open(encoding="utf-8") as stream:
            header = json.loads(next(stream))
            if (
                header.get("type") != "visual_index"
                or header.get("version") != VISUAL_VERSION
                or header.get("model") != MODEL_ID
                or header.get("dimension") != VISUAL_DIMENSION
            ):
                return None
            return header, [json.loads(line) for line in stream if line.strip()]
    except (OSError, StopIteration, json.JSONDecodeError, TypeError, ValueError):
        return None


def visual_index_status() -> tuple[bool, str, int]:
    metadata = _read_metadata()
    if metadata is None or not VISUAL_INDEX_FILE.exists():
        return False, "Índice visual ainda não foi criado.", 0
    count = len(metadata[1])
    return True, f"Imagens semelhantes disponível para {count:,} artes.", count


def _log(message: str):
    ensure_app_dir()
    with VISUAL_LOG_FILE.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


class VisualSearchIndex:
    def __init__(self, source_dirs):
        self.source_dirs = normalize_source_dirs(source_dirs)
        self.index = None
        self.entries = []
        self.model = None
        self.processor = None
        self.device = "cpu"
        self.torch = None
        self.dtype = None

    def _load_model(self):
        if self.model is not None:
            return
        _terminal_log(f"carregando modelo {MODEL_ID}...")
        _faiss, _np, torch, _Image, _ImageOps, AutoModel, AutoProcessor = _dependencies()
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        _terminal_log(f"dispositivo selecionado: {self.device}.")
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.dtype = dtype
        self.processor = AutoProcessor.from_pretrained(MODEL_ID)
        self.model = AutoModel.from_pretrained(
            MODEL_ID, torch_dtype=dtype, low_cpu_mem_usage=True
        ).to(self.device).eval()
        self.torch = torch
        _terminal_log("modelo carregado com sucesso.")

    def _catalog_records(self):
        records = load_catalog_records(self.source_dirs)
        if records is None:
            raise ValueError("Atualize o catálogo antes do índice visual.")
        return [
            record for record in records
            if record.get("active", True)
            and Path(record.get("filename", "")).suffix.casefold() in SUPPORTED_EXTENSIONS
        ]

    def load(self):
        faiss, _np, *_rest = _dependencies()
        metadata = _read_metadata()
        if metadata is None or not VISUAL_INDEX_FILE.exists():
            raise ValueError("Crie ou reconstrua o índice visual primeiro.")
        index = faiss.read_index(str(VISUAL_INDEX_FILE))
        if index.d != VISUAL_DIMENSION or index.ntotal != len(metadata[1]):
            raise ValueError("O índice visual está inconsistente. Reconstrua-o.")
        self.index, self.entries = index, metadata[1]
        return len(self.entries)

    def _encode_paths(self, paths: list[str]):
        self._load_model()
        _faiss, _np, _torch, Image, ImageOps, *_rest = _dependencies()
        images = []
        try:
            for path in paths:
                with Image.open(path) as opened:
                    image = ImageOps.exif_transpose(opened).convert("RGB")
                    image.thumbnail((MAX_MODEL_INPUT_EDGE, MAX_MODEL_INPUT_EDGE))
                    images.append(image)
            inputs = self.processor(images=images, return_tensors="pt").to(
                self.device, self.dtype
            )
            with self.torch.inference_mode():
                features = self.model.get_image_features(**inputs)
                features = self.torch.nn.functional.normalize(features, p=2, dim=1)
            return features.float().cpu().numpy()
        finally:
            for image in images:
                image.close()

    def release_model(self):
        """Libera pesos e cache CUDA, mantendo o índice FAISS pesquisável."""
        self.model = None
        self.processor = None
        if self.torch is not None and self.device == "cuda":
            self.torch.cuda.empty_cache()

    def update(
        self, *, rebuild=False, batch_size=16,
        progress_callback: Callable[[int, int, int, str], None] | None = None,
        cancel_callback: Callable[[], bool] | None = None,
    ) -> VisualUpdateResult:
        _terminal_log("iniciando atualização do índice.")
        faiss, np, *_rest = _dependencies()
        started = time.monotonic()
        records = self._catalog_records()
        current = {visual_record_identity(record): record for record in records}
        old_entries = []
        if not rebuild and VISUAL_INDEX_FILE.exists():
            try:
                self.load()
                old_entries = self.entries
            except (ValueError, RuntimeError):
                rebuild = True
        if rebuild:
            index = faiss.IndexIDMap2(faiss.IndexFlatIP(VISUAL_DIMENSION))
            old_entries = []
        else:
            index = self.index
        old = {entry["identity"]: entry for entry in old_entries}
        next_id = max((int(entry["id"]) for entry in old_entries), default=-1) + 1
        new_identities = [identity for identity in current if identity not in old]
        removed = [entry for identity, entry in old.items() if identity not in current]

        removed_by_signature = {}
        for entry in removed:
            removed_by_signature.setdefault(entry["file_signature"], []).append(entry)
        reused = 0
        for identity in list(new_identities):
            candidates = removed_by_signature.get(visual_file_signature(current[identity]), [])
            if len(candidates) == 1:
                previous = candidates.pop()
                old.pop(previous["identity"], None)
                old[identity] = {**previous, "identity": identity,
                                 "source": int(current[identity].get("source", 0)),
                                 "relative_path": str(current[identity].get("relative_path", ""))}
                new_identities.remove(identity)
                reused += 1

        changed = [
            identity for identity, entry in old.items()
            if identity in current
            and entry["file_signature"] != visual_file_signature(current[identity])
        ]
        truly_removed = [entry for identity, entry in old.items() if identity not in current]
        ids_to_remove = [int(entry["id"]) for entry in truly_removed]
        ids_to_remove.extend(int(old[identity]["id"]) for identity in changed)
        if ids_to_remove:
            index.remove_ids(np.asarray(ids_to_remove, dtype="int64"))

        work = [(identity, int(old[identity]["id"]), "updated") for identity in changed]
        for identity in new_identities:
            work.append((identity, next_id, "added"))
            next_id += 1
        entries = {
            identity: entry for identity, entry in old.items()
            if identity in current and identity not in changed
        }
        added = updated = errors = processed = 0
        cancelled = False
        # Um arquivo corrompido não impede os outros; lotes são subdivididos em
        # inferências unitárias no fallback para identificar exatamente a falha.
        for offset in range(0, len(work), batch_size):
            if cancel_callback and cancel_callback():
                cancelled = True
                break
            batch = work[offset:offset + batch_size]
            successful = []
            try:
                vectors = self._encode_paths([current[item[0]]["path"] for item in batch])
                successful = list(zip(batch, vectors))
            except Exception:
                for item in batch:
                    try:
                        vector = self._encode_paths([current[item[0]]["path"]])[0]
                        successful.append((item, vector))
                    except Exception as exc:
                        errors += 1
                        _log(f"ERRO | {current[item[0]].get('path', '')} | {type(exc).__name__}: {exc}")
            if successful:
                vectors = np.asarray([vector for _item, vector in successful], dtype="float32")
                ids = np.asarray([item[0][1] for item in successful], dtype="int64")
                index.add_with_ids(vectors, ids)
                for (identity, vector_id, kind), _vector in successful:
                    record = current[identity]
                    entries[identity] = {
                        "type": "visual_record", "id": int(vector_id),
                        "identity": identity, "file_signature": visual_file_signature(record),
                        "source": int(record.get("source", 0)),
                        "relative_path": str(record.get("relative_path", "")),
                    }
                    added += kind == "added"
                    updated += kind == "updated"
            processed += len(batch)
            if progress_callback:
                progress_callback(processed, len(work), errors,
                                  f"Gerando embeddings visuais: {processed:,} de {len(work):,}")
            _terminal_log(
                f"imagens processadas: {processed:,} de {len(work):,}; erros: {errors}."
            )
        if cancelled:
            return VisualUpdateResult(
                len(records), 0, 0, 0, reused, len(entries), errors, True,
                time.monotonic() - started,
            )
        ordered = sorted(entries.values(), key=lambda entry: int(entry["id"]))
        self._write(index, ordered, faiss)
        self.index, self.entries = index, ordered
        result = VisualUpdateResult(
            len(records), added, updated, len(truly_removed), reused,
            len(ordered) - added - updated - reused, errors, False,
            time.monotonic() - started,
        )
        _log(f"Índice visual atualizado | total={len(ordered)} novos={added} alterados={updated} erros={errors}")
        return result

    def _write(self, index, entries, faiss):
        ensure_app_dir()
        index_temp = str(VISUAL_INDEX_FILE) + ".tmp"
        metadata_temp = str(VISUAL_METADATA_FILE) + ".tmp"
        header = {
            "type": "visual_index", "version": VISUAL_VERSION, "model": MODEL_ID,
            "dimension": VISUAL_DIMENSION,
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
            os.replace(index_temp, VISUAL_INDEX_FILE)
            os.replace(metadata_temp, VISUAL_METADATA_FILE)
        finally:
            Path(index_temp).unlink(missing_ok=True)
            Path(metadata_temp).unlink(missing_ok=True)

    def search_similar(self, image_path, records_by_identity, limit=200, exclude_identity=None):
        if self.index is None:
            self.load()
        vector = self._encode_paths([str(image_path)]).astype("float32")
        requested = min(limit + (1 if exclude_identity else 0), len(self.entries))
        scores, ids = self.index.search(vector, requested)
        by_id = {int(entry["id"]): entry for entry in self.entries}
        results = []
        for score, vector_id in zip(scores[0], ids[0]):
            entry = by_id.get(int(vector_id))
            if not entry or entry["identity"] == exclude_identity:
                continue
            record = records_by_identity.get(entry["identity"])
            if record:
                results.append((record, max(0.0, min(1.0, float(score)))))
            if len(results) >= limit:
                break
        return results
