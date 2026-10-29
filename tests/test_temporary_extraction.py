from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from meury_app.batch_order_processor import extract_in_temporary_folder


class TemporaryExtractionTests(unittest.TestCase):
    def test_pdf_is_local_and_copy_is_removed_on_success_or_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / 'original.pdf'
                source.write_bytes(b'%PDF-test')
                staging = root / 'temporarios'
                staging.mkdir()
                schema = root / 'batch_order_extraction.schema.json'
                schema.write_text('{}', encoding='utf-8')

                def extract(**kwargs):
                    self.assertEqual(kwargs['schema_path'], schema)
                    self.assertEqual(kwargs['pdf_path'].read_bytes(), source.read_bytes())
                    self.assertEqual(kwargs['pdf_path'].parent, kwargs['project'])
                    self.assertEqual(kwargs['project'].parent, staging)
                    if fail:
                        raise RuntimeError('falha de leitura')
                    return ({'pedido': '123'}, 'codex', '')

                with patch('meury_app.batch_order_processor.extract_with_fallback', side_effect=extract):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, 'falha de leitura'):
                            extract_in_temporary_folder(temporary_folder=staging, pdf_path=source, schema_path=schema)
                    else:
                        result = extract_in_temporary_folder(temporary_folder=staging, pdf_path=source, schema_path=schema)
                        self.assertEqual(result[0]['pedido'], '123')
                self.assertEqual(list(staging.iterdir()), [])
                self.assertEqual(source.read_bytes(), b'%PDF-test')
