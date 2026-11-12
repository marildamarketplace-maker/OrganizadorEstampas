"""Update checks, downloads and installation each require an explicit action."""
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch

from meury_app.update_ui import UpdateController


class ManualUpdateTests(unittest.TestCase):
    def controller(self):
        controller = UpdateController.__new__(UpdateController)
        controller.root = Mock()
        controller.app = Mock(root=controller.root)
        controller.window = None
        controller.status = Mock()
        controller.events = queue.Queue()
        controller.running = False
        return controller

    def test_startup_schedules_only_ui_queue_polling(self):
        app = Mock()
        with patch('meury_app.update_ui.tk.Menu'), patch('meury_app.update_ui.tk.StringVar'), \
             patch('meury_app.update_ui.check') as check:
            controller = UpdateController(app)
        self.assertEqual(app.root.after.call_count, 1)
        self.assertEqual(app.root.after.call_args.args, (100, controller.poll))
        check.assert_not_called()

    def test_nonmanual_check_never_contacts_release_service(self):
        controller = self.controller()
        with patch('meury_app.update_ui.check') as check:
            controller.check()
        check.assert_not_called()

    def test_explicit_check_contacts_release_service(self):
        controller = self.controller()

        def thread(*, target, **kwargs):
            return Mock(start=target)

        with patch('meury_app.update_ui.sys.platform', 'win32'), \
             patch('meury_app.update_ui.threading.Thread', side_effect=thread), \
             patch('meury_app.update_ui.check', return_value=(b'manifest', {'version': '2'})) as check:
            controller.check(True)
        check.assert_called_once()
        self.assertEqual(controller.events.get_nowait()[0], 'release')

    def test_offer_does_not_download_or_install_without_click(self):
        controller = self.controller()
        with patch('meury_app.update_ui.tk.Toplevel'), patch('meury_app.update_ui.tk.Text'), \
             patch('meury_app.update_ui.ttk.Label'), patch('meury_app.update_ui.ttk.Button') as button, \
             patch('meury_app.update_ui.sys.frozen', True, create=True), \
             patch('meury_app.update_ui.executable', return_value=Path(__import__('sys').executable)), \
             patch('meury_app.update_ui.download') as download, \
             patch('meury_app.update_ui.os.access', return_value=True), \
             patch('meury_app.update_ui.subprocess.Popen') as install, \
             patch('meury_app.update_ui.threading.Thread') as thread:
            controller.offer(b'manifest', {'version': '2', 'notes': 'Novidades'})
        download.assert_not_called()
        install.assert_not_called()
        thread.assert_not_called()
        self.assertIn('Baixar atualização', [call.kwargs.get('text') for call in button.call_args_list])

    def test_download_requires_explicit_method_and_never_installs(self):
        controller = self.controller()
        controller.download_button = Mock()
        with tempfile.TemporaryDirectory() as temporary, \
             patch('meury_app.update_ui.state_dir', return_value=Path(temporary)), \
             patch('meury_app.update_ui.threading.Thread', side_effect=lambda *, target, **kwargs: Mock(start=target)), \
             patch('meury_app.update_ui.download') as download, \
             patch('meury_app.update_ui.subprocess.Popen') as install:
            controller.start_download(b'manifest', {'version': '2'})
        download.assert_called_once()
        install.assert_not_called()

    def test_other_computers_keep_app_and_download_open(self):
        controller = self.controller()
        controller.window = Mock()
        controller.work = Path('/download-already-validated')
        with patch('meury_app.update_ui.threading.enumerate', return_value=[]), \
             patch('meury_app.shared_sessions.active_sessions', return_value=['Maria']), \
             patch('meury_app.update_ui.messagebox.showinfo') as notice, \
             patch('meury_app.update_ui.subprocess.Popen') as install:
            controller.install()
        self.assertIn('Maria', notice.call_args.args[1])
        controller.root.destroy.assert_not_called()
        install.assert_not_called()
        self.assertEqual(controller.work, Path('/download-already-validated'))


if __name__ == '__main__':
    unittest.main()
