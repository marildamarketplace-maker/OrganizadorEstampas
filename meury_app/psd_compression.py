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
from types import SimpleNamespace

from .compression_store import CompressionStore
from .index_journal import catalog_lock
from .platform_utils import publish_without_overwrite
from .psd_search import Cancelled, checkpoint, portable_key, safe_source, validate_internal
from .sevenzip import SevenZip


def write_content_report(identity, payload):
    """Only final validated members belong in the content section."""
    report = Path(payload["report"])
    lines = ["CONTEÚDO FINAL DA COMPACTAÇÃO", f"Execução: {identity}",
             f"Compactado: {payload['archive']}", f"Validação: {payload['validation']}",
             f"PSDs incluídos: {len(payload.get('final_files', []))}",
             "Localizações registradas no momento da compactação; disponibilidade atual não verificada.",
             "", "ARQUIVOS EFETIVAMENTE INCLUÍDOS"]
    for item in payload.get("final_files", []):
        lines.extend([f"Nome: {Path(item['path']).name}", f"Origem: {item['path']}",
                      f"Interno: {item['internal']}", f"Tamanho: {item['size']} bytes", ""])
    lines.extend(["", "ARQUIVOS IGNORADOS"])
    for item in payload.get("analysis", {}).get("files", []):
        if not item.get("selected"):
            lines.append(f"{item['path']} — {item.get('decision', 'não selecionado')}")
    lines.extend(["", "ERROS E AVISOS"])
    lines.extend(str(error) for error in payload.get("errors", []))
    lines.extend(str(error) for error in payload.get("analysis", {}).get("issues", []))
    lines.extend(f"Nome não encontrado: {name}" for name in payload.get("analysis", {}).get("missing", []))
    # Atomic replacement also allows an interrupted report to be rebuilt on recovery.
    temporary = report.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, report)


