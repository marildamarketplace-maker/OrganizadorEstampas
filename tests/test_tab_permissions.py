import unittest
from unittest.mock import Mock, patch

from meury_app.ui import App
from meury_app.user_store import PERMISSOES_ABAS, pode_acessar_aba


class TabPermissionsTests(unittest.TestCase):
    def test_each_permission_grants_only_its_tab(self):
        for key, _ in PERMISSOES_ABAS:
            identity = {'permissoes': [key]}
            for other, _ in PERMISSOES_ABAS:
                self.assertEqual(pode_acessar_aba(identity, other), key == other)
            self.assertFalse(pode_acessar_aba(identity, 'usuarios'))

    def test_admin_all_and_default_deny(self):
        for key, _ in PERMISSOES_ABAS:
            self.assertTrue(pode_acessar_aba({'permissoes': ['admin']}, key))
            for identity in (None, {}, {'permissoes': []}, {'permissoes': ['inventada']}, {'permissoes': 'admin'}):
                self.assertFalse(pode_acessar_aba(identity, key))
        self.assertTrue(pode_acessar_aba({'permissoes': ['admin']}, 'usuarios'))
        self.assertFalse(pode_acessar_aba({'permissoes': ['usuarios']}, 'usuarios'))
        self.assertFalse(pode_acessar_aba({'permissoes': ['admin']}, 'inventada'))

    def test_notebook_visibility_and_feature_initialization(self):
        cases = [(None, []), ({'permissoes': []}, [])]
        cases += [({'permissoes': [key]}, [label]) for key, label in PERMISSOES_ABAS]
        cases += [({'id': 1, 'permissoes': ['admin']}, [label for _, label in PERMISSOES_ABAS] + ['Configurações', 'Usuários'])]
        for identity, expected in cases:
            with self.subTest(identity=identity):
                app = App.__new__(App)
                app.usuario_atual = identity
                app.root = Mock()
                app.app_data_dir_var = Mock()
                app._scrollable_page = Mock(side_effect=lambda parent, **kwargs: (Mock(), Mock(), Mock()))
                app._build_organizer_tab = Mock()
                app._build_collector_tab = Mock()
                app._build_search_tab = Mock()
                app._source_paths_row = Mock()
                with patch('meury_app.ui.ttk.Frame'), patch('meury_app.ui.ttk.Entry'), patch('meury_app.ui.ttk.Button'), patch('meury_app.ui.ttk.Label') as label, \
                     patch('meury_app.ui.ttk.Notebook') as notebook, \
                     patch('meury_app.ui.CompressionTab') as compression, \
                     patch('meury_app.ui.ManualSeparationTab') as separation, \
                     patch('meury_app.ui.ExistenceCheckerTab') as existence, \
                     patch('meury_app.ui.OrderBatchTab') as batch, \
                     patch('meury_app.ui.UserManagementTab'):
                    notebook.return_value.tabs.return_value = expected
                    app._build_ui()
                    actual = [call.kwargs['text'] for call in notebook.return_value.add.call_args_list]
                    self.assertEqual(actual, expected)
                    self.assertEqual(compression.called, 'Compactação de PSDs' in expected)
                    self.assertEqual(separation.called, 'Separar estampas' in expected)
                    self.assertEqual(batch.called, 'Separar pedidos' in expected)
                    self.assertEqual(existence.called, 'Verificador de existência' in expected)
                    if not expected:
                        label.assert_called_once()
                        notebook.return_value.pack_forget.assert_called_once()
