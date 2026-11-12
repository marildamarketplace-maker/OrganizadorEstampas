"""File coordination for local disks and SMB; OS locks are the authority."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import unicodedata
import uuid

import portalocker


class ResourceBusy(RuntimeError):
    """A cooperating process currently owns the resource."""


def resource_path(directory: Path, key: str) -> Path:
    normalized = unicodedata.normalize('NFKC', str(key)).strip().casefold()
    return directory / (hashlib.sha256(normalized.encode('utf-8')).hexdigest() + '.lock')


def atomic_write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content.encode('utf-8') if isinstance(content, str) else content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def file_lock(path: Path, *, shared=False, timeout=0, cancel=None, owner=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = (portalocker.LOCK_SH if shared else portalocker.LOCK_EX) | portalocker.LOCK_NB
    lock = portalocker.Lock(str(path), mode='a+b', flags=flags, timeout=0)
    deadline = time.monotonic() + timeout
    while True:
        if cancel and cancel():
            raise RuntimeError('Operação cancelada enquanto aguardava recurso compartilhado.')
        try:
            stream = lock.acquire()
            break
        except portalocker.exceptions.AlreadyLocked as exc:
            if time.monotonic() >= deadline:
                raise ResourceBusy(f'Recurso em uso por outro computador: {path.name}') from exc
            time.sleep(0.1)
        # Permission and I/O errors must propagate, never be treated as free locks.
    try:
        if owner and not shared:
            stream.seek(0)
            stream.truncate()
            stream.write(json.dumps({'computer': socket.gethostname(), 'pid': os.getpid(),
                                     'execution': uuid.uuid4().hex, 'resource': path.name}).encode())
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        lock.release()
