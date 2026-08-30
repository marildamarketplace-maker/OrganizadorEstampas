from pathlib import Path
import unittest
from unittest.mock import patch

from meury_app.platform_utils import open_with_default_application


class PlatformUtilsTest(unittest.TestCase):
    def test_opens_windows_path_with_startfile(self):
        with patch("meury_app.platform_utils.platform.system", return_value="Windows"), \
             patch("meury_app.platform_utils.os.startfile", create=True) as startfile:
            open_with_default_application(Path("arquivo.txt"))
        startfile.assert_called_once()

    def test_opens_macos_path_without_shell(self):
        with patch("meury_app.platform_utils.platform.system", return_value="Darwin"), \
             patch("meury_app.platform_utils.subprocess.Popen") as popen:
            open_with_default_application(Path("arquivo.txt"))
        command = popen.call_args.args[0]
        self.assertEqual(command[0], "open")
        self.assertTrue(Path(command[1]).is_absolute())


if __name__ == "__main__":
    unittest.main()
