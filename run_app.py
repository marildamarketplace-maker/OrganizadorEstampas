"""Executa o MV Image sem empacotar: python run_app.py (Windows ou macOS)."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    try:
        if sys.platform not in ('win32', 'darwin'):
            raise ValueError('Execução disponível somente para Windows e macOS.')
        python = ROOT / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
        if not python.is_file():
            if (ROOT / '.venv').exists():
                raise ValueError('A pasta .venv não contém um Python compatível com este SO. '
                                 'Renomeie o ambiente antigo e execute novamente.')
            subprocess.run([sys.executable, '-m', 'venv', str(ROOT / '.venv')], check=True, cwd=ROOT)
        print('Preparando execução do MV Image...', flush=True)
        subprocess.run([str(python), '-c',
                        'import tkinter; tkinter.Tcl().eval("info patchlevel")'],
                       check=True, cwd=ROOT)
        subprocess.run([str(python), '-m', 'meury_app.dependency_setup', 'core'],
                       check=True, cwd=ROOT)
        subprocess.run([str(python), str(ROOT / 'app.py'), *sys.argv[1:]],
                       check=True, cwd=ROOT)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'ERRO: não foi possível executar o aplicativo: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
