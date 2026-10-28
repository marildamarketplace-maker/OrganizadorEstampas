"""Compression history alongside the existing local operational database."""
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
        try:
            with connection:
                connection.execute("CREATE TABLE IF NOT EXISTS compression_run (id TEXT PRIMARY KEY, started TEXT NOT NULL, updated TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL)")
                yield connection
        finally:
            connection.close()

    def save(self, identity, state, payload):
        with self.connect() as connection:
            connection.execute("INSERT INTO compression_run VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, state=excluded.state, payload=excluded.payload",
                               (identity, now(), now(), state, json.dumps(payload, ensure_ascii=False)))

    def recover(self):
        # Caller holds the process lock: a second running instance cannot be marked abandoned.
        with self.connect() as connection:
            abandoned = list(connection.execute("SELECT * FROM compression_run WHERE state='em andamento'"))
            for row in abandoned:
                try:
                    payload = json.loads(row["payload"])
                except (ValueError, TypeError):
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                try:
                    log = Path(str(payload.get("log", "")))
                    if log.is_absolute() and log.is_file() and log.suffix == ".txt":
                        with log.open("a", encoding="utf-8") as stream:
                            stream.write(f"{now()} Estado: interrompida (detectado ao reiniciar). Verifique artefatos incompletos nesta pasta.\n")
                except OSError:
                    pass  # History remains available even if the output share is offline.
            connection.execute("UPDATE compression_run SET state='interrompida', updated=? WHERE state='em andamento'", (now(),))

    def list(self, limit=200):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT id, started, updated, state FROM compression_run ORDER BY started DESC LIMIT ?", (limit,))]

    def get(self, identity):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM compression_run WHERE id=?", (identity,)).fetchone()
            if not row:
                raise ValueError("Execução não encontrada")
            result = dict(row)
            result["payload"] = json.loads(result["payload"])
            return result
