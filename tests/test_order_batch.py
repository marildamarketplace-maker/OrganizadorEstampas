from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from meury_app.order_batch import executar_lote
from meury_app.user_store import criar_usuario
from meury_app.batch_output import output_sink, print as batch_print
from meury_app.order_batch_tab import OrderBatchTab


class OrderBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.db = self.project / 'users.db'
        self.admin = criar_usuario(self.db, 'admin', 'senha-admin', ['admin'])

    def test_same_core_logs_reports_no_login_and_both_index_choices(self):
        for update in (True, False):
            with self.subTest(update=update), patch('meury_app.batch_order_processor.ensure_daily_index') as index, \
                 patch('meury_app.batch_order_processor.solicitar_login_lote', side_effect=AssertionError('não deve pedir login')), \
                 patch('meury_app.batch_order_processor.choose_index_update', side_effect=AssertionError('pergunta pertence à UI')):
                logs = []
                self.assertEqual(executar_lote(self.project, self.db, self.admin, update_index=update, log=logs.append), 0)
                self.assertIn('Nenhum PDF encontrado', ''.join(logs))
                self.assertTrue(list(self.project.glob('pedidos_pdf/relatorios/*/LOG_FINAL_ACAO_HUMANA.txt')))
                self.assertEqual(index.called, update)
                self.assertFalse((self.project / 'pedidos_pdf/.controle/em_execucao').exists())
                self.assertIsNone(output_sink.get())

    def test_regular_user_needs_permission(self):
        user = criar_usuario(self.db, 'normal', 'senha-normal', ['separar_estampas'])
        with self.assertRaises(PermissionError), patch('meury_app.order_batch.run_batch') as core:
            executar_lote(self.project, self.db, user, update_index=False, log=lambda text: None)
        core.assert_not_called()
        user = criar_usuario(self.db, 'operadora', 'senha-operadora', ['separar_pedidos'])
        with patch('meury_app.order_batch.run_batch', return_value=0) as core:
            self.assertEqual(executar_lote(self.project, self.db, user, update_index=False, log=lambda text: None), 0)
        core.assert_called_once()

    def test_existing_lock_preserved_and_errors_release_own_lock(self):
        lock = self.project / 'pedidos_pdf/.controle/em_execucao'
        lock.mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError, 'andamento'):
            executar_lote(self.project, self.db, self.admin, update_index=False, log=lambda text: None)
        self.assertTrue(lock.is_dir())
        lock.rmdir()
        with patch('meury_app.order_batch.run_batch', side_effect=RuntimeError('falha')):
            with self.assertRaises(RuntimeError):
                executar_lote(self.project, self.db, self.admin, update_index=False, log=lambda text: None)
        self.assertFalse(lock.exists())
        self.assertIsNone(output_sink.get())

    def test_log_context_does_not_capture_other_threads(self):
        import threading
        logs = []
        token = output_sink.set(logs.append)
        try:
            worker = threading.Thread(target=lambda: batch_print('outro contexto'))
            with patch('builtins.print') as console:
                worker.start()
                worker.join()
            console.assert_called_once()
            batch_print('lote')
        finally:
            output_sink.reset(token)
        self.assertEqual(logs, ['lote\n'])


class BatchTabTests(unittest.TestCase):
    def test_cancelling_question_does_not_start_work(self):
        tab = OrderBatchTab.__new__(OrderBatchTab)
        tab.app = Mock()
        tab.worker = None
        tab.project = Mock()
        tab.project.get.return_value = '/tmp/lote'
        with patch('meury_app.order_batch_tab.messagebox.askyesnocancel', return_value=None), \
             patch('meury_app.order_batch_tab.threading.Thread') as worker:
            tab.start()
        worker.assert_not_called()
        tab.app._set_busy.assert_not_called()
