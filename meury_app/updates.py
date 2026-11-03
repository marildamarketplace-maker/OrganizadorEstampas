"""Signed release transport. No private credentials are shipped to clients."""
from __future__ import annotations
import base64
import hashlib
import json
import platform
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path

MAX_MANIFEST = 256 * 1024
MAX_PACKAGE = 4 * 1024**3
HOSTS = {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value):
        raise ValueError('Versão inválida.')
    return tuple(map(int, value.split('.')))


def architecture():
    machine = platform.machine().lower()
    return {'amd64': 'x86_64', 'aarch64': 'arm64'}.get(machine, machine)


def validate_url(url):
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Destino de atualização não permitido.')
    return url


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def open_url(url):
    validate_url(url)
    import certifi
    opener = urllib.request.build_opener(Redirects(), urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))
    return opener.open(urllib.request.Request(url, headers={'User-Agent': 'MV-Image-Updater'}), timeout=20)


def trust_config():
    # Build-time asset only; never take trust anchors from .env or remote metadata.
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    config = json.loads((base / 'assets/update-trust.json').read_text())
    if not config.get('public_key') or not config.get('manifest_url'):
        raise ValueError('Atualizações ainda não configuradas pelo distribuidor.')
    validate_url(config['manifest_url'])
    return config


def verify_manifest(raw, public_key, current, system=None, arch=None):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    envelope = json.loads(raw)
    payload = base64.b64decode(envelope['payload'], validate=True)
    signature = base64.b64decode(envelope['signature'], validate=True)
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True)).verify(signature, payload)
    document = json.loads(payload)
    if document['schema'] != 1:
        raise ValueError('Formato de atualização incompatível.')
    if version(document['version']) <= version(current):
        return None
    key = f'{system or sys.platform}-{arch or architecture()}'
    item = dict(document['packages'][key])
    validate_url(item['url'])
    if type(item['size']) is not int or not 0 < item['size'] <= MAX_PACKAGE:
        raise ValueError('Tamanho de pacote inválido.')
    if not re.fullmatch('[a-f0-9]{64}', item['sha256']):
        raise ValueError('Hash inválido.')
    item.update(version=document['version'], notes=str(document.get('notes', ''))[:16000])
    return item


def check(current):
    config = trust_config()
    with open_url(config['manifest_url']) as response:
        raw = response.read(MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST:
        raise ValueError('Metadados excedem o limite.')
    return raw, verify_manifest(raw, config['public_key'], current)


def download(item, destination, progress=lambda value: None):
    destination = Path(destination)
    partial = destination.with_suffix('.partial')
    digest = hashlib.sha256()
    count = 0
    started = time.monotonic()
    try:
        with open_url(item['url']) as response, partial.open('xb') as output:
            while block := response.read(1024 * 1024):
                if time.monotonic() - started > 1200:
                    raise TimeoutError('Tempo de download excedido.')
                count += len(block)
                if count > item['size']:
                    raise ValueError('Pacote excede o tamanho declarado.')
                output.write(block)
                digest.update(block)
                progress(count / item['size'])
        if count != item['size'] or digest.hexdigest() != item['sha256']:
            raise ValueError('Pacote incompleto ou adulterado.')
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
