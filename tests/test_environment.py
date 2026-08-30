from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from meury_app.environment import load_local_environment
from meury_app.environment import environment_file_candidates


class EnvironmentTest(unittest.TestCase):
    def test_frozen_app_only_trusts_env_beside_executable(self):
        with patch("meury_app.environment.sys.frozen", True, create=True), \
             patch("meury_app.environment.sys.executable", "/Aplicativo/app.exe"):
            candidates = environment_file_candidates()
        self.assertEqual(candidates, [Path("/Aplicativo/.env")])

    def test_loads_env_from_current_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / ".env"
            env_file.write_text("MEURY_ENV_TEST=carregado\n", encoding="utf-8")
            with patch("meury_app.environment.environment_file_candidates", return_value=[env_file]), \
                 patch.dict(os.environ, {}, clear=True):
                loaded = load_local_environment()
                self.assertEqual(os.environ.get("MEURY_ENV_TEST"), "carregado")
        self.assertEqual(loaded, env_file)

    def test_does_not_override_existing_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / ".env"
            env_file.write_text("MEURY_ENV_TEST=arquivo\n", encoding="utf-8")
            with patch("meury_app.environment.environment_file_candidates", return_value=[env_file]), \
                 patch.dict(os.environ, {"MEURY_ENV_TEST": "sistema"}, clear=True):
                load_local_environment()
                self.assertEqual(os.environ["MEURY_ENV_TEST"], "sistema")


if __name__ == "__main__":
    unittest.main()
