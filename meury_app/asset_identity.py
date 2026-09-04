"""Identidade remota estável para cada arquivo indexado."""

from __future__ import annotations

from pathlib import PurePosixPath
import hashlib


def relative_asset_identity(record: dict) -> str:
    """Distingue nomes iguais localizados em diretórios relativos diferentes."""
    persisted = str(record.get("asset_id", "")).strip().replace("\\", "/")
    if persisted:
        return persisted.casefold()
    filename = str(record.get("filename", "")).strip()
    raw_relative = str(record.get("relative_path", "")).strip().replace("\\", "/")
    relative = PurePosixPath(raw_relative)
    if not raw_relative or raw_relative == ".":
        relative = PurePosixPath(filename)
    elif relative.name.casefold() != filename.casefold():
        relative = relative / filename
    # Alguns compartilhamentos SMB expõem entradas com nome finalizado em ponto
    # (inclusive "."). No Python 3.13, ``with_suffix("")`` rejeita esse nome.
    # A identidade ainda pode ser usada com segurança sem remover a extensão,
    # portanto essa anomalia jamais deve interromper uma indexação inteira.
    try:
        without_extension = relative.with_suffix("") if relative.suffix else relative
    except ValueError:
        without_extension = relative
    return without_extension.as_posix().casefold()


def storage_asset_segment(record: dict) -> str:
    identity = relative_asset_identity(record)
    stem = PurePosixPath(identity).name or "arquivo"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"{stem}-{digest}"
