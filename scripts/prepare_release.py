"""Validate all release artifacts and produce unsigned metadata for offline signing."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meury_app import __version__
from meury_app.updates import version, validate_url

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('artifacts', type=Path)
    parser.add_argument('repository', help='owner/repository')
    parser.add_argument('notes', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    version(__version__)
    packages = {}
    for key in ('win32-x86_64',):
        filename = f'MV-Image-{__version__}-{key}.zip'
        path = args.artifacts / filename
        digest = hashlib.sha256()
        with path.open('rb') as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
        url = f'https://github.com/{args.repository}/releases/download/v{__version__}/{filename}'
        validate_url(url)
        packages[key] = {'url': url, 'size': path.stat().st_size, 'sha256': digest.hexdigest()}
    args.output.write_text(json.dumps({'schema': 1, 'version': __version__,
                                     'notes': args.notes.read_text(), 'packages': packages}, indent=2))
