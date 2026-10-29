"""Transactional compression history alongside the local operational database."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .config import OPERATIONAL_DB_FILE


def now():
    return datetime.now(timezone.utc).isoformat()


class CompressionStore:
    def __init__(self, path=OPERATIONAL_DB_FILE):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                connection.execute("CREATE TABLE IF NOT EXISTS compression_run (id TEXT PRIMARY KEY, started TEXT NOT NULL, updated TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL)")
                # Existing analyses are never taken as evidence of inclusion.
                connection.execute("""CREATE TABLE IF NOT EXISTS compression_file (
                    run_id TEXT NOT NULL REFERENCES compression_run(id),
                    name TEXT NOT NULL, original_path TEXT NOT NULL,
                    internal_path TEXT NOT NULL, size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL, name_key TEXT NOT NULL,
                    path_key TEXT NOT NULL, PRIMARY KEY(run_id, internal_path))""")
                connection.execute("CREATE INDEX IF NOT EXISTS compression_file_name ON compression_file(name_key)")
                connection.execute("CREATE INDEX IF NOT EXISTS compression_file_path ON compression_file(path_key)")
                yield connection
        finally:
            connection.close()

    def save(self, identity, state, payload):
        with self.connect() as connection:
            connection.execute("INSERT INTO compression_run VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, state=excluded.state, payload=excluded.payload",
                               (identity, now(), now(), state, json.dumps(payload, ensure_ascii=False)))
            if payload.get("published") and state in {"concluída", "concluída com avisos", "recuperada"}:
                for item in payload.get("final_files", []):
                    path = item["path"]
                    name = Path(path).name
                    connection.execute("""INSERT INTO compression_file VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(run_id, internal_path) DO UPDATE SET name=excluded.name,
                        original_path=excluded.original_path, size=excluded.size,
                        mtime_ns=excluded.mtime_ns, name_key=excluded.name_key, path_key=excluded.path_key""",
                        (identity, name, path, item["internal"], item["size"], item["mtime_ns"], name.casefold(), path.casefold()))

    def recover(self, engine=None, locked=False):
        from .index_journal import catalog_lock
        if not locked:
            with catalog_lock(self.path.parent / "compactacao.lock"):
                return self.recover(engine, locked=True)
        from .psd_compression import recover_run
        with self.connect() as connection:
            rows = list(connection.execute("SELECT * FROM compression_run WHERE state IN ('em andamento', 'pendente de recuperação')"))
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (ValueError, TypeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            state, payload = recover_run(row["id"], payload, engine)
            self.save(row["id"], state, payload)

    def list(self, limit=200, query=""):
        with self.connect() as connection:
            if query.strip():
                key = query.strip().casefold()
                return [dict(row) for row in connection.execute("""SELECT DISTINCT r.id, r.started, r.updated, r.state
                    FROM compression_run r JOIN compression_file f ON f.run_id=r.id
                    WHERE instr(f.name_key, ?) > 0 OR instr(f.path_key, ?) > 0
                    ORDER BY r.started DESC LIMIT ?""", (key, key, limit))]
            return [dict(row) for row in connection.execute("SELECT id, started, updated, state FROM compression_run ORDER BY started DESC LIMIT ?", (limit,))]

    def files(self, identity):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM compression_file WHERE run_id=? ORDER BY internal_path", (identity,))]

    def get(self, identity):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM compression_run WHERE id=?", (identity,)).fetchone()
            if not row:
                raise ValueError("Execução não encontrada")
            result = dict(row)
            result["payload"] = json.loads(result["payload"])
            result["files"] = [dict(item) for item in connection.execute("SELECT * FROM compression_file WHERE run_id=? ORDER BY internal_path", (identity,))]
            return result
