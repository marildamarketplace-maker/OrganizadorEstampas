"""Native regression for login/application sharing one Tcl interpreter."""
import gc
import os
from pathlib import Path
import threading
import tkinter as tk
import unittest
from unittest.mock import patch

from meury_app.login import solicitar_login


@unittest.skipUnless(os.environ.get('MEURY_TEST_TK') == '1', 'Requer sessão gráfica Tk')
class LoginTkTests(unittest.TestCase):
    def test_login_reuses_root_and_releases_callbacks(self):
        root = tk.Tk()
        root.withdraw()
        from meury_app.app_icon import apply_app_icon
        apply_app_icon(root)
        self.addCleanup(root.destroy)
        identity = {'id': 1, 'usuario': 'teste', 'permissoes': ['mapear_estampas']}

        def submit():
            frame = root.winfo_children()[0]
            entries = [child for child in frame.winfo_children() if child.winfo_class() == 'TEntry']
            entries[0].insert(0, 'teste')
            entries[1].insert(0, 'senha-teste')
            for child in frame.winfo_children():
                if child.winfo_class() == 'TButton' and child.cget('text') == 'Entrar':
                    child.invoke()
        root.after(20, submit)
        root.after(3000, root.quit)
        with patch('meury_app.login.autenticar_usuario', return_value=identity):
            self.assertEqual(solicitar_login(Path('/unused'), root=root), identity)
        gc.collect()
        self.assertTrue(root.winfo_exists())
        self.assertEqual(root.winfo_children(), [])
        self.assertEqual(root.tk.call('info', 'vars', 'PY_VAR*'), '')
        worker = threading.Thread(target=gc.collect)
        worker.start()
        worker.join()
        # Build the real application on that same interpreter.
        from meury_app.ui import App
        from meury_app.config import DEFAULT_CONFIG
        with patch('meury_app.ui.load_config', return_value=dict(DEFAULT_CONFIG)), \
             patch.object(App, '_try_load_saved_index'), \
             patch.object(App, '_refresh_semantic_status'), \
             patch.object(App, '_refresh_visual_status'), \
             patch.object(App, '_refresh_catalog_statistics'):
            application = App(usuario_atual=identity, root=root)
        self.assertIs(application.root, root)
        root.after(50, root.quit)
        application.run()

    def test_cancel_destroys_root(self):
        root = tk.Tk()
        root.withdraw()
        def cancel():
            for child in root.winfo_children()[0].winfo_children():
                if child.winfo_class() == 'TButton' and child.cget('text') == 'Cancelar':
                    child.invoke()
        root.after(20, cancel)
        self.assertIsNone(solicitar_login(Path('/unused'), root=root))
        gc.collect()
        with self.assertRaises(tk.TclError):
            root.winfo_exists()
