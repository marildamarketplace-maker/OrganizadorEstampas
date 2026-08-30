"""Estado operacional persistente do indexador, armazenado em SQLite local."""

from __future__ import annotations

from pathlib import Path
from contextlib import closing
import sqlite3
import time


SCHEMA_VERSION = 8
STATE_COLUMNS = (
    "path", "codigo", "variante", "filename", "asset_id", "size", "last_modified",
    "content_hash", "indexed", "changed", "scan_status", "processing_status",
    "preview_status", "preview_path", "preview_content_hash", "cloud_status",
    "storage_key", "preview_url", "cloud_content_hash", "supabase_status",
    "supabase_content_hash",
    "preview_attempts", "preview_last_error", "preview_last_attempt_at",
    "cloud_attempts", "cloud_last_error", "cloud_last_attempt_at",
    "supabase_attempts", "supabase_last_error", "supabase_last_attempt_at",
    "active", "missing_locally",
    "last_error", "missing_detected_at", "review_required", "review_reason",
    "attention_status", "attention_reason", "attention_at", "last_indexed_at",
    "last_synced_at",
)


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS image_state (
            source INTEGER NOT NULL,
            relative_path TEXT NOT NULL,
            path TEXT NOT NULL DEFAULT '',
            codigo TEXT NOT NULL DEFAULT '',
            variante TEXT NOT NULL DEFAULT '',
            filename TEXT NOT NULL DEFAULT '',
            asset_id TEXT NOT NULL DEFAULT '',
            size INTEGER NOT NULL DEFAULT -1,
            last_modified INTEGER NOT NULL DEFAULT -1,
            content_hash TEXT NOT NULL DEFAULT '',
            indexed INTEGER NOT NULL DEFAULT 1,
            changed INTEGER NOT NULL DEFAULT 0,
            scan_status TEXT NOT NULL DEFAULT 'indexed',
            processing_status TEXT NOT NULL DEFAULT 'indexed',
            preview_status TEXT NOT NULL DEFAULT 'pending',
            preview_path TEXT NOT NULL DEFAULT '',
            preview_content_hash TEXT NOT NULL DEFAULT '',
            cloud_status TEXT NOT NULL DEFAULT 'pending',
            storage_key TEXT NOT NULL DEFAULT '',
            preview_url TEXT NOT NULL DEFAULT '',
            cloud_content_hash TEXT NOT NULL DEFAULT '',
            supabase_status TEXT NOT NULL DEFAULT 'pending',
            supabase_content_hash TEXT NOT NULL DEFAULT '',
            preview_attempts INTEGER NOT NULL DEFAULT 0,
            preview_last_error TEXT NOT NULL DEFAULT '',
            preview_last_attempt_at TEXT NOT NULL DEFAULT '',
            cloud_attempts INTEGER NOT NULL DEFAULT 0,
            cloud_last_error TEXT NOT NULL DEFAULT '',
            cloud_last_attempt_at TEXT NOT NULL DEFAULT '',
            supabase_attempts INTEGER NOT NULL DEFAULT 0,
            supabase_last_error TEXT NOT NULL DEFAULT '',
            supabase_last_attempt_at TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            missing_locally INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            missing_detected_at TEXT NOT NULL DEFAULT '',
            review_required INTEGER NOT NULL DEFAULT 0,
            review_reason TEXT NOT NULL DEFAULT '',
            attention_status TEXT NOT NULL DEFAULT '',
            attention_reason TEXT NOT NULL DEFAULT '',
            attention_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_indexed_at TEXT NOT NULL DEFAULT '',
            last_synced_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (source, relative_path)
        );
        CREATE INDEX IF NOT EXISTS idx_image_state_pending
            ON image_state(processing_status, preview_status, cloud_status, supabase_status);
        CREATE INDEX IF NOT EXISTS idx_image_state_missing
            ON image_state(missing_locally);
        CREATE TABLE IF NOT EXISTS quarantine_issue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source INTEGER,
            relative_path TEXT NOT NULL DEFAULT '',
            filename TEXT NOT NULL DEFAULT '',
            path TEXT NOT NULL,
            reason TEXT NOT NULL,
            technical_message TEXT NOT NULL DEFAULT '',
            detected_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'REQUIRES_ATTENTION',
            UNIQUE(path, reason, technical_message, status)
        );
        CREATE INDEX IF NOT EXISTS idx_quarantine_status
            ON quarantine_issue(status, detected_at);
        CREATE TABLE IF NOT EXISTS scan_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at TEXT NOT NULL,
            total_found INTEGER NOT NULL DEFAULT 0,
            unchanged_files INTEGER NOT NULL DEFAULT 0,
            added_files INTEGER NOT NULL DEFAULT 0,
            changed_files INTEGER NOT NULL DEFAULT 0,
            absent_files INTEGER NOT NULL DEFAULT 0,
            moved_files INTEGER NOT NULL DEFAULT 0,
            errors INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(image_state)")
    }
    if "asset_id" not in columns:
        connection.execute(
            "ALTER TABLE image_state ADD COLUMN asset_id TEXT NOT NULL DEFAULT ''"
        )
    if "missing_detected_at" not in columns:
        connection.execute(
            "ALTER TABLE image_state ADD COLUMN missing_detected_at TEXT NOT NULL DEFAULT ''"
        )
    if "review_required" not in columns:
        connection.execute(
            "ALTER TABLE image_state ADD COLUMN review_required INTEGER NOT NULL DEFAULT 0"
        )
    if "review_reason" not in columns:
        connection.execute(
            "ALTER TABLE image_state ADD COLUMN review_reason TEXT NOT NULL DEFAULT ''"
        )
    for name in ("attention_status", "attention_reason", "attention_at"):
        if name not in columns:
            connection.execute(
                f"ALTER TABLE image_state ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
            )
    for name in ("preview_content_hash", "cloud_content_hash", "supabase_content_hash"):
        if name not in columns:
            connection.execute(
                f"ALTER TABLE image_state ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
            )
    retry_columns = {
        "preview_attempts": "INTEGER NOT NULL DEFAULT 0",
        "preview_last_error": "TEXT NOT NULL DEFAULT ''",
        "preview_last_attempt_at": "TEXT NOT NULL DEFAULT ''",
        "cloud_attempts": "INTEGER NOT NULL DEFAULT 0",
        "cloud_last_error": "TEXT NOT NULL DEFAULT ''",
        "cloud_last_attempt_at": "TEXT NOT NULL DEFAULT ''",
        "supabase_attempts": "INTEGER NOT NULL DEFAULT 0",
        "supabase_last_error": "TEXT NOT NULL DEFAULT ''",
        "supabase_last_attempt_at": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in retry_columns.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE image_state ADD COLUMN {name} {definition}")
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def _values(record: dict, now: str) -> tuple:
    return (
        int(record.get("source", 0)),
        str(record.get("relative_path", "")).replace("\\", "/").casefold(),
        str(record.get("relative_path", "")).replace("\\", "/"),
        str(record.get("codigo", record.get("design_id", ""))),
        str(record.get("variante", "")), str(record.get("filename", "")),
        str(record.get("asset_id", "")),
        int(record.get("size", -1)), int(record.get("mtime_ns", -1)),
        str(record.get("content_hash", "")), int(bool(record.get("indexed", True))),
        int(bool(record.get("changed", False))), str(record.get("scan_status", "indexed")),
        str(record.get("processing_status", "indexed")),
        str(record.get("preview_status", "pending")), str(record.get("preview_path", "")),
        str(record.get("preview_content_hash", "")), str(record.get("cloud_status", "pending")),
        str(record.get("storage_key", "")), str(record.get("preview_url", "")),
        str(record.get("cloud_content_hash", "")), str(record.get("supabase_status", "pending")),
        str(record.get("supabase_content_hash", "")),
        int(record.get("preview_attempts", 0)), str(record.get("preview_last_error", "")),
        str(record.get("preview_last_attempt_at", "")), int(record.get("cloud_attempts", 0)),
        str(record.get("cloud_last_error", "")), str(record.get("cloud_last_attempt_at", "")),
        int(record.get("supabase_attempts", 0)), str(record.get("supabase_last_error", "")),
        str(record.get("supabase_last_attempt_at", "")),
        int(bool(record.get("active", True))), int(bool(record.get("missing_locally", False))),
        str(record.get("last_error", "")), str(record.get("missing_detected_at", "")),
        int(bool(record.get("review_required", False))), str(record.get("review_reason", "")),
        str(record.get("attention_status", "")), str(record.get("attention_reason", "")),
        str(record.get("attention_at", "")), now,
        str(record.get("last_indexed_at", "")), str(record.get("last_synced_at", "")),
    )


def sync_records(db_path: Path, records: list[dict]) -> None:
    """Migra ou atualiza registros em uma transação idempotente."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    columns = (
        "source, relative_path, path, codigo, variante, filename, asset_id, size, last_modified, "
        "content_hash, indexed, changed, scan_status, processing_status, preview_status, "
        "preview_path, preview_content_hash, cloud_status, storage_key, preview_url, "
        "cloud_content_hash, supabase_status, supabase_content_hash, "
        "preview_attempts, preview_last_error, preview_last_attempt_at, "
        "cloud_attempts, cloud_last_error, cloud_last_attempt_at, "
        "supabase_attempts, supabase_last_error, supabase_last_attempt_at, active, "
        "missing_locally, last_error, missing_detected_at, review_required, review_reason, "
        "attention_status, attention_reason, attention_at, updated_at, last_indexed_at, last_synced_at"
    )
    update_columns = [name.strip() for name in columns.split(",")][2:]
    assignments = ", ".join(f"{name}=excluded.{name}" for name in update_columns)
    sql = (
        f"INSERT INTO image_state ({columns}, created_at) VALUES "
        f"({','.join('?' for _ in range(45))}) "
        f"ON CONFLICT(source, relative_path) DO UPDATE SET {assignments}"
    )
    with closing(_connect(db_path)) as connection, connection:
        ensure_schema(connection)
        connection.executemany(
            sql,
            (values + (now,) for values in (_values(record, now) for record in records)),
        )


def overlay_records(db_path: Path, records: list[dict]) -> None:
    """Aplica o estado SQLite sobre registros do catálogo sem mudar seus consumidores."""
    if not db_path.exists():
        sync_records(db_path, records)
        return
    by_identity = {
        (int(record.get("source", 0)), str(record.get("relative_path", "")).replace("\\", "/").casefold()): record
        for record in records
    }
    with closing(_connect(db_path)) as connection, connection:
        ensure_schema(connection)
        for row in connection.execute("SELECT * FROM image_state"):
            record = by_identity.get((int(row["source"]), str(row["relative_path"])))
            if record is None:
                continue
            for column in STATE_COLUMNS:
                value = row[column]
                # Bancos anteriores à v8 recebem esta coluna vazia. Preserve a
                # identidade derivada do JSONL até o próximo sync persistí-la.
                if column == "asset_id" and not str(value).strip():
                    continue
                if column == "last_modified":
                    record["mtime_ns"] = int(value)
                elif column in {
                    "indexed", "changed", "active", "missing_locally", "review_required"
                }:
                    record[column] = bool(value)
                else:
                    record[column] = value


def record_quarantine_issues(db_path: Path, issues: list[dict]) -> None:
    if not issues:
        return
    sql = """
        INSERT INTO quarantine_issue (
            source, relative_path, filename, path, reason, technical_message,
            detected_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'REQUIRES_ATTENTION')
        ON CONFLICT(path, reason, technical_message, status)
        DO UPDATE SET detected_at=excluded.detected_at
    """
    with closing(_connect(db_path)) as connection, connection:
        ensure_schema(connection)
        connection.executemany(sql, (
            (
                issue.get("source"), str(issue.get("relative_path", "")),
                str(issue.get("filename", "")), str(issue.get("path", "")),
                str(issue.get("reason", "")), str(issue.get("technical_message", "")),
                str(issue.get("detected_at", "")),
            )
            for issue in issues
        ))


def load_quarantine_issues(db_path: Path, limit: int = 1000) -> list[dict]:
    if not db_path.exists():
        return []
    with closing(_connect(db_path)) as connection, connection:
        ensure_schema(connection)
        rows = connection.execute(
            "SELECT * FROM quarantine_issue WHERE status='REQUIRES_ATTENTION' "
            "ORDER BY detected_at DESC, id DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def record_scan_summary(db_path: Path, summary) -> None:
    with closing(_connect(db_path)) as connection, connection:
        ensure_schema(connection)
        connection.execute(
            "INSERT INTO scan_summary (scanned_at, total_found, unchanged_files, "
            "added_files, changed_files, absent_files, moved_files, errors) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                time.strftime("%Y-%m-%d %H:%M:%S"), int(summary.total_found),
                int(summary.unchanged_files), int(summary.added_files),
                int(summary.changed_files), int(summary.absent_files),
                int(summary.moved_files), int(summary.errors),
            ),
        )


def load_latest_scan_summary(db_path: Path) -> dict:
    if not db_path.exists():
        return {}
    with closing(_connect(db_path)) as connection, connection:
        ensure_schema(connection)
        row = connection.execute(
            "SELECT * FROM scan_summary ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else {}
