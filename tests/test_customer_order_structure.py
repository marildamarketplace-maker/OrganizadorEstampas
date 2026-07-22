from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook

from meury_app.indexer import image_key
from meury_app.processor import process_excel


class CustomerOrderStructureTest(unittest.TestCase):
    def test_searches_by_customer_and_copies_to_customer_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "estampas"
            output = root / "saida"

            mv_image = source / "MV" / "6652" / "6652-A.jpg"
            client_image = source / "CLIENTE1" / "MV5501" / "MV5501-A.jpg"
            mv_image.parent.mkdir(parents=True)
            client_image.parent.mkdir(parents=True)
            mv_image.write_bytes(b"imagem-mv")
            client_image.write_bytes(b"imagem-cliente1")

            workbook = Workbook()
            sheet = workbook.active
            sheet.append([
                "ID do Pedido", "Data do Pedido", "ID do Cliente",
                "BASE", "ID da Estampa", "Variante",
            ])
            sheet.append(["PEDIDO1", "18/07/2026", "MV", "base1", "6652", "A"])
            sheet.append(["PEDIDO2", "19/07/2026", "CLIENTE1", "BASE2", "MV5501", "A"])
            sheet.append(["pedido3", "20/07/2026", "cliente1", "base1", "6652", "a"])
            excel = root / "pedidos.xlsx"
            workbook.save(excel)

            index = {
                image_key("MV", "6652", "6652-A"): [str(mv_image)],
                image_key("CLIENTE1", "MV5501", "MV5501-A"): [str(client_image)],
            }
            results, summary = process_excel(excel, output, index)

            self.assertEqual(
                [item.status for item in results],
                ["COPIADO", "COPIADO", "COPIADO"],
            )
            self.assertEqual(summary.copiados, 3)
            self.assertEqual(summary.pedidos_criados, 3)
            self.assertEqual(
                (output / "MV" / "18-07-2026" / "PEDIDO1" / "BASE1" / "6652-A.JPG").read_bytes(),
                b"imagem-mv",
            )
            self.assertEqual(
                (output / "CLIENTE1" / "19-07-2026" / "PEDIDO2" / "BASE2" / "MV5501-A.JPG").read_bytes(),
                b"imagem-cliente1",
            )
            lowercase_result = results[2]
            self.assertEqual(lowercase_result.pedido, "PEDIDO3")
            self.assertEqual(lowercase_result.cliente, "CLIENTE1")
            self.assertEqual(lowercase_result.base, "BASE1")
            self.assertEqual(lowercase_result.estampa, "6652")
            self.assertEqual(lowercase_result.variante, "A")
            self.assertEqual(
                (output / "CLIENTE1" / "20-07-2026" / "PEDIDO3" / "BASE1" / "6652-A.JPG").read_bytes(),
                b"imagem-mv",
            )

            second_results, second_summary = process_excel(excel, output, index)
            self.assertEqual(
                [item.status for item in second_results],
                ["JÁ EXISTE", "JÁ EXISTE", "JÁ EXISTE"],
            )
            self.assertEqual(second_summary.copiados, 0)
            self.assertEqual(second_summary.ignorados, 3)
            self.assertFalse(
                (output / "MV" / "18-07-2026" / "PEDIDO1" / "BASE1" / "6652-A_2.JPG").exists()
            )


if __name__ == "__main__":
    unittest.main()
