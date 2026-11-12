"""Exclusive application sessions across PCs sharing the same data directory."""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import uuid

from .shared_database import needs_preparation, prepare_database
from .shared_files import atomic_write, file_lock, ResourceBusy

# Entries exist only while this process owns the OS locks. Never persist credentials.
_SESSIONS: dict[str, tuple[Path, Path]] = {}
PENDING_USER = 'sessão iniciando ou aguardando login'


def _display_user(info: dict) -> str:
    username = info.get('usuario')
    if not isinstance(username, str) or not username.strip():
        return PENDING_USER
    # Metadata is untrusted input; keep notices on one line and reasonably bounded.
    return ' '.join(username.split())[:120]


def active_sessions(data: Path, *, exclude_own=True) -> list[str]:
    result = []
    for path in (data / '.sessions').glob('*.lock'):
        if exclude_own and path.stem in _SESSIONS:
            continue
        try:
            with file_lock(path):
                continue  # Stale metadata does not represent an active session.
        except ResourceBusy:
            try:
                info = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
                result.append(_display_user(info) if isinstance(info, dict) else PENDING_USER)
            except (OSError, ValueError):
                result.append(PENDING_USER)
    return sorted(set(result))


def set_session_user(data: Path, identity: dict) -> None:
    """Publish only the authenticated username, after login has succeeded."""
    username = identity.get('usuario')
    if not isinstance(username, str) or not username.strip():
        raise ValueError('Identidade autenticada sem nome de usuário.')
    root = Path(data).resolve()
    for execution, (session_root, metadata) in tuple(_SESSIONS.items()):
        if session_root == root:
            atomic_write(metadata, json.dumps({
                'usuario': username, 'pid': os.getpid(), 'execution': execution,
            }, ensure_ascii=False))
            return
    raise RuntimeError('Não há sessão exclusiva para registrar o usuário conectado.')


def _busy_message(data: Path) -> str:
    users = active_sessions(data, exclude_own=False)
    if users and any(user != PENDING_USER for user in users):
        names = ', '.join(user for user in users if user != PENDING_USER)
        return f'Sistema em uso por {names}. Aguarde o usuário fechar o aplicativo para entrar.'
    if users:
        return 'Sistema em uso: há uma sessão iniciando ou aguardando login. Aguarde seu encerramento para entrar.'
    return 'Sistema indisponível: outra sessão ou manutenção está em andamento. Tente novamente após o encerramento.'


@contextmanager
def session(data: Path, *, timeout=0):
    data = Path(data).resolve()
    database = data / 'estado_indexador.sqlite3'
    gate = data / '.update-data.lock'
    with ExitStack() as stack:
        try:
            # Covers login, reads, workers and maintenance. Never downgrade this lock.
            stack.enter_context(file_lock(gate, timeout=timeout))
        except ResourceBusy as exc:
            raise ResourceBusy(_busy_message(data)) from exc
        execution = uuid.uuid4().hex
        lease = data / '.sessions' / (execution + '.lock')
        metadata = lease.with_suffix('.json')
        stack.enter_context(file_lock(lease))
        try:
            atomic_write(metadata, json.dumps({
                'usuario': None, 'pid': os.getpid(), 'execution': execution,
            }))
            _SESSIONS[execution] = (data, metadata)
            if needs_preparation(database):
                prepare_database(database)
            yield
        finally:
            # Remove metadata before releasing the lease; readers never trust stale files.
            _SESSIONS.pop(execution, None)
            metadata.unlink(missing_ok=True)
