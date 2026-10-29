import os
from pathlib import Path
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from meury_app.order_batch_tab import OrderBatchTab
from meury_app.user_store import criar_usuario


@unittest.skipUnless(os.environ.get('MEURY_TEST_TK') == '1', 'Requer sessão gráfica Tk')
class OrderBatchTkTests(unittest.TestCase):
    def test_question_background_processing_live_logs_and_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            database = project / 'users.db'
            user = criar_usuario(database, 'operadora', 'senha-operadora', ['separar_pedidos'])
            root = tk.Tk()
            root.withdraw()
            try:
                app = SimpleNamespace(root=root, usuario_atual={'id': user, 'permissoes': ['separar_pedidos']})
                app._set_busy = lambda busy: tab.set_busy(busy)
                tab = OrderBatchTab(root, app)
                tab.append_log('Primeira mensagem\n')
                tab.follow_logs.set(False)
                with patch.object(tab.log, 'see') as see:
                    tab.append_log('Segunda mensagem\n')
                    see.assert_not_called()
                tab.copy_logs()
                self.assertIn('Segunda mensagem', root.clipboard_get())
                self.assertEqual(str(tab.log['state']), 'disabled')
                tab.project.set(str(project))
                with patch('meury_app.order_batch_tab.OPERATIONAL_DB_FILE', database), \
                     patch('meury_app.order_batch_tab.messagebox.askyesnocancel', return_value=False) as question, \
                     patch('meury_app.batch_order_processor.solicitar_login_lote', side_effect=AssertionError('sem login')):
                    root.after(10, tab.start)
                    def wait():
                        if tab.status.get().startswith('Concluído') or tab.status.get().startswith('Falha'):
                            root.quit()
                        else:
                            root.after(50, wait)
                    root.after(50, wait)
                    root.after(5000, root.quit)
                    root.mainloop()
                question.assert_called_once()
                self.assertEqual(tab.status.get(), 'Concluído.')
                self.assertIn('Nenhum PDF encontrado', tab.log.get('1.0', 'end'))
                self.assertIn('Relatório completo:', tab.log.get('1.0', 'end'))
                self.assertEqual(str(tab.start_button['state']), 'normal')
                tab.read_reports()
                root.update_idletasks()
                reader = tab.report_reader
                self.assertTrue(reader.paths)
                self.assertEqual(reader.selected_path().name, 'LOG_FINAL_ACAO_HUMANA.txt')
                self.assertTrue(reader.text.get('1.0', 'end').strip())
                self.assertEqual(str(reader.text['state']), 'disabled')
                tab.read_reports()
                self.assertIs(tab.report_reader, reader)
                reader.window.destroy()
                entry = project / 'pedidos_pdf/entrada'
                completed = entry / 'concluido'
                (entry / 'aguardando.pdf').write_bytes(b'%PDF-teste')
                (completed / 'feito.pdf').write_bytes(b'%PDF-teste')
                tab.pdf_panel.refresh()
                self.assertEqual(tab.pdf_panel.frames['entrada']['text'], 'Entrada (1)')
                self.assertEqual(tab.pdf_panel.frames['entrada/concluido']['text'], 'Concluídos (1)')
                tab.pdf_panel.tables['entrada'].selection_set('0')
                with patch('meury_app.order_pdf_panel.open_with_default_application') as open_pdf:
                    tab.pdf_panel.open_pdf('entrada')
                open_pdf.assert_called_once_with(entry.resolve() / 'aguardando.pdf')
                (entry / 'aguardando.pdf').rename(completed / 'aguardando.pdf')
                tab.pdf_panel.refresh()
                self.assertEqual(tab.pdf_panel.frames['entrada']['text'], 'Entrada (0)')
                self.assertEqual(tab.pdf_panel.frames['entrada/concluido']['text'], 'Concluídos (2)')
            finally:
                root.destroy()
