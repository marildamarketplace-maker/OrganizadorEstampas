"""Small, cancellable adapter for the official 7-Zip command line."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

from .psd_search import checkpoint, validate_internal


class ArchiveUnavailable(RuntimeError):
    """Validation could not run reliably; retry rather than declaring corruption."""


def locate():
    configured = os.environ.get("MEURY_7Z_PATH", "")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise ValueError("MEURY_7Z_PATH deve apontar para um executável absoluto existente.")
        return str(path)
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "vendor" / "7zip"
    bundled_names = ("7za.exe", "7z.exe") if sys.platform == "win32" else ("7zz",)
    for name in bundled_names:
        if (bundle / name).is_file():
            return str(bundle / name)
    path_names = ("7z.exe", "7za.exe") if sys.platform == "win32" else ("7zz", "7z", "7za")
    for name in path_names:
        found = shutil.which(name)
        if found and Path(found).resolve().parent != Path.cwd().resolve():
            return found
    if sys.platform == "win32":
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(variable)
            if base and (Path(base) / "7-Zip" / "7z.exe").is_file():
                return str(Path(base) / "7-Zip" / "7z.exe")
    raise RuntimeError("7-Zip não encontrado. Configure MEURY_7Z_PATH com o caminho absoluto do 7zz/7za.exe/7z.exe. Consulte o README.")


class SevenZip:
    def __init__(self, executable=None):
        self.executable = executable or locate()

    def run(self, args, cancel=lambda: False, source=None):
        checkpoint(cancel)
        # Files avoid pipe saturation and unbounded diagnostic RAM; never use a shell.
        with tempfile.TemporaryFile() as output:
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
            process = subprocess.Popen([self.executable, *args], stdin=source or subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, **options)
            try:
                while True:
                    try:
                        code = process.wait(timeout=0.15)
                        break
                    except subprocess.TimeoutExpired:
                        checkpoint(cancel)
                checkpoint(cancel)
            finally:
                if process.poll() is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass  # The child exited between poll() and terminate().
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        process.wait()
            output.seek(0, 2)
            length = output.tell()
            if length > 16 * 1024 * 1024:
                raise RuntimeError("Diagnóstico 7z excedeu o limite seguro de 16 MiB.")
            output.seek(0)
            text = output.read().decode("utf-8", errors="replace")
            if code != 0:
                raise RuntimeError(f"7-Zip retornou código {code}: {text[-4000:]}")
            return text

    def add(self, archive, item, stream, cancel):
        name = validate_internal(item.internal)
        self.run(["a", "-t7z", "-m0=LZMA2", "-mx=5", "-ms=off", "-bd", "-y",
                  "-scsUTF-8", "-sccUTF-8", f"-si{name}", str(archive)], cancel, stream)

    def validate(self, archive, items, cancel):
        def inspect(args):
            try:
                return self.run(args, cancel)
            except RuntimeError as exc:
                text = str(exc).casefold()
                # 7z may also say "cannot open as archive" after an OS access error.
                # Such a diagnostic must remain retryable, even if it mentions corruption.
                if any(marker in text for marker in ("access is denied", "access denied", "permission denied", "acesso negado", "network path", "network name", "sharing violation")):
                    raise ArchiveUnavailable(str(exc)) from exc
                if any(marker in text for marker in ("data error", "crc failed", "headers error", "is not archive", "can not open the file as", "unexpected end of archive")):
                    raise
                raise ArchiveUnavailable(str(exc)) from exc
        inspect(["t", "-bd", "-sccUTF-8", str(archive)])
        listing = inspect(["l", "-slt", "-ba", "-sccUTF-8", str(archive)])
        entries = []
        for block in listing.replace("\r\n", "\n").strip().split("\n\n"):
            record = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
            if record.get("Folder") == "+":
                continue
            if "Path" in record:
                entries.append((record["Path"].replace("\\", "/"), int(record.get("Size", -1))))
                if int(record.get("Size", 0)) and "LZMA2" not in record.get("Method", ""):
                    raise RuntimeError("Método real do compactado não é LZMA2.")
        if sorted(entries) != sorted((item.internal, item.size) for item in items):
            raise RuntimeError("Inventário do 7z diverge do manifesto aprovado.")
        header = inspect(["l", "-slt", "-sccUTF-8", str(archive)]).split("----------", 1)[0]
        if "Solid = -" not in header:
            raise RuntimeError("Não foi possível confirmar compressão sólida desativada.")
        properties = dict(line.split(" = ", 1) for line in header.splitlines() if " = " in line)
        if properties.get("Type") != "7z":
            raise RuntimeError("O contêiner real não é 7z.")
        self.last_inspection = {key: properties.get(key) for key in ("Type", "Method", "Solid", "Blocks")}
        self.last_inspection["entries"] = len(entries)
        self.last_inspection["level"] = "-mx=5 solicitado ao processo; o preset não é armazenado no cabeçalho 7z"
        return "Teste de integridade OK; entradas, tamanhos, LZMA2 e modo não sólido conferidos."

    def probe(self, cancel):
        """Exercise the exact flags, stream input and listing contract before real PSDs."""
        from .psd_search import Candidate
        self.version = self.run(["i", "-sccUTF-8"], cancel).strip().splitlines()[0]
        with tempfile.TemporaryDirectory(prefix="meury-7z-probe-") as temporary:
            root = Path(temporary)
            source = root / "probe"
            source.write_bytes(b"meury 7zip capability\n" * 8)
            item = Candidate(str(source), str(root), "origem-01/teste.psd", source.stat().st_size, 0)
            with source.open("rb") as stream:
                self.add(root / "probe.7z", item, stream, cancel)
            self.validate(root / "probe.7z", [item], cancel)
