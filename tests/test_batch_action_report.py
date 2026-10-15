from pathlib import Path
import tempfile
import unittest

from meury_app.batch_order_processor import write_final_action_log


class BatchActionReportTest(unittest.TestCase):
    def render_report(self, rows):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "relatorios" / "20261014_184127"
            run_dir.mkdir(parents=True)
            prepared_rows = []
            for position, row in enumerate(rows, start=1):
                prepared_rows.append({
                    "arquivo": f"pedido_{position}.pdf",
                    "pedido": str(20004762 + position),
                    "resultado": "FALHA",
                    "validacoes": [],
                    "acoes": [],
                    "relatorioJson": str(run_dir / f"{position:03d}_pedido.json"),
                    "logIndividual": str(run_dir / f"{position:03d}_pedido.log"),
                    **row,
                })
            output = run_dir / "LOG_FINAL_ACAO_HUMANA.txt"
            content = write_final_action_log(
                output,
                prepared_rows,
                run_dir=run_dir,
                generated_at="2026-10-14T18:46:18-03:00",
            )
            self.assertEqual(output.read_text(encoding="utf-8"), content)
            return content, run_dir, prepared_rows

    def test_lists_codes_under_direct_actions_without_technical_boilerplate(self):
        content, run_dir, rows = self.render_report([
            {
                "arquivo": "20004763.pdf",
                "pedido": "20004763",
                "validacoes": [
                    "Estampa não encontrada: 5362-R70",
                    "Estampa não encontrada: 5760-CR70",
                    "Estampa com mais de um arquivo candidato: MV4822-A ou MV4822",
                    "Estampa com mais de um arquivo candidato: 4896-C",
                ],
                "acoes": [
                    "Conferir os códigos/variantes no PDF e cadastrar ou disponibilizar "
                    "os arquivos de estampa ausentes antes de reprocessar.",
                    "Escolher o arquivo correto de cada estampa duplicada, corrigir a "
                    "duplicidade no catálogo e reprocessar.",
                ],
            },
            {
                "arquivo": "20004766.pdf",
                "pedido": "20004766",
                "validacoes": ["Estampa não encontrada: 5073-R50"],
            },
            {
                "arquivo": "20004749.pdf",
                "pedido": "20004749",
                "resultado": "SUCESSO",
            },
        ])

        self.assertIn("RESUMO DOS PEDIDOS", content)
        self.assertIn("Localizar estampas:", content)
        self.assertIn("Escolher o arquivo correto:", content)
        for code in ("5362-R70", "5760-CR70", "MV4822-A", "4896-C", "5073-R50"):
            self.assertEqual(content.count(code), 1, code)
        for filename in ("20004763.pdf", "20004766.pdf", "20004749.pdf"):
            self.assertIn(filename, content)
        self.assertNotIn("MV4822-A ou MV4822", content)
        self.assertNotIn("Resultado técnico:", content)
        self.assertNotIn("Por que precisa de validação:", content)
        self.assertNotIn("FIM DO LOG FINAL", content)
        self.assertLessEqual(content.count(str(run_dir)), 1)
        for row in rows:
            self.assertNotIn(row["relatorioJson"], content)
            self.assertNotIn(row["logIndividual"], content)
        self.assertIn("índice", content.lower())
        self.assertRegex(content.lower(), r"(?:novamente|reprocess)")
        self.assertLess(len(content.splitlines()), 35)

    def test_does_not_remove_distinct_variants_from_duplicate_candidates(self):
        content, _run_dir, _rows = self.render_report([{
            "validacoes": [
                "Estampa com mais de um arquivo candidato: MV4822-A ou MV4822-B",
                "Estampa com mais de um arquivo candidato: 4896-C ou 4896-D",
            ],
        }])

        for code in ("MV4822-A", "MV4822-B", "4896-C", "4896-D"):
            self.assertIn(code, content)

    def test_success_and_previously_processed_need_no_further_action(self):
        content, _run_dir, _rows = self.render_report([
            {"arquivo": "novo.pdf", "pedido": "1001", "resultado": "SUCESSO"},
            {"arquivo": "anterior.pdf", "pedido": "1002", "resultado": "JÁ PROCESSADO"},
        ])

        self.assertIn("novo.pdf", content)
        self.assertIn("anterior.pdf", content)
        self.assertRegex(content.lower(), r"(?:sem pendências|nenhuma ação)")
        self.assertNotIn("Localizar estampas:", content)
        self.assertNotIn("Escolher o arquivo correto:", content)
        self.assertNotRegex(content.lower(), r"(?:rode|execute|rodar|executar).{0,30}novamente")
        self.assertNotRegex(content.lower(), r"reprocesse|reprocessar")
        self.assertLess(len(content.splitlines()), 20)

    def test_processing_errors_keep_reason_action_and_relative_detail_files(self):
        content, run_dir, rows = self.render_report([{
            "validacoes": ["Erro de processamento: pasta sem permissão"],
            "acoes": ["Liberar permissão de escrita na pasta de destino."],
        }])

        self.assertIn("pasta sem permissão", content)
        self.assertIn("Liberar permissão de escrita na pasta de destino.", content)
        self.assertIn(Path(rows[0]["relatorioJson"]).name, content)
        self.assertIn(Path(rows[0]["logIndividual"]).name, content)
        self.assertNotIn(rows[0]["relatorioJson"], content)
        self.assertNotIn(rows[0]["logIndividual"], content)
        self.assertLessEqual(content.count(str(run_dir)), 1)

    def test_unknown_validation_and_its_action_are_not_silently_dropped(self):
        content, _run_dir, _rows = self.render_report([{
            "validacoes": ["Cliente não identificado no cabeçalho do pedido."],
            "acoes": ["Conferir o nome e código do cliente no PDF."],
        }])

        self.assertIn("Cliente não identificado no cabeçalho do pedido.", content)
        self.assertIn("Conferir o nome e código do cliente no PDF.", content)

    def test_successful_order_with_move_failure_only_needs_pdf_moved(self):
        content, _run_dir, _rows = self.render_report([{
            "resultado": "SUCESSO",
            "validacoes": ["PDF não foi movido para concluído: arquivo em uso"],
            "acoes": [
                "Mover manualmente o PDF para a pasta 'concluido' e verificar "
                "permissões ou bloqueios no arquivo."
            ],
        }])

        self.assertIn("arquivo em uso", content)
        self.assertIn("Mover", content)
        self.assertIn("concluido", content)
        self.assertNotIn("Localizar estampas:", content)
        self.assertNotIn("Escolher o arquivo correto:", content)
        self.assertNotRegex(content.lower(), r"(?:rode|execute|rodar|executar).{0,30}novamente")
        self.assertNotRegex(content.lower(), r"reprocesse|reprocessar o pdf")

    def test_untrusted_fields_cannot_insert_report_lines(self):
        content, _run_dir, _rows = self.render_report([{
            "arquivo": "pedido\nforjado.pdf",
            "pedido": "123\r\nSTATUS FALSO",
            "validacoes": ["Estampa não encontrada: MV1000-A\nSUCESSO FALSO"],
            "acoes": ["Conferir\tcliente\nno PDF."],
        }, {
            "arquivo": "cliente\nforjado.pdf",
            "resultado": "SUCESSO",
        }])

        self.assertIn(r"pedido\nforjado.pdf", content)
        self.assertIn(r"123\r\nSTATUS FALSO", content)
        self.assertIn(r"MV1000-A\nSUCESSO FALSO", content)
        self.assertIn(r"cliente\nforjado.pdf", content)
        self.assertNotIn("pedido\nforjado.pdf", content)
        self.assertNotIn("\nSTATUS FALSO", content)
        self.assertNotIn("\nSUCESSO FALSO", content)


if __name__ == "__main__":
    unittest.main()
