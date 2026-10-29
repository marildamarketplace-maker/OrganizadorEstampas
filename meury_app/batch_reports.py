"""Consulta limitada e somente leitura dos relatórios de pedidos."""
from pathlib import Path

REPORT_EXTENSIONS = {'.txt', '.csv', '.json', '.log'}
MAX_PREVIEW_BYTES = 512 * 1024


def list_reports(project: Path) -> list[Path]:
    folder = Path(project).expanduser().resolve() / 'pedidos_pdf' / 'relatorios'
    if not folder.is_dir():
        return []
    result = []
    runs = sorted((p for p in folder.iterdir() if p.is_dir() and not p.is_symlink()), reverse=True)
    for run in runs[:100]:
        files = sorted((p for p in run.iterdir() if p.is_file() and not p.is_symlink()
                        and p.suffix.lower() in REPORT_EXTENSIONS),
                       key=lambda p: (p.name != 'LOG_FINAL_ACAO_HUMANA.txt', p.name))
        result.extend(files[:1000 - len(result)])
        if len(result) >= 1000:
            break
    return result


def report_path(project: Path, path: Path) -> Path:
    folder = Path(project).expanduser().resolve() / 'pedidos_pdf' / 'relatorios'
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(folder.resolve()) or resolved.suffix.lower() not in REPORT_EXTENSIONS:
        raise ValueError('Arquivo fora da pasta de relatórios ou formato não permitido.')
    if not resolved.is_file():
        raise ValueError('O relatório não está mais disponível.')
    return resolved


def read_report(project: Path, path: Path) -> str:
    path = report_path(project, path)
    with path.open('rb') as stream:
        raw = stream.read(MAX_PREVIEW_BYTES + 1)
    text = raw[:MAX_PREVIEW_BYTES].decode('utf-8-sig', errors='replace')
    if len(raw) > MAX_PREVIEW_BYTES:
        text += '\n\n[Prévia limitada a 512 KB. Use “Abrir arquivo completo” para ler o restante.]'
    return text
