from pathlib import Path
import unittest

from meury_app.batch_order_processor import (
    build_prompt,
    move_to_completed,
    valid_extraction,
)


class BatchOrderPromptTest(unittest.TestCase):
    def test_exports_only_products_containing_sublime(self):
        prompt = build_prompt(Path("/projeto"), Path("/pedido.pdf"))

        self.assertIn(
            'Inclua em "produtos" somente\nlinhas cuja descrição do produto contenha a palavra isolada "SUBLIME"',
            prompt,
        )
        self.assertIn("Ignore completamente todas as outras linhas", prompt)
        self.assertIn("cada produto SUBLIME incluído", prompt)

    def test_uses_customer_field_instead_of_issuing_company(self):
        prompt = build_prompt(Path("/projeto"), Path("/pedido.pdf"))

        self.assertIn('campo "Cliente" como clienteNome', prompt)
        self.assertIn('Nunca use o campo "Empresa" como clienteNome', prompt)

    def test_codex_only_extracts_and_does_not_create_the_order(self):
        prompt = build_prompt(Path("/projeto"), Path("/pedido.pdf"))

        self.assertIn("Não execute programas, não crie pastas e não copie arquivos", prompt)
        self.assertIn("A etapa seguinte do processador cuidará da criação", prompt)
        self.assertNotIn("criar_pedido.py", prompt)

    def test_validates_cached_extraction(self):
        extraction = {
            "pedido": "20003945",
            "data": "25/08/2026",
            "clienteCodigo": "5211",
            "clienteNome": "MAGA WOMAN LTDA",
            "produtos": [{
                "tecidoCodigo": "1065",
                "tecidoNome": "OXFORD",
                "estampa": "MV23069",
                "variante": "A",
            }],
        }

        self.assertTrue(valid_extraction(extraction))
        extraction["produtos"][0]["estampa"] = ""
        self.assertFalse(valid_extraction(extraction))

    def test_moves_completed_pdf_without_overwriting_same_name(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "entrada" / "pedido.pdf"
            completed = root / "concluido"
            source.parent.mkdir()
            completed.mkdir()
            source.write_bytes(b"novo")
            (completed / "pedido.pdf").write_bytes(b"anterior")

            destination = move_to_completed(source, completed)

            self.assertEqual(destination.name, "pedido_2.pdf")
            self.assertFalse(source.exists())
            self.assertEqual(destination.read_bytes(), b"novo")


if __name__ == "__main__":
    unittest.main()
