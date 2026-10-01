from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from meury_app import indexer
from meury_app.index_journal import CandidateJournal, catalog_lock


class IndexRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.source = self.root / "originais"
        self.source.mkdir()
        for name, filename in {
            "INDEX_FILE": "catalog.jsonl", "LEGACY_INDEX_FILE": "legacy.json",
            "ANALYSIS_RESULTS_FILE": "analysis.jsonl", "DUPLICATES_LOG_FILE": "duplicates.txt",
        }.items():
            self.stack.enter_context(patch.object(indexer, name, self.root / filename))
        self.stack.enter_context(patch.object(indexer, "ensure_app_dir"))

    def image(self, name, content=b"%PDF-1.4"):
        path = self.source / "100" / (name + ".pdf")
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        return path

    def test_large_unchanged_scan_has_no_hash_checkpoint_catalog_write_or_file_stat(self):
        for n in range(1100):
            self.image(f"100-A-{n}")
        indexer.build_index(self.source)
        before = indexer.INDEX_FILE.read_bytes()
        real_stat = Path.stat

        def no_original_stat(path, *args, **kwargs):
            if path.suffix == ".pdf":
                self.fail("Unchanged file used Path.stat instead of DirEntry metadata")
            return real_stat(path, *args, **kwargs)

        with patch.object(Path, "stat", no_original_stat), patch.object(
            indexer, "calculate_content_hash", side_effect=AssertionError("hash")
        ), patch.object(indexer, "_write_catalog", side_effect=AssertionError("rewrite")):
            _, result = indexer.update_index_incremental(self.source)
        self.assertEqual(result.unchanged_files, 1100)
        for key in ("catalog_writes", "journal_bytes_written", "hash_bytes_read", "checkpoints"):
            self.assertEqual(result.performance["counters"][key], 0)
        self.assertEqual(result.performance["counters"]["entry_stat_calls"], 1100)
        self.assertEqual(indexer.INDEX_FILE.read_bytes(), before)
        report = json.loads(indexer.INDEX_FILE.with_suffix(".performance.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "completed")

    def test_interrupted_scan_preserves_catalog_and_reuses_candidate_after_truncated_tail(self):
        self.image("100-A")
        indexer.build_index(self.source)
        before = indexer.INDEX_FILE.read_bytes()
        self.image("100-B", b"%PDF-new")
        real_scan = indexer._entries_with_metrics

        def interrupted(*args):
            yield from real_scan(*args)
            raise KeyboardInterrupt()

        with patch.object(indexer, "_entries_with_metrics", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                indexer.update_index_incremental(self.source)
        self.assertEqual(indexer.INDEX_FILE.read_bytes(), before)
        journal = indexer.INDEX_FILE.with_suffix(".scan.jsonl")
        with journal.open("ab") as stream:
            stream.write(b'{"key": "incomplete')
        # Diário parcial não aparece para consumidores normais.
        self.assertEqual(len(indexer.load_index(self.source)), 1)
        with patch.object(indexer, "calculate_content_hash", side_effect=AssertionError("rehash")):
            _, result = indexer.update_index_incremental(self.source)
        self.assertEqual(result.added_files, 1)
        self.assertEqual(result.performance["counters"]["recovered_candidates"], 1)
        self.assertFalse(journal.exists())

    def test_first_build_recovery_does_not_publish_partial_catalog(self):
        self.image("100-A")
        with patch.object(indexer, "_write_catalog", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                indexer.build_index(self.source)
        self.assertFalse(indexer.INDEX_FILE.exists())
        with patch.object(indexer, "calculate_content_hash", side_effect=AssertionError("rehash")):
            _, result = indexer.build_index(self.source)
        self.assertEqual(result.total_files, 1)
        self.assertEqual(result.performance["counters"]["recovered_candidates"], 1)

    def test_abrupt_process_exit_recovers_durable_batch_and_replays_only_tail(self):
        for number in range(1025):
            self.image(f"100-A-{number}")
        script = """
import os, sys
from pathlib import Path
from meury_app import indexer
indexer.INDEX_FILE = Path(sys.argv[1])
indexer.LEGACY_INDEX_FILE = indexer.INDEX_FILE.with_suffix('.legacy.json')
indexer.DUPLICATES_LOG_FILE = indexer.INDEX_FILE.with_suffix('.duplicates.txt')
original = indexer.calculate_content_hash
calls = 0
def hash_then_exit(path):
    global calls
    calls += 1
    if calls == 1001:
        os._exit(73)
    return original(path)
indexer.calculate_content_hash = hash_then_exit
indexer.build_index(Path(sys.argv[2]))
"""
        completed = subprocess.run(
            [sys.executable, "-c", script, str(indexer.INDEX_FILE), str(self.source)],
            env={**os.environ, "MEURY_APP_DATA_PATH": str(self.root)},
            capture_output=True, timeout=60,
        )
        self.assertEqual(completed.returncode, 73, completed.stderr)
        self.assertFalse(indexer.INDEX_FILE.exists())
        _, result = indexer.build_index(self.source)
        self.assertEqual(result.performance["counters"]["recovered_candidates"], 1000)
        self.assertEqual(result.performance["counters"]["hash_files"], 25)
        _, repeated = indexer.update_index_incremental(self.source)
        self.assertEqual(repeated.unchanged_files, 1025)
        self.assertEqual(repeated.performance["counters"]["catalog_writes"], 0)

    def test_changed_candidate_invalidates_recovery_cache(self):
        original = self.image("100-A")
        with patch.object(indexer, "_write_catalog", side_effect=OSError("stop")):
            with self.assertRaises(OSError):
                indexer.build_index(self.source)
        original.write_bytes(b"%PDF-different-content")
        _, result = indexer.build_index(self.source)
        self.assertEqual(result.performance["counters"]["recovered_candidates"], 0)
        record = indexer.load_catalog_records(self.source)[0]
        self.assertEqual(record["content_hash"], hashlib.sha256(original.read_bytes()).hexdigest())

    def test_inaccessible_directory_does_not_publish_false_absence(self):
        self.image("100-A")
        indexer.build_index(self.source)
        before = indexer.INDEX_FILE.read_bytes()
        real_scandir = os.scandir

        def inaccessible(path):
            if Path(path).name == "100":
                raise PermissionError("disconnected")
            return real_scandir(path)

        with patch.object(indexer.os, "scandir", inaccessible):
            with self.assertRaises(OSError):
                indexer.update_index_incremental(self.source)
        self.assertEqual(indexer.INDEX_FILE.read_bytes(), before)
        self.assertTrue(indexer.load_catalog_records(self.source)[0]["active"])

    def test_entry_stat_error_aborts_instead_of_marking_missing(self):
        file = self.image("100-A")
        indexer.build_index(self.source)
        before = indexer.INDEX_FILE.read_bytes()

        class Entry:
            path = str(file)
            name = file.name
            def is_dir(self, **kwargs): return False
            def is_file(self, **kwargs): return True
            def stat(self, **kwargs): raise PermissionError("unreadable")

        class Entries:
            def __enter__(self): return iter([Entry()])
            def __exit__(self, *args): pass

        with patch.object(indexer.os, "scandir", return_value=Entries()):
            with self.assertRaises(OSError):
                indexer.update_index_incremental(self.source)
        self.assertEqual(indexer.INDEX_FILE.read_bytes(), before)

    def test_atomic_replace_failure_keeps_previous_catalog(self):
        self.image("100-A")
        indexer.build_index(self.source)
        before = indexer.INDEX_FILE.read_bytes()
        self.image("100-B")
        real_replace = os.replace

        def fail_publish(source, destination):
            if Path(destination) == indexer.INDEX_FILE:
                raise PermissionError("locked catalog")
            return real_replace(source, destination)

        with patch.object(indexer.os, "replace", fail_publish):
            with self.assertRaises(PermissionError):
                indexer.update_index_incremental(self.source)
        self.assertEqual(indexer.INDEX_FILE.read_bytes(), before)
        self.assertEqual(len(indexer.load_index(self.source)), 1)
        _, result = indexer.update_index_incremental(self.source)
        self.assertEqual(result.added_files, 1)

    def test_commit_recovery_before_operational_overlay_keeps_new_hash(self):
        file = self.image("100-A")
        indexer.build_index(self.source)
        file.write_bytes(b"%PDF-new-content")
        with patch.object(indexer, "sync_records", side_effect=OSError("sqlite unavailable")):
            with self.assertRaises(OSError):
                indexer.update_index_incremental(self.source)
        self.assertTrue(indexer.INDEX_FILE.with_suffix(".commit.json").exists())
        records = indexer.load_catalog_records(self.source)
        self.assertEqual(records[0]["content_hash"], hashlib.sha256(file.read_bytes()).hexdigest())
        self.assertFalse(indexer.INDEX_FILE.with_suffix(".commit.json").exists())
        _, result = indexer.update_index_incremental(self.source)
        self.assertEqual(result.unchanged_files, 1)

    def test_file_mutation_during_hash_does_not_commit(self):
        file = self.image("100-A")
        real_hash = indexer.calculate_content_hash

        def mutate(path):
            value = real_hash(path)
            path.write_bytes(b"%PDF-mutated-after-read")
            return value

        with patch.object(indexer, "calculate_content_hash", mutate):
            with self.assertRaises(OSError):
                indexer.build_index(self.source)
        self.assertFalse(indexer.INDEX_FILE.exists())

    def test_concurrent_update_rejected_and_lock_released(self):
        self.image("100-A")
        with catalog_lock(indexer.INDEX_FILE.with_suffix(".lock")):
            with self.assertRaises(RuntimeError):
                indexer.build_index(self.source)
        indexer.build_index(self.source)

    def test_journal_from_other_root_is_not_reused(self):
        file = self.image("100-A")
        path = indexer.INDEX_FILE.with_suffix(".scan.jsonl")
        journal = CandidateJournal(path, [self.source], indexer.INDEX_VERSION)
        journal.add("example", file.stat(), None, "0" * 64)
        journal.flush()
        other = self.root / "other"
        other.mkdir()
        recovered = CandidateJournal(path, [other], indexer.INDEX_VERSION)
        self.assertIsNone(recovered.lookup("example", file.stat()))


if __name__ == "__main__":
    unittest.main()
