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
    without_extension = relative.with_suffix("") if relative.suffix else relative
    return without_extension.as_posix().casefold()


def storage_asset_segment(record: dict) -> str:
    identity = relative_asset_identity(record)
    stem = PurePosixPath(identity).name or "arquivo"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"{stem}-{digest}"
