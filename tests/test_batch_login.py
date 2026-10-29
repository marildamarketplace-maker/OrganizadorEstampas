from contextlib import redirect_stdout
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import getpass
import sqlite3

from meury_app import batch_order_processor as batch


class BatchLoginTests(unittest.TestCase):
    def test_valid_login_uses_configured_database_and_hidden_password(self):
        identity = {'id': 1, 'usuario': 'admin', 'permissoes': ['admin']}
        with patch.object(batch, 'configured_app_dir', return_value=Path('/dados')), \
             patch('builtins.input', return_value=' admin '), \
             patch.object(batch.getpass, 'getpass', return_value='admin') as password, \
             patch.object(batch, 'autenticar_usuario', return_value=identity) as authenticate, \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(batch.solicitar_login_lote(), identity)
        authenticate.assert_called_once_with(Path('/dados/estado_indexador.sqlite3'), 'admin', 'admin')
        password.assert_called_once()
        self.assertNotIn('admin', output.getvalue())

    def test_three_failures_stop(self):
        with patch('builtins.input', return_value='user'), \
             patch.object(batch.getpass, 'getpass', return_value='wrong'), \
             patch.object(batch, 'autenticar_usuario', return_value=None) as authenticate, \
             patch.object(batch.time, 'sleep'), redirect_stdout(io.StringIO()):
            self.assertIsNone(batch.solicitar_login_lote())
        self.assertEqual(authenticate.call_count, 3)

    def test_cancellation_database_failure_and_insecure_console_fail_closed(self):
        for error in (EOFError(), KeyboardInterrupt(), sqlite3.OperationalError(),
                      getpass.GetPassWarning('insecure')):
            with self.subTest(error=type(error)), patch('builtins.input', return_value='user'), \
                 patch.object(batch.getpass, 'getpass', side_effect=error), \
                 patch.object(batch, 'autenticar_usuario') as authenticate, \
                 redirect_stdout(io.StringIO()):
                self.assertIsNone(batch.solicitar_login_lote())
                authenticate.assert_not_called()

    def test_failed_login_never_asks_to_sync_even_with_index_flags(self):
        for flag in (None, True, False):
            with self.subTest(flag=flag), \
                 patch.object(batch, 'parse_args', return_value=SimpleNamespace(projeto='.', codex='', atualizar_indice=flag)), \
                 patch.object(batch, 'load_local_environment'), \
                 patch.object(batch, 'configure_terminal_style'), \
                 patch.object(batch, 'print_terminal_header'), \
                 patch.object(batch, 'solicitar_login_lote', return_value=None), \
                 patch.object(batch, 'choose_index_update') as choose, \
                 patch.object(batch, 'load_order_context') as context, \
                 patch.object(batch, 'ensure_daily_index') as sync:
                self.assertEqual(batch.main(), 1)
                choose.assert_not_called()
                context.assert_not_called()
                sync.assert_not_called()

    def test_login_precedes_index_prompt(self):
        calls = []
        with patch.object(batch, 'parse_args', return_value=SimpleNamespace(projeto='.', codex='', atualizar_indice=None)), \
             patch.object(batch, 'load_local_environment'), \
             patch.object(batch, 'configure_terminal_style'), \
             patch.object(batch, 'print_terminal_header'), \
             patch.object(batch, 'solicitar_login_lote', side_effect=lambda: calls.append('login') or {'id': 1}), \
             patch.object(batch, 'choose_index_update', side_effect=lambda value: calls.append('indice') or (_ for _ in ()).throw(ValueError('fim do teste'))), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(batch.main(), 1)
        self.assertEqual(calls, ['login', 'indice'])
