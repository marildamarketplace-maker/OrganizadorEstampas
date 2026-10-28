"""Cross-platform contracts, plus native Windows filesystem acceptance tests."""
from pathlib import Path
from unittest.mock import Mock, patch
import os
import runpy
import tempfile
import unittest

from meury_app.platform_utils import open_existing_location, publish_without_overwrite
from meury_app.psd_search import Candidate, SearchSettings, analyze, validate_internal
from meury_app.sevenzip import SevenZip, locate


class PlatformContractsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.vendor = self.root / "vendor" / "7zip"
        self.vendor.mkdir(parents=True)

    def test_runtime_discovery_uses_only_platform_binary(self):
        for name in ("7zz", "7za.exe", "7z.exe"):
            (self.vendor / name).write_bytes(b"fixture")
        for system, name in (("darwin", "7zz"), ("win32", "7za.exe")):
            with self.subTest(system=system), patch("meury_app.sevenzip.sys.platform", system), \
                 patch("meury_app.sevenzip.sys._MEIPASS", str(self.root), create=True), \
                 patch.dict(os.environ, {"MEURY_7Z_PATH": ""}):
                self.assertEqual(locate(), str(self.vendor / name))

    def test_windows_path_discovery_uses_exe_and_program_files(self):
        program_files = self.root / "Program Files"
        executable = program_files / "7-Zip" / "7z.exe"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"fixture")
        with patch("meury_app.sevenzip.sys.platform", "win32"), \
             patch("meury_app.sevenzip.sys._MEIPASS", str(self.root), create=True), \
             patch.dict(os.environ, {"MEURY_7Z_PATH": "", "ProgramFiles": str(program_files)}), \
             patch("meury_app.sevenzip.shutil.which", return_value=None) as which:
            self.assertEqual(locate(), str(executable))
        self.assertEqual([call.args[0] for call in which.call_args_list], ["7z.exe", "7za.exe"])

    def test_windows_process_options_do_not_use_posix_session(self):
        process = Mock()
        process.wait.return_value = 0
        process.poll.return_value = 0
        with patch("meury_app.sevenzip.sys.platform", "win32"), \
             patch("meury_app.sevenzip.subprocess.CREATE_NO_WINDOW", 0x08000000, create=True), \
             patch("meury_app.sevenzip.subprocess.Popen", return_value=process) as popen:
            SevenZip("fixture.exe").run(["i"])
        self.assertEqual(popen.call_args.kwargs["creationflags"], 0x08000000)
        self.assertNotIn("start_new_session", popen.call_args.kwargs)

    def test_windows_listing_crlf_and_backslashes(self):
        engine = SevenZip("fixture.exe")
        engine.run = Mock(side_effect=["", "Path = origem-01\\sub pasta\\á.psd\r\nSize = 12\r\nMethod = LZMA2:12\r\n\r\n", "Type = 7z\r\nMethod = LZMA2:12\r\nSolid = -\r\nBlocks = 1\r\n----------\r\n"])
        item = Candidate("fixture", "fixture", "origem-01/sub pasta/á.psd", 12, 0)
        engine.validate(self.root / "archive.7z", [item], lambda: False)
        self.assertEqual(engine.last_inspection["entries"], 1)

    def test_windows_location_and_publication_contracts(self):
        original = self.root / "nome com espaços Ω.psd"
        original.write_bytes(b"fixture")
        with patch("meury_app.platform_utils.platform.system", return_value="Windows"), \
             patch("meury_app.platform_utils.os.startfile", create=True) as startfile:
            open_existing_location(original)
        startfile.assert_called_once_with(str(self.root))
        with patch("meury_app.platform_utils.platform.system", return_value="Windows"), \
             patch("meury_app.platform_utils.os.rename") as rename:
            publish_without_overwrite(original, self.root / "final.7z")
        rename.assert_called_once_with(original, self.root / "final.7z")

    def test_portable_names_reject_reserved_noncanonical_and_invalid_utf8(self):
        for name in ("a/CON .psd", "a/CONIN$.psd", "a/LPT9.psd", "a//b.psd", "a/./b.psd", "a/\udcff.psd"):
            with self.subTest(name=repr(name)), self.assertRaises(ValueError):
                validate_internal(name)

    def test_hook_rejects_foreign_runtime_and_requires_windows_dll(self):
        source = Path(__file__).resolve().parents[1] / "hooks" / "hook-meury_app.sevenzip.py"
        hook = self.root / "hooks" / source.name
        hook.parent.mkdir()
        hook.write_text(source.read_text())
        (self.vendor / "License.txt").write_text("fixture")
        (self.vendor / "7zz").write_bytes(b"fixture")
        with patch("sys.platform", "win32"), self.assertRaises(RuntimeError):
            runpy.run_path(str(hook))
        (self.vendor / "7z.exe").write_bytes(b"fixture")
        with patch("sys.platform", "win32"), self.assertRaises(RuntimeError):
            runpy.run_path(str(hook))
        (self.vendor / "7z.dll").write_bytes(b"fixture")
        with patch("sys.platform", "win32"):
            result = runpy.run_path(str(hook))
        self.assertEqual({Path(item[0]).name for item in result["binaries"]}, {"7z.exe", "7z.dll"})
        with patch("sys.platform", "darwin"):
            result = runpy.run_path(str(hook))
        self.assertEqual({Path(item[0]).name for item in result["binaries"]}, {"7zz"})


@unittest.skipUnless(os.name == "nt", "Requer filesystem/processos Windows reais")
class NativeWindowsPsdTest(unittest.TestCase):
    def test_junction_cycle_is_ignored(self):
        import _winapi
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "origem"
            source.mkdir()
            (source / "arte.psd").write_bytes(b"PSD")
            junction = source / "ciclo"
            # CPython's Windows fixture API avoids invoking a command interpreter.
            _winapi.CreateJunction(str(source), str(junction))
            try:
                result = analyze(SearchSettings((str(source),), str(root / "saida")))
                self.assertEqual(len(result.files), 1)
                self.assertTrue(any("redirecionamento" in issue for issue in result.issues))
            finally:
                junction.rmdir()

    def test_native_rename_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = root / "tmp.7z", root / "final.7z"
            first.write_bytes(b"new")
            second.write_bytes(b"existing")
            with self.assertRaises(FileExistsError):
                publish_without_overwrite(first, second)
            self.assertEqual(first.read_bytes(), b"new")
            self.assertEqual(second.read_bytes(), b"existing")
