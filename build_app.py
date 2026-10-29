"""Build nativo do MV Image: python build_app.py (Windows ou macOS)."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def build_command(python, platform):
    if platform not in ('win32', 'darwin'):
        raise ValueError('Build disponível somente para Windows e macOS.')
    separator = ';' if platform == 'win32' else ':'
    icon = 'ico' if platform == 'win32' else 'icns'
    command = [str(python), '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
               '--icon', f'assets/meury-logo.{icon}',
               '--add-data', f'assets{separator}assets',
               '--add-data', f'meury_app/batch_order_extraction.schema.json{separator}meury_app',
               '--additional-hooks-dir', 'hooks']
    for module in ('faiss', 'openai', 'google.cloud.storage', 'google.oauth2.service_account'):
        command.extend(['--hidden-import', module])
    for module in ('pypdfium2', 'pypdfium2_raw'):
        command.extend(['--collect-all', module])
    for module in ('torch', 'torchvision', 'transformers', 'scipy', 'sklearn', 'av'):
        command.extend(['--exclude-module', module])
    return command + ['--name', 'MV Image', 'app.py']


def main():
    try:
        platform = sys.platform
        build_command(sys.executable, platform)
        python = ROOT / '.venv' / ('Scripts/python.exe' if platform == 'win32' else 'bin/python')
        if not python.is_file():
            if (ROOT / '.venv').exists():
                raise ValueError('A pasta .venv não contém um Python compatível com este SO. '
                                 'Renomeie o ambiente antigo e execute o build novamente.')
            subprocess.run([sys.executable, '-m', 'venv', str(ROOT / '.venv')], check=True, cwd=ROOT)
        print(f"Preparando build para {'Windows' if platform == 'win32' else 'macOS'}...", flush=True)
        # O PyInstaller pode excluir tkinter silenciosamente se Tcl não inicializar.
        subprocess.run([str(python), '-c',
                        'import tkinter; tkinter.Tcl().eval("info patchlevel")'],
                       check=True, cwd=ROOT)
        subprocess.run([str(python), '-m', 'meury_app.dependency_setup', 'core'], check=True, cwd=ROOT)
        subprocess.run(build_command(python, platform), check=True, cwd=ROOT)
        output = ROOT / 'dist' / ('MV Image/MV Image.exe' if platform == 'win32' else 'MV Image.app')
        if not output.exists():
            raise ValueError(f'O empacotador não gerou o aplicativo esperado: {output}')
        schema = (
            output.parent / '_internal' / 'meury_app' / 'batch_order_extraction.schema.json'
            if platform == 'win32' else
            output / 'Contents' / 'Resources' / 'meury_app' / 'batch_order_extraction.schema.json'
        )
        if not schema.is_file():
            raise ValueError(f'O aplicativo gerado está sem o esquema obrigatório: {schema}')
        resources = schema.parent.parent
        icon_extension = 'ico' if platform == 'win32' else 'icns'
        for filename in ('meury-logo.png', f'meury-logo.{icon_extension}'):
            logo = resources / 'assets' / filename
            if not logo.is_file():
                raise ValueError(f'O aplicativo gerado está sem a logo obrigatória da MV: {logo}')
        print(f'Aplicativo criado em {output}')
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'ERRO: não foi possível criar o aplicativo: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
