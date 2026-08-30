from pathlib import Path
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import meury_app.config as config_module


class AppDataDirectoryTest(unittest.TestCase):
    def test_migrates_data_and_persists_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = root / "atual"
            destination = root / "nova"
            location_file = root / "bootstrap" / "data_location.json"
            current.mkdir()
            (current / "config.json").write_text("{}", encoding="utf-8")
            (current / "previews").mkdir()
            (current / "previews" / "amostra.webp").write_bytes(b"preview")
            database = current / "estado_indexador.sqlite3"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE teste (valor TEXT)")
                connection.execute("INSERT INTO teste VALUES ('preservado')")

            with patch.object(config_module, "APP_DIR", current), \
                 patch.object(config_module, "DEFAULT_APP_DIR", location_file.parent), \
                 patch.object(config_module, "APP_DIR_LOCATION_FILE", location_file), \
                 patch.dict("os.environ", {}, clear=True):
                selected = config_module.select_app_data_dir(destination)

            self.assertEqual(selected, destination.resolve())
            self.assertTrue((destination / "config.json").is_file())
            self.assertEqual(
                (destination / "previews" / "amostra.webp").read_bytes(), b"preview"
            )
            with closing(sqlite3.connect(destination / "estado_indexador.sqlite3")) as connection:
                self.assertEqual(
                    connection.execute("SELECT valor FROM teste").fetchone()[0],
                    "preservado",
                )
            self.assertEqual(
                json.loads(location_file.read_text(encoding="utf-8"))["path"],
                str(destination.resolve()),
            )

    def test_rejects_non_empty_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = root / "atual"
            destination = root / "ocupada"
            current.mkdir()
            destination.mkdir()
            (destination / "arquivo.txt").touch()
            with patch.object(config_module, "APP_DIR", current), \
                 patch.dict("os.environ", {}, clear=True), \
                 self.assertRaisesRegex(ValueError, "pasta vazia"):
                config_module.select_app_data_dir(destination)


if __name__ == "__main__":
    unittest.main()
