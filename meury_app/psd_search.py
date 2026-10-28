"""Read-only PSD discovery and portable archive manifests."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
import os
import re
import stat
import unicodedata


class Cancelled(Exception):
    pass


def checkpoint(cancel):
    if cancel():
        raise Cancelled("Cancelamento solicitado")


def parse_names(text):
    names = {}
    for token in re.split(r"[\n,;]+", text):
        token = token.strip()
        if token.casefold().endswith(".psd"):
            token = token[:-4]
        if token:
            names.setdefault(token.casefold(), token)
    return tuple(names.values())


@dataclass(frozen=True)
class SearchSettings:
    sources: tuple[str, ...]
    output: str
    names: str = ""
    start: str = ""
    end: str = ""

    def bounds(self):
        try:
            first = date.fromisoformat(self.start) if self.start else None
            last = date.fromisoformat(self.end) if self.end else None
            if (first and first.isoformat() != self.start) or (last and last.isoformat() != self.end):
                raise ValueError("Use AAAA-MM-DD")
            if first and last and first > last:
                raise ValueError("Data inicial posterior à final")
            return (
                datetime.combine(first, time.min).timestamp() if first else None,
                datetime.combine(last + timedelta(days=1), time.min).timestamp() if last else None,
            )
        except (ValueError, OverflowError, OSError) as exc:
            raise ValueError("Datas inválidas. Use AAAA-MM-DD e início <= fim.") from exc


@dataclass
class Candidate:
    path: str
    source: str
    internal: str
    size: int
    mtime_ns: int
    selected: bool = True
    situation: str = "Disponível"
    decision: str = "incluir"


@dataclass
class Analysis:
    settings: SearchSettings
    files: list[Candidate] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    incomplete: bool = False

    def snapshot(self):
        return asdict(self)


def redirected(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & 0x400  # any Windows reparse point
    )


def validate_internal(value):
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Nome de arquivo sem representação UTF-8 segura.") from exc
    path = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (not value or any(part in {"", ".", ".."} for part in value.split("/"))
            or path.is_absolute() or windows.root or windows.drive or ".." in path.parts
            or "\\" in value or any(ord(c) < 32 for c in value)):
        raise ValueError(f"Caminho interno inseguro: {value!r}")
    for part in path.parts:
        if (part.endswith((".", " ")) or any(c in part for c in '<>:"|?*')
                or part.split(".")[0].rstrip(" ").upper() in {
                    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}):
            raise ValueError(f"Nome incompatível com extração Windows: {value!r}")
    return value


def portable_key(value):
    return unicodedata.normalize("NFC", value).casefold()


def safe_source(path, root):
    """Reject redirections in every component, including a redirected root ancestor."""
    path = Path(os.path.abspath(path))
    root = Path(os.path.abspath(root))
    path.relative_to(root)
    for current in (path, *path.parents):
        if redirected(current):
            raise ValueError(f"Link/redirecionamento ignorado: {current}")
    path.resolve().relative_to(root.resolve())
    return path


def analyze(settings, cancel=lambda: False, progress=lambda *args: None):
    low, high = settings.bounds()
    if not settings.sources or not settings.output.strip():
        raise ValueError("Adicione origens e informe a saída.")
    output = Path(settings.output).expanduser().resolve()
    ancestor = output
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.is_dir() or not os.access(ancestor, os.W_OK | os.X_OK):
        raise PermissionError(f"Destino sem permissão de gravação: {ancestor}")
    result = Analysis(settings)
    terms = {name.casefold(): name for name in parse_names(settings.names)}
    matched = set()
    seen = set()
    physical = set()
    roots = []
    for text in settings.sources:
        root = Path(os.path.abspath(Path(text).expanduser()))
        if root.resolve() == output:
            raise ValueError("A saída não pode ser igual a uma origem.")
        if root not in roots:
            roots.append(root)
    for number, root in enumerate(roots, 1):
        checkpoint(cancel)
        try:
            safe_source(root, root)
            if not root.is_dir():
                raise OSError("Origem indisponível")
        except (OSError, ValueError) as exc:
            result.issues.append(f"{root}: {exc}")
            result.incomplete = True
            continue
        pending = [root]
        while pending:
            checkpoint(cancel)
            directory = pending.pop()
            try:
                safe_source(directory, root)
                # Exclude a nested destination, not a selected origin below its output parent.
                if output.is_relative_to(root.resolve()) and (
                    directory.resolve() == output or output in directory.resolve().parents
                ):
                    continue
                with os.scandir(directory) as entries:
                    for entry in entries:
                        checkpoint(cancel)
                        path = Path(entry.path)
                        try:
                            if redirected(path):
                                result.issues.append(f"Link/redirecionamento ignorado: {path}")
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                pending.append(path)
                                continue
                            if not entry.is_file(follow_symlinks=False) or path.suffix.casefold() != ".psd":
                                continue
                            info = entry.stat(follow_symlinks=False)
                            key = os.path.normcase(str(path.resolve()))
                            identity = (info.st_dev, info.st_ino) if info.st_ino else key
                            if key in seen or identity in physical:
                                result.issues.append(f"Origem sobreposta/arquivo repetido ignorado: {path}")
                                continue
                            seen.add(key)
                            physical.add(identity)
                            name = path.stem.casefold()
                            if terms and name not in terms:
                                continue
                            if (low is not None and info.st_mtime < low) or (high is not None and info.st_mtime >= high):
                                continue
                            matched.add(name)
                            # Each file gets a unique namespace: even case/Unicode collisions in
                            # directory components remain distinct on case-insensitive extraction.
                            internal = validate_internal(f"origem-{number:02d}/item-{len(result.files)+1:06d}/{path.relative_to(root).as_posix()}")
                            result.files.append(Candidate(str(path), str(root), internal, info.st_size, info.st_mtime_ns))
                            progress("Analisando", len(result.files), 0)
                        except (OSError, ValueError) as exc:
                            result.issues.append(f"{path}: {exc}")
                            result.incomplete = True
            except (OSError, ValueError) as exc:
                result.issues.append(f"{directory}: {exc}")
                result.incomplete = True
    result.missing = [text for key, text in terms.items() if key not in matched]
    groups = {}
    for item in result.files:
        groups.setdefault(Path(item.path).name.casefold(), []).append(item)
    for group in groups.values():
        if len(group) > 1:
            for item in group:
                item.situation = f"Homônimo ({len(group)} ocorrências)"
    return result
