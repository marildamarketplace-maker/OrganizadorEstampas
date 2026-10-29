from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from meury_app.user_store import criar_usuario, autenticar_usuario


class AuthenticationTests(unittest.TestCase):
    def test_authentication_and_no_hash_exposure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.db'
            criar_usuario(path, 'Maria', 'senha-segura', ['admin'])
            result = autenticar_usuario(path, ' maria ', 'senha-segura')
            self.assertEqual(result, {'id': 1, 'usuario': 'Maria', 'permissoes': ['admin']})
            self.assertIsNone(autenticar_usuario(path, 'Maria', 'incorreta'))
            self.assertIsNone(autenticar_usuario(path, "' OR 1=1 --", 'senha-segura'))
            self.assertIsNone(autenticar_usuario(path, '', 'senha-segura'))
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("UPDATE usuarios SET senha_hash='invalid'")
            self.assertIsNone(autenticar_usuario(path, 'Maria', 'senha-segura'))

    def test_database_error_does_not_authenticate(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(sqlite3.OperationalError):
                autenticar_usuario(Path(directory), 'admin', 'admin')


class StartupTests(unittest.TestCase):
    def test_cancel_does_not_create_tabs(self):
        import app
        with patch.object(app, 'apply_app_icon'), patch.object(app.tk, 'Tk'), patch.object(app, 'solicitar_login', return_value=None), patch.object(app, 'App') as application:
            app.main()
        application.assert_not_called()

    def test_authenticated_startup_passes_identity(self):
        import app
        identity = {'id': 1, 'usuario': 'admin', 'permissoes': ['admin']}
        with patch.object(app, 'apply_app_icon'), patch.object(app.tk, 'Tk') as root, patch.object(app, 'solicitar_login', return_value=identity) as login, patch.object(app, 'App') as application:
            app.main()
        login.assert_called_once_with(app.OPERATIONAL_DB_FILE, root=root.return_value)
        application.assert_called_once_with(usuario_atual=identity, root=root.return_value)
        application.return_value.run.assert_called_once()
