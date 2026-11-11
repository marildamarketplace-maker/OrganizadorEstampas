import unittest
from unittest.mock import patch

from meury_app.batch_output import output_sink
from meury_app.batch_order_processor import terminal_text
from meury_app.log_colors import LogColorParser


class LogColorsTests(unittest.TestCase):
    def test_colors_bold_and_reset(self):
        self.assertEqual(LogColorParser().feed('A\x1b[1;32mOK\x1b[0m B'), [
            ('A', ()), ('OK', ('ansi_32', 'bold')), (' B', ())])

    def test_partial_sequence_and_persistent_style(self):
        parser = LogColorParser()
        self.assertEqual(parser.feed('\x1b[3'), [])
        self.assertEqual(parser.feed('3mAlerta'), [('Alerta', ('ansi_33',))])
        self.assertEqual(parser.feed('!\x1b[39m fim'), [('!', ('ansi_33',)), (' fim', ())])

    def test_other_sgr_codes_do_not_leak_into_text(self):
        self.assertEqual(LogColorParser().feed('\x1b[999mtexto\x1b[m'), [('texto', ())])

    def test_gui_colors_do_not_change_terminal_global_state(self):
        with patch('meury_app.batch_order_processor._TERMINAL_COLORS', False):
            self.assertEqual(terminal_text('OK', color='green'), 'OK')
            token = output_sink.set(lambda text: None)
            try:
                self.assertEqual(terminal_text('OK', color='green'), '\x1b[32mOK\x1b[0m')
            finally:
                output_sink.reset(token)
            self.assertEqual(terminal_text('OK', color='green'), 'OK')

    def test_panel_inserts_clean_text_with_tags_and_preserves_scroll_option(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from meury_app.order_batch_tab import OrderBatchTab
        log = Mock()
        panel = SimpleNamespace(log=log, log_parser=LogColorParser(), follow_logs=Mock())
        panel.follow_logs.get.return_value = False
        OrderBatchTab.append_log(panel, '\x1b[31mFalha\x1b[0m\n')
        self.assertEqual([call.args for call in log.insert.call_args_list], [
            ('end', 'Falha', ('ansi_31',)), ('end', '\n', ())])
        log.see.assert_not_called()
        log.configure.assert_called_with(state='disabled')
