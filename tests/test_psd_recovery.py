"""Publication/SQLite crash boundary and final-content history contracts."""
from pathlib import Path
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from meury_app.compression_store import CompressionStore
from meury_app.psd_compression import compress
from meury_app.psd_search import SearchSettings, analyze, Cancelled
from meury_app.sevenzip import SevenZip, locate, ArchiveUnavailable


class ManifestEngine:
    """Stores an inspectable inventory independently of the approved manifest."""
    def probe(self, cancel):
        pass

    def add(self, archive, item, stream, cancel):
        entries = json.loads(archive.read_text()) if archive.exists() else []
        content = stream.read()
        entries.append([item.internal, len(content)])
        archive.write_text(json.dumps(entries), encoding="utf-8")

    def validate(self, archive, items, cancel):
        actual = json.loads(archive.read_text(encoding="utf-8"))
        expected = [[item.internal, item.size] for item in items]
        if sorted(actual) != sorted(expected):
            raise RuntimeError("Inventário divergente")
        return "Integridade e inventário OK (simulado)"


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "origem"
        self.source.mkdir()
        self.psd = self.source / "Árvore.psd"
        self.psd.write_bytes(b"conteudo PSD")
        self.ignored = self.source / "ignorado.psd"
        self.ignored.write_bytes(b"ignorado")
        self.settings = SearchSettings((str(self.source),), str(self.root / "saida"))
        self.store = CompressionStore(self.root / "db" / "history.sqlite3")

    def analysis(self):
        result = analyze(self.settings)
        for item in result.files:
            item.selected = item.path == str(self.psd)
        return result

    def run_archive(self, engine=None):
        return compress(self.analysis(), store=self.store, engine=engine or ManifestEngine())

    def interrupted(self):
        real_save = self.store.save
        def fail_final(identity, state, payload):
            if payload.get("published"):
                raise sqlite3.OperationalError("disco do banco cheio")
            real_save(identity, state, payload)
        with patch.object(self.store, "save", side_effect=fail_final):
            with self.assertRaises(sqlite3.OperationalError):
                self.run_archive()
        row = self.store.get(self.store.list()[0]["id"])
        self.assertEqual(row["state"], "em andamento")
        self.assertEqual(row["payload"]["stage"], "publishing")
        self.assertTrue(Path(row["payload"]["archive"]).is_file())
        self.assertEqual(row["files"], [])
        return row

    def test_completed_files_search_multiple_runs_and_report(self):
        first = self.run_archive()
        second = self.run_archive()
        for result in (first, second):
            files = self.store.files(result["id"])
            self.assertEqual(len(files), 1)
            item = files[0]
            self.assertEqual(item["name"], self.psd.name)
            self.assertEqual(item["original_path"], str(self.psd))
            self.assertEqual(item["size"], self.psd.stat().st_size)
            self.assertEqual(item["mtime_ns"], self.psd.stat().st_mtime_ns)
            self.assertEqual(item["internal_path"], result["final_files"][0]["internal"])
            report = Path(result["report"]).read_text(encoding="utf-8")
            included, ignored = report.split("ARQUIVOS IGNORADOS")
            self.assertIn(str(self.psd), included)
            self.assertNotIn(str(self.ignored), included)
            self.assertIn(str(self.ignored), ignored)
            self.assertIn(result["archive"], report)
            self.assertIn(result["id"], report)
            self.assertIn("PSDs incluídos: 1", report)
            self.assertIn("Integridade e inventário OK", report)
        self.assertEqual(len(self.store.list(query="ÁRVORE")), 2)
        self.assertEqual(len(self.store.list(query=str(self.psd))), 2)
        self.assertEqual(self.store.list(query="ignorado"), [])

    def test_failed_and_cancelled_have_no_file_records(self):
        engine = ManifestEngine()
        engine.validate = Mock(side_effect=RuntimeError("CRC inválido"))
        failed = self.run_archive(engine)
        self.assertEqual(failed["state"], "falhou")
        self.assertEqual(self.store.files(failed["id"]), [])
        engine.add = Mock(side_effect=Cancelled("cancelado"))
        cancelled = self.run_archive(engine)
        self.assertEqual(cancelled["state"], "cancelada")
        self.assertEqual(self.store.files(cancelled["id"]), [])

    def test_sqlite_failure_recovery_without_originals_and_idempotency(self):
        row = self.interrupted()
        self.psd.unlink()
        self.ignored.unlink()
        self.source.rmdir()
        self.store.recover(ManifestEngine())
        self.store.recover(ManifestEngine())
        recovered = self.store.get(row["id"])
        self.assertEqual(recovered["state"], "recuperada")
        self.assertEqual(len(recovered["files"]), 1)
        self.assertEqual(recovered["files"][0]["original_path"], str(self.psd))
        self.assertIn("PSDs incluídos: 1", Path(recovered["payload"]["report"]).read_text(encoding="utf-8"))

    def test_missing_is_distinct_from_invalid(self):
        row = self.interrupted()
        Path(row["payload"]["archive"]).unlink()
        self.store.recover(ManifestEngine())
        self.assertEqual(self.store.get(row["id"])["state"], "compactado ausente")
        row = self.interrupted()
        Path(row["payload"]["archive"]).write_text('[["errado.psd", 12]]')
        self.store.recover(ManifestEngine())
        self.assertEqual(self.store.get(row["id"])["state"], "compactado inválido")
        self.assertEqual(self.store.files(row["id"]), [])
        self.assertTrue(Path(row["payload"]["archive"]).exists())

    def test_inaccessible_is_pending_then_retried(self):
        row = self.interrupted()
        archive = Path(row["payload"]["archive"])
        original_open = Path.open
        def unavailable(path, *args, **kwargs):
            if path == archive:
                raise PermissionError("NAS temporariamente inacessível")
            return original_open(path, *args, **kwargs)
        with patch.object(Path, "open", unavailable):
            self.store.recover(ManifestEngine())
        self.assertEqual(self.store.get(row["id"])["state"], "pendente de recuperação")
        self.assertEqual(self.store.files(row["id"]), [])
        self.store.recover(ManifestEngine())
        self.assertEqual(self.store.get(row["id"])["state"], "recuperada")

    def test_validator_unavailable_remains_pending(self):
        row = self.interrupted()
        engine = ManifestEngine()
        engine.validate = Mock(side_effect=ArchiveUnavailable("7z sem acesso"))
        self.store.recover(engine)
        self.assertEqual(self.store.get(row["id"])["state"], "pendente de recuperação")
        self.store.recover(ManifestEngine())
        self.assertEqual(len(self.store.files(row["id"])), 1)

    def test_7zip_access_error_has_priority_over_archive_diagnostic(self):
        engine = SevenZip("fixture")
        engine.run = Mock(side_effect=RuntimeError("Access is denied. Can not open the file as archive"))
        with self.assertRaises(ArchiveUnavailable):
            engine.validate(self.root / "archive.7z", [], lambda: False)
        engine.run = Mock(side_effect=RuntimeError("Data Error"))
        with self.assertRaises(RuntimeError) as failure:
            engine.validate(self.root / "archive.7z", [], lambda: False)
        self.assertNotIsInstance(failure.exception, ArchiveUnavailable)

    def test_offline_destination_is_not_marked_missing(self):
        row = self.interrupted()
        archive = Path(row["payload"]["archive"])
        original_stat = Path.stat
        def offline(path, *args, **kwargs):
            if path == archive or path == archive.parent:
                raise FileNotFoundError("compartilhamento desconectado")
            return original_stat(path, *args, **kwargs)
        with patch.object(Path, "stat", offline):
            self.store.recover(ManifestEngine())
        self.assertEqual(self.store.get(row["id"])["state"], "pendente de recuperação")
        self.store.recover(ManifestEngine())
        self.assertEqual(self.store.get(row["id"])["state"], "recuperada")

    def test_publication_sees_committed_intent_without_file_relations(self):
        from meury_app.platform_utils import publish_without_overwrite
        def publish(source, destination):
            row = self.store.get(self.store.list()[0]["id"])
            self.assertEqual(row["state"], "em andamento")
            self.assertEqual(row["payload"]["stage"], "publishing")
            self.assertEqual(row["payload"]["archive"], str(destination))
            self.assertEqual(len(row["payload"]["approved_manifest"]), 1)
            self.assertEqual(row["files"], [])
            publish_without_overwrite(source, destination)
        with patch("meury_app.psd_compression.publish_without_overwrite", side_effect=publish):
            result = self.run_archive()
        self.assertEqual(len(self.store.files(result["id"])), 1)

    def test_report_failure_is_retried_without_losing_published_archive(self):
        with patch("meury_app.psd_compression.write_content_report", side_effect=PermissionError("relatório indisponível")):
            result = self.run_archive()
        self.assertEqual(result["state"], "pendente de recuperação")
        self.assertTrue(Path(result["archive"]).exists())
        self.assertEqual(self.store.files(result["id"]), [])
        self.store.recover(ManifestEngine())
        self.assertEqual(self.store.get(result["id"])["state"], "recuperada")
        self.assertEqual(len(self.store.files(result["id"])), 1)
        self.assertTrue(Path(result["report"]).is_file())

    def test_final_state_and_relations_roll_back_together(self):
        row = self.interrupted()
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER reject_member BEFORE INSERT ON compression_file BEGIN SELECT RAISE(ABORT, 'simulated full disk'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.recover(ManifestEngine())
        self.assertEqual(self.store.get(row["id"])["state"], "em andamento")
        self.assertEqual(self.store.files(row["id"]), [])
        with self.store.connect() as db:
            db.execute("DROP TRIGGER reject_member")
        self.store.recover(ManifestEngine())
        self.assertEqual(self.store.get(row["id"])["state"], "recuperada")
        self.store.recover(ManifestEngine())
        self.assertEqual(len(self.store.files(row["id"])), 1)

    def test_legacy_migration_preserves_history_without_inventing_inclusion(self):
        self.store.path.parent.mkdir(parents=True)
        payload = {"analysis": self.analysis().snapshot(), "included": 1}
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute("CREATE TABLE compression_run (id TEXT PRIMARY KEY, started TEXT NOT NULL, updated TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL)")
            db.execute("INSERT INTO compression_run VALUES ('legacy', 'old', 'old', 'concluída', ?)", (json.dumps(payload),))
        row = self.store.get("legacy")
        self.assertEqual(row["payload"], json.loads(json.dumps(payload)))
        self.assertEqual(row["files"], [])
        self.assertEqual(self.store.list(query=self.psd.name), [])

    def test_uniqueness_inside_run(self):
        result = self.run_archive()
        row = self.store.get(result["id"])
        self.store.save(row["id"], row["state"], row["payload"])
        self.assertEqual(len(self.store.files(row["id"])), 1)
        with self.assertRaises(sqlite3.IntegrityError), self.store.connect() as db:
            db.execute("INSERT INTO compression_file SELECT * FROM compression_file")

    def test_real_7z_recovery_checks_inventory_and_crc(self):
        try:
            engine = SevenZip(locate())
        except RuntimeError:
            self.skipTest("7-Zip não instalado")
        result = self.run_archive(engine)
        payload = self.store.get(result["id"])["payload"]
        with self.store.connect() as db:
            db.execute("DELETE FROM compression_file WHERE run_id=?", (result["id"],))
        self.store.save(result["id"], "em andamento", payload)
        self.psd.unlink()
        self.store.recover(engine)
        self.assertEqual(self.store.get(result["id"])["state"], "recuperada")
        payload["approved_manifest"][0]["size"] += 1
        self.store.save(result["id"], "em andamento", payload)
        self.store.recover(engine)
        self.assertEqual(self.store.get(result["id"])["state"], "compactado inválido")
        payload["approved_manifest"][0]["size"] -= 1
        Path(result["archive"]).write_bytes(b"not a 7z archive")
        self.store.save(result["id"], "em andamento", payload)
        self.store.recover(engine)
        self.assertEqual(self.store.get(result["id"])["state"], "compactado inválido")
