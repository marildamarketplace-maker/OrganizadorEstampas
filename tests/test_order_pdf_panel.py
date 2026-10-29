from pathlib import Path
import tempfile
import unittest

from meury_app.order_pdf_panel import list_pdfs


class OrderPdfTests(unittest.TestCase):
    def test_entry_and_completed_are_separate_and_only_pdfs_are_listed(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            entry = project / 'pedidos_pdf/entrada'
            completed = entry / 'concluido'
            completed.mkdir(parents=True)
            for name in ('Z.PDF', 'a.pdf', 'ignorar.txt'):
                (entry / name).write_bytes(b'teste')
            (completed / 'feito.pdf').write_bytes(b'teste')
            (entry / 'link.pdf').symlink_to(completed / 'feito.pdf')
            self.assertEqual([p.name for p in list_pdfs(project, 'entrada')], ['a.pdf', 'Z.PDF'])
            self.assertEqual([p.name for p in list_pdfs(project, 'entrada/concluido')], ['feito.pdf'])
            self.assertTrue((entry / 'a.pdf').exists())
            with self.assertRaises(ValueError):
                list_pdfs(project, '../fora')

    def test_missing_folders_are_empty_and_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self.assertEqual(list_pdfs(project, 'entrada'), [])
            self.assertFalse((project / 'pedidos_pdf').exists())
