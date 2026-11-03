import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from scripts.configure_release_version import configure
from scripts.publish_release_draft import publish_draft


class AutomatedReleaseTests(unittest.TestCase):
    def test_run_number_embeds_version_and_preserves_module(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / '__init__.py'
            path.write_text('__version__ = "1.0.0"\n\nfrom .image_policy import configure_pillow_limits\n')
            self.assertEqual(configure(path, '42'), '1.0.42')
            self.assertIn('from .image_policy', path.read_text())
            self.assertEqual(configure(path, '42'), '1.0.42')
            self.assertEqual(configure(path, '43'), '1.0.43')

    def test_invalid_run_number_does_not_change_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / '__init__.py'
            path.write_text('__version__ = "1.0.0"\n')
            for value in ('', '0', '-1', '1;echo', '01', '../1'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    configure(path, value)
            self.assertEqual(path.read_text(), '__version__ = "1.0.0"\n')

    def test_existing_public_release_is_never_modified(self):
        runner = Mock(return_value=Mock(returncode=0, stdout=json.dumps({'isDraft': False})))
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(RuntimeError):
            publish_draft('example/releases', Path(temporary), Path('notes.md'), '1.0.1', runner)
        runner.assert_called_once()

    def test_retry_updates_draft_without_publishing(self):
        runner = Mock(return_value=Mock(returncode=0, stdout=json.dumps({'isDraft': True})))
        with tempfile.TemporaryDirectory() as temporary:
            publish_draft('example/releases', Path(temporary), Path('notes.md'), '1.0.1', runner)
        self.assertIn('upload', runner.call_args_list[1].args[0])
        self.assertIn('--clobber', runner.call_args_list[1].args[0])
        self.assertNotIn('--draft=false', runner.call_args_list[2].args[0])

    def test_new_release_starts_as_draft(self):
        runner = Mock(return_value=Mock(returncode=1, stdout=''))
        with tempfile.TemporaryDirectory() as temporary:
            publish_draft('example/releases', Path(temporary), Path('notes.md'), '1.0.1', runner)
        self.assertIn('--draft', runner.call_args_list[1].args[0])
