"""Adaptação do lote para uma sessão autenticada da interface."""
from pathlib import Path

from .batch_output import output_sink
from .batch_order_processor import print_terminal_header, run_batch
from .batch_output import print
from .user_store import exigir_permissao


def executar_lote(project: Path, database: Path, usuario_id: int, *, update_index: bool,
                  log, codex_path: str = '', temporary_folder: Path | None = None, cancel_callback=None) -> int:
    exigir_permissao(database, usuario_id, 'separar_pedidos')
    project = Path(project).resolve()
    token = output_sink.set(log)
    try:
        print_terminal_header(project / 'pedidos_pdf' / 'entrada')
        if not update_index:
            print('[ÍNDICE] Usando o índice salvo, sem atualização.')
        return run_batch(project, codex_path=codex_path, update_index=update_index,
                         temporary_folder=temporary_folder, **({'cancel_callback': cancel_callback} if cancel_callback else {}))
    finally:
        output_sink.reset(token)
