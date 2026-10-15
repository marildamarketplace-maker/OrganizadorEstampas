"""Regras compartilhadas para excluir estampas pelo nome do arquivo."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


DEFAULT_IGNORED_FILENAME_TERMS = ("-", ".", "MOCKUP", "MOCLKUP")


def normalize_ignored_filename_terms(
    terms: str | Iterable[object] | None,
) -> tuple[str, ...]:
    """Normaliza a configuração, aceitando texto separado por vírgula ou lista."""
    if terms is None:
        return ()
    values = terms.split(",") if isinstance(terms, str) else terms
    normalized = []
    for value in values:
        term = str(value).strip().casefold()
        if term and term not in normalized:
            normalized.append(term)
    return tuple(normalized)


def record_filename_stem(record: dict) -> str:
    """Obtém somente o nome, sem diretórios e sem a última extensão."""
    value = (
        record.get("filename")
        or record.get("relative_path")
        or record.get("path")
        or ""
    )
    filename = str(value).replace("\\", "/").rsplit("/", 1)[-1]
    return Path(filename).stem.casefold()


def is_ignored_filename(
    record: dict, ignored_terms: str | Iterable[object] | None,
) -> bool:
    """Indica se o registro deve ficar fora de todas as filas de publicação.

    O termo "-" representa hífen no fim do nome, preservando variantes como
    "6245-A". O termo "." representa qualquer ponto no nome sem extensão.
    Os demais termos são procurados como texto, sem diferenciar caixa.
    """
    stem = record_filename_stem(record)
    if not stem:
        return False
    for term in normalize_ignored_filename_terms(ignored_terms):
        if term == "-":
            if stem.endswith("-"):
                return True
        elif term == ".":
            if "." in stem:
                return True
        elif term in stem:
            return True
    return False