def recover_run(identity, payload, engine=None):
    """Validate the persisted publication intent without consulting original PSDs."""
    from .sevenzip import ArchiveUnavailable
    payload.setdefault("errors", [])
    manifest = payload.get("approved_manifest")
    if payload.get("stage") != "publishing" or not manifest or not payload.get("archive"):
        state = "interrompida"
    else:
        archive = Path(payload["archive"])
        try:
            # Do not use exists(): some platforms suppress access errors as False.
            info = archive.stat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Destino publicado não é arquivo regular")
            with archive.open("rb") as stream:
                stream.read(1)
        except FileNotFoundError as exc:
            # A missing share/mount is not evidence that its archive was deleted.
            try:
                archive.parent.stat()
            except OSError:
                payload["recovery_error"] = str(exc)
                return "pendente de recuperação", payload
            state = "compactado ausente"
            payload["validation"] = "Recuperação: compactado final ausente; inclusão não confirmada."
        except OSError as exc:
            payload["recovery_error"] = str(exc)
            return "pendente de recuperação", payload
        except ValueError as exc:
            payload["recovery_error"] = str(exc)
            state = "compactado inválido"
        else:
            try:
                validator = engine or SevenZip()
            except Exception as exc:
                payload["recovery_error"] = str(exc)
                return "pendente de recuperação", payload
            try:
                keys = set()
                for item in manifest:
                    key = portable_key(validate_internal(item["internal"]))
                    if key in keys or not isinstance(item["size"], int) or item["size"] < 0:
                        raise ValueError("Manifesto persistido inválido")
                    keys.add(key)
                    if not isinstance(item["path"], str) or not isinstance(item["mtime_ns"], int):
                        raise ValueError("Metadados do manifesto inválidos")
                items = [SimpleNamespace(**item) for item in manifest]
                payload["validation"] = validator.validate(archive, items, lambda: False)
                after = archive.stat()
                if (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                    raise ArchiveUnavailable("Compactado alterado durante a validação; tente novamente")
            except (OSError, ArchiveUnavailable) as exc:
                payload["recovery_error"] = str(exc)
                return "pendente de recuperação", payload
            except (RuntimeError, ValueError, TypeError, KeyError) as exc:
                payload["recovery_error"] = str(exc)
                payload["validation"] = f"Falha na validação de recuperação: {exc}"
                state = "compactado inválido"
            else:
                payload.update(published=True, final_files=manifest, included=len(manifest),
                               included_paths=[item["path"] for item in manifest], recovered=True)
                payload.update(size_summary(sum(item["size"] for item in manifest), info.st_size))
                payload.pop("recovery_error", None)
                payload["ended"] = datetime.now().isoformat()
                payload.setdefault("report", str(archive.with_suffix(".conteudo.txt")))
                try:
                    write_content_report(identity, payload)
                except OSError as exc:
                    payload["recovery_error"] = f"Relatório pendente: {exc}"
                    return "pendente de recuperação", payload
                return "recuperada", payload
    # Keep the technical log, including legacy interrupted runs.
    try:
        log = Path(str(payload.get("log", "")))
        if log.is_absolute() and log.suffix == ".txt":
            with log.open("a", encoding="utf-8") as stream:
                stream.write(f"{datetime.now().isoformat()} Estado: {state} (recuperação).\n")
    except OSError:
        pass
    payload.update(published=False, final_files=[], included=0, included_paths=[])
    return state, payload


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
        store.recover(locked=True)
        output.mkdir(parents=True, exist_ok=True)
        # Exclusive directory reserves the result names, including across application instances.
        identity = uuid.uuid4().hex
        folder = output / f"compactacao_{datetime.now():%Y%m%d_%H%M%S}_{identity[:12]}"
        folder.mkdir()
        base = folder / folder.name
        archive = base.with_suffix(".7z")
        log_path = base.with_suffix(".txt")
        payload = {"analysis": analysis.snapshot(), "archive": str(archive), "log": str(log_path),
                   "report": str(base.with_suffix(".conteudo.txt")),
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
                payload["approved_manifest"] = [dict(path=item.path, internal=item.internal,
                                                     size=item.size, mtime_ns=item.mtime_ns) for item in selected]
                payload["stage"] = "preparing"
                persist()
                checkpoint(cancel)
                engine = engine or SevenZip()
                record(f"Mecanismo: {getattr(engine, 'executable', type(engine).__name__)}")
                progress("Verificando capacidade do 7-Zip", 0, 0)
                engine.probe(cancel)
                payload["mechanism"] = getattr(engine, "version", "Mecanismo simulado")
                record(f"Versão: {payload['mechanism']}")
                # NAS/Samba can map a leading dot to the Windows Hidden attribute,
                # which survives renaming the temporary archive to its final name.
                with tempfile.NamedTemporaryFile(prefix="incompleto-", suffix=".7z", dir=folder, delete=False) as reserved:
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
                payload["stage"] = "publishing"
                persist()
                publish_without_overwrite(temporary, archive)
                temporary = None
                payload["included"] = len(selected)
                payload["included_paths"] = [item.path for item in selected]
                payload["published"] = True
                payload["final_files"] = payload["approved_manifest"]
                payload.update(size_summary(sum(item.size for item in selected), validated_size))
                state = "concluída com avisos" if analysis.incomplete or analysis.issues or analysis.missing else "concluída"
            except Cancelled as exc:
                state = "cancelada"
                payload["errors"].append(sanitized(exc))
            except Exception as exc:
                state = "pendente de recuperação" if payload.get("stage") == "publishing" else "falhou"
                payload["errors"].append(sanitized(exc))
            finally:
                # 7-Zip can create additional update temporaries beside its target.
                for leftover in folder.glob("incompleto-*"):
                    try:
                        leftover.unlink()
                    except OSError as exc:
                        payload["errors"].append(f"Temporário não removido: {leftover}: {sanitized(exc)}")
                if state == "concluída" and payload["errors"]:
                    state = "concluída com avisos"
                payload["ended"] = datetime.now().isoformat()
                if payload["published"]:
                    try:
                        write_content_report(identity, payload)
                    except OSError as exc:
                        payload["errors"].append(f"Não foi possível gerar relatório: {sanitized(exc)}")
                        state = "pendente de recuperação"
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
