import unittest
from unittest.mock import Mock, patch

from meury_app.order_batch_tab import OrderBatchTab
from meury_app.manual_separation_tab import ManualSeparationTab


class OrderPathPermissionsTests(unittest.TestCase):
    def test_each_tab_saves_only_its_own_output(self):
        config = {'output_dir': 'mapear', 'batch_output_dir': 'pdf',
                  'manual_output_dir': 'manual', 'collector_output_dir': 'coletar'}
        batch = self.make_tab(True)
        batch.output_folder = Mock()
        batch.output_folder.get.return_value = 'novo-pdf'
        with patch('meury_app.order_batch_tab.load_config', return_value=dict(config)), \
             patch('meury_app.order_batch_tab.save_config') as save:
            batch.save_output()
        self.assertEqual(save.call_args.args[0], {**config, 'batch_output_dir': 'novo-pdf'})
        manual = ManualSeparationTab.__new__(ManualSeparationTab)
        manual.can_edit_paths = True
        manual.output = Mock()
        manual.output.get.return_value = 'novo-manual'
        with patch('meury_app.manual_separation_tab.load_config', return_value=dict(config)), \
             patch('meury_app.manual_separation_tab.save_config') as save:
            manual.save_output()
        self.assertEqual(save.call_args.args[0], {**config, 'manual_output_dir': 'novo-manual'})

    def make_tab(self, admin):
        tab = OrderBatchTab.__new__(OrderBatchTab)
        tab.can_edit_paths = admin
        for name in ('path_entry', 'temporary_entry', 'choose_button', 'temporary_button', 'output_entry', 'output_button'):
            setattr(tab, name, Mock())
        return tab

    def test_operator_cannot_open_chooser_or_save_paths(self):
        tab = self.make_tab(False)
        with patch('meury_app.order_batch_tab.filedialog.askdirectory') as chooser, \
             patch('meury_app.order_batch_tab.save_config') as save:
            tab.choose()
            tab.choose_temporary()
            tab.choose_output()
            tab.save_output()
        chooser.assert_not_called()
        save.assert_not_called()

    def test_permissions_are_preserved_after_processing(self):
        for admin in (True, False):
            tab = self.make_tab(admin)
            tab.set_path_states(busy=True)
            tab.set_path_states()
            for entry in (tab.path_entry, tab.temporary_entry):
                entry.configure.assert_called_with(state='normal' if admin else 'readonly')
            for button in (tab.choose_button, tab.temporary_button):
                button.configure.assert_called_with(state='normal' if admin else 'disabled')
