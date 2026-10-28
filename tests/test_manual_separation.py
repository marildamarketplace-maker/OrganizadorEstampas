from pathlib import Path
import tempfile
import unittest

from meury_app.indexer import ImageIndex, image_key
from meury_app.manual_separation import folder_component, parse_designs, separate_designs


class ManualSeparationTests(unittest.TestCase):
    def test_parse_and_validate(self):
        self.assertEqual(parse_designs('6162-a; MV23069-B;6162-A;'), [('6162', 'A'), ('MV23069', 'B')])
        for value in ('', '6162', '../6162-A', '6162-A; inválida'):
            with self.assertRaises(ValueError):
                parse_designs(value)
        for value in ('', '..', '../cliente', 'a/b', 'a\\b', 'CON', 'a\x00b'):
            with self.assertRaises(ValueError):
                folder_component(value)

    def test_structure_missing_duplicates_and_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source = root / 'source'
            source.mkdir()
            image = source / '6162.jpg'
            image.write_bytes(b'original')
            index = ImageIndex({image_key('6162', '6162'): [str(image)],
                                image_key('7000', '7000-B'): ['a.jpg', 'b.jpg']}, source_dirs=[source])
            destination, results = separate_designs('Cliente Um', 'Categoria livre', '6162-A;7000-B;8000-C', root / 'out', index)
            self.assertEqual(destination, root / 'out' / 'Cliente Um' / 'Categoria livre')
            self.assertEqual((destination / '6162.JPG').read_bytes(), b'original')
            self.assertIn('COPIADO', results[0])
            self.assertIn('DUPLICADO', results[1])
            self.assertIn('NÃO ENCONTRADO', results[2])
            image.write_bytes(b'alterado')
            _, repeated = separate_designs('Cliente Um', 'Categoria livre', '6162-A', root / 'out', index)
            self.assertIn('JÁ EXISTE', repeated[0])
            self.assertEqual((destination / '6162.JPG').read_bytes(), b'original')

    def test_catalog_fallback_and_stale_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            image = root / 'MV23069 47x94.jpg'
            image.write_bytes(b'original')
            index = {image_key('OTHER', image.stem): [str(image)],
                     image_key('6162', '6162-B'): [str(root / 'missing.jpg')]}
            _, results = separate_designs('Cliente', 'Categoria', 'MV23069-A;6162-B', root / 'out', index)
            self.assertIn('COPIADO', results[0])
            self.assertIn('FALHA', results[1])

    def test_symlink_destination_and_source_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            output = root / 'out'
            output.mkdir()
            outside = root / 'outside'
            outside.mkdir()
            try:
                (output / 'Cliente').symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f'Symlink indisponível: {exc}')
            with self.assertRaises(ValueError):
                separate_designs('Cliente', 'Categoria', '6162-A', output, {})
            source = root / 'source'
            source.mkdir()
            image = outside / '6162-A.jpg'
            image.write_bytes(b'original')
            link = source / image.name
            link.symlink_to(image)
            index = ImageIndex({image_key('6162', '6162-A'): [str(link)]}, source_dirs=[source])
            with self.assertRaises(ValueError):
                separate_designs('Outro', 'Categoria', '6162-A', output, index)

    def test_portable_names_and_unicode(self):
        self.assertEqual(folder_component('João & Cia'), 'João & Cia')
        self.assertEqual(folder_component('Cafe\u0301'), 'Café')
        for name in ('CON.txt', 'COM¹', 'LPT².pdf', 'Categoria.', 'a:b', 'a?b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                folder_component(name)

    def test_atomic_copy_preserves_concurrent_destination_and_cleans_temp(self):
        from meury_app.manual_separation import copy_without_overwrite
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, target = root / 'source.jpg', root / 'target.jpg'
            source.write_bytes(b'new')
            target.write_bytes(b'preserve')
            with self.assertRaises(FileExistsError):
                copy_without_overwrite(source, target)
            self.assertEqual(target.read_bytes(), b'preserve')
            self.assertEqual(list(root.glob('.separar-*')), [])

    def test_cancel_prevents_copy(self):
        import threading
        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryDirectory() as tmp:
            _, results = separate_designs('Cliente', 'Categoria', '6162-A', tmp, {}, cancel=cancel)
            self.assertIn('CANCELADO', results[0])

    def test_open_folder_error_is_handled(self):
        from unittest.mock import Mock, patch
        from meury_app.manual_separation_tab import ManualSeparationTab
        tab = ManualSeparationTab.__new__(ManualSeparationTab)
        tab.destination = Path('missing')
        tab.app = Mock()
        with patch('meury_app.manual_separation_tab.open_existing_location', side_effect=FileNotFoundError('ausente')), \
             patch('meury_app.manual_separation_tab.messagebox.showerror') as error:
            tab.open_destination()
        error.assert_called_once()

    def test_worker_events_are_consumed_by_ui(self):
        import queue
        from unittest.mock import Mock
        from meury_app.manual_separation_tab import ManualSeparationTab
        tab = ManualSeparationTab.__new__(ManualSeparationTab)
        tab.events = queue.Queue()
        tab.status = Mock()
        tab.finish = Mock()
        tab.app = Mock()
        tab.events.put(('progress', 'Buscando...'))
        tab.events.put(('done', Path('saida'), ['6162-A: COPIADO'], None))
        tab.poll_events()
        tab.status.set.assert_called_once_with('Buscando...')
        tab.finish.assert_called_once_with(Path('saida'), ['6162-A: COPIADO'], None)
        tab.app.root.after.assert_not_called()
