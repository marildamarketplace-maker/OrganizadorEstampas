from contextlib import closing
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from meury_app.operational_store import ensure_schema
from meury_app.user_store import criar_usuario, verificar_senha


class UserStoreTests(unittest.TestCase):
    def test_schema_migration_is_idempotent(self):
        with closing(sqlite3.connect(':memory:')) as connection:
            ensure_schema(connection)
            ensure_schema(connection)
            self.assertEqual(connection.execute('SELECT count(*) FROM usuarios').fetchone()[0], 0)
            for invalid in ('{}', 'invalid', '[1]', '[""]', 'null'):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute('INSERT INTO usuarios(usuario, senha_hash, permissoes) VALUES (?, ?, ?)', ('test', 'hash', invalid))

    def test_create_hash_permissions_and_unique_username(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.db'
            criar_usuario(path, 'Maria', 'senha-segura', ['separar', 'separar'])
            criar_usuario(path, 'Outro', 'senha-segura')
            with closing(sqlite3.connect(path)) as connection:
                rows = connection.execute('SELECT senha_hash, permissoes FROM usuarios ORDER BY id').fetchall()
            self.assertNotEqual(rows[0][0], rows[1][0])
            self.assertNotIn('senha-segura', rows[0][0])
            self.assertEqual(json.loads(rows[0][1]), ['separar'])
            self.assertEqual(json.loads(rows[1][1]), [])
            self.assertTrue(verificar_senha('senha-segura', rows[0][0]))
            self.assertFalse(verificar_senha('errada', rows[0][0]))
            self.assertFalse(verificar_senha('senha-segura', 'invalid'))
            with self.assertRaises(sqlite3.IntegrityError):
                criar_usuario(path, 'maria', 'senha-segura')
            for permissions in ('admin', [1], ['']):
                with self.assertRaises(ValueError):
                    criar_usuario(path, 'teste', 'senha-segura', permissions)
            with self.assertRaises(ValueError):
                criar_usuario(path, '', 'senha-segura')

    def test_admin_has_all_permissions(self):
        from meury_app.user_store import tem_permissao
        self.assertTrue(tem_permissao(['admin'], 'separar_estampas'))
        self.assertTrue(tem_permissao(['admin'], 'nova_operacao'))
        self.assertTrue(tem_permissao(['separar_estampas'], 'separar_estampas'))
        self.assertFalse(tem_permissao(['separar_estampas'], 'compactar_psds'))
        self.assertFalse(tem_permissao([], 'admin'))
