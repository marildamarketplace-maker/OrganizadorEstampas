"""Initial per-user installation of an unpacked, signed application."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meury_app.update_install import target_path, executable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path, nargs='?', default=Path(sys.executable).parent / ('MV Image' if sys.platform == 'win32' else 'MV Image.app'))
    args = parser.parse_args()
    source = args.source.resolve()
    target = target_path()
    if target.exists():
        parser.error('Já existe uma instalação. Use o atualizador dentro do aplicativo.')
    if not executable(source).is_file():
        parser.error('Pasta do aplicativo inválida.')
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
    subprocess.Popen([str(executable(target))])


if __name__ == '__main__':
    main()
