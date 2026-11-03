"""Publish a draft only. Promotion to latest remains an explicit release operation."""
import base64
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meury_app import __version__

def publish_draft(repository, artifacts, notes, version, runner=subprocess.run):
    """Retries may replace draft assets, but must never overwrite a published release."""
    import json
    tag = f'v{version}'
    probe = runner(['gh', 'release', 'view', tag, '--repo', repository,
                    '--json', 'isDraft'], capture_output=True, text=True)
    files = list(map(str, sorted(artifacts.iterdir())))
    if probe.returncode == 0:
        if not json.loads(probe.stdout)['isDraft']:
            raise RuntimeError('A versão já foi publicada; não será sobrescrita.')
        runner(['gh', 'release', 'upload', tag, '--repo', repository, '--clobber', *files], check=True)
        runner(['gh', 'release', 'edit', tag, '--repo', repository,
                '--title', f'MV Image {version}', '--notes-file', str(notes)], check=True)
    else:
        runner(['gh', 'release', 'create', tag, '--repo', repository, '--draft',
                '--title', f'MV Image {version}', '--notes-file', str(notes), *files], check=True)


if __name__ == '__main__':
    repository = os.environ['DISTRIBUTION_REPOSITORY']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Repositório de distribuição inválido.')
    artifacts = Path('release-artifacts')
    import shutil
    for directory in artifacts.glob('initial-*'):
        if directory.is_dir():
            shutil.make_archive(str(directory), 'zip', directory)
            shutil.rmtree(directory)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        private = root / 'key.pem'
        private.write_bytes(base64.b64decode(os.environ['UPDATE_PRIVATE_KEY'], validate=True))
        private.chmod(0o600)
        from cryptography.hazmat.primitives.serialization import load_pem_private_key, Encoding, PublicFormat
        signing_key = load_pem_private_key(private.read_bytes(), password=None)
        public = base64.b64encode(signing_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        if public != os.environ['UPDATE_PUBLIC_KEY']:
            raise ValueError('A chave de publicação não corresponde à chave pública dos aplicativos.')
        manifest = root / 'manifest.json'
        notes = Path('RELEASE_NOTES.md')
        subprocess.run([sys.executable, 'scripts/prepare_release.py', str(artifacts), repository, str(notes), str(manifest)], check=True)
        subprocess.run([sys.executable, 'scripts/sign_update.py', str(manifest), str(private), str(artifacts / 'update.json')], check=True)
        publish_draft(repository, artifacts, notes, __version__)
