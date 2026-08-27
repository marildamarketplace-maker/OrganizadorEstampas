from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import Workbook

import meury_app.indexer as indexer_module
from meury_app.indexer import (
    build_index,
    image_key,
    load_index,
    update_index_incremental,
)
from meury_app.processor import process_csv_text, process_excel, process_order_payload


class CustomerOrderStructureTest(unittest.TestCase):
    def test_copies_all_exclusive_images_found_by_filename_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "MATEUS CALL" / "MATEUS FELIPE DE SOUZA"
            source_dir.mkdir(parents=True)
            first = source_dir / "MV23069 47x94.jpg"
            second = source_dir / "MV23069 47x94..jpg"
            first.write_bytes(b"imagem 1")
            second.write_bytes(b"imagem 2")
            output = root / "saida"
            payload = {
                "pedido": "20003945",
                "data": "25-08-2026",
                "clienteCodigo": "5211",
                "clienteNome": "MAGA WOMAN LTDA",
                "produtos": [{
                    "tecidoCodigo": "9109",
                    "tecidoNome": "SEDA MICHELANGELO",
                    "estampa": "MV23069",
                    "variante": "A",
                }],
            }
            index = {
                image_key("MATEUS", "MV23069 47x94"): [str(first)],
                image_key("MATEUS", "MV23069 47x94."): [str(second)],
            }

            results, summary = process_order_payload(payload, output, index)

            self.assertEqual(summary.copiados, 2)
            self.assertEqual(results[0].status, "COPIADO")
            destination = (
                output / "5211-MAGA-WOMAN-LTDA" / "25-08-2026"
                / "20003945" / "9109-SEDA-MICHELANGELO"
            )
            self.assertTrue((destination / "MV23069 47X94.JPG").exists())
            self.assertTrue((destination / "MV23069 47X94..JPG").exists())
            self.assertEqual(Path(summary.report_xlsx).parent, destination.parent)

    def test_incremental_index_adds_new_images_without_repeating_existing_ones(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "estampas"
            existing = source / "6162 NATAL CORRIDAS" / "6162-A.png"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"existente")
            cache = root / "indice.json"
            duplicates_log = root / "duplicidades.txt"

            with (
                patch.object(indexer_module, "INDEX_FILE", cache),
                patch.object(indexer_module, "DUPLICATES_LOG_FILE", duplicates_log),
                patch.object(indexer_module, "ensure_app_dir"),
            ):
                build_index(source)

                new_in_existing_folder = existing.parent / "6162-B.png"
                duplicate = source / "outra" / "6162 FESTA" / "6162-A.jpg"
                new_in_existing_folder.write_bytes(b"nova")
                duplicate.parent.mkdir(parents=True)
                duplicate.write_bytes(b"duplicada")

                index, result = update_index_incremental(source)
                same_index, second_result = update_index_incremental(source)

            self.assertEqual(result.added_files, 2)
            self.assertEqual(result.scanned_files, 3)
            self.assertEqual(result.duplicates, 1)
            self.assertEqual(
                index[image_key("6162", "6162-B")],
                [str(new_in_existing_folder.resolve())],
            )
            self.assertEqual(len(index[image_key("6162", "6162-A")]), 2)
            self.assertEqual(second_result.added_files, 0)
            self.assertEqual(same_index, index)

    def test_incremental_index_requires_an_existing_complete_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "estampas"
            source.mkdir()
            with (
                patch.object(indexer_module, "INDEX_FILE", root / "indice.json"),
                patch.object(
                    indexer_module,
                    "DUPLICATES_LOG_FILE",
                    root / "duplicidades.txt",
                ),
                patch.object(indexer_module, "ensure_app_dir"),
            ):
                with self.assertRaisesRegex(ValueError, "índice completo"):
                    update_index_incremental(source)

    def test_variant_a_accepts_image_without_a_suffix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "estampas" / "4233" / "4233.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"variante-a")
            output = root / "saida"

            results, summary = process_csv_text(
                "pedido;26/08/2026;cliente;base;4233;A",
                output,
                {image_key("4233", "4233"): [str(source)]},
            )

            self.assertEqual(results[0].status, "COPIADO")
            self.assertEqual(summary.copiados, 1)
            self.assertTrue(
                (output / "CLIENTE" / "26-08-2026" / "PEDIDO" / "BASE" / "4233.PNG").exists()
            )

    def test_variant_b_does_not_use_image_without_suffix(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "4233" / "4233.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"variante-a")

            results, summary = process_csv_text(
                "pedido;26/08/2026;cliente;base;4233;B",
                Path(temporary) / "saida",
                {image_key("4233", "4233"): [str(source)]},
            )

            self.assertEqual(results[0].status, "NÃO ENCONTRADO")
            self.assertEqual(summary.nao_encontrados, 1)

    def test_variant_a_reports_duplicate_when_both_name_patterns_exist(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suffixed = root / "4233-A.png"
            plain = root / "4233.png"
            suffixed.write_bytes(b"com-sufixo")
            plain.write_bytes(b"sem-sufixo")

            results, summary = process_csv_text(
                "pedido;26/08/2026;cliente;base;4233;A",
                root / "saida",
                {
                    image_key("4233", "4233-A"): [str(suffixed)],
                    image_key("4233", "4233"): [str(plain)],
                },
            )

            self.assertEqual(results[0].status, "DUPLICADO")
            self.assertEqual(summary.duplicados, 1)

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
            self.assertEqual(results[0].arquivo_procurado, "6162-A ou 6162")

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

    def test_index_uses_initial_code_from_descriptive_design_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "estampas"
            natal = source / "6162 NATAL CORRIDAS" / "6162-A.png"
            poa = source / "6149 POA 8CM" / "6149.png"
            for image in (natal, poa):
                image.parent.mkdir(parents=True)
                image.write_bytes(b"imagem")

            cache = root / "indice.json"
            duplicates_log = root / "duplicidades.txt"
            with (
                patch.object(indexer_module, "INDEX_FILE", cache),
                patch.object(indexer_module, "DUPLICATES_LOG_FILE", duplicates_log),
                patch.object(indexer_module, "ensure_app_dir"),
            ):
                index, _ = build_index(source)

            self.assertEqual(
                index[image_key("6162", "6162-A")],
                [str(natal.resolve())],
            )
            self.assertEqual(
                index[image_key("6149", "6149")],
                [str(poa.resolve())],
            )

            results, summary = process_csv_text(
                "pedido;26/08/2026;cliente;base;6149;A",
                root / "saida",
                index,
            )
            self.assertEqual(results[0].status, "COPIADO")
            self.assertEqual(summary.copiados, 1)

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
            jpeg_image = source_b / "MV" / "8000" / "8000-A.jpeg"
            jpeg_image.parent.mkdir(parents=True)
            jpeg_image.write_bytes(b"jpeg-suportado")

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
            self.assertEqual(result.total_files, 4)
            self.assertEqual(result.indexed_names, 3)
            self.assertEqual(result.duplicates, 1)
            self.assertEqual(
                len(index[image_key("6652", "6652-A")]),
                2,
            )
            self.assertIn(image_key("7001", "7001-X"), index)
            self.assertIn(image_key("8000", "8000-A"), index)
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
