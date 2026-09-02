"""Resolução segura da pasta original armazenada de forma relativa."""

from __future__ import annotations

from pathlib import Path
import os

from .config import resolve_record_path, validate_original_images_path
from .platform_utils import open_with_default_application


class OriginalFolderError(ValueError):
    """Erro apresentável ao usuário ao localizar a mídia original."""


def resolve_original_directory(record: dict, config: dict | None = None) -> Path:
    try:
        root = resolve_record_path({**record, "relative_path": ""}, config=config)
        validate_original_images_path({"original_images_path": str(root)})
    except (OSError, ValueError) as exc:
        raise OriginalFolderError(str(exc)) from exc
    raw_relative = str(
        record.get("original_relative_path") or record.get("relative_path") or ""
    ).strip()
    if not raw_relative:
        raise OriginalFolderError("Este registro não possui caminho original relativo.")
    relative = Path(raw_relative.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise OriginalFolderError("O caminho original relativo armazenado é inválido.")

    # Aceita tanto o formato de diretório (6844/A) quanto catálogos legados que
    # guardam o arquivo completo (6844/A/6844-A.tif).
    filename = str(record.get("original_filename") or record.get("filename") or "")
    if relative.name.casefold() == filename.casefold() or relative.suffix:
        relative = relative.parent
    try:
        target = resolve_record_path({"relative_path": str(relative)}, [root])
    except ValueError as exc:
        raise OriginalFolderError(str(exc)) from exc
    if not target.is_dir():
        raise OriginalFolderError(
            "A pasta original não foi encontrada. O arquivo pode ter sido movido "
            "ou o volume pode estar desconectado:\n"
            f"{target}"
        )
    if not os.access(target, os.R_OK | os.X_OK):
        raise OriginalFolderError(f"Sem permissão para abrir a pasta original:\n{target}")
    return target


def open_original_directory(record: dict, config: dict | None = None) -> Path:
    target = resolve_original_directory(record, config)
    try:
        open_with_default_application(target)
    except PermissionError as exc:
        raise OriginalFolderError(f"Sem permissão para abrir a pasta original:\n{target}") from exc
    except OSError as exc:
        raise OriginalFolderError(f"O sistema não conseguiu abrir a pasta original:\n{exc}") from exc
    return target
