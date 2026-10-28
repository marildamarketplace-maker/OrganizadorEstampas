"""Operações pequenas que variam entre os sistemas operacionais suportados."""

from __future__ import annotations

from pathlib import Path
import os
import platform
import subprocess


def open_existing_location(path: Path | str) -> None:
    """Open an existing directory, or the parent of an existing original file."""
    target = Path(path).expanduser()
    if not target.exists():
        raise FileNotFoundError(f"O caminho já não existe ou está inacessível: {target}")
    directory = (target if target.is_dir() else target.parent).resolve(strict=True)
    system = platform.system()
    if system == "Windows":
        os.startfile(str(directory))  # type: ignore[attr-defined]
    else:
        command = "open" if system == "Darwin" else "xdg-open"
        subprocess.run([command, str(directory)], check=True, timeout=15,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def publish_without_overwrite(source: Path, destination: Path) -> None:
    """Atomically publish a validated sibling, refusing an existing result."""
    system = platform.system()
    if system == "Windows":
        os.rename(source, destination)  # Windows rename refuses an existing destination.
    elif system == "Darwin":
        import ctypes
        library = ctypes.CDLL(None, use_errno=True)
        rename = library.renamex_np
        rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        # RENAME_EXCL, from the macOS SDK sys/stdio.h.
        if rename(os.fsencode(source), os.fsencode(destination), 0x4):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(destination))
    else:
        os.link(source, destination)
        source.unlink()


def open_with_default_application(path: Path | str) -> None:
    """Abre um arquivo ou diretório no aplicativo padrão do sistema."""
    target = Path(path).expanduser().resolve()
    system = platform.system()
    if system == "Windows":
        os.startfile(str(target))  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
