from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from meury_app.compression_store import CompressionStore
from meury_app.psd_search import SearchSettings, analyze
from meury_app.sevenzip import SevenZip, locate
from meury_app.psd_compression import compress
from test_psd_compression import FakeEngine


class IndividualCompressionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        for sub in ("one", "two"):
            folder = self.source / sub
            folder.mkdir()
            (folder / "arte.psd").write_bytes(b"original PSD")
        self.settings = SearchSettings((str(self.source),), "", mode="individual")
        self.store = CompressionStore(self.root / "history" / "state.sqlite3")

    def run_compression(self, engine=None, cancel=lambda: False):
        return compress(analyze(self.settings), cancel=cancel, store=self.store,
                        engine=engine or FakeEngine())

    def test_unit_archives_beside_originals_with_homonyms(self):
        result = self.run_compression()
        self.assertEqual(result["state"], "concluída")
        self.assertEqual(len(result["results"]), 2)
        for row in result["results"]:
            archive = Path(row["archive"])
            self.assertEqual(archive.name, archive.parent.name + ".7z")
            self.assertEqual((archive.parent / "arte.psd").read_bytes(), b"original PSD")
            self.assertTrue(Path(row["log"]).is_file())
            self.assertTrue(Path(row["report"]).is_file())
            self.assertEqual(row["final_files"][0]["internal"], "arte.psd")

    def test_existing_archive_is_preserved_and_other_files_continue(self):
        archive = self.source / "one" / "one.7z"
        archive.write_bytes(b"existing")
        result = self.run_compression()
        self.assertEqual(result["state"], "concluída com avisos")
        self.assertEqual(archive.read_bytes(), b"existing")
        self.assertTrue((self.source / "two" / "two.7z").exists())
        self.assertTrue(result["errors"])

    def test_cancellation_preserves_completed_archive(self):
        # Trigger cancellation between units, after the first publication.
        calls = []
        def progress(*args):
            calls.append(args)
        engine = FakeEngine()
        result = compress(analyze(self.settings), store=self.store, engine=engine,
                          progress=progress,
                          cancel=lambda: any(args[1:] == (1, 2) for args in calls))
        self.assertEqual(result["state"], "cancelada")
        self.assertEqual(len(list(self.source.rglob("*.7z"))), 1)

    def test_failed_unit_leaves_no_archive_and_does_not_remove_foreign_temporary(self):
        foreign = self.source / "one" / "incompleto-other.7z"
        foreign.write_bytes(b"unrelated")
        class Broken(FakeEngine):
            def validate(self, archive, items, cancel):
                raise RuntimeError("validation failed")
        result = self.run_compression(Broken())
        self.assertEqual(result["state"], "falhou")
        self.assertEqual(foreign.read_bytes(), b"unrelated")
        self.assertEqual(list(self.source.rglob("one.7z")), [])
        self.assertEqual(list(self.source.rglob("two.7z")), [])

    def test_ignored_output_does_not_exclude_source(self):
        settings = replace(self.settings, output=str(self.source))
        self.assertEqual(len(analyze(settings).files), 2)

    def test_real_sevenzip_individual_archives(self):
        try:
            engine = SevenZip(locate())
        except RuntimeError as exc:
            self.skipTest(str(exc))
        result = self.run_compression(engine)
        self.assertEqual(result["state"], "concluída", result["errors"])
        for row in result["results"]:
            self.assertEqual(engine.validate(Path(row["archive"]),
                             [type("Item", (), item)() for item in row["final_files"]],
                             lambda: False), row["validation"])

    def test_three_psds_share_one_archive_and_subfolder_is_separate(self):
        for name in ("second.psd", "third.psd"):
            (self.source / "one" / name).write_bytes(b"extra")
        nested = self.source / "one" / "nested.v1"
        nested.mkdir()
        (nested / "child.psd").write_bytes(b"nested")
        result = self.run_compression()
        self.assertEqual(result["state"], "concluída")
        by_folder = {Path(row["archive"]).parent: row for row in result["results"]}
        self.assertEqual(len(by_folder), 3)
        self.assertEqual({item["internal"] for item in by_folder[self.source / "one"]["final_files"]},
                         {"arte.psd", "second.psd", "third.psd"})
        self.assertEqual(Path(by_folder[nested]["archive"]).name, "nested.v1.7z")
        self.assertEqual(by_folder[nested]["included"], 1)

    def test_multiple_origins_overlap_and_selection(self):
        other = self.root / "other"
        other.mkdir()
        (other / "extra.psd").write_bytes(b"extra")
        ignored = self.source / "one" / "ignored.psd"
        ignored.write_bytes(b"ignored")
        analysis = analyze(replace(self.settings, sources=(
            str(self.source), str(self.source / "one"), str(other))))
        for item in analysis.files:
            if Path(item.path) == ignored:
                item.selected = False
        result = compress(analysis, store=self.store, engine=FakeEngine())
        self.assertEqual(result["state"], "concluída com avisos")  # overlapping origin is reported
        self.assertEqual(len(result["results"]), 3)
        included = [item["path"] for row in result["results"] for item in row["final_files"]]
        self.assertEqual(len(included), 3)
        self.assertNotIn(str(ignored), included)
        self.assertFalse((other / "extra.7z").exists())
        self.assertTrue((other / "other.7z").exists())
