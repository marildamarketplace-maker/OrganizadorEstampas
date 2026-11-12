"""Connection policy and offline preparation of the shared operational database."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
from itertools import islice
import uuid


DB_TIMEOUT = 10


def readonly_uri(path: Path) -> str:
    uri = path.resolve().as_uri()
    if uri.startswith('file://') and not uri.startswith('file:///'):
        uri = uri.replace('file://', 'file:////', 1)
    return uri + '?mode=ro'


def connect(path: Path, *, readonly=False) -> sqlite3.Connection:
    if not readonly:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(readonly_uri(path) if readonly else path,
                                 uri=readonly, timeout=DB_TIMEOUT)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        if connection.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal':
            raise RuntimeError('Banco em WAL. Feche o aplicativo em todos os PCs e abra a nova versão para preparar o compartilhamento.')
        from .operational_store import SCHEMA_VERSION
        if connection.execute('PRAGMA user_version').fetchone()[0] > SCHEMA_VERSION:
            raise RuntimeError('Banco criado por uma versão mais recente do aplicativo.')
        if not readonly:
            connection.execute('PRAGMA synchronous=FULL')
        return connection
    except BaseException:
        connection.close()
        raise


def needs_preparation(path: Path) -> bool:
    from .operational_store import SCHEMA_VERSION
    if not path.exists():
        return True
    with closing(sqlite3.connect(readonly_uri(path), uri=True, timeout=DB_TIMEOUT)) as connection:
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError('Banco criado por uma versão mais recente do aplicativo.')
        return version < SCHEMA_VERSION or connection.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'delete'


def prepare_database(path: Path) -> None:
    """Caller holds the exclusive .update-data.lock; never called by login/readers."""
    from .operational_store import ensure_schema, SCHEMA_VERSION
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=DB_TIMEOUT)) as connection:
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError('Banco criado por uma versão mais recente do aplicativo.')
        if path.stat().st_size:
            backup = path.with_name(path.name + '.backup-' + datetime.now().strftime('%Y%m%d_%H%M%S') + '-' + uuid.uuid4().hex)
            with closing(sqlite3.connect(backup)) as destination:
                connection.backup(destination)
        if connection.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal':
            if connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0:
                raise RuntimeError('Banco em uso; feche todos os PCs antes de converter WAL.')
        mode = connection.execute('PRAGMA journal_mode=DELETE').fetchone()[0]
        if mode.lower() != 'delete':
            raise RuntimeError('Não foi possível preparar o banco compartilhado.')
        connection.execute('PRAGMA synchronous=FULL')
        with connection:
            ensure_schema(connection)


def ensure_current_schema(connection: sqlite3.Connection) -> None:
    """Initialize empty stores for tools/tests; migrate existing stores only offline."""
    from .operational_store import SCHEMA_VERSION, ensure_schema
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version == 0 and not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='image_state'").fetchone():
        ensure_schema(connection)
        connection.commit()
        return
    raise RuntimeError('Banco precisa de preparação exclusiva. Feche todos os PCs e abra o aplicativo novamente.')


def write_batches(connection: sqlite3.Connection, sql: str, values, *, batch_size=200) -> None:
    """Bound writer lock duration; callers must support idempotent replay."""
    iterator = iter(values)
    while batch := list(islice(iterator, batch_size)):
        with connection:
            connection.executemany(sql, batch)
