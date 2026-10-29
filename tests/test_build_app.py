import subprocess
import unittest
from unittest.mock import patch

import build_app


class BuildAppTests(unittest.TestCase):
    def test_mv_logo_is_included_on_both_platforms(self):
        for platform, separator, extension in [('win32', ';', 'ico'), ('darwin', ':', 'icns')]:
            with self.subTest(platform=platform):
                command = build_app.build_command('python', platform)
                self.assertEqual(command[command.index('--icon') + 1], f'assets/meury-logo.{extension}')
                self.assertIn(f'assets{separator}assets', command)

    def test_missing_packaged_logo_fails_build(self):
        for platform in ('win32', 'darwin'):
            for missing_file in (2, 3):
                with self.subTest(platform=platform, missing_file=missing_file), \
                     patch.object(build_app.sys, 'platform', platform), \
                     patch.object(build_app.Path, 'is_file', side_effect=[True] * missing_file + [False]), \
                     patch.object(build_app.Path, 'exists', return_value=True), \
                     patch.object(build_app.subprocess, 'run'), \
                     patch('sys.stderr') as stderr:
                    self.assertEqual(build_app.main(), 1)
                    self.assertIn('logo obrigatória da MV', ''.join(
                        call.args[0] for call in stderr.write.call_args_list
                    ))

    def test_schema_is_included_on_both_platforms(self):
        for platform, separator in [('win32', ';'), ('darwin', ':')]:
            with self.subTest(platform=platform):
                command = build_app.build_command('python', platform)
                self.assertIn(
                    f'meury_app/batch_order_extraction.schema.json{separator}meury_app',
                    command,
                )

    def test_missing_packaged_schema_fails_build(self):
        for platform in ('win32', 'darwin'):
            with self.subTest(platform=platform), \
                 patch.object(build_app.sys, 'platform', platform), \
                 patch.object(build_app.Path, 'is_file', side_effect=[True, False]), \
                 patch.object(build_app.Path, 'exists', return_value=True), \
                 patch.object(build_app.subprocess, 'run'), \
                 patch('sys.stderr') as stderr:
                self.assertEqual(build_app.main(), 1)
                self.assertIn('esquema obrigatório', ''.join(
                    call.args[0] for call in stderr.write.call_args_list
                ))

    def test_native_interpreter_runs_dependencies_before_packaging(self):
        for platform, relative in [('win32', 'Scripts/python.exe'), ('darwin', 'bin/python')]:
            with self.subTest(platform=platform), \
                 patch.object(build_app.sys, 'platform', platform), \
                 patch.object(build_app.Path, 'is_file', return_value=True), \
                 patch.object(build_app.Path, 'exists', return_value=True), \
                 patch.object(build_app.subprocess, 'run') as run:
                self.assertEqual(build_app.main(), 0)
                python = str(build_app.ROOT / '.venv' / relative)
                self.assertIn('import tkinter', run.call_args_list[0].args[0][-1])
                self.assertEqual(run.call_args_list[1].args[0],
                                 [python, '-m', 'meury_app.dependency_setup', 'core'])
                self.assertEqual(run.call_args_list[2].args[0],
                                 build_app.build_command(python, platform))

    def test_dependency_failure_stops_packaging(self):
        with patch.object(build_app.sys, 'platform', 'win32'), \
             patch.object(build_app.Path, 'is_file', return_value=True), \
             patch.object(build_app.subprocess, 'run',
                          side_effect=subprocess.CalledProcessError(1, 'dependencies')) as run:
            self.assertEqual(build_app.main(), 1)
            run.assert_called_once()

    def test_unsupported_system_does_not_modify_environment(self):
        with patch.object(build_app.sys, 'platform', 'linux'), \
             patch.object(build_app.subprocess, 'run') as run:
            self.assertEqual(build_app.main(), 1)
            run.assert_not_called()
