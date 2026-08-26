from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import Workbook

import meury_app.indexer as indexer_module
from meury_app.indexer import build_index, image_key, load_index
from meury_app.processor import process_csv_text, process_excel, process_order_payload


class CustomerOrderStructureTest(unittest.TestCase):
    def test_treats_missing_variant_suffix_as_a(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "estampas" / "6162" / "6162-A.jpg"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"imagem")
            output = root / "saida"
            payload = {
                "pedido": "85951917582",
                "data": "05-08-2026",
                "clienteCodigo": "1710",
                "clienteNome": "CENTRAL ARACATUBA DE MALHAS LTDA ME",
                "produtos": [{
                    "tecidoCodigo": "1416",
                    "tecidoNome": "TRICOLINE SUBLIME",
                    "estampa": "6162",
                    "variante": "",
                }],
            }

            results, summary = process_order_payload(
                payload,
                output,
                {image_key("6162", "6162-A"): [str(source)]},
            )

            self.assertEqual(summary.copiados, 1)
            self.assertEqual(results[0].variante, "A")
            self.assertEqual(results[0].arquivo_procurado, "6162-A")

    def test_processes_pdf_extraction_json_without_interface(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "estampas" / "6162" / "6162-A.jpg"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"imagem")
            output = root / "saida"
            payload = {
                "pedido": "85951917582",
                "data": "05-08-2026",
                "clienteCodigo": "1710",
                "clienteNome": "MV PRINTS LTDA",
                "produtos": [{
                    "tecidoCodigo": "1416",
                    "tecidoNome": "TRICOLINE",
                    "estampa": "6162",
                    "variante": "A",
                }],
            }

            results, summary = process_order_payload(
                payload,
                output,
                {image_key("6162", "6162-A"): [str(source)]},
            )

            self.assertEqual(summary.copiados, 1)
            self.assertEqual(results[0].cliente, "1710-MV-PRINTS-LTDA")
            self.assertEqual(results[0].base, "1416-TRICOLINE")
            self.assertTrue(
                (
                    output / "1710-MV-PRINTS-LTDA" / "05-08-2026"
                    / "85951917582" / "1416-TRICOLINE" / "6162-A.JPG"
                ).exists()
            )

    def test_rejects_incomplete_pdf_extraction_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "tecidoCodigo"):
                process_order_payload(
                    {
                        "pedido": "1",
                        "data": "05-08-2026",
                        "clienteCodigo": "1710",
                        "clienteNome": "MV PRINTS LTDA",
                        "produtos": [{"estampa": "6162", "variante": "A"}],
                    },
                    Path(temporary),
                    {},
                )

    def test_creates_order_and_base_folders_when_image_is_not_found(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "saida"
            _, summary = process_order_payload(
                {
                    "pedido": "85951917582",
                    "data": "05-08-2026",
                    "clienteCodigo": "1710",
                    "clienteNome": "MV PRINTS LTDA",
                    "produtos": [{
                        "tecidoCodigo": "1416",
                        "tecidoNome": "TRICOLINE",
                        "estampa": "9999",
                        "variante": "Z",
                    }],
                },
                output,
                {},
            )

            self.assertEqual(summary.nao_encontrados, 1)
            self.assertEqual(summary.pedidos_criados, 1)
            self.assertTrue(
                (
                    output / "1710-MV-PRINTS-LTDA" / "05-08-2026"
                    / "85951917582" / "1416-TRICOLINE"
                ).is_dir()
            )

    def test_index_uses_design_folder_and_ignores_intermediate_folders(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "design"
            direct_image = source / "3232" / "3232-A.jpg"
            nested_image = (
                source / "asdkjasd" / "clientex" / "asd"
                / "5151" / "5151-A.jpg"
            )
            for image in (direct_image, nested_image):
                image.parent.mkdir(parents=True, exist_ok=True)
                image.write_bytes(b"imagem")

            cache = root / "indice.json"
            duplicates_log = root / "duplicidades.txt"
            with (
                patch.object(indexer_module, "INDEX_FILE", cache),
                patch.object(indexer_module, "DUPLICATES_LOG_FILE", duplicates_log),
                patch.object(indexer_module, "ensure_app_dir"),
            ):
                index, result = build_index(source)

            self.assertIn(image_key("3232", "3232-A"), index)
            self.assertIn(image_key("5151", "5151-A"), index)
            self.assertEqual(result.total_files, 2)
            self.assertEqual(result.duplicates, 0)
            self.assertIsNone(result.duplicates_log)
            self.assertFalse(duplicates_log.exists())

    def test_processes_csv_text_without_header_in_excel_column_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "estampas" / "MV" / "6652" / "6652-A.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"pdf")
            output = root / "saida"
            csv_text = "pedido-csv;23/07/2026;cliente-csv;base-csv;6652;a"
            index = {
                image_key("6652", "6652-A"): [str(source)],
            }

            results, summary = process_csv_text(csv_text, output, index)

            self.assertEqual(summary.copiados, 1)
            self.assertEqual(results[0].pedido, "PEDIDO-CSV")
            self.assertEqual(results[0].cliente, "CLIENTE-CSV")
            self.assertEqual(results[0].base, "BASE-CSV")
            self.assertTrue(
                (
                    output
                    / "CLIENTE-CSV"
                    / "23-07-2026"
                    / "PEDIDO-CSV"
                    / "BASE-CSV"
                    / "6652-A.PDF"
                ).exists()
            )

    def test_index_scans_multiple_source_folders(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_a = root / "origem-a"
            source_b = root / "origem-b"
            image_a = source_a / "MV" / "6652" / "6652-A.jpg"
            duplicate_a = source_b / "MV" / "6652" / "6652-A.png"
            image_b = source_b / "MV" / "7001" / "7001-X.pdf"
            for image in (image_a, duplicate_a, image_b):
                image.parent.mkdir(parents=True, exist_ok=True)
                image.write_bytes(b"imagem")
            ignored_image = source_b / "MV" / "8000" / "8000-A.jpeg"
            ignored_image.parent.mkdir(parents=True)
            ignored_image.write_bytes(b"formato-nao-suportado")

            cache = root / "indice.json"
            duplicates_log = root / "duplicidades.txt"
            with (
                patch.object(indexer_module, "INDEX_FILE", cache),
                patch.object(indexer_module, "DUPLICATES_LOG_FILE", duplicates_log),
                patch.object(indexer_module, "ensure_app_dir"),
            ):
                index, result = build_index([source_a, source_b])
                loaded = load_index([source_a, source_b])

            self.assertEqual(result.source_dirs, 2)
            self.assertEqual(result.total_files, 3)
            self.assertEqual(result.indexed_names, 2)
            self.assertEqual(result.duplicates, 1)
            self.assertEqual(
                len(index[image_key("6652", "6652-A")]),
                2,
            )
            self.assertIn(image_key("7001", "7001-X"), index)
            self.assertNotIn(image_key("8000", "8000-A"), index)
            self.assertEqual(loaded, index)
            self.assertEqual(result.duplicates_log, str(duplicates_log.resolve()))
            log_text = duplicates_log.read_text(encoding="utf-8")
            self.assertIn(str(image_a.resolve()), log_text)
            self.assertIn(str(duplicate_a.resolve()), log_text)

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
                image_key("6652", "6652-A"): [str(mv_image)],
                image_key("MV5501", "MV5501-A"): [str(client_image)],
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
