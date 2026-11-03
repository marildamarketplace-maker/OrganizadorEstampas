"""CI-only patch version: MAJOR.MINOR.GITHUB_RUN_NUMBER, identical across jobs."""
from pathlib import Path
import os
import re


def configure(path: Path, run_number: str) -> str:
    if not re.fullmatch(r'[1-9][0-9]{0,8}', run_number):
        raise ValueError('Número de execução inválido.')
    source = path.read_text(encoding='utf-8')
    pattern = r'^__version__ = [\'"](0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)[\'"]$'
    match = re.search(pattern, source, flags=re.MULTILINE)
    if match is None:
        raise ValueError('Versão base não encontrada.')
    result = f'{match[1]}.{match[2]}.{run_number}'
    source = source[:match.start()] + f'__version__ = "{result}"' + source[match.end():]
    path.write_text(source, encoding='utf-8')
    return result


if __name__ == '__main__':
    if os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise SystemExit('Publicação permitida somente a partir de main.')
    root = Path(__file__).resolve().parents[1]
    print('Versão da publicação:', configure(root / 'meury_app/__init__.py', os.environ['GITHUB_RUN_NUMBER']))
