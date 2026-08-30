import unittest
from pathlib import Path


MIGRATIONS = Path(__file__).parents[1] / "supabase" / "migrations"


class SupabaseMigrationsTest(unittest.TestCase):
    def test_remote_identity_migrations_preserve_all_relative_paths(self):
        multiple = (MIGRATIONS / "003_multiplos_arquivos_por_variante.sql").read_text(
            encoding="utf-8"
        )
        relative = (MIGRATIONS / "004_identidade_por_caminho_relativo.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("codigo, variante, arquivo_id", multiple)
        self.assertIn("original_relative_path", relative)
        self.assertNotIn("DELETE FROM", relative.upper())

    def test_search_migration_has_shared_fields_and_fts_index(self):
        sql = (MIGRATIONS / "002_estampas_pesquisa.sql").read_text(encoding="utf-8")
        for field in (
            "titulo", "descricao", "tema", "palavras_chave", "cores",
            "elementos_visuais", "ocasioes", "categorias", "texto_pesquisa",
            "ai_metadata", "ai_processed_hash", "processing_error", "processed_at",
            "search_vector",
        ):
            self.assertIn(field, sql)
        self.assertIn("to_tsvector('simple'", sql)
        self.assertIn("USING gin (search_vector)", sql)
        self.assertIn("BEFORE INSERT OR UPDATE", sql)

    def test_indexer_payload_does_not_own_ai_fields(self):
        source = (Path(__file__).parents[1] / "meury_app" / "supabase_sync.py").read_text(
            encoding="utf-8"
        )
        payload_body = source.split("def _payload", 1)[1].split("def sync_pending_records", 1)[0]
        for field in (
            "titulo", "descricao", "tema", "palavras_chave", "cores",
            "elementos_visuais", "ocasioes", "categorias", "texto_pesquisa",
            "ai_metadata", "ai_processed_hash", "processing_error", "processed_at",
        ):
            self.assertNotIn(f'"{field}"', payload_body)


if __name__ == "__main__":
    unittest.main()
