"""Sign a release description using an externally supplied Ed25519 PEM key."""
import argparse
import base64
import json
from pathlib import Path
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest', type=Path)
    parser.add_argument('key', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    payload = json.dumps(json.loads(args.manifest.read_text()), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    key = load_pem_private_key(args.key.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('A chave deve ser Ed25519.')
    args.output.write_text(json.dumps({'payload': base64.b64encode(payload).decode(),
                                     'signature': base64.b64encode(key.sign(payload)).decode()}))
