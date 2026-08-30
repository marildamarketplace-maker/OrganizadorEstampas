"""Política curta de retry para serviços externos, sem esconder erros permanentes."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError
from typing import Callable, TypeVar
import time


T = TypeVar("T")
TRANSIENT_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}


def is_retryable_external_error(exc: Exception) -> bool:
    # HTTPError delega atributos ao stream em algumas versões do Python e pode
    # lançar KeyError ao usar getattr depois que o stream foi encerrado.
    explicit = vars(exc).get("retryable")
    if explicit is not None:
        return bool(explicit)
    if isinstance(exc, HTTPError):
        return exc.code in TRANSIENT_HTTP_CODES
    if isinstance(exc, (ValueError, FileNotFoundError, PermissionError)):
        return False
    status_code = getattr(exc, "code", None)
    try:
        if status_code is not None:
            return int(status_code) in TRANSIENT_HTTP_CODES
    except (TypeError, ValueError):
        pass
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", "")).casefold()
        if code in {"accessdenied", "invalidaccesskeyid", "signaturedoesnotmatch", "nosuchbucket"}:
            return False
    return isinstance(exc, (OSError, TimeoutError, ConnectionError, RuntimeError))


@dataclass(frozen=True)
class RetryFailure(Exception):
    cause: Exception
    attempts: int

    def __str__(self) -> str:
        return str(self.cause)


def run_with_retry(
    action: Callable[[], T], *, max_attempts: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[T, int]:
    attempts = 0
    while True:
        attempts += 1
        try:
            return action(), attempts
        except Exception as exc:
            if attempts >= max_attempts or not is_retryable_external_error(exc):
                raise RetryFailure(exc, attempts) from exc
            sleep(0.1 * (2 ** (attempts - 1)))
