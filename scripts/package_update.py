"""Archive only the clean application tree; dereference PyInstaller framework links."""
import argparse
from pathlib import Path
import stat
import zipfile


def package(source, output):
    source = source.resolve()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*')):
            if path.is_dir() and not path.is_symlink():
                continue
            relative = path.relative_to(source.parent)
            ca_bundle = path.name == 'cacert.pem' and path.parent.name in ('certifi', 'botocore')
            if path.name.startswith('.env') or (path.suffix.lower() in ('.sqlite', '.sqlite3', '.pem', '.key') and not ca_bundle) or path.name in ('config.json', 'update-private.pem'):
                raise ValueError(f'Arquivo local proibido no pacote: {relative}')
            resolved = path.resolve()
            if source != resolved and source not in resolved.parents:
                raise ValueError('Link para fora do pacote.')
            info = zipfile.ZipInfo(relative.as_posix())
            if path.is_symlink():
                import os
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, os.readlink(path).encode())
                continue
            info.external_attr = (stat.S_IFREG | (0o755 if path.stat().st_mode & 0o111 else 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    package(args.source, args.output)
