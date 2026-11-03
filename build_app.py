"""Build nativo do MV Image: python build_app.py (Windows ou macOS)."""
from pathlib import Path
import subprocess
import sys
import os

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
    if platform == 'darwin':
        command.extend(['--osx-bundle-identifier', 'com.meuryshop.mvimage'])
    if platform == 'darwin' and os.environ.get('MV_IMAGE_CODESIGN_IDENTITY'):
        command.extend(['--codesign-identity', os.environ['MV_IMAGE_CODESIGN_IDENTITY']])
    return command + ['--name', 'MV Image', 'app.py']


def finalize_macos_bundle(output):
    import plistlib
    from meury_app import __version__
    plist = output / 'Contents' / 'Info.plist'
    metadata = plistlib.loads(plist.read_bytes())
    metadata.update(CFBundleShortVersionString=__version__, CFBundleVersion=__version__)
    plist.write_bytes(plistlib.dumps(metadata))
    subprocess.run(['codesign', '--force', '--deep', '--sign',
                    os.environ.get('MV_IMAGE_CODESIGN_IDENTITY', '-'), str(output)],
                   check=True, cwd=ROOT)


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
        native_signing = (['--codesign-identity', os.environ['MV_IMAGE_CODESIGN_IDENTITY']]
                          if platform == 'darwin' and os.environ.get('MV_IMAGE_CODESIGN_IDENTITY') else [])
        helper_name = 'MV Image Updater'
        subprocess.run([str(python), '-m', 'PyInstaller', '--noconfirm', '--clean',
                        '--onefile', *native_signing, '--name', helper_name,
                        '--add-data', f"assets{';' if platform == 'win32' else ':'}assets",
                        'update_helper.py'], check=True, cwd=ROOT)
        import shutil
        helper_file = ROOT / 'dist' / (helper_name + ('.exe' if platform == 'win32' else ''))
        helper_destination = (output.parent / '_internal' if platform == 'win32'
                              else output / 'Contents' / 'Frameworks')
        shutil.copy2(helper_file, helper_destination / helper_file.name)
        subprocess.run([str(python), '-m', 'PyInstaller', '--noconfirm', '--clean',
                        '--onefile', *native_signing, '--name', 'Install MV Image',
                        'scripts/install_app.py'], check=True, cwd=ROOT)
        if platform == 'darwin':
            finalize_macos_bundle(output)
        print(f'Aplicativo criado em {output}')
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'ERRO: não foi possível criar o aplicativo: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
