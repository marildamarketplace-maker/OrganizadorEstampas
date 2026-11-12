"""Garante um scan local diário antes do processamento de pedidos em lote."""

from __future__ import annotations

from pathlib import Path
import time

from .shared_files import ResourceBusy

from .batch_output import print
from .config import load_config
from . import indexer
from .index_run_state import index_updated_today, state_file_for


def _progress(_count: int, message: str) -> None:
    print(f"[indice] {message}", flush=True)


def _run_daily_index(
    *, retain_index: bool, cancel_callback=None,
) -> tuple[bool, dict[str, list[str]] | None, list[Path]]:
    """Executa o preflight diário e opcionalmente preserva o índice montado."""
    config = load_config()
    sources = [
        Path(value)
        for value in config.get("source_dirs", [])
        if str(value).strip()
    ]
    if not sources:
        raise ValueError(
            "Nenhuma pasta de estampas está configurada. Abra o aplicativo e "
            "adicione ao menos uma pasta de entrada."
        )

    state_file = state_file_for(indexer.INDEX_FILE)
    print("[indice] Verificando a atualização diária do índice...", flush=True)
    if index_updated_today(sources, state_file=state_file):
        print("[indice] O índice já foi atualizado hoje; etapa dispensada.", flush=True)
        index = indexer.load_index(sources) if retain_index else None
        return False, index, sources

    print(
        "[indice] Índice ainda não atualizado hoje. Iniciando scan local...",
        flush=True,
    )
    while True:
        if cancel_callback and cancel_callback():
            raise RuntimeError('Atualização do índice cancelada.')
        try:
            if indexer.index_catalog_available(sources):
                index, result = indexer.update_index_incremental(
                    sources, progress_callback=_progress,
                )
                print(
                    "[indice] Atualização rápida concluída em "
                    f"{result.elapsed_seconds:.1f}s. "
                    f"Total encontrado: {result.total_found:,}. "
                    f"Inalteradas: {result.unchanged_files:,}. "
                    f"Novas: {result.added_files:,}. "
                    f"Alteradas: {result.changed_files:,}. "
                    f"Movidas/renomeadas: {result.moved_files:,}. "
                    f"Revisão necessária: {result.review_files:,}. "
                    f"Ausentes: {result.absent_files:,}. "
                    f"Erros: {result.errors:,}. "
                    f"Duplicados: {result.duplicates:,}.",
                    flush=True,
                )
            else:
                index, result = indexer.build_index(sources, progress_callback=_progress)
                print(
                    "[indice] Índice concluído em "
                    f"{result.elapsed_seconds:.1f}s. "
                    f"Pastas: {result.source_dirs}. "
                    f"Arquivos: {result.total_files:,}. "
                    f"Nomes: {result.indexed_names:,}. "
                    f"Duplicados: {result.duplicates:,}.",
                    flush=True,
                )
                if result.duplicates_log:
                    print(f"[indice] Log de duplicidades: {result.duplicates_log}", flush=True)
            break
        except ResourceBusy:
            saved = indexer.load_index_payload(sources)
            if saved is not None:
                print('[indice] Outro PC está atualizando. Usando o último catálogo publicado.', flush=True)
                return False, saved['index'] if retain_index else None, sources
            print('[indice] Aguardando o primeiro catálogo; use Cancelar para interromper.', flush=True)
            time.sleep(0.5)
    return True, index if retain_index else None, sources


def ensure_daily_index(*, cancel_callback=None) -> bool:
    """Atualiza o índice sem mantê-lo em memória para outro consumidor.

    Retorna ``True`` quando um scan foi executado e ``False`` quando o marcador
    diário permitiu pular a etapa.
    """
    updated, _index, _sources = _run_daily_index(retain_index=False, cancel_callback=cancel_callback)
    return updated


def prepare_daily_index(*, cancel_callback=None) -> tuple[dict[str, list[str]], list[Path], bool]:
    """Entrega ao lote o índice atualizado sem remontá-lo desnecessariamente."""
    updated, index, sources = _run_daily_index(retain_index=True, cancel_callback=cancel_callback)
    if index is None:
        raise RuntimeError("A preparação diária não retornou o índice de estampas.")
    return index, sources, updated


def main() -> int:
    from .shared_sessions import session
    from .config import configured_app_dir
    try:
        with session(configured_app_dir()):
            ensure_daily_index()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[indice] ERRO: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
