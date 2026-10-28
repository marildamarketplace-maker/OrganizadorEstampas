"""Coordinate approved PSD manifests, audit logs and atomic archive publication."""
from datetime import datetime
from copy import deepcopy
from pathlib import Path
import json
import os
import re
import stat
import tempfile
import time
import uuid

from .compression_store import CompressionStore
from .index_journal import catalog_lock
from .platform_utils import publish_without_overwrite
from .psd_search import Cancelled, checkpoint, portable_key, safe_source, validate_internal
from .sevenzip import SevenZip


def sanitized(value):
    if isinstance(value, BaseException):
        value = str(value)
    if isinstance(value, dict):
        return {key: sanitized(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitized(item) for item in value]
    if not isinstance(value, str):
        return value
    text = str(value)
    text = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+@", r"\1[credenciais]@", text)
    text = re.sub(r"(?i)(token|password|secret|api_key)=([^\s&]+)", r"\1=[oculto]", text)
    return text


def lock_path(store):
    return store.path.parent / "compactacao.lock"


def signature(item):
    path = safe_source(item.path, item.source)
    info = path.stat()
    if (info.st_size, info.st_mtime_ns) != (item.size, item.mtime_ns):
        raise RuntimeError(f"Original alterado desde a análise: {path}")
    return path


def size_summary(original, compressed):
    difference = original - compressed
    return {"original_bytes": original, "archive_bytes": compressed, "difference_bytes": difference,
            "difference_percent": difference / original * 100 if original else None,
            "storage_note": "Originais preservados: o compactado aumenta o uso total de armazenamento. Nenhum espaço foi liberado automaticamente."}


def compress(analysis, cancel=lambda: False, progress=lambda *args: None, store=None, engine=None):
    store = store or CompressionStore()
    analysis = deepcopy(analysis)  # Freeze the approved manifest; never mutate UI-owned records.
    selected = [item for item in analysis.files if item.selected]
    if not selected:
        raise ValueError("Selecione ao menos um PSD.")
    keys = set()
    components = {}
    for item in selected:
        validate_internal(item.internal)
        key = portable_key(item.internal)
        if key in keys:
            raise ValueError("Colisão entre caminhos internos no manifesto.")
        keys.add(key)
        parts = item.internal.split("/")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            folded = portable_key(prefix)
            if folded in components and components[folded] != prefix:
                raise ValueError("Colisão de caixa/Unicode em diretório interno.")
            components[folded] = prefix
    if any("/".join(key.split("/")[:index]) in keys for key in keys for index in range(1, len(key.split("/")))):
        raise ValueError("Colisão entre arquivo e diretório no manifesto.")
    output = Path(analysis.settings.output).expanduser().resolve()
    if any(output == Path(source).expanduser().resolve() for source in analysis.settings.sources):
        raise ValueError("Saída igual à origem")
    with catalog_lock(lock_path(store)):
        store.recover()
        output.mkdir(parents=True, exist_ok=True)
        # Exclusive directory reserves the result names, including across application instances.
        identity = uuid.uuid4().hex
        folder = output / f"compactacao_{datetime.now():%Y%m%d_%H%M%S}_{identity[:12]}"
        folder.mkdir()
        base = folder / folder.name
        archive = base.with_suffix(".7z")
        log_path = base.with_suffix(".txt")
        payload = {"analysis": analysis.snapshot(), "archive": str(archive), "log": str(log_path),
                   "errors": [], "validation": "Não executada", "included": 0,
                   "processed": 0, "published": False, "included_paths": []}
        state = "em andamento"
        temporary = None
        last_saved = 0
        with log_path.open("x", encoding="utf-8") as log:
            def record(message):
                log.write(f"{datetime.now().isoformat()} {json.dumps(sanitized(message), ensure_ascii=False)}\n")
                log.flush()

            def persist():
                store.save(identity, state, sanitized(payload))

            try:
                record(f"Início | execução {identity}")
                record(payload["analysis"])
                record(f"Destino {folder}; compactado {archive}; LZMA2, normal (5), sólido desativado")
                persist()
                checkpoint(cancel)
                engine = engine or SevenZip()
                record(f"Mecanismo: {getattr(engine, 'executable', type(engine).__name__)}")
                progress("Verificando capacidade do 7-Zip", 0, 0)
                engine.probe(cancel)
                payload["mechanism"] = getattr(engine, "version", "Mecanismo simulado")
                record(f"Versão: {payload['mechanism']}")
                with tempfile.NamedTemporaryFile(prefix=".incompleto-", suffix=".7z", dir=folder, delete=False) as reserved:
                    temporary = Path(reserved.name)
                temporary.unlink()  # 7z creates a new container, not an empty existing file
                total = len(selected)
                for current, item in enumerate(selected, 1):
                    checkpoint(cancel)
                    path = signature(item)
                    progress("Compactando", current - 1, total)
                    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                    with os.fdopen(os.open(path, flags), "rb") as stream:
                        info = os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode):
                            raise RuntimeError(f"Origem não é arquivo regular: {path}")
                        if (info.st_size, info.st_mtime_ns) != (item.size, item.mtime_ns):
                            raise RuntimeError(f"Original alterado ao abrir: {path}")
                        engine.add(temporary, item, stream, cancel)
                        info = os.fstat(stream.fileno())
                        if (info.st_size, info.st_mtime_ns) != (item.size, item.mtime_ns):
                            raise RuntimeError(f"Original alterado durante leitura: {path}")
                    signature(item)
                    payload["processed"] = current
                    record(f"Compactado temporário (ainda não publicado): {path} -> {item.internal}; {item.size} bytes; decisão: {item.decision}")
                    if time.monotonic() - last_saved >= 1:
                        persist()
                        last_saved = time.monotonic()
                    progress("Compactando", current, total)
                checkpoint(cancel)
                progress("Validando integridade e manifesto", 0, 0)
                payload["validation"] = engine.validate(temporary, selected, cancel)
                payload["inspection"] = getattr(engine, "last_inspection", {})
                for item in selected:
                    checkpoint(cancel)
                    signature(item)
                validated_size = temporary.stat().st_size
                record(payload["validation"])
                record("Tamanho/modificação conferidos antes e depois; não é comparação criptográfica nem snapshot.")
                checkpoint(cancel)
                # Persist intent before publishing; recovery can explain an interrupted finalization.
                persist()
                publish_without_overwrite(temporary, archive)
                temporary = None
                payload["included"] = len(selected)
                payload["included_paths"] = [item.path for item in selected]
                payload["published"] = True
                payload.update(size_summary(sum(item.size for item in selected), validated_size))
                state = "concluída com avisos" if analysis.incomplete or analysis.issues or analysis.missing else "concluída"
            except Cancelled as exc:
                state = "cancelada"
                payload["errors"].append(sanitized(exc))
            except Exception as exc:
                state = "falhou"
                payload["errors"].append(sanitized(exc))
            finally:
                # 7-Zip can create additional update temporaries beside its target.
                for leftover in folder.glob(".incompleto-*"):
                    try:
                        leftover.unlink()
                    except OSError as exc:
                        payload["errors"].append(f"Temporário não removido: {leftover}: {sanitized(exc)}")
                if state == "concluída" and payload["errors"]:
                    state = "concluída com avisos"
                payload["ended"] = datetime.now().isoformat()
                try:
                    record(f"Fim | estado: {state}")
                    record(payload)
                except OSError as exc:
                    payload["errors"].append(f"Não foi possível finalizar o TXT: {sanitized(exc)}")
                    if state == "concluída":
                        state = "concluída com avisos"
                finally:
                    persist()  # Output disk/network failure must not leave local history running.
        return {"id": identity, "state": state, **payload}
