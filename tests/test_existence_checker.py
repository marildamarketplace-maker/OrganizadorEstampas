import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from meury_app.existence_checker import check_existence
from meury_app.existence_checker_tab import ExistenceCheckerTab


class ExistenceCheckerTests(unittest.TestCase):
    def test_optional_variant_matches_only_bare_or_a(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('100.jpg', '200-A.png', '300-B.jpg', '400-AB.jpg'):
                (root / name).touch()
            matches, _, _, _ = check_existence(root, '100;200;300;400;200-A;100;500')
            self.assertEqual(len(matches), 6)
            for code in ('100', '200', '200-A'):
                self.assertEqual(len(matches[code]), 1)
            for code in ('300', '400', '500'):
                self.assertEqual(matches[code], [])

    def test_results_hide_found_and_preserve_incomplete_warning(self):
        import queue
        tab = Mock()
        tab.events = queue.Queue()
        tab.events.put(('done', ({'100': ['100.jpg'], '200': []}, [], 1, False)))
        ExistenceCheckerTab.poll_events(tab)
        tab.write_log.assert_called_once_with('200: NÃO ENCONTRADO')
        tab.write_log.reset_mock()
        tab.events.put(('done', ({'100': ['100.jpg'], '200': []}, [], 1, True)))
        ExistenceCheckerTab.poll_events(tab)
        tab.write_log.assert_called_once_with('200: INCONCLUSIVO — busca incompleta')

    def test_recursive_matches_duplicates_and_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / 'categoria' / 'subpasta'
            nested.mkdir(parents=True)
            for name in ('6162.JPG', '6162-B.png', '6162-BC.jpg', '16162-B.jpg', '6162-B.txt'):
                (root / name).touch()
            for name in ('6162-b 45x45.TIFF', 'MV23069-A.preview.png'):
                (nested / name).touch()
            matches, errors, scanned, cancelled = check_existence(root, '6162-A;6162-B;6162-C;MV23069-A')
            self.assertEqual(len(matches['6162-A']), 1)
            self.assertEqual(len(matches['6162-B']), 2)
            self.assertEqual(matches['6162-C'], [])
            self.assertEqual(len(matches['MV23069-A']), 1)
            self.assertEqual(scanned, 6)
            self.assertFalse(errors)
            self.assertFalse(cancelled)

    def test_cancel_and_unreadable_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            cancel = threading.Event()
            cancel.set()
            self.assertTrue(check_existence(tmp, '123-A', cancel=cancel)[3])

            def walk(root, onerror, followlinks):
                onerror(PermissionError('Pasta sem acesso'))
                return iter(())

            with patch('meury_app.existence_checker.os.walk', side_effect=walk):
                result = check_existence(tmp, '123-A')
            self.assertIn('Pasta sem acesso', result[1][0])

    def test_invalid_inputs(self):
        for folder, text in (('', '123-A'), ('.', ''), ('pasta-inexistente-123456', '123-A')):
            with self.assertRaises(ValueError):
                check_existence(folder, text)
