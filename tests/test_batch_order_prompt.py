from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from meury_app.batch_order_processor import (
    api_compatible_schema,
    build_prompt,
    discover_codex_models,
    extract_with_fallback,
    main as batch_main,
    move_to_completed,
    run_codex,
    run_creator,
    run_openai_api,
    valid_extraction,
    validation_requirements,
    write_final_action_log,
)
from meury_app.indexer import image_key


class BatchOrderPromptTest(unittest.TestCase):
    def test_creator_reuses_supplied_index_without_starting_subprocess(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "estampas" / "6162-A.jpg"
            source.parent.mkdir()
            source.write_bytes(b"imagem")
            extraction = root / "extracao.json"
            extraction.write_text(json.dumps({
                "pedido": "20003945",
                "data": "25/08/2026",
                "clienteCodigo": "5211",
                "clienteNome": "MAGA WOMAN LTDA",
                "produtos": [{
                    "tecidoCodigo": "1065",
                    "tecidoNome": "OXFORD",
                    "estampa": "6162",
                    "variante": "A",
                }],
            }), encoding="utf-8")
            log = root / "execucao.log"

            with patch("meury_app.batch_order_processor.subprocess.run") as subprocess_run:
                result = run_creator(
                    extraction,
                    log,
                    root / "saida",
                    {image_key("6162", "6162-A"): [str(source)]},
                )

            subprocess_run.assert_not_called()
            self.assertEqual(result["resultadoFinal"], "SUCESSO")
            self.assertEqual(result["quantidadeCopiada"], 1)
            logged = log.read_text(encoding="utf-8")
            self.assertIn("Copiando 6162-A.jpg", logged)
            self.assertIn("Relatórios concluídos", logged)
            self.assertIn("Tempo total da criação", logged)

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

    def valid_order(self):
        return {
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

    def test_openai_fallback_sends_pdf_with_strict_schema_without_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "pedido.pdf"
            pdf.write_bytes(b"%PDF-1.4\nconteudo")
            schema = root / "schema.json"
            schema.write_text(
                (Path(__file__).parents[1] / "meury_app" /
                 "batch_order_extraction.schema.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            log = root / "execucao.log"
            response = SimpleNamespace(
                output_text=json.dumps(self.valid_order()),
                id="resp_test",
                usage=SimpleNamespace(input_tokens=100, output_tokens=50),
            )
            client = SimpleNamespace(
                responses=SimpleNamespace(create=Mock(return_value=response)),
            )

            result = run_openai_api(
                pdf, schema, log, client=client, model="gpt-test",
            )

            self.assertEqual(result["pedido"], "20003945")
            request = client.responses.create.call_args.kwargs
            self.assertFalse(request["store"])
            self.assertEqual(request["model"], "gpt-test")
            self.assertTrue(request["text"]["format"]["strict"])
            file_input = request["input"][0]["content"][0]
            self.assertEqual(file_input["type"], "input_file")
            self.assertEqual(file_input["detail"], "high")
            self.assertTrue(file_input["file_data"].startswith(
                "data:application/pdf;base64,",
            ))
            self.assertNotIn(str(root), request["input"][0]["content"][1]["text"])
            self.assertNotIn("OPENAI_API_KEY", log.read_text(encoding="utf-8"))

    def test_codex_uses_requested_model_chain_with_high_reasoning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex = root / "codex"
            codex.write_text("", encoding="utf-8")
            final_path = root / "final.json"

            def complete(command, **_kwargs):
                model = command[command.index("--model") + 1]
                if model == "gpt-6-astra":
                    final_path.write_text(
                        json.dumps({"pedido": "incompleto"}), encoding="utf-8"
                    )
                    return SimpleNamespace(returncode=0, stdout="ok")
                if model == "gpt-5.5":
                    final_path.write_text(
                        json.dumps(self.valid_order()), encoding="utf-8"
                    )
                    return SimpleNamespace(returncode=0, stdout="ok")
                return SimpleNamespace(returncode=1, stdout="falha transitória")

            with (
                patch.dict(os.environ, {}, clear=True),
                patch(
                    "meury_app.batch_order_processor.discover_codex_models",
                    return_value=(
                        "gpt-6-astra",
                        "gpt-5.6-sol",
                        "gpt-5.6-terra",
                        "gpt-5.6-luna",
                        "gpt-5.5",
                    ),
                ),
                patch(
                    "meury_app.batch_order_processor.subprocess.run",
                    side_effect=complete,
                ) as subprocess_run,
            ):
                result = run_codex(
                    codex,
                    root,
                    root / "pedido.pdf",
                    root / "schema.json",
                    final_path,
                    root / "codex.log",
                )

            self.assertEqual(result["pedido"], "20003945")
            commands = [call.args[0] for call in subprocess_run.call_args_list]
            self.assertEqual(
                [command[command.index("--model") + 1] for command in commands],
                [
                    "gpt-6-astra",
                    "gpt-5.6-sol",
                    "gpt-5.6-terra",
                    "gpt-5.6-luna",
                    "gpt-5.5",
                ],
            )
            for command in commands:
                self.assertIn("--config", command)
                self.assertIn('model_reasoning_effort="high"', command)

    def test_codex_continues_with_next_model_after_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex = root / "codex"
            codex.write_text("", encoding="utf-8")
            final_path = root / "final.json"
            calls = 0

            def complete(command, **_kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise subprocess.TimeoutExpired(command, 1, output="parcial")
                final_path.write_text(
                    json.dumps(self.valid_order()), encoding="utf-8"
                )
                return SimpleNamespace(returncode=0, stdout="ok")

            with (
                patch.dict(os.environ, {}, clear=True),
                patch(
                    "meury_app.batch_order_processor.discover_codex_models",
                    return_value=("gpt-6-astra", "gpt-5.6-sol"),
                ),
                patch(
                    "meury_app.batch_order_processor.subprocess.run",
                    side_effect=complete,
                ) as subprocess_run,
            ):
                result = run_codex(
                    codex,
                    root,
                    root / "pedido.pdf",
                    root / "schema.json",
                    final_path,
                    root / "codex.log",
                )

            self.assertEqual(result["pedido"], "20003945")
            commands = [call.args[0] for call in subprocess_run.call_args_list]
            self.assertEqual(
                [command[command.index("--model") + 1] for command in commands],
                ["gpt-6-astra", "gpt-5.6-sol"],
            )

    def test_discovers_visible_codex_models_in_catalog_order(self):
        catalog = {
            "models": [
                {"slug": "modelo-a", "visibility": "list"},
                {"slug": "oculto", "visibility": "hide"},
                {"slug": "modelo-b", "visibility": "list"},
                {"slug": "modelo-a", "visibility": "list"},
                {"slug": "", "visibility": "list"},
            ]
        }
        discover_codex_models.cache_clear()
        with patch(
            "meury_app.batch_order_processor.subprocess.run",
            return_value=SimpleNamespace(
                returncode=0,
                stdout=json.dumps(catalog),
                stderr="",
            ),
        ) as subprocess_run:
            models = discover_codex_models(Path("/bin/codex-teste"))
        discover_codex_models.cache_clear()

        self.assertEqual(models, ("modelo-a", "modelo-b"))
        self.assertEqual(
            subprocess_run.call_args.args[0],
            ["/bin/codex-teste", "debug", "models"],
        )

    def test_uses_static_model_chain_when_catalog_discovery_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex = root / "codex"
            codex.write_text("", encoding="utf-8")
            final_path = root / "final.json"
            log_path = root / "codex.log"

            def complete(command, **_kwargs):
                final_path.write_text(
                    json.dumps(self.valid_order()), encoding="utf-8"
                )
                return SimpleNamespace(returncode=0, stdout="ok")

            with (
                patch.dict(os.environ, {}, clear=True),
                patch(
                    "meury_app.batch_order_processor.discover_codex_models",
                    side_effect=RuntimeError("catálogo indisponível"),
                ),
                patch(
                    "meury_app.batch_order_processor.subprocess.run",
                    side_effect=complete,
                ) as subprocess_run,
            ):
                result = run_codex(
                    codex,
                    root,
                    root / "pedido.pdf",
                    root / "schema.json",
                    final_path,
                    log_path,
                )

            self.assertEqual(result["pedido"], "20003945")
            command = subprocess_run.call_args.args[0]
            self.assertEqual(command[command.index("--model") + 1], "gpt-6-astra")
            self.assertIn(
                "Origem: lista de contingência",
                log_path.read_text(encoding="utf-8"),
            )

    def test_schema_removes_unsupported_validation_keywords(self):
        with tempfile.TemporaryDirectory() as temporary:
            schema_path = Path(temporary) / "schema.json"
            schema_path.write_text(json.dumps({
                "$schema": "example",
                "type": "object",
                "properties": {"name": {"type": "string", "minLength": 1}},
                "required": ["name"],
                "additionalProperties": False,
            }), encoding="utf-8")

            schema = api_compatible_schema(schema_path)

            self.assertNotIn("$schema", schema)
            self.assertNotIn("minLength", schema["properties"]["name"])

    def test_uses_api_after_codex_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "execucao.log"
            primary = Mock(side_effect=RuntimeError("codex indisponível"))
            fallback = Mock(return_value=self.valid_order())

            extraction, provider, model = extract_with_fallback(
                codex=None,
                project=root,
                pdf_path=root / "pedido.pdf",
                schema_path=root / "schema.json",
                codex_final_path=root / "final.json",
                log_path=log,
                codex_runner=primary,
                api_runner=fallback,
            )

            self.assertEqual(extraction["pedido"], "20003945")
            self.assertEqual(provider, "openai_api")
            self.assertTrue(model)
            fallback.assert_called_once()
            self.assertIn("Falha do Codex", log.read_text(encoding="utf-8"))

    def test_uses_api_after_invalid_codex_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fallback = Mock(return_value=self.valid_order())

            _extraction, provider, _model = extract_with_fallback(
                codex=root / "codex",
                project=root,
                pdf_path=root / "pedido.pdf",
                schema_path=root / "schema.json",
                codex_final_path=root / "final.json",
                log_path=root / "execucao.log",
                codex_runner=Mock(return_value={"pedido": "incompleto"}),
                api_runner=fallback,
            )

            self.assertEqual(provider, "openai_api")
            fallback.assert_called_once()

    def test_openai_fallback_requires_key_when_no_client_is_injected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "pedido.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                    run_openai_api(pdf, root / "schema.json", root / "log.txt")

    def test_validation_requirements_explains_missing_duplicates_and_errors(self):
        reasons, actions = validation_requirements({
            "resultadoFinal": "FALHA",
            "naoEncontradas": ["6162-A"],
            "duplicadas": [{"estampa": "7001-X", "arquivos": ["a.pdf", "b.pdf"]}],
            "erros": ["pasta sem permissão"],
        })

        self.assertTrue(any("6162-A" in reason for reason in reasons))
        self.assertTrue(any("7001-X" in reason for reason in reasons))
        self.assertTrue(any("pasta sem permissão" in reason for reason in reasons))
        self.assertTrue(any("reprocessar" in action for action in actions))

    def test_final_log_is_explicit_and_lists_all_pdfs_by_required_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            final_log_path = run_dir / "LOG_FINAL_ACAO_HUMANA.txt"
            rows = [
                {
                    "arquivo": "pedido_com_erro.pdf",
                    "pedido": "2001",
                    "resultado": "FALHA",
                    "validacoes": ["Estampa não encontrada: 6162-A"],
                    "acoes": ["Cadastrar a estampa e reprocessar."],
                    "relatorioJson": str(run_dir / "001.json"),
                    "logIndividual": str(run_dir / "001.log"),
                },
                {
                    "arquivo": "pedido_ok\nforjado.pdf",
                    "pedido": "2002",
                    "resultado": "SUCESSO",
                    "validacoes": [],
                    "acoes": [],
                },
            ]

            content = write_final_action_log(
                final_log_path,
                rows,
                run_dir=run_dir,
                generated_at="2026-10-02T10:00:00-03:00",
            )

            self.assertEqual(final_log_path.read_text(encoding="utf-8"), content)
            self.assertIn("LOG FINAL DO PROCESSAMENTO DE PDFs", content)
            self.assertIn("STATUS GERAL: AÇÃO HUMANA NECESSÁRIA", content)
            self.assertIn("PDFs que exigem ação humana: 1", content)
            self.assertIn("pedido_com_erro.pdf", content)
            self.assertIn("Por que precisa de validação", content)
            self.assertIn("Cadastrar a estampa e reprocessar", content)
            self.assertIn(r"pedido_ok\nforjado.pdf", content)
            self.assertNotIn("pedido_ok\nforjado.pdf", content)

    def test_batch_main_writes_final_action_log_with_processing_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            input_dir = project / "pedidos_pdf" / "entrada"
            input_dir.mkdir(parents=True)
            (input_dir / "pedido.pdf").write_bytes(b"%PDF-1.4")
            extraction_dir = project / "pedidos_pdf" / ".controle" / "extracoes"
            extraction_dir.mkdir(parents=True)
            (extraction_dir / "v1_hash-teste.json").write_text(
                json.dumps(self.valid_order()), encoding="utf-8"
            )
            creator_result = {
                "pedido": "20003945",
                "pastaCriada": "",
                "quantidadeCopiada": 0,
                "copiadas": [],
                "naoEncontradas": ["6162-A"],
                "duplicadas": [],
                "jaExistentes": [],
                "erros": [],
                "resultadoFinal": "FALHA",
            }

            with (
                patch(
                    "meury_app.batch_order_processor.parse_args",
                    return_value=SimpleNamespace(projeto=str(project), codex=""),
                ),
                patch("meury_app.batch_order_processor.load_local_environment"),
                patch(
                    "meury_app.batch_order_processor.load_order_context",
                    return_value=(project / "saida", {}),
                ),
                patch(
                    "meury_app.batch_order_processor.file_hash",
                    return_value="hash-teste",
                ),
                patch(
                    "meury_app.batch_order_processor.run_creator",
                    return_value=creator_result,
                ),
                redirect_stdout(io.StringIO()),
            ):
                exit_code = batch_main()

            report_dirs = list((project / "pedidos_pdf" / "relatorios").iterdir())
            self.assertEqual(exit_code, 1)
            self.assertEqual(len(report_dirs), 1)
            final_log = (
                report_dirs[0] / "LOG_FINAL_ACAO_HUMANA.txt"
            ).read_text(encoding="utf-8")
            self.assertIn("pedido.pdf", final_log)
            self.assertIn("Estampa não encontrada: 6162-A", final_log)
            self.assertIn("AÇÃO HUMANA NECESSÁRIA", final_log)


if __name__ == "__main__":
    unittest.main()
