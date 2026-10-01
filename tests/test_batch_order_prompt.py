from pathlib import Path
from types import SimpleNamespace
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from meury_app.batch_order_processor import (
    api_compatible_schema,
    build_prompt,
    extract_with_fallback,
    move_to_completed,
    run_openai_api,
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


if __name__ == "__main__":
    unittest.main()
