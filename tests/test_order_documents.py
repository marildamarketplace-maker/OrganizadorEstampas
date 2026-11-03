from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
import pypdfium2 as pdfium
from meury_app.order_documents import discover_documents, prepare_document, move_document
from meury_app import batch_order_processor as batch


class DocumentsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def image(self, name, mode='RGB', color='red'):
        path = self.root / name
        with Image.new(mode, (80, 40), color) as image:
            image.save(path)
        return path

    def test_mixed_discovery_numeric_order_and_symlinks(self):
        for i in range(10, 0, -1):
            self.image(f'200_{i}.PNG')
        self.image('201.JPEG')
        (self.root / '202.PDF').write_bytes(b'pdf')
        (self.root / 'ignored.jpg').symlink_to(self.root / '201.JPEG')
        documents = discover_documents(self.root)
        self.assertEqual(len(documents), 3)
        self.assertEqual([p.stem for p in documents[0].pages], [f'200_{i}' for i in range(1, 11)])
        self.assertFalse(documents[0].error)
        self.assertEqual(documents[-1].digest(), hashlib.sha256(b'pdf').hexdigest())

    def test_ambiguities(self):
        for names in [('a_1.jpg', 'a_1.png'), ('a_2.jpg',), ('a.jpg', 'a_1.png'), ('a.pdf', 'a.png')]:
            with self.subTest(names=names), tempfile.TemporaryDirectory() as directory:
                for name in names:
                    (Path(directory) / name).touch()
                self.assertTrue(discover_documents(Path(directory))[0].error)

    def test_staging_multipage_transparency_and_digest(self):
        self.image('200_1.png', 'RGBA', (0, 0, 0, 0))
        second = self.image('200_2.jpg')
        document = discover_documents(self.root)[0]
        original = [p.read_bytes() for p in document.pages]
        digest = document.digest()
        target = self.root / 'staged.pdf'
        prepare_document(document, target)
        with pdfium.PdfDocument(target) as pdf:
            self.assertEqual(len(pdf), 2)
            page = pdf[0]
            bitmap = page.render()
            image = bitmap.to_pil()
            self.assertEqual(image.convert('RGB').getpixel((20, 20)), (255, 255, 255))
            image.close(); bitmap.close(); page.close()
        self.assertEqual([p.read_bytes() for p in document.pages], original)
        self.image(second.name, color='blue')
        self.assertNotEqual(document.digest(), digest)

    def test_exif_orientation(self):
        path = self.root / 'a.jpg'
        with Image.new('RGB', (80, 40), 'red') as image:
            exif = image.getexif(); exif[274] = 6
            image.save(path, exif=exif)
        document = discover_documents(self.root)[0]
        prepare_document(document, self.root / 'staged.pdf')
        with pdfium.PdfDocument(self.root / 'staged.pdf') as pdf:
            page = pdf[0]
            self.assertEqual(page.get_size(), (40, 80))
            page.close()

    def test_invalid_image(self):
        (self.root / 'a.jpg').write_bytes(b'invalid')
        with self.assertRaises(ValueError):
            prepare_document(discover_documents(self.root)[0], self.root / 'stage.pdf')

    def test_move_collision_preserves_group_and_rollback(self):
        self.image('200_1.jpg'); self.image('200_2.png')
        document = discover_documents(self.root)[0]
        completed = self.root / 'concluido'; completed.mkdir()
        (completed / '200_1.jpg').write_bytes(b'previous')
        import shutil
        real_move = shutil.move
        calls = []
        def fail_second(source, target):
            calls.append(source)
            if len(calls) == 2:
                raise OSError('locked')
            return real_move(source, target)
        with patch('meury_app.order_documents.shutil.move', side_effect=fail_second):
            with self.assertRaises(OSError):
                move_document(document, completed)
        self.assertTrue(all(p.exists() for p in document.pages))
        self.assertEqual(len(list(completed.iterdir())), 1)
        moved = move_document(document, completed)
        self.assertEqual(len(discover_documents(completed)), 2)
        self.assertTrue(all(p.name.startswith('concluido2_') for p in moved))

    def test_both_providers_receive_one_staged_document(self):
        self.image('200_1.jpg'); self.image('200_2.png')
        document = discover_documents(self.root)[0]
        def primary(*args):
            with pdfium.PdfDocument(args[2]) as pdf:
                self.assertEqual(len(pdf), 2)
            raise RuntimeError('fallback')
        def fallback(path, *args):
            with pdfium.PdfDocument(path) as pdf:
                self.assertEqual(len(pdf), 2)
            return {'ok': True}
        with patch.object(batch, 'valid_extraction', return_value=True):
            result = batch.extract_in_temporary_folder(temporary_folder=self.root, document=document,
                codex=None, project=self.root, pdf_path=document.pages[0], schema_path=self.root/'schema',
                codex_final_path=self.root/'final', log_path=self.root/'log',
                codex_runner=primary, api_runner=fallback)
        self.assertEqual(result[1], 'openai_api')

    def test_batch_groups_once_and_continues_after_bad_group(self):
        import json
        from tests.test_batch_order_prompt import BatchOrderPromptTest
        entry = self.root / 'pedidos_pdf' / 'entrada'
        entry.mkdir(parents=True)
        for name in ('200_1.jpg', '200_2.png', 'bad_2.jpg'):
            with Image.new('RGB', (80, 40), 'red') as image:
                image.save(entry / name)
        result = {'pedido': '200', 'resultadoFinal': 'SUCESSO', 'pastaCriada': '',
                  'quantidadeCopiada': 0, 'copiadas': [], 'naoEncontradas': [],
                  'duplicadas': [], 'jaExistentes': [], 'erros': []}
        with patch.object(batch, 'load_order_context', return_value=(self.root/'out', {})), \
             patch.object(batch, 'extract_with_fallback', return_value=(BatchOrderPromptTest().valid_order(), 'codex', '')) as extractor, \
             patch.object(batch, 'run_creator', return_value=result):
            self.assertEqual(batch.run_batch(self.root, update_index=False), 1)
        self.assertEqual(extractor.call_count, 1)
        self.assertTrue((entry/'concluido'/'200_1.jpg').exists())
        self.assertTrue((entry/'bad_2.jpg').exists())
        history = [json.loads(line) for line in (self.root/'pedidos_pdf/.controle/historico.jsonl').read_text().splitlines()]
        self.assertEqual(history[0]['arquivosOrigem'], ['200_1.jpg', '200_2.png'])
