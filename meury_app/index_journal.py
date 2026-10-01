"""Diário de candidatos verificados; nunca publica uma varredura parcial.

A retomada enumera novamente todas as origens e reconcilia contra o catálogo
publicado. Só reutiliza validação/hash com a mesma identidade e assinatura
física. Nenhum estado operacional ou ausência é aplicado pelo diário.
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import time

from .index_diagnostics import count, stage


def physical_signature(stat):
    return [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino]


@contextmanager
def catalog_lock(path):
    """Lock de processo liberado pelo SO inclusive após encerramento abrupto."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Outra atualização deste catálogo está em andamento.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class CandidateJournal:
    batch_size = 1000
    interval_seconds = 5.0

    def __init__(self, path: Path, sources: list[Path], version: int):
        self.path = path
        self.header = {"type": "candidate_journal", "version": 1,
                       "index_version": version, "sources": [
                           [str(root), root.stat().st_dev, root.stat().st_ino]
                           for root in sources]}
        self.cached = {}
        self.pending = []
        self.last_flush = time.monotonic()
        self.initialized = False
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        with stage("journal_recovery"):
            with self.path.open("rb") as stream:
                header = stream.readline()
                count("journal_bytes_read", len(header))
                try:
                    compatible = json.loads(header) == self.header
                except (ValueError, UnicodeDecodeError):
                    compatible = False
                if not compatible:
                    return  # Sobrescrito somente quando houver novo trabalho.
                self.initialized = True
                valid_end = stream.tell()
                for line in stream:
                    count("journal_bytes_read", len(line))
                    try:
                        event = json.loads(line)
                        if (not line.endswith(b"\n") or not isinstance(event, dict)
                                or not isinstance(event.get("key"), str)
                                or not isinstance(event.get("signature"), list)
                                or len(event["signature"]) != 5
                                or not isinstance(event.get("hash"), str)
                                or len(event["hash"]) != 64
                                or "validation" not in event
                                or (event["validation"] is not None and (
                                    not isinstance(event["validation"], list)
                                    or len(event["validation"]) != 2))):
                            break
                    except (ValueError, UnicodeDecodeError):
                        break
                    self.cached[event["key"]] = event
                    valid_end = stream.tell()
            # Remove somente a cauda incompleta, permitindo append recuperável.
            if self.path.stat().st_size != valid_end:
                with self.path.open("r+b") as stream:
                    stream.truncate(valid_end)
                    stream.flush()
                    os.fsync(stream.fileno())

    def lookup(self, key, stat):
        event = self.cached.pop(key, None)
        if event and event["signature"] == physical_signature(stat):
            count("recovered_candidates")
            return event
        return None

    def add(self, key, stat, validation, content_hash):
        event = {"key": key, "signature": physical_signature(stat),
                 "validation": validation, "hash": content_hash}
        self.pending.append(event)
        self.flush_if_needed()

    def flush_if_needed(self):
        if self.pending and (len(self.pending) >= self.batch_size
                             or time.monotonic() - self.last_flush >= self.interval_seconds):
            self.flush()

    def flush(self):
        if not self.pending:
            return
        with stage("checkpoints"):
            events = self.pending if self.initialized else [self.header, *self.pending]
            data = b"".join((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
                            for event in events)
            with self.path.open("ab" if self.initialized else "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            count("checkpoints")
            count("journal_records_written", len(self.pending))
            count("journal_bytes_written", len(data))
            self.pending.clear()
            self.initialized = True
            self.last_flush = time.monotonic()

    def complete(self):
        self.path.unlink(missing_ok=True)
