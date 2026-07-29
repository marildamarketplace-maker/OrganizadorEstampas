from pathlib import Path
import tempfile
import unittest

from meury_app.image_collector import collect_images


class ImageCollectorTest(unittest.TestCase):
    def test_copies_selected_formats_using_immediate_parent_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "design"
            output = root / "saida"
            png = source / "clientes" / "7751" / "7751-A.png"
            jpeg = source / "outra" / "8800" / "8800-A.jpeg"
            pdf = source / "documentos" / "9900" / "9900-A.pdf"
            for path in (png, jpeg, pdf):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(path.suffix.encode())

            result = collect_images(
                [source],
                output,
                {".png", ".jpeg"},
            )

            self.assertEqual(result.found, 2)
            self.assertEqual(result.copied, 2)
            self.assertEqual(result.skipped, 0)
            self.assertEqual(
                (output / "7751" / "7751-A.png").read_bytes(),
                b".png",
            )
            self.assertEqual(
                (output / "8800" / "8800-A.jpeg").read_bytes(),
                b".jpeg",
            )
            self.assertFalse((output / "9900" / "9900-A.pdf").exists())

    def test_does_not_overwrite_conflicting_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_a = root / "entrada-a" / "7751" / "7751-A.png"
            source_b = root / "entrada-b" / "7751" / "7751-A.png"
            output = root / "saida"
            for path, content in ((source_a, b"a"), (source_b, b"b")):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

            result = collect_images(
                [source_a.parents[1], source_b.parents[1]],
                output,
                {".png"},
            )

            self.assertEqual(result.found, 2)
            self.assertEqual(result.copied, 1)
            self.assertEqual(result.skipped, 1)
            self.assertEqual(len(result.conflicts), 1)
            self.assertEqual(
                (output / "7751" / "7751-A.png").read_bytes(),
                b"a",
            )

    def test_excludes_output_when_it_is_inside_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "design"
            image = source / "7751" / "7751-A.jpg"
            output = source / "resultado"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"imagem")
            existing_output = output / "antiga" / "antiga.jpg"
            existing_output.parent.mkdir(parents=True)
            existing_output.write_bytes(b"antiga")

            result = collect_images([source], output, {"jpg"})

            self.assertEqual(result.found, 1)
            self.assertTrue((output / "7751" / "7751-A.jpg").exists())

    def test_does_not_repeat_file_from_overlapping_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "design"
            design_folder = source / "7751"
            image = design_folder / "7751-A.png"
            output = Path(temporary) / "saida"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"imagem")

            result = collect_images(
                [source, design_folder],
                output,
                {".png"},
            )

            self.assertEqual(result.found, 1)
            self.assertEqual(result.copied, 1)
            self.assertEqual(result.skipped, 0)


if __name__ == "__main__":
    unittest.main()
