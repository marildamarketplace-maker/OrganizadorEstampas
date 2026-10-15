from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from meury_app.indexer import image_key
from meury_app.processor import _order_copy_prefix, process_csv_text


class AtomicOrderCopyTest(unittest.TestCase):
    def prepare_order(self, root):
        source = root / "estampas" / "MV27951-A.jpg"
        source.parent.mkdir()
        source.write_bytes(b"imagem completa da estampa")
        output = root / "saida"
        destination = (
            output / "CLIENTE" / "30-09-2026" / "20004749"
            / "MICROFIBRA" / "MV27951-A.JPG"
        )
        index = {image_key("MV27951", "MV27951-A"): [str(source)]}
        return source, output, destination, index

    def process_order(self, output, index):
        return process_csv_text(
            "20004749;30/09/2026;CLIENTE;MICROFIBRA;MV27951;A",
            output,
            index,
        )

    def test_final_filename_is_published_only_after_copy_finishes(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output, destination, index = self.prepare_order(Path(temporary))
            original_copy = shutil.copy2

            def observe_copy(copy_source, copy_destination):
                self.assertFalse(destination.exists())
                self.assertEqual(Path(copy_destination).parent, destination.parent)
                self.assertNotEqual(Path(copy_destination), destination)
                result = original_copy(copy_source, copy_destination)
                self.assertFalse(destination.exists())
                return result

            with patch("meury_app.processor.shutil.copy2", side_effect=observe_copy):
                results, summary = self.process_order(output, index)

            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(results[0].status, "COPIADO")
            self.assertEqual(summary.copiados, 1)
            self.assertEqual(list(destination.parent.glob("*.tmp")), [])

    def test_failed_or_interrupted_copy_leaves_no_final_file_and_can_retry(self):
        for error in (OSError("cópia falhou"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                with tempfile.TemporaryDirectory() as temporary:
                    source, output, destination, index = self.prepare_order(Path(temporary))

                    def interrupt_copy(_copy_source, copy_destination):
                        Path(copy_destination).write_bytes(b"imagem parcial")
                        raise error

                    with patch(
                        "meury_app.processor.shutil.copy2", side_effect=interrupt_copy,
                    ):
                        with self.assertRaises(type(error)):
                            self.process_order(output, index)

                    self.assertFalse(destination.exists())
                    self.assertEqual(list(destination.parent.glob("*.tmp")), [])
                    results, summary = self.process_order(output, index)
                    self.assertEqual(destination.read_bytes(), source.read_bytes())
                    self.assertEqual(results[0].status, "COPIADO")
                    self.assertEqual(summary.copiados, 1)

    def test_retry_cleans_only_its_dead_process_temporary_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output, destination, index = self.prepare_order(Path(temporary))
            destination.parent.mkdir(parents=True)
            prefix = _order_copy_prefix(destination)
            abandoned = destination.parent / f"{prefix}999999-partial.tmp"
            active = destination.parent / f"{prefix}{os.getpid()}-active.tmp"
            invalid_pid = destination.parent / f"{prefix}999999999999-malformed.tmp"
            other_destination = destination.with_name("MV27952-A.JPG")
            other = destination.parent / f"{_order_copy_prefix(other_destination)}999999-other.tmp"
            unrelated = destination.parent / "anotacao.tmp"
            for path in (abandoned, active, invalid_pid, other, unrelated):
                path.write_bytes(b"preservar ou recuperar")

            with patch(
                "meury_app.processor._copy_process_is_running",
                side_effect=lambda pid: pid == os.getpid(),
            ):
                self.process_order(output, index)

            self.assertFalse(abandoned.exists())
            for path in (active, invalid_pid, other, unrelated):
                self.assertTrue(path.exists())
            self.assertEqual(destination.read_bytes(), source.read_bytes())

    def test_retry_recovers_after_copy_process_exits_without_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, output, destination, index = self.prepare_order(Path(temporary))
            destination.parent.mkdir(parents=True)
            script = "\n".join([
                "import os, sys",
                "from pathlib import Path",
                "import meury_app.processor as processor",
                "def interrupt_copy(source, temporary):",
                "    Path(temporary).write_bytes(b'copia parcial')",
                "    os._exit(19)",
                "processor.shutil.copy2 = interrupt_copy",
                "processor._copy_order_file_atomically(Path(sys.argv[1]), Path(sys.argv[2]))",
            ])

            completed = subprocess.run(
                [sys.executable, "-c", script, str(source), str(destination)],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertEqual(completed.returncode, 19, completed.stderr)
            self.assertFalse(destination.exists())
            self.assertEqual(len(list(destination.parent.glob("*.tmp"))), 1)
            results, summary = self.process_order(output, index)
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(list(destination.parent.glob("*.tmp")), [])
            self.assertEqual(results[0].status, "COPIADO")
            self.assertEqual(summary.copiados, 1)

    def test_existing_complete_file_is_preserved_and_stale_copy_is_cleaned(self):
        with tempfile.TemporaryDirectory() as temporary:
            _source, output, destination, index = self.prepare_order(Path(temporary))
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"arquivo existente")
            abandoned = destination.parent / (
                f"{_order_copy_prefix(destination)}999999-partial.tmp"
            )
            abandoned.write_bytes(b"copia parcial")

            with patch(
                "meury_app.processor._copy_process_is_running", return_value=False,
            ), patch("meury_app.processor.shutil.copy2") as copy:
                results, summary = self.process_order(output, index)

            copy.assert_not_called()
            self.assertEqual(destination.read_bytes(), b"arquivo existente")
            self.assertFalse(abandoned.exists())
            self.assertEqual(results[0].status, "JÁ EXISTE")
            self.assertEqual(summary.copiados, 0)


if __name__ == "__main__":
    unittest.main()
