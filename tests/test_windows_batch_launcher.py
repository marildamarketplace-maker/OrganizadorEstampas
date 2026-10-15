"""Exercise the Windows launcher with isolated workers, never production orders."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "run_order_batch_windows.ps1"
WORKER = '''
import argparse, json, os, pathlib, subprocess, sys, time
parser = argparse.ArgumentParser()
parser.add_argument('--projeto', required=True)
parser.add_argument('--codex', default='')
args = parser.parse_args()
root = pathlib.Path(args.projeto)
child = None
if (root / 'hold').exists():
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
payload = {'pid': os.getpid(), 'child': child.pid if child else None}
(root / 'ready.json').write_text(json.dumps(payload))
if child:
    time.sleep(120)
sys.exit(7)
'''


@unittest.skipUnless(os.name == "nt", "Windows process management")
class WindowsBatchLauncherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="meury launcher ")
        self.root = Path(self.temp.name)
        self.worker = self.root / "batch_order_processor.py"
        self.worker.write_text(WORKER, encoding="utf-8")
        self.launchers = []
        self.workers = []
        self.control = self.root / "pedidos_pdf" / ".controle"

    def tearDown(self):
        for pid in self.workers:
            subprocess.run(
                ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
        for process in self.launchers:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=15)
        self.temp.cleanup()

    def launch(self, *options, project=None):
        process = subprocess.Popen(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(LAUNCHER), "-ProjectRoot", str(project or self.root),
             "-PythonBin", sys.executable, "-ProcessorPath", str(self.worker),
             "-CodexBin", "", *options],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace",
        )
        self.launchers.append(process)
        return process

    def ready(self, process, project=None):
        path = (project or self.root) / "ready.json"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if path.exists():
                try:
                    result = json.loads(path.read_text())
                    self.workers.append(result["pid"])
                    return result
                except json.JSONDecodeError:
                    pass
            if process.poll() is not None:
                self.fail(process.communicate(timeout=5)[0])
            time.sleep(0.05)
        self.fail("Worker did not start")

    def test_recovers_stale_directory_and_preserves_unknown_files(self):
        lock = self.control / "em_execucao"
        lock.mkdir(parents=True)
        unrelated = lock / "preservar.txt"
        unrelated.write_text("keep")
        process = self.launch()
        output, _ = process.communicate(timeout=25)
        self.assertEqual(process.returncode, 7, output)
        self.assertIn("Trava antiga sem lote ativo", output)
        self.assertEqual(unrelated.read_text(), "keep")
        self.assertFalse((self.control / "windows_execution.json").exists())

    def test_can_keep_active_batch_or_restart_its_entire_tree(self):
        self.check_restart(use_switch=False)

    def test_restart_switch_stops_previous_batch(self):
        self.check_restart(use_switch=True)

    def check_restart(self, use_switch):
        (self.root / "hold").touch()
        first = self.launch()
        owner = self.ready(first)
        second = self.launch()
        output, _ = second.communicate(input="N\n", timeout=25)
        self.assertEqual(second.returncode, 2, output)
        self.assertIn("Deseja encerrar o lote anterior", output)
        self.assertIsNone(first.poll())
        (self.root / "hold").unlink()
        replacement = self.launch(*(["-Reiniciar"] if use_switch else []))
        output, _ = replacement.communicate(
            input=None if use_switch else "S\n", timeout=30,
        )
        self.assertEqual(replacement.returncode, 7, output)
        self.assertIn("Encerrando o lote anterior e seus processos filhos", output)
        first.communicate(timeout=15)
        remaining = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"if (Get-Process -Id {owner['child']} -ErrorAction SilentlyContinue) {{ exit 1 }}"],
            capture_output=True, timeout=10,
        )
        self.assertEqual(remaining.returncode, 0, "Child process survived restart")

    def test_does_not_stop_batch_for_another_project(self):
        other = self.root / "other project"
        other.mkdir()
        (other / "hold").touch()
        first = self.launch(project=other)
        self.ready(first, project=other)
        second = self.launch("-Reiniciar")
        output, _ = second.communicate(timeout=25)
        self.assertEqual(second.returncode, 7, output)
        self.assertNotIn("Encerrando o lote anterior", output)
        self.assertIsNone(first.poll())

    def test_old_owner_does_not_remove_new_marker(self):
        marker = self.root / "marker.json"
        marker.write_text(json.dumps({"token": "new-owner"}))
        command = (
            f". '{LAUNCHER}' -ProjectRoot '{self.root}' -PythonBin '{sys.executable}' "
            f"-ProcessorPath '{self.worker}'; "
            f"Remove-OwnMarker '{marker}' 'old-owner' '{self.control / 'em_execucao'}'"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(marker.read_text())["token"], "new-owner")


if __name__ == "__main__":
    unittest.main()
