from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from meury_app.user_store import criar_usuario, cadastrar_usuario, autenticar_usuario
from meury_app.ui import App


class UserManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'users.db'
        self.admin = criar_usuario(self.path, 'admin', 'senha-admin', ['admin'])
        self.regular = criar_usuario(self.path, 'normal', 'senha-normal', [])

    def test_admin_creates_user_with_selected_permissions(self):
        cadastrar_usuario(self.path, self.admin, 'operador', 'senha-operador', ['separar_estampas'])
        identity = autenticar_usuario(self.path, 'operador', 'senha-operador')
        self.assertEqual(identity['permissoes'], ['separar_estampas'])
        cadastrar_usuario(self.path, self.admin, 'novo-admin', 'senha-admin2', ['admin'])
        self.assertEqual(autenticar_usuario(self.path, 'novo-admin', 'senha-admin2')['permissoes'], ['admin'])

    def test_non_admin_missing_session_and_revoked_admin_cannot_create(self):
        for actor in (self.regular, 99999, None):
            with self.assertRaises(PermissionError):
                cadastrar_usuario(self.path, actor, 'intruso', 'senha-intruso', ['admin'])
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE usuarios SET permissoes='[]' WHERE id=?", (self.admin,))
        with self.assertRaises(PermissionError):
            cadastrar_usuario(self.path, self.admin, 'intruso', 'senha-intruso', ['admin'])
        self.assertIsNone(autenticar_usuario(self.path, 'intruso', 'senha-intruso'))

    def test_invalid_password_permission_and_duplicate(self):
        with self.assertRaises(ValueError):
            cadastrar_usuario(self.path, self.admin, 'teste', 'curta', [])
        with self.assertRaises(ValueError):
            cadastrar_usuario(self.path, self.admin, 'teste', 'senha-teste', ['inventada'])
        with self.assertRaises(sqlite3.IntegrityError):
            cadastrar_usuario(self.path, self.admin, 'NORMAL', 'senha-teste', ['admin'])


class TabVisibilityTests(unittest.TestCase):
    def test_users_tab_only_created_for_admin(self):
        for identity, expected in ((None, False), ({'permissoes': []}, False),
                                   ({'id': 1, 'permissoes': ['admin']}, True)):
            with self.subTest(identity=identity):
                application = App.__new__(App)
                application.usuario_atual = identity
                notebook = Mock()
                with patch('meury_app.ui.ttk.Frame') as frame, patch('meury_app.ui.UserManagementTab') as tab:
                    application._build_users_tab(notebook)
                    self.assertEqual(tab.called, expected)
                    if expected:
                        tab.assert_called_once_with(frame.return_value, identity)
                        notebook.add.assert_called_once_with(frame.return_value, text='Usuários')
                    else:
                        frame.assert_not_called()
                        notebook.add.assert_not_called()

class UserLifecycleTests(unittest.TestCase):
    setUp = UserManagementTests.setUp
    def test_list_edit_password_and_delete(self):
        from meury_app.user_store import listar_usuarios, atualizar_usuario, excluir_usuario
        users = listar_usuarios(self.path, self.admin)
        self.assertEqual(len(users), 2)
        self.assertTrue(all(set(user) == {'id', 'usuario', 'permissoes', 'criado_em'} for user in users))
        atualizar_usuario(self.path, self.admin, self.regular, 'brenda', ['compactar_psds'])
        self.assertEqual(autenticar_usuario(self.path, 'brenda', 'senha-normal')['permissoes'], ['compactar_psds'])
        atualizar_usuario(self.path, self.admin, self.regular, 'brenda', ['separar_estampas'], 'nova-senha')
        self.assertIsNone(autenticar_usuario(self.path, 'brenda', 'senha-normal'))
        self.assertIsNotNone(autenticar_usuario(self.path, 'brenda', 'nova-senha'))
        excluir_usuario(self.path, self.admin, self.regular)
        self.assertIsNone(autenticar_usuario(self.path, 'brenda', 'nova-senha'))

    def test_authorization_and_self_protection(self):
        from meury_app.user_store import listar_usuarios, atualizar_usuario, excluir_usuario
        for actor in (self.regular, None, 99999):
            with self.assertRaises(PermissionError):
                listar_usuarios(self.path, actor)
            with self.assertRaises(PermissionError):
                atualizar_usuario(self.path, actor, self.regular, 'normal', ['admin'])
            with self.assertRaises(PermissionError):
                excluir_usuario(self.path, actor, self.admin)
        with self.assertRaises(ValueError):
            excluir_usuario(self.path, self.admin, self.admin)
        with self.assertRaises(ValueError):
            atualizar_usuario(self.path, self.admin, self.admin, 'admin', [])
        self.assertIsNotNone(autenticar_usuario(self.path, 'admin', 'senha-admin'))

    def test_failed_edit_preserves_original(self):
        from meury_app.user_store import atualizar_usuario, excluir_usuario
        for name, permissions, password, exception in (
            ('admin', [], 'nova-senha', sqlite3.IntegrityError),
            ('normal', ['invalida'], None, ValueError),
            ('normal', [], 'curta', ValueError),
        ):
            with self.assertRaises(exception):
                atualizar_usuario(self.path, self.admin, self.regular, name, permissions, password)
            self.assertIsNotNone(autenticar_usuario(self.path, 'normal', 'senha-normal'))
        with self.assertRaises(ValueError):
            excluir_usuario(self.path, self.admin, 99999)
