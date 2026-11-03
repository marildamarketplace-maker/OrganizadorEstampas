"""Native signing runs only in the protected release environment."""
import base64
import os
from pathlib import Path
import secrets
import json
import subprocess
import sys
import tempfile
import shlex


def run(*args):
    result = subprocess.run(list(args), stdout=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError('Falha em etapa de assinatura nativa.')


def sign_mac(root):
    certificate = root / 'developer.p12'
    certificate.write_bytes(base64.b64decode(os.environ['CERTIFICATE'], validate=True))
    keychain = root / 'release.keychain-db'
    password = secrets.token_urlsafe(32)
    prior = shlex.split(subprocess.check_output(['security', 'list-keychains', '-d', 'user'], text=True))
    run('security', 'create-keychain', '-p', password, str(keychain))
    try:
        run('security', 'unlock-keychain', '-p', password, str(keychain))
        run('security', 'import', str(certificate), '-k', str(keychain), '-P', os.environ['CERT_PASSWORD'], '-T', '/usr/bin/codesign')
        run('security', 'set-key-partition-list', '-S', 'apple-tool:,apple:,codesign:', '-s', '-k', password, str(keychain))
        run('security', 'list-keychains', '-d', 'user', '-s', str(keychain), *prior)
        identity = os.environ['SIGN_IDENTITY']
        env = dict(os.environ, MV_IMAGE_CODESIGN_IDENTITY=identity)
        subprocess.run([sys.executable, 'build_app.py'], check=True, env=env)
        app = Path('dist/MV Image.app')
        # Sign Mach-O binaries inside-out; do not rely on codesign --deep to sign.
        candidates = sorted(app.rglob('*'), key=lambda p: len(p.parts), reverse=True)
        for path in candidates:
            if path.is_file() and not path.is_symlink():
                with path.open('rb') as file:
                    magic = file.read(4)
                if magic in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'):
                    run('codesign', '--force', '--options', 'runtime', '--timestamp', '--keychain', str(keychain), '--sign', identity, str(path))
            elif path.suffix in ('.framework', '.app') and path.is_dir() and not path.is_symlink():
                run('codesign', '--force', '--options', 'runtime', '--timestamp', '--keychain', str(keychain), '--sign', identity, str(path))
        for path in (app, Path('dist/Install MV Image')):
            run('codesign', '--force', '--options', 'runtime', '--timestamp', '--keychain', str(keychain), '--sign', identity, str(path))
        run('codesign', '--verify', '--deep', '--strict', str(app))
        key = root / 'notary.p8'
        key.write_bytes(base64.b64decode(os.environ['NOTARY_KEY'], validate=True))
        for index, path in enumerate((app, Path('dist/Install MV Image'))):
            archive = root / f'notarize-{index}.zip'
            run('ditto', '-c', '-k', '--keepParent', str(path), str(archive))
            result = subprocess.run(['xcrun', 'notarytool', 'submit', str(archive), '--key', str(key),
                                     '--key-id', os.environ['NOTARY_KEY_ID'], '--issuer',
                                     os.environ['NOTARY_ISSUER'], '--wait', '--output-format', 'json'],
                                    capture_output=True, text=True)
            if result.returncode or json.loads(result.stdout).get('status') != 'Accepted':
                raise RuntimeError('A notarização não foi aceita pela Apple.')
        run('xcrun', 'stapler', 'staple', str(app))
    finally:
        run('security', 'list-keychains', '-d', 'user', '-s', *prior)
        run('security', 'delete-keychain', str(keychain))


def sign_windows(root):
    cert = root / 'publisher.pfx'
    cert.write_bytes(base64.b64decode(os.environ['CERTIFICATE'], validate=True))
    kits = Path(os.environ['ProgramFiles(x86)']) / 'Windows Kits/10/bin'
    tool = sorted(kits.glob('*/x64/signtool.exe'))[-1]
    for path in [*Path('dist/MV Image').rglob('*.exe'), Path('dist/Install MV Image.exe')]:
        run(str(tool), 'sign', '/f', str(cert), '/p', os.environ['CERT_PASSWORD'], '/fd', 'SHA256', '/tr', 'http://timestamp.digicert.com', '/td', 'SHA256', str(path))
        run(str(tool), 'verify', '/pa', str(path))


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as temporary:
        (sign_mac if sys.argv[1] == 'macos' else sign_windows)(Path(temporary))
