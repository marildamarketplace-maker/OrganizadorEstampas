"""Regressões da verificação de esquema antes do login, inclusive UNC Windows."""
from contextlib import closing
from pathlib import Path, PureWindowsPath
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from meury_app.login import _readonly_database_uri, solicitar_login
from meury_app.operational_store import SCHEMA_VERSION


class LoginDatabaseUriTests(unittest.TestCase):
    def test_unc_has_empty_authority_and_preserves_escaped_filename(self):
        path = Mock()
        path.resolve.return_value = PureWindowsPath(
            r'\\MYCLOUDEX2ULTRA\Meury Shop\dados #1\usuários.db')
        uri = _readonly_database_uri(path)
        self.assertEqual(uri,
            'file:////MYCLOUDEX2ULTRA/Meury%20Shop/dados%20%231/usu%C3%A1rios.db?mode=ro')
        # Esta é a URI entregue ao SQLite na abertura do programa.
        path.exists.return_value = True
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = (SCHEMA_VERSION + 1,)
        with patch('meury_app.login.sqlite3.connect', return_value=connection) as connect:
            with self.assertRaisesRegex(RuntimeError, 'mais recente'):
                solicitar_login(path)
        connect.assert_called_once_with(uri, uri=True)
        connection.close.assert_called_once()

    def test_windows_local_drive_is_preserved(self):
        path = Mock()
        path.resolve.return_value = PureWindowsPath(r'C:\Users\Maria\MV Image\state.db')
        self.assertEqual(_readonly_database_uri(path),
                         'file:///C:/Users/Maria/MV%20Image/state.db?mode=ro')

    def test_real_database_is_readable_but_cannot_be_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'usuários #1.db'
            with closing(sqlite3.connect(path)) as connection:
                connection.execute('PRAGMA user_version=1')
            with closing(sqlite3.connect(_readonly_database_uri(path), uri=True)) as connection:
                self.assertEqual(connection.execute('PRAGMA user_version').fetchone(), (1,))
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute('CREATE TABLE forbidden (id INTEGER)')

    def test_missing_database_is_not_created_by_readonly_check(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.db'
            with self.assertRaises(sqlite3.OperationalError):
                sqlite3.connect(_readonly_database_uri(path), uri=True)
            self.assertFalse(path.exists())
