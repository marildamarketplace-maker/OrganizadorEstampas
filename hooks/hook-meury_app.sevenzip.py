"""Optional, manually provisioned official 7-Zip runtime for PyInstaller."""
from pathlib import Path
import sys

directory = Path(__file__).resolve().parent.parent / "vendor" / "7zip"
binaries = []
datas = []
names = ("7za.exe", "7z.exe") if sys.platform == "win32" else ("7zz",)
executables = [directory / name for name in names if (directory / name).is_file()]
if not executables and any((directory / name).is_file() for name in ("7zz", "7za.exe", "7z.exe")):
    raise RuntimeError("O runtime em vendor/7zip pertence a outro sistema operacional.")
if executables:
    licenses = list(directory.glob("*.txt"))
    if not licenses:
        raise RuntimeError("Inclua os avisos de licença oficiais *.txt em vendor/7zip antes do build.")
    for executable in executables:
        binaries.append((str(executable), "vendor/7zip"))
        if executable.name == "7z.exe":
            library = directory / "7z.dll"
            if not library.is_file():
                raise RuntimeError("7z.exe requer vendor/7zip/7z.dll; ou use 7za.exe standalone.")
            binaries.append((str(library), "vendor/7zip"))
    datas = [(str(path), "vendor/7zip") for path in licenses]
