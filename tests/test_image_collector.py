from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from meury_app.image_collector import CONFLICT_DETAILS_LIMIT, collect_images


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

    def test_reports_search_progress_every_thousand_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "design"
            output = root / "saida"
            folder = source / "7751"
            folder.mkdir(parents=True)
            for number in range(1001):
                (folder / f"{number}.png").write_bytes(b"")

            updates = []
            collect_images(
                [source],
                output,
                {".png"},
                progress_callback=lambda current, total, message: updates.append(
                    (current, total, message)
                ),
            )

            search_updates = [
                update for update in updates
                if update[1] == 0 and "até agora" in update[2]
            ]
            self.assertEqual(len(search_updates), 1)
            self.assertEqual(search_updates[0][0], 1000)
            self.assertIn("1,000 encontradas", search_updates[0][2])

    def test_limits_existing_file_details_and_throttles_copy_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "design"
            output = root / "saida"
            source_folder = source / "7751"
            output_folder = output / "7751"
            source_folder.mkdir(parents=True)
            output_folder.mkdir(parents=True)
            total = CONFLICT_DETAILS_LIMIT + 5
            for number in range(total):
                name = f"{number}.png"
                (source_folder / name).write_bytes(b"origem")
                (output_folder / name).write_bytes(b"destino")

            updates = []
            result = collect_images(
                [source],
                output,
                {".png"},
                progress_callback=lambda current, total, message: updates.append(
                    (current, total, message)
                ),
            )

            self.assertEqual(result.skipped, total)
            self.assertEqual(len(result.conflicts), CONFLICT_DETAILS_LIMIT)
            self.assertEqual(result.conflicts_omitted, 5)
            copy_updates = [update for update in updates if update[1] == total]
            self.assertEqual([update[0] for update in copy_updates], [100, total])

    def test_reports_found_and_pending_sizes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "design" / "7751"
            output = root / "saida"
            source.mkdir(parents=True)
            (source / "nova.png").write_bytes(b"a" * 2048)
            (source / "existente.png").write_bytes(b"b" * 1024)
            existing = output / "7751" / "existente.png"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"anterior")

            result = collect_images(
                [source.parents[0]],
                output,
                {".png"},
            )

            self.assertEqual(result.found_bytes, 3072)
            self.assertEqual(result.planned_count, 1)
            self.assertEqual(result.planned_bytes, 2048)
            self.assertEqual(result.copied_bytes, 2048)

    def test_requires_confirmation_before_copying_when_requested(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "design" / "7751"
            output = root / "saida"
            source.mkdir(parents=True)
            (source / "imagem.png").write_bytes(b"imagem")
            confirmation_data = []

            result = collect_images(
                [source.parents[0]],
                output,
                {".png"},
                confirm_callback=lambda *data: confirmation_data.append(data) or False,
            )

            self.assertEqual(confirmation_data, [(1, 6, 1, 6)])
            self.assertTrue(result.cancelled)
            self.assertTrue(result.declined)
            self.assertEqual(result.copied, 0)
            self.assertFalse((output / "7751" / "imagem.png").exists())

    def test_can_cancel_without_removing_already_copied_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "design"
            output = root / "saida"
            folder = source / "7751"
            folder.mkdir(parents=True)
            for number in range(10):
                (folder / f"{number}.png").write_bytes(b"imagem")

            cancel_event = threading.Event()
            from meury_app import image_collector

            original_copy = image_collector.shutil.copy2

            def copy_then_cancel(source_file, destination):
                copied = original_copy(source_file, destination)
                cancel_event.set()
                return copied

            with patch.object(
                image_collector.shutil,
                "copy2",
                side_effect=copy_then_cancel,
            ):
                result = collect_images(
                    [source],
                    output,
                    {".png"},
                    cancel_callback=cancel_event.is_set,
                )

            self.assertTrue(result.cancelled)
            self.assertEqual(result.copied, 1)
            self.assertEqual(result.processed, 1)
            self.assertEqual(len(list(output.rglob("*.png"))), 1)

    def test_can_cancel_during_search(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "design"
            output = root / "saida"
            folder = source / "7751"
            folder.mkdir(parents=True)
            for number in range(10):
                (folder / f"{number}.png").write_bytes(b"imagem")

            checks = 0

            def cancel_during_search():
                nonlocal checks
                checks += 1
                return checks > 5

            result = collect_images(
                [source],
                output,
                {".png"},
                cancel_callback=cancel_during_search,
            )

            self.assertTrue(result.cancelled)
            self.assertEqual(result.copied, 0)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
