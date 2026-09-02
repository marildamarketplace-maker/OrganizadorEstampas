import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from meury_app.config import load_config
from meury_app.ui import App


class SourceDirectoriesTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.config_file = self.root / "config.json"
        self.stack.enter_context(patch("meury_app.config.CONFIG_FILE", self.config_file))
        self.stack.enter_context(patch("meury_app.config.ensure_app_dir"))
        self.stack.enter_context(patch.dict("os.environ", {"ORIGINAL_IMAGES_PATH": ""}))
        self.first = self.root / "primeira"
        self.second = self.root / "segunda"
        self.first.mkdir()
        self.second.mkdir()

    def app(self, sources):
        app = App.__new__(App)
        app.source_dirs = [str(source) for source in sources]
        app.config = {"source_dirs": list(app.source_dirs)}
        for attribute in (
            "excel_var", "input_mode_var", "output_var", "collector_output_var",
        ):
            setattr(app, attribute, Mock(get=Mock(return_value="")))
        app.semantic_enabled_var = Mock(get=Mock(return_value=False))
        app.collector_extension_vars = {}
        app.collector_source_dirs = []
        app.source_list = Mock()
        app.index_status_var = Mock()
        app.index = {"old": ["old"]}
        app.search_engine = app.semantic_engine = app.visual_engine = Mock()
        return app

    def test_adds_second_folder_and_persists_both_in_order(self):
        app = self.app([self.first])
        with patch("meury_app.ui.filedialog.askdirectory", return_value=str(self.second)):
            app.select_source()
        expected = [str(self.first), str(self.second)]
        self.assertEqual(app.source_dirs, expected)
        self.assertEqual(load_config()["source_dirs"], expected)
        self.assertEqual(load_config()["original_images_path"], str(self.first))
        self.assertEqual(app.index, {})
        self.assertIsNone(app.search_engine)
        self.assertIsNone(app.semantic_engine)
        self.assertIsNone(app.visual_engine)

    def test_startup_keeps_all_saved_folders_despite_environment_root(self):
        app = self.app([self.first, self.second])
        app._save_paths()
        with ExitStack() as patches:
            patches.enter_context(patch.dict("os.environ", {"ORIGINAL_IMAGES_PATH": "/old"}))
            for target in ("tk.Tk", "tk.StringVar", "tk.BooleanVar", "ThumbnailCache"):
                patches.enter_context(patch("meury_app.ui." + target))
            for method in (
                "_build_style", "_build_ui", "_try_load_saved_index",
                "_refresh_semantic_status", "_refresh_visual_status",
                "_refresh_catalog_statistics",
            ):
                patches.enter_context(patch.object(App, method))
            restarted = App()
        self.assertEqual(restarted.source_dirs, [str(self.first), str(self.second)])
        self.assertEqual(restarted.root_path_error, "")

    def test_duplicate_path_or_cancel_does_not_change_configuration(self):
        app = self.app([self.first])
        for selected in (str(self.first / ".." / "primeira"), ""):
            with self.subTest(selected=selected), \
                 patch("meury_app.ui.filedialog.askdirectory", return_value=selected):
                app.select_source()
            self.assertEqual(app.source_dirs, [str(self.first)])
            self.assertFalse(self.config_file.exists())
            self.assertEqual(app.index, {"old": ["old"]})

    def test_remove_first_folder_updates_legacy_root_and_persisted_list(self):
        app = self.app([self.first, self.second])
        app.source_list.curselection.return_value = (0,)
        app.remove_selected_sources()
        config = load_config()
        self.assertEqual(config["source_dirs"], [str(self.second)])
        self.assertEqual(config["original_images_path"], str(self.second))

    def test_remove_all_folders_is_preserved_even_with_environment_default(self):
        app = self.app([self.first, self.second])
        app.source_list.curselection.return_value = (0, 1)
        app.remove_selected_sources()
        with patch.dict("os.environ", {"ORIGINAL_IMAGES_PATH": str(self.first)}):
            config = load_config()
        self.assertEqual(config["source_dirs"], [])
        self.assertEqual(config["original_images_path"], "")

    def test_legacy_and_environment_defaults_migrate_to_list(self):
        for legacy in ({"source_dir": str(self.first)},
                       {"original_images_path": str(self.first)}, {}):
            with self.subTest(legacy=legacy):
                self.config_file.write_text(json.dumps(legacy), encoding="utf-8")
                with patch.dict("os.environ", {"ORIGINAL_IMAGES_PATH": str(self.first)}):
                    self.assertEqual(load_config()["source_dirs"], [str(self.first)])

    def test_first_run_uses_environment_default(self):
        with patch.dict("os.environ", {"ORIGINAL_IMAGES_PATH": str(self.first)}):
            self.assertEqual(load_config()["source_dirs"], [str(self.first)])

    def test_validation_reports_unavailable_second_folder_without_dropping_it(self):
        app = self.app([self.first, self.second])
        self.second.rmdir()
        app._validate_source_paths()
        self.assertIn(str(self.second), app.root_path_error)
        self.assertEqual(app.source_dirs, [str(self.first), str(self.second)])


if __name__ == "__main__":
    unittest.main()
