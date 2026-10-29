import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from meury_app.user_management_tab import UserManagementTab
from meury_app.user_store import criar_usuario, autenticar_usuario


@unittest.skipUnless(os.environ.get('MEURY_TEST_TK') == '1', 'Requer sessão gráfica Tk')
class UserManagementTkTests(unittest.TestCase):
    def test_create_select_edit_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'users.db'
            admin = criar_usuario(database, 'admin', 'senha-admin', ['admin'])
            root = tk.Tk()
            root.withdraw()
            try:
                with patch('meury_app.user_management_tab.OPERATIONAL_DB_FILE', database):
                    tab = UserManagementTab(root, {'id': admin, 'usuario': 'admin', 'permissoes': ['admin']})
                    self.assertEqual(len(tab.records), 1)
                    tab.usuario.set('operadora')
                    tab.senha.set('senha-operadora')
                    tab.confirmacao.set('senha-operadora')
                    tab.permissoes['separar_estampas'].set(True)
                    tab.create()
                    self.assertEqual(len(tab.records), 2)
                    identity = autenticar_usuario(database, 'operadora', 'senha-operadora')
                    tab.table.selection_set(str(identity['id']))
                    tab.select_user()
                    self.assertEqual(tab.usuario.get(), 'operadora')
                    self.assertEqual(tab.senha.get(), '')
                    self.assertTrue(tab.permissoes['separar_estampas'].get())
                    tab.permissoes['compactar_psds'].set(True)
                    tab.create()
                    identity = autenticar_usuario(database, 'operadora', 'senha-operadora')
                    self.assertIn('compactar_psds', identity['permissoes'])
                    tab.table.selection_set(str(identity['id']))
                    tab.select_user()
                    with patch('meury_app.user_management_tab.messagebox.askyesno', return_value=False):
                        tab.delete()
                    self.assertEqual(len(tab.records), 2)
                    with patch('meury_app.user_management_tab.messagebox.askyesno', return_value=True):
                        tab.delete()
                    self.assertEqual(len(tab.records), 1)
                    root.update_idletasks()
            finally:
                root.destroy()
