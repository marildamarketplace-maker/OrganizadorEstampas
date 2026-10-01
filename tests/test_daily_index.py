from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from meury_app import daily_index
from meury_app import indexer as indexer_module
from meury_app.index_run_state import index_updated_today, record_index_update


class IndexRunStateTest(unittest.TestCase):
    def test_matches_date_and_source_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_file = root / "estado.json"
            today = datetime(2026, 10, 1, 9, 30)
            record_index_update([root / "artes"], state_file=state_file, now=today)

            self.assertTrue(
                index_updated_today([root / "artes"], state_file=state_file, now=today)
            )
            self.assertFalse(
                index_updated_today(
                    [root / "outras"], state_file=state_file, now=today,
                )
            )
            self.assertFalse(
                index_updated_today(
                    [root / "artes"],
                    state_file=state_file,
                    now=datetime(2026, 10, 2, 0, 1),
                )
            )

    def test_corrupt_state_is_treated_as_pending(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_file = Path(temporary) / "estado.json"
            state_file.write_text("{invalido", encoding="utf-8")
            self.assertFalse(index_updated_today([], state_file=state_file))

    def test_successful_index_scan_records_the_daily_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "artes"
            source.mkdir()
            index_file = root / "indice.jsonl"
            with patch.object(indexer_module, "INDEX_FILE", index_file), \
                 patch.object(indexer_module, "LEGACY_INDEX_FILE", root / "legado.json"), \
                 patch.object(
                     indexer_module, "DUPLICATES_LOG_FILE", root / "duplicados.txt",
                 ), \
                 patch.object(indexer_module, "ensure_app_dir"):
                indexer_module.build_index(source)

            self.assertTrue(
                index_updated_today(
                    [source], state_file=root / "ultima_atualizacao_indice.json",
                )
            )


class DailyIndexTest(unittest.TestCase):
    def test_skips_scan_when_already_updated_today(self):
        with patch.object(daily_index, "load_config", return_value={"source_dirs": ["artes"]}), \
             patch.object(daily_index, "index_updated_today", return_value=True), \
             patch.object(daily_index.indexer, "update_index_incremental") as incremental:
            updated = daily_index.ensure_daily_index()

        self.assertFalse(updated)
        incremental.assert_not_called()

    def test_runs_incremental_scan_for_existing_catalog(self):
        result = Mock(
            elapsed_seconds=1.2,
            total_found=10,
            unchanged_files=6,
            added_files=2,
            changed_files=1,
            moved_files=1,
            review_files=0,
            absent_files=0,
            errors=0,
            duplicates=0,
        )
        with patch.object(daily_index, "load_config", return_value={"source_dirs": ["artes"]}), \
             patch.object(daily_index, "index_updated_today", return_value=False), \
             patch.object(daily_index.indexer, "index_catalog_available", return_value=True), \
             patch.object(
                 daily_index.indexer,
                 "update_index_incremental",
                 return_value=({}, result),
             ) as incremental:
            updated = daily_index.ensure_daily_index()

        self.assertTrue(updated)
        incremental.assert_called_once()

    def test_builds_initial_index_when_catalog_does_not_exist(self):
        result = Mock(
            elapsed_seconds=1.2,
            source_dirs=1,
            total_files=10,
            indexed_names=9,
            duplicates=1,
            duplicates_log=None,
        )
        with patch.object(
            daily_index, "load_config", return_value={"source_dirs": ["artes"]},
        ), patch.object(
            daily_index, "index_updated_today", return_value=False,
        ), patch.object(
            daily_index.indexer, "index_catalog_available", return_value=False,
        ), patch.object(
            daily_index.indexer, "build_index", return_value=({}, result),
        ) as build:
            updated = daily_index.ensure_daily_index()

        self.assertTrue(updated)
        build.assert_called_once()

    def test_requires_configured_sources(self):
        with patch.object(daily_index, "load_config", return_value={"source_dirs": []}):
            with self.assertRaisesRegex(ValueError, "Nenhuma pasta"):
                daily_index.ensure_daily_index()

    def test_batch_launchers_update_before_processing_orders(self):
        project = Path(__file__).parents[1]
        for filename in (
            "processar_pedidos_lote_mac.sh",
            "processar_pedidos_lote_windows.bat",
        ):
            script = (project / "pedidos_pdf" / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                daily_position = script.index("meury_app.daily_index")
                processor_position = script.rindex("--projeto")
                self.assertLess(daily_position, processor_position)
                self.assertLess(script.index("TRAVA"), daily_position)


if __name__ == "__main__":
    unittest.main()
