from pathlib import Path
import tempfile
import unittest

from meury_app.batch_reports import list_reports, read_report, MAX_PREVIEW_BYTES


class BatchReportsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.reports = self.project / 'pedidos_pdf/relatorios'

    def report(self, run, name, text):
        path = self.reports / run / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def test_latest_run_and_summary_first(self):
        older = self.report('20261028_100000', 'resumo.csv', 'resultado\nSUCESSO')
        log = self.report('20261029_100000', 'pedido.log', 'processando')
        summary = self.report('20261029_100000', 'LOG_FINAL_ACAO_HUMANA.txt', 'Ação: revisar estampa')
        self.report('20261029_100000', 'outro.exe', 'não listar')
        self.assertEqual(list_reports(self.project), [p.resolve() for p in (summary, log, older)])
        self.assertEqual(read_report(self.project, summary), 'Ação: revisar estampa')

    def test_empty_deleted_and_large_file(self):
        self.assertEqual(list_reports(self.project), [])
        large = self.report('20261029', 'lote.txt', 'x' * (MAX_PREVIEW_BYTES + 50))
        self.assertIn('Prévia limitada', read_report(self.project, large))
        large.unlink()
        with self.assertRaises(ValueError):
            read_report(self.project, large)

    def test_outside_paths_and_symlinks_are_not_read(self):
        outside = self.project / 'outside.txt'
        outside.write_text('private')
        self.reports.mkdir(parents=True)
        (self.reports / 'linked_run').symlink_to(self.project, target_is_directory=True)
        run = self.reports / '20261029'
        run.mkdir()
        link = run / 'linked.txt'
        link.symlink_to(outside)
        self.assertEqual(list_reports(self.project), [])
        for path in (outside, link):
            with self.assertRaises(ValueError):
                read_report(self.project, path)
