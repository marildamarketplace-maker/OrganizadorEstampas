from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from meury_app import batch_order_processor as batch
from meury_app.indexer import image_key


class BatchIndexOptionTest(unittest.TestCase):
    def test_command_line_selects_interactive_update_or_saved_index(self):
        for flags, expected in (
            ([], None),
            (["--atualizar-indice"], True),
            (["--sem-atualizar-indice"], False),
        ):
            with self.subTest(flags=flags), patch(
                "sys.argv", ["processar_lote", "--projeto", "projeto", *flags]
            ):
                self.assertIs(batch.parse_args().atualizar_indice, expected)

    def test_explicit_update_choice_does_not_prompt(self):
        with patch("builtins.input") as prompt:
            self.assertTrue(batch.choose_index_update(True))
            self.assertFalse(batch.choose_index_update(False))
        prompt.assert_not_called()

    def test_interactive_choice_accepts_portuguese_answers(self):
        for answer, expected in (
            ("S", True), ("sim", True), (" Sim ", True),
            ("N", False), ("nao", False), ("não", False),
        ):
            with self.subTest(answer=answer), patch(
                "builtins.input", return_value=answer
            ):
                self.assertIs(batch.choose_index_update(None), expected)

    def test_interactive_choice_repeats_until_valid_answer(self):
        with (
            patch("builtins.input", side_effect=["talvez", "N"]) as prompt,
            redirect_stdout(io.StringIO()),
        ):
            self.assertFalse(batch.choose_index_update(None))
        self.assertEqual(prompt.call_count, 2)

    def _assert_cached_order_is_separated(self, *, answer, update_index):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            source = project / "estampas" / "MV27951-A.jpg"
            source.parent.mkdir()
            source.write_bytes(b"imagem do catalogo salvo")
            order_index = {image_key("MV27951", "MV27951-A"): [str(source)]}
            output = project / "saida"
            config = {"output_dir": str(output), "source_dirs": [str(source.parent)]}
            input_dir = project / "pedidos_pdf" / "entrada"
            input_dir.mkdir(parents=True)
            pdf = input_dir / "20004749.pdf"
            pdf.write_bytes(b"%PDF-1.4\ncache de extracao no teste")
            digest = batch.file_hash(pdf)
            extraction_dir = project / "pedidos_pdf" / ".controle" / "extracoes"
            extraction_dir.mkdir(parents=True)
            extraction = extraction_dir / f"v{batch.EXTRACTION_VERSION}_{digest}.json"
            extraction.write_text(json.dumps({
                "pedido": "20004749",
                "data": "30/09/2026",
                "clienteCodigo": "5376",
                "clienteNome": "CASA PINTO HOME LTDA",
                "produtos": [{
                    "tecidoCodigo": "",
                    "tecidoNome": "MICROFIBRA",
                    "estampa": "MV27951",
                    "variante": "A",
                }],
            }), encoding="utf-8")

            with (
                patch('meury_app.batch_order_processor.solicitar_login_lote',
                      return_value={'id': 1, 'usuario': 'admin', 'permissoes': ['admin']}),
                patch.object(batch, "parse_args", return_value=SimpleNamespace(
                    projeto=str(project), codex="", atualizar_indice=None,
                )),
                patch.object(batch, "load_local_environment"),
                patch.object(batch, "load_config", return_value=config),
                patch("builtins.input", return_value=answer) as prompt,
                patch.object(batch, "prepare_daily_index", return_value=(
                    order_index, [source.parent], True,
                )) as prepare,
                patch.object(batch, "ensure_daily_index") as ensure,
                patch.object(batch.indexer, "load_index_payload", return_value={
                    "index": order_index,
                }) as load_saved,
                patch.object(batch.indexer, "build_index", side_effect=AssertionError(
                    "O teste não deve varrer o catálogo real."
                )) as build,
                patch.object(batch.indexer, "update_index_incremental", side_effect=AssertionError(
                    "O teste não deve atualizar o catálogo real."
                )) as incremental,
                patch.object(batch, "extract_with_fallback", side_effect=AssertionError(
                    "A extração salva deve evitar chamadas externas."
                )) as extract,
                patch.object(batch, "run_creator", wraps=batch.run_creator) as creator,
                redirect_stdout(io.StringIO()),
            ):
                exit_code = batch.main()

            self.assertEqual(exit_code, 0)
            prompt.assert_called_once()
            creator.assert_called_once()
            self.assertIs(creator.call_args.args[3], order_index)
            extract.assert_not_called()
            ensure.assert_not_called()
            build.assert_not_called()
            incremental.assert_not_called()
            if update_index:
                prepare.assert_called_once_with()
                load_saved.assert_not_called()
            else:
                prepare.assert_not_called()
                load_saved.assert_called_once_with([source.parent])
            self.assertFalse(pdf.exists())
            self.assertTrue((input_dir / "concluido" / pdf.name).is_file())
            copies = list(output.rglob("MV27951-A.JPG"))
            self.assertEqual(len(copies), 1)
            self.assertEqual(copies[0].read_bytes(), source.read_bytes())

    def test_no_reuses_saved_index_and_separates_cached_order(self):
        self._assert_cached_order_is_separated(answer="N", update_index=False)

    def test_yes_preserves_daily_index_preparation_before_separation(self):
        self._assert_cached_order_is_separated(answer="S", update_index=True)

    def test_no_with_empty_input_does_not_prepare_or_load_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch('meury_app.batch_order_processor.solicitar_login_lote',
                      return_value={'id': 1, 'usuario': 'admin', 'permissoes': ['admin']}),
                patch.object(batch, "parse_args", return_value=SimpleNamespace(
                    projeto=temporary, codex="", atualizar_indice=None,
                )),
                patch.object(batch, "load_local_environment"),
                patch("builtins.input", return_value="N"),
                patch.object(batch, "load_order_context") as load_context,
                patch.object(batch, "ensure_daily_index") as ensure,
                patch.object(batch, "prepare_daily_index") as prepare,
                redirect_stdout(io.StringIO()),
            ):
                exit_code = batch.main()
        self.assertEqual(exit_code, 0)
        load_context.assert_not_called()
        ensure.assert_not_called()
        prepare.assert_not_called()

    def test_yes_with_empty_input_keeps_daily_index_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch('meury_app.batch_order_processor.solicitar_login_lote',
                      return_value={'id': 1, 'usuario': 'admin', 'permissoes': ['admin']}),
                patch.object(batch, "parse_args", return_value=SimpleNamespace(
                    projeto=temporary, codex="", atualizar_indice=True,
                )),
                patch.object(batch, "load_local_environment"),
                patch.object(batch, "load_order_context") as load_context,
                patch.object(batch, "ensure_daily_index") as ensure,
                redirect_stdout(io.StringIO()),
            ):
                exit_code = batch.main()
        self.assertEqual(exit_code, 0)
        ensure.assert_called_once_with()
        load_context.assert_not_called()

    def test_missing_saved_index_requires_user_to_choose_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            source = project / "estampas"
            with (
                patch.object(batch, "load_config", return_value={
                    "output_dir": str(project / "saida"),
                    "source_dirs": [str(source)],
                }),
                patch.object(batch.indexer, "load_index_payload", return_value=None),
                patch.object(batch, "prepare_daily_index") as prepare,
                patch.object(batch.indexer, "build_index") as build,
                patch.object(batch.indexer, "update_index_incremental") as incremental,
                redirect_stdout(io.StringIO()),
            ):
                with self.assertRaisesRegex(ValueError, "S para atualizar"):
                    batch.load_order_context(update_index=False)
            prepare.assert_not_called()
            build.assert_not_called()
            incremental.assert_not_called()


if __name__ == "__main__":
    unittest.main()
