"""Independent-process regressions for shared data and simultaneous batches."""
from contextlib import closing, redirect_stdout
import io
import json
import multiprocessing
from pathlib import Path
import socket
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from meury_app.shared_files import file_lock, resource_path, ResourceBusy, atomic_write
from meury_app.shared_database import connect, prepare_database, needs_preparation
from meury_app.shared_sessions import session, active_sessions, set_session_user, PENDING_USER
from meury_app.user_store import criar_usuario, autenticar_usuario
from meury_app.operational_store import overlay_records, sync_records, SCHEMA_VERSION


def hold_lock(path, ready, stop, shared=False):
    with file_lock(Path(path), shared=shared, owner=not shared):
        ready.put('ready')
        stop.wait(20)


def hold_session(path, ready, stop):
    with session(Path(path)):
        set_session_user(Path(path), {'usuario': 'Maria'})
        ready.put('ready')
        stop.wait(20)


def hold_writer(path, ready, stop):
    with closing(connect(Path(path))) as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE usuarios SET usuario=usuario')
        ready.put('ready')
        stop.wait(20)


def order(number):
    return {'pedido': number, 'data': '12/11/2026', 'clienteCodigo': '1',
            'clienteNome': 'Cliente', 'produtos': [{'tecidoCodigo': '1',
                'tecidoNome': 'OXFORD', 'estampa': '6162', 'variante': 'A'}]}


def batch_worker(project, output, barrier, creator_barrier, events):
    from meury_app import batch_order_processor as batch

    def context(**kwargs):
        barrier.wait(15)
        return Path(output), {}

    def creator(extraction, *args):
        number = json.loads(extraction.read_text())['pedido']
        events.put(('start', number, time.monotonic()))
        if creator_barrier is not None:
            creator_barrier.wait(15)
        time.sleep(0.15)
        events.put(('end', number, time.monotonic()))
        return {'pedido': number, 'resultadoFinal': 'SUCESSO', 'pastaCriada': '',
                'quantidadeCopiada': 0, 'naoEncontradas': [], 'duplicadas': [], 'erros': []}

    with patch.object(batch, 'load_order_context', side_effect=context), \
         patch.object(batch, 'run_creator', side_effect=creator), redirect_stdout(io.StringIO()):
        code = batch.run_batch(Path(project), update_index=False)
    events.put(('done', code))


class SharedConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ctx = multiprocessing.get_context('spawn')

    def start_holder(self, function, path, *args):
        ready, stop = self.ctx.Queue(), self.ctx.Event()
        process = self.ctx.Process(target=function, args=(str(path), ready, stop, *args))
        process.start()

        def cleanup():
            if process.is_alive():
                stop.set()
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)
            ready.close()
        self.addCleanup(cleanup)
        self.assertEqual(ready.get(timeout=15), 'ready')
        return process, stop

    def test_same_resource_blocks_other_process_and_crash_releases(self):
        lock = resource_path(self.root, 'Pedido A')
        self.assertEqual(lock, resource_path(self.root, 'pedido a'))
        process, _ = self.start_holder(hold_lock, lock)
        with self.assertRaises(ResourceBusy):
            with file_lock(lock):
                pass
        with file_lock(resource_path(self.root, 'Pedido B')):
            pass
        process.terminate()
        process.join(5)
        with file_lock(lock):
            pass

    def test_shared_leases_allow_readers_but_block_maintenance(self):
        gate = self.root / '.update-data.lock'
        self.start_holder(hold_lock, gate, True)
        with file_lock(gate, shared=True):
            pass
        with self.assertRaises(ResourceBusy):
            with file_lock(gate):
                pass

    def test_session_metadata_ignores_dead_process(self):
        process, _ = self.start_holder(hold_session, self.root)
        self.assertEqual(active_sessions(self.root), ['Maria'])
        with self.assertRaises(ResourceBusy):
            with file_lock(self.root / '.update-data.lock'):
                pass
        process.terminate()
        process.join(5)
        self.assertEqual(active_sessions(self.root), [])
        with file_lock(self.root / '.update-data.lock'):
            pass

    def test_second_session_is_refused_with_authenticated_username_before_database_access(self):
        self.start_holder(hold_session, self.root)
        with patch('meury_app.shared_sessions.needs_preparation') as prepare:
            with self.assertRaisesRegex(ResourceBusy, 'Sistema em uso por Maria'):
                with session(self.root):
                    self.fail('second session entered')
            prepare.assert_not_called()

    def test_login_period_is_already_exclusive_and_does_not_invent_username(self):
        with session(self.root):
            self.assertEqual(active_sessions(self.root, exclude_own=False), [PENDING_USER])
            with self.assertRaisesRegex(ResourceBusy, 'aguardando login'):
                with session(self.root):
                    self.fail('login race')
            set_session_user(self.root, {'usuario': 'João', 'senha': 'never-persist'})
            with self.assertRaisesRegex(ResourceBusy, 'Sistema em uso por João'):
                with session(self.root):
                    pass
            metadata = list((self.root / '.sessions').glob('*.json'))
            self.assertEqual(len(metadata), 1)
            self.assertNotIn('never-persist', metadata[0].read_text())
        with session(self.root):
            self.assertEqual(active_sessions(self.root, exclude_own=False), [PENDING_USER])

    def test_stale_metadata_does_not_attribute_new_session_to_previous_user(self):
        folder = self.root / '.sessions'
        folder.mkdir()
        (folder / 'old.lock').touch()
        (folder / 'old.json').write_text(json.dumps({'usuario': 'Anterior'}))
        with session(self.root):
            with self.assertRaises(ResourceBusy) as error:
                with session(self.root):
                    pass
            self.assertNotIn('Anterior', str(error.exception))
        self.assertEqual(active_sessions(self.root), [])

    def test_terminal_refuses_second_session_without_prompting_for_login(self):
        from meury_app import batch_order_processor as batch
        with session(self.root):
            set_session_user(self.root, {'usuario': 'Maria'})
            with patch.object(batch, 'configured_app_dir', return_value=self.root), \
                 patch.object(batch, 'load_local_environment'), \
                 patch.object(batch, 'solicitar_login_lote') as login, \
                 redirect_stdout(io.StringIO()) as output:
                self.assertEqual(batch.main(), 1)
            login.assert_not_called()
            self.assertIn('Sistema em uso por Maria', output.getvalue())

    def test_exception_releases_entire_session_and_clears_identity(self):
        with self.assertRaisesRegex(ValueError, 'failed'):
            with session(self.root):
                set_session_user(self.root, {'usuario': 'Maria'})
                raise ValueError('failed')
        with session(self.root):
            self.assertEqual(active_sessions(self.root, exclude_own=False), [PENDING_USER])

    def test_maintenance_blocks_new_session(self):
        with file_lock(self.root / '.update-data.lock'):
            with self.assertRaises(ResourceBusy):
                with session(self.root):
                    pass
        self.assertFalse((self.root / 'estado_indexador.sqlite3').exists())

    def test_wal_conversion_preserves_data_and_produces_backup(self):
        path = self.root / 'estado_indexador.sqlite3'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE preserved (value TEXT)')
            db.execute("INSERT INTO preserved VALUES ('original')")
        self.assertTrue(needs_preparation(path))
        with session(self.root):
            with closing(connect(path)) as db:
                self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
                self.assertEqual(db.execute('PRAGMA synchronous').fetchone()[0], 2)
                self.assertEqual(db.execute('SELECT value FROM preserved').fetchone()[0], 'original')
            self.assertFalse(needs_preparation(path))
        backups = list(self.root.glob('*.backup-*'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(db.execute('SELECT value FROM preserved').fetchone()[0], 'original')

    def test_conversion_requires_no_other_sessions(self):
        path = self.root / 'estado_indexador.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.execute('PRAGMA journal_mode=WAL')
        self.start_holder(hold_lock, self.root / '.update-data.lock', True)
        with self.assertRaises(ResourceBusy):
            with session(self.root):
                pass
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertFalse(list(self.root.glob('*.backup-*')))

    def test_future_schema_is_refused_without_modification(self):
        path = self.root / 'estado_indexador.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.execute(f'PRAGMA user_version={SCHEMA_VERSION + 1}')
        before = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'mais recente'):
            with session(self.root):
                pass
        self.assertEqual(path.read_bytes(), before)

    def test_login_reads_while_another_process_holds_reserved_write_lock(self):
        path = self.root / 'estado_indexador.sqlite3'
        prepare_database(path)
        criar_usuario(path, 'operador', 'senha-segura', ['separar_pedidos'])
        self.start_holder(hold_writer, path)
        started = time.monotonic()
        self.assertEqual(autenticar_usuario(path, 'operador', 'senha-segura')['usuario'], 'operador')
        self.assertLess(time.monotonic() - started, 3)

    def test_login_and_overlay_do_not_write_or_migrate(self):
        path = self.root / 'estado_indexador.sqlite3'
        prepare_database(path)
        criar_usuario(path, 'operador', 'senha-segura')
        statements = []
        original = connect

        def traced(*args, **kwargs):
            db = original(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db

        with patch('meury_app.user_store.connect', side_effect=traced), \
             patch('meury_app.operational_store.connect', side_effect=traced):
            autenticar_usuario(path, 'operador', 'senha-segura')
            overlay_records(path, [])
        self.assertTrue(statements)
        self.assertTrue(all(sql.lstrip().upper().startswith('SELECT') for sql in statements), statements)
        missing = self.root / 'missing.db'
        overlay_records(missing, [])
        self.assertFalse(missing.exists())

    def test_io_failure_never_bypasses_lock_and_cancel_stops_wait(self):
        with patch('meury_app.shared_files.portalocker.Lock.acquire', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                with file_lock(self.root / 'lock'):
                    self.fail('lock bypassed')
        with self.assertRaisesRegex(RuntimeError, 'cancelada'):
            with file_lock(self.root / 'lock', cancel=lambda: True):
                pass

    def test_failed_atomic_publication_preserves_previous_file(self):
        path = self.root / 'config.json'
        atomic_write(path, 'original')
        with patch('meury_app.shared_files.os.replace', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                atomic_write(path, 'replacement')
        self.assertEqual(path.read_text(), 'original')
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_published_catalog_remains_readable_while_another_process_owns_scan(self):
        from PIL import Image
        from meury_app import indexer
        source = self.root / 'artes'
        source.mkdir()
        (source / '6162').mkdir()
        Image.new('RGB', (2, 2)).save(source / '6162' / '6162-A.jpg')
        catalog = self.root / 'indice.jsonl'
        with patch.object(indexer, 'INDEX_FILE', catalog), \
             patch.object(indexer, 'LEGACY_INDEX_FILE', self.root / 'legacy.json'), \
             patch.object(indexer, 'DUPLICATES_LOG_FILE', self.root / 'duplicates.txt'), \
             patch.object(indexer, 'ensure_app_dir'):
            indexer.build_index(source)
            self.start_holder(hold_lock, catalog.with_suffix('.lock'))
            payload = indexer.load_index_payload(source)
            self.assertEqual(len(payload['records']), 1)
            with self.assertRaises(ResourceBusy):
                indexer.update_index_incremental(source)

    def test_bounded_writes_replay_after_partial_failure(self):
        from meury_app.shared_database import write_batches
        path = self.root / 'partial.db'
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)')
            sql = 'INSERT OR REPLACE INTO items VALUES (?, ?)'

            def interrupted():
                for number in range(205):
                    yield number, 'value'
                raise OSError('disconnected')

            with self.assertRaises(OSError):
                write_batches(db, sql, interrupted())
            self.assertEqual(db.execute('SELECT count(*) FROM items').fetchone()[0], 200)
            write_batches(db, sql, ((number, 'value') for number in range(405)))
            self.assertEqual(db.execute('SELECT count(*) FROM items').fetchone()[0], 405)

    def test_history_after_interrupted_tail_keeps_next_record_readable(self):
        from meury_app.batch_order_processor import append_history, load_successful_records
        path = self.root / 'history.jsonl'
        path.write_text('{"partial":', encoding='utf-8')
        append_history(path, {'sha256': 'digest', 'resultadoFinal': 'SUCESSO'})
        self.assertIn('digest', load_successful_records(path))

    def run_two_batches(self, same_business_order=False):
        from meury_app.batch_order_processor import file_hash, EXTRACTION_VERSION
        project = self.root / 'project'
        inputs = project / 'pedidos_pdf/entrada'
        cache = project / 'pedidos_pdf/.controle/extracoes'
        inputs.mkdir(parents=True)
        cache.mkdir(parents=True)
        for number in ('100', '200'):
            pdf = inputs / (number + '.pdf')
            pdf.write_bytes(b'%PDF-' + number.encode())
            atomic_write(cache / f'v{EXTRACTION_VERSION}_{file_hash(pdf)}.json',
                         json.dumps(order('999' if same_business_order else number)))
        barrier, events = self.ctx.Barrier(2), self.ctx.Queue()
        creator_barrier = None if same_business_order else self.ctx.Barrier(2)
        workers = [self.ctx.Process(target=batch_worker,
                   args=(str(project), str(self.root / 'output'), barrier, creator_barrier, events))
                   for _ in range(2)]
        for process in workers:
            process.start()
        try:
            received = [events.get(timeout=25) for _ in range(6)]
            for process in workers:
                process.join(10)
                self.assertEqual(process.exitcode, 0)
        finally:
            for process in workers:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            events.close()
        self.assertEqual(len(list((project / 'pedidos_pdf/relatorios').iterdir())), 2)
        reports = list((project / 'pedidos_pdf/relatorios').glob('*/LOG_FINAL_ACAO_HUMANA.txt'))
        occupied_reports = [path.read_text() for path in reports if 'reservado por outro PC' in path.read_text()]
        self.assertTrue(occupied_reports)
        self.assertTrue(all('Tudo certo. Nenhuma ação necessária.' not in text for text in occupied_reports))
        self.assertEqual(len(list((inputs / 'concluido').glob('*.pdf'))), 2)
        history = project / 'pedidos_pdf/.controle/historico.jsonl'
        self.assertEqual(len([json.loads(line) for line in history.read_text().splitlines()]), 2)
        self.assertEqual([event[1] for event in received if event[0] == 'done'], [0, 0])
        return received

    def test_different_orders_run_in_parallel_without_duplicate_processing(self):
        events = self.run_two_batches()
        self.assertCountEqual([event[1] for event in events if event[0] == 'start'], ['100', '200'])
        starts = [event[2] for event in events if event[0] == 'start']
        ends = [event[2] for event in events if event[0] == 'end']
        self.assertLess(max(starts), min(ends))

    def test_different_inputs_for_same_output_are_serialized(self):
        events = [event for event in self.run_two_batches(True) if event[0] != 'done']
        events.sort(key=lambda event: event[2])
        self.assertEqual([event[0] for event in events], ['start', 'end', 'start', 'end'])


if __name__ == '__main__':
    unittest.main()
