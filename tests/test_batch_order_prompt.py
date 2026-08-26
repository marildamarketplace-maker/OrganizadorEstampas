from pathlib import Path
import unittest

from meury_app.batch_order_processor import build_prompt


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


if __name__ == "__main__":
    unittest.main()
