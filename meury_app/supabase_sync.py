"""Sincronização independente do catálogo comercial com Supabase."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json
import os
import time

from .asset_identity import relative_asset_identity
from .indexer import _operational_db_path, load_catalog_records
from .operational_store import sync_records
from .retry_policy import RetryFailure, is_retryable_external_error, run_with_retry


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    key: str
    table: str = "estampas"

    @classmethod
    def from_environment(cls) -> "SupabaseConfig":
        config = cls(
            url=os.environ.get("SUPABASE_URL", "").strip().rstrip("/"),
            key=(os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
                 or os.environ.get("SUPABASE_KEY", "").strip()),
            table=os.environ.get("SUPABASE_TABLE", "estampas").strip() or "estampas",
        )
        missing = []
        if not config.url:
            missing.append("SUPABASE_URL")
        if not config.key:
            missing.append("SUPABASE_KEY")
        if missing:
            raise ValueError("Configuração Supabase incompleta: " + ", ".join(missing))
        return config


class SupabaseClient(Protocol):
    def upsert(self, rows: list[dict]) -> None: ...


class SupabaseHttpError(RuntimeError):
    def __init__(self, status: int, detail: str, *, retryable: bool):
        super().__init__(f"Supabase HTTP {status}: {detail}")
        self.code = status
        self.retryable = retryable


class SupabaseRestClient:
    """Cliente PostgREST mínimo, sem dependência nativa ou específica de SO."""

    def __init__(self, config: SupabaseConfig):
        self.config = config

    def upsert(self, rows: list[dict]) -> None:
        query = urlencode({"on_conflict": "codigo,variante,arquivo_id"})
        table = quote(self.config.table, safe="")
        request = Request(
            f"{self.config.url}/rest/v1/{table}?{query}",
            data=json.dumps(rows, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "apikey": self.config.key,
                "Authorization": f"Bearer {self.config.key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                if not 200 <= response.status < 300:
                    raise SupabaseHttpError(
                        response.status, "resposta inesperada", retryable=response.status >= 500
                    )
        except HTTPError as exc:
            raw = exc.read(4096).decode("utf-8", "replace")
            detail = raw
            sqlstate = ""
            try:
                payload = json.loads(raw)
                sqlstate = str(payload.get("code", ""))
                detail = str(
                    payload.get("message") or payload.get("details") or payload.get("hint") or raw
                )
            except (json.JSONDecodeError, AttributeError, TypeError):
                pass
            detail = " ".join(detail.split())[:1000] or exc.reason or "erro sem detalhes"
            # Erros SQL possuem resposta HTTP 5xx, mas não melhoram com retry.
            permanent_sql_error = bool(sqlstate) and not sqlstate.startswith(("08", "53", "57P"))
            raise SupabaseHttpError(
                exc.code, detail,
                retryable=(exc.code >= 500 and not permanent_sql_error),
            ) from exc


@dataclass(frozen=True)
class SupabaseSyncResult:
    pending: int
    completed: int
    failed: int
    elapsed_seconds: float


def _payload(record: dict) -> dict:
    filename = str(record.get("filename", ""))
    relative = Path(str(record.get("relative_path", "")).replace("\\", "/"))
    if relative.name.casefold() == filename.casefold() or relative.suffix:
        relative = relative.parent
    return {
        "codigo": str(record.get("codigo", record.get("design_id", ""))).strip(),
        "variante": str(record.get("variante", "")).strip() or "SEM-VARIANTE",
        "arquivo_id": relative_asset_identity(record),
        "preview_url": str(record.get("preview_url", "")),
        "storage_key": str(record.get("storage_key", "")),
        "original_relative_path": relative.as_posix() if str(relative) != "." else "",
        "original_filename": filename,
        "original_extension": Path(filename).suffix.casefold(),
        "content_hash": str(record.get("content_hash", "")),
        # INSERTS sempre entram na fila do sistema externo. Em UPDATE, o trigger
        # remoto preserva o estado da IA quando o content_hash não mudou.
        "processing_status": "PENDING",
    }


def sync_pending_records(
    source_dirs, *, client: SupabaseClient | None = None, limit: int | None = None,
    batch_size: int = 100,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> SupabaseSyncResult:
    if batch_size < 1 or batch_size > 500:
        raise ValueError("batch_size deve estar entre 1 e 500 registros.")
    records = load_catalog_records(source_dirs)
    if records is None:
        raise ValueError("Atualize o índice antes da sincronização com Supabase.")
    pending = [
        record for record in records
        if record.get("active", True)
        and not record.get("missing_locally", False)
        and bool(record.get("codigo", record.get("design_id")))
        and record.get("cloud_status") == "completed"
        and bool(record.get("preview_url"))
        and bool(record.get("storage_key"))
        and bool(record.get("content_hash"))
        and (
            record.get("supabase_status") in {"pending", "failed"}
            or (
                record.get("supabase_status") in {"completed", "synced"}
                and record.get("supabase_content_hash") != record.get("content_hash")
            )
        )
    ]
    if limit is not None:
        pending = pending[:limit]
    if pending and client is None:
        client = SupabaseRestClient(SupabaseConfig.from_environment())
    started = time.monotonic()
    completed = failed = 0

    def register_attempts(record: dict, attempts: int) -> None:
        record["supabase_attempts"] = int(record.get("supabase_attempts", 0)) + attempts
        record["supabase_last_attempt_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def mark_completed(record: dict) -> None:
        record["supabase_status"] = "completed"
        record["supabase_content_hash"] = str(record.get("content_hash", ""))
        record["last_synced_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        record["last_error"] = ""
        record["supabase_last_error"] = ""

    def mark_failed(record: dict, exc: Exception) -> None:
        record["supabase_status"] = "failed"
        record["last_error"] = f"Supabase: {type(exc).__name__}: {exc}"
        record["supabase_last_error"] = f"{type(exc).__name__}: {exc}"

    processed = 0
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset:offset + batch_size]
        try:
            if client is None:  # Garantido pela inicialização sob demanda acima.
                raise RuntimeError("Cliente Supabase indisponível.")
            _, attempts = run_with_retry(
                lambda: client.upsert([_payload(record) for record in batch])
            )
            for record in batch:
                register_attempts(record, attempts)
                mark_completed(record)
            completed += len(batch)
        except RetryFailure as batch_failure:
            for record in batch:
                register_attempts(record, batch_failure.attempts)
            if is_retryable_external_error(batch_failure.cause):
                # Uma indisponibilidade do serviço afetaria todos os itens. Não
                # transforme um lote de 100 em outras 300 chamadas sem utilidade.
                for record in batch:
                    mark_failed(record, batch_failure.cause)
                failed += len(batch)
            else:
                # Erro permanente do lote pode ser causado por um único payload;
                # nesse caso, o modo unitário identifica exatamente qual deles.
                for record in batch:
                    try:
                        _, attempts = run_with_retry(lambda: client.upsert([_payload(record)]))
                        register_attempts(record, attempts)
                        mark_completed(record)
                        completed += 1
                    except RetryFailure as record_failure:
                        register_attempts(record, record_failure.attempts)
                        mark_failed(record, record_failure.cause)
                        failed += 1
        # O estado do lote é confirmado em uma única transação SQLite. Se o
        # processo parar antes daqui, repetir o UPSERT remoto continua seguro.
        sync_records(_operational_db_path(), batch)
        processed += len(batch)
        if progress_callback:
            progress_callback(
                processed, len(pending),
                f"Sincronizando Supabase: {processed:,} de {len(pending):,}",
            )
    return SupabaseSyncResult(
        pending=len(pending), completed=completed, failed=failed,
        elapsed_seconds=time.monotonic() - started,
    )
