"""Garante um scan local diário antes do processamento de pedidos em lote."""

from __future__ import annotations

from pathlib import Path

from .config import load_config
from . import indexer
from .index_run_state import index_updated_today, state_file_for


def _progress(_count: int, message: str) -> None:
    print(f"[indice] {message}", flush=True)


def ensure_daily_index() -> bool:
    """Atualiza o índice quando ainda não houve scan das mesmas origens hoje.

    Retorna ``True`` quando um scan foi executado e ``False`` quando o marcador
    diário permitiu pular a etapa.
    """
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
        return False

    print(
        "[indice] Índice ainda não atualizado hoje. Iniciando scan local...",
        flush=True,
    )
    if indexer.index_catalog_available(sources):
        _index, result = indexer.update_index_incremental(
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
        _index, result = indexer.build_index(sources, progress_callback=_progress)
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
    return True


def main() -> int:
    try:
        ensure_daily_index()
    except (OSError, ValueError) as exc:
        print(f"[indice] ERRO: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
