"""Initial per-user installation of an unpacked, signed application."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meury_app.update_install import target_path, executable


def install():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path, nargs='?', default=Path(sys.executable).parent / ('MV Image' if sys.platform == 'win32' else 'MV Image.app'))
    args = parser.parse_args()
    source = args.source.resolve()
    target = target_path()
    if target.exists():
        if not executable(target).is_file():
            raise ValueError(f'A instalação existente está incompleta: {target}')
        print(f'O MV Image já está instalado em {target}. Abrindo o aplicativo...', flush=True)
        subprocess.Popen([str(executable(target))], cwd=target.parent)
        return
    if not executable(source).is_file():
        raise ValueError(f'Aplicativo não encontrado em {source}. Extraia todo o pacote '
                         'e mantenha o instalador ao lado da pasta MV Image '
                         '(ou MV Image.app no macOS).')
    print(f'Instalando MV Image em {target}...', flush=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.installing')
    try:
        shutil.copytree(source, temporary, symlinks=True)
        temporary.rename(target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    if sys.platform == 'win32':
        # Pass values via environment; no interpolation into PowerShell source.
        env = dict(os.environ, MV_IMAGE_EXE=str(executable(target)),
                   MV_IMAGE_LINK=str(Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/MV Image.lnk'))
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                        '$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:MV_IMAGE_LINK);'
                        '$s.TargetPath=$env:MV_IMAGE_EXE;$s.WorkingDirectory=Split-Path $env:MV_IMAGE_EXE;$s.Save()'],
                       env=env, check=True)
    print('Instalação concluída. Abrindo MV Image...', flush=True)
    subprocess.Popen([str(executable(target))], cwd=target.parent)


def main():
    try:
        install()
        return 0
    except Exception as exc:
        message = f'Não foi possível instalar ou abrir o MV Image:\n{exc}'
        try:
            log = Path.home() / '.mv-image-updates' / 'install.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open('a', encoding='utf-8') as output:
                traceback.print_exc(file=output)
            message += f'\n\nDiagnóstico salvo em: {log}'
        except OSError:
            pass
        print(message, file=sys.stderr, flush=True)
        if sys.platform == 'win32':
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, message, 'Instalação MV Image', 0x10)
                return 1
            except Exception:
                pass
        try:
            input('\nPressione Enter para fechar...')
        except (EOFError, OSError):
            pass
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
