import base64
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.exceptions import InvalidSignature
from meury_app import updates
from meury_app.update_install import extract, backup_data, restore_data
from meury_app.operational_store import ensure_schema, SCHEMA_VERSION


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        self.package = b'archive'
        self.item = {'url': 'https://github.com/org/releases/releases/download/v1/package.zip',
                     'size': len(self.package), 'sha256': hashlib.sha256(self.package).hexdigest()}

    def manifest(self, **changes):
        doc = {'schema': 1, 'version': '1.2.0', 'notes': 'Novidades', 'packages': {'darwin-arm64': self.item}}
        doc.update(changes)
        payload = json.dumps(doc).encode()
        return json.dumps({'payload': base64.b64encode(payload).decode(),
                           'signature': base64.b64encode(self.key.sign(payload)).decode()}).encode()

    def verify(self, raw, current='1.0.0'):
        return updates.verify_manifest(raw, self.public, current, 'darwin', 'arm64')

    def test_signature_platform_and_version(self):
        self.assertEqual(self.verify(self.manifest())['version'], '1.2.0')
        self.assertIsNone(self.verify(self.manifest(), '1.2.0'))
        self.assertIsNone(self.verify(self.manifest(), '2.0.0'))
        with self.assertRaises(KeyError):
            updates.verify_manifest(self.manifest(), self.public, '1.0.0', 'win32', 'x86_64')
        for value in ('1', '1.0.0b', '01.0.0', '../1.0.0'):
            with self.assertRaises(ValueError):
                updates.version(value)

    def test_tampered_signature(self):
        envelope = json.loads(self.manifest())
        envelope['payload'] = base64.b64encode(b'{}').decode()
        with self.assertRaises(InvalidSignature):
            self.verify(json.dumps(envelope))

    def test_url_and_size_restrictions(self):
        for url in ('http://github.com/a', 'https://evil.test/a', 'https://user@github.com/a', 'https://github.com:444/a'):
            with self.assertRaises(ValueError):
                updates.validate_url(url)
        self.item['size'] = updates.MAX_PACKAGE + 1
        with self.assertRaises(ValueError):
            self.verify(self.manifest())

    def test_download_hash_size_cleanup(self):
        for body in (self.package, b'broken', self.package + b'extra'):
            with tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary) / 'package.zip'
                with patch.object(updates, 'open_url', return_value=io.BytesIO(body)):
                    if body == self.package:
                        updates.download(self.item, target)
                        self.assertEqual(target.read_bytes(), body)
                    else:
                        with self.assertRaises(ValueError):
                            updates.download(self.item, target)
                        self.assertFalse(target.exists())
                    self.assertFalse(target.with_suffix('.partial').exists())

    def test_safe_extraction(self):
        for filename in ('../escape', '/absolute', 'x/../../escape', 'C:/escape', 'x\\escape'):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                package = root / 'bad.zip'
                with zipfile.ZipFile(package, 'w') as archive:
                    archive.writestr(filename, 'bad')
                with self.assertRaises(ValueError):
                    extract(package, root / 'out')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with zipfile.ZipFile(root / 'link.zip', 'w') as archive:
                info = zipfile.ZipInfo('link')
                info.external_attr = 0o120777 << 16
                archive.writestr(info, '/outside')
            with self.assertRaises(ValueError):
                extract(root / 'link.zip', root / 'out')

    def test_backup_wal_and_restore_custom_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / 'custom'
            data.mkdir()
            db = data / 'estado_indexador.sqlite3'
            with sqlite3.connect(db) as connection:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('CREATE TABLE preserved (value)')
                connection.execute("INSERT INTO preserved VALUES ('original')")
                connection.commit()
                backup_data(data, Path(temporary) / 'backup')
                connection.execute('DELETE FROM preserved')
            connection.close()
            restore_data(data, Path(temporary) / 'backup')
            with sqlite3.connect(db) as connection:
                self.assertEqual(connection.execute('SELECT value FROM preserved').fetchone()[0], 'original')
            connection.close()

    def test_future_schema_and_transaction_rollback(self):
        with sqlite3.connect(':memory:') as connection:
            connection.execute(f'PRAGMA user_version={SCHEMA_VERSION + 1}')
            with self.assertRaises(RuntimeError):
                ensure_schema(connection)
        with sqlite3.connect(':memory:') as connection:
            ensure_schema(connection)
            connection.rollback()
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='image_state'").fetchone())


class InstallCycleTests(unittest.TestCase):
    def cycle(self, success):
        from meury_app import update_install as installer
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / 'state'
            state.mkdir()
            work = state / 'job-test'
            work.mkdir()
            target = root / 'Applications/MV Image.app'
            binary = target / 'Contents/MacOS/MV Image'
            binary.parent.mkdir(parents=True)
            binary.write_text('old')
            data = root / 'custom-data'
            data.mkdir()
            (data / 'config.json').write_text('original')
            package = work / 'package.zip'
            with zipfile.ZipFile(package, 'w') as archive:
                archive.writestr('MV Image.app/Contents/MacOS/MV Image', 'new')
            (work / 'manifest.json').write_text('{}')
            item = {'version': '1.1.0', 'size': package.stat().st_size, 'sha256': hashlib.sha256(package.read_bytes()).hexdigest()}
            def launch(*args, **kwargs):
                if 'env' in kwargs:
                    (data / 'config.json').write_text('migrated')
                    if success:
                        (work / 'healthy').touch()
                return Mock(poll=lambda: 1)
            with patch.object(installer, 'state_dir', return_value=state), \
                 patch.object(installer, 'target_path', return_value=target), \
                 patch.object(installer.sys, 'platform', 'darwin'), \
                 patch('meury_app.config.APP_DIR', data), \
                 patch.object(updates, 'trust_config', return_value={'public_key': 'test'}), \
                 patch.object(updates, 'verify_manifest', return_value=item), \
                 patch.object(installer.subprocess, 'Popen', side_effect=launch):
                installer.install(work)
            self.assertEqual(binary.read_text(), 'new' if success else 'old')
            self.assertEqual((data / 'config.json').read_text(), 'migrated' if success else 'original')
            self.assertEqual(json.loads((work / 'journal.json').read_text())['phase'], 'healthy' if success else 'rolled_back')
            self.assertFalse(target.with_name(target.name + '.previous').exists())

    def test_successful_update(self):
        self.cycle(True)

    def test_failed_start_rolls_back_data_and_executables(self):
        self.cycle(False)

    def test_shared_instances_block_exclusive_updater(self):
        import portalocker
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / 'lock')
            with portalocker.Lock(path, mode='a', flags=portalocker.LOCK_SH | portalocker.LOCK_NB):
                with self.assertRaises(portalocker.exceptions.LockException):
                    with portalocker.Lock(path, mode='a', timeout=0.05):
                        pass


class PackagingTests(unittest.TestCase):
    def test_preserves_internal_mac_links_and_executable_mode(self):
        from scripts.package_update import package
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'MV Image.app'
            binary = source / 'Contents/MacOS/MV Image'
            binary.parent.mkdir(parents=True)
            binary.write_text('binary')
            binary.chmod(0o755)
            try:
                (source / 'Contents/current').symlink_to('MacOS', target_is_directory=True)
            except OSError:
                self.skipTest('Criação de links simbólicos indisponível neste ambiente.')
            archive = root / 'update.zip'
            package(source, archive)
            with patch('meury_app.update_install.sys.platform', 'darwin'):
                extract(archive, root / 'extracted')
            installed = root / 'extracted/MV Image.app/Contents'
            self.assertTrue((installed / 'current').is_symlink())
            self.assertEqual((installed / 'current/MV Image').read_text(), 'binary')
            self.assertTrue(os.access(installed / 'MacOS/MV Image', os.X_OK))

    def test_rejects_environment_and_private_keys_but_allows_ca_certificates(self):
        from scripts.package_update import package
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'app'
            source.mkdir()
            for name in ('.env', '.env.production', 'private.pem', 'state.sqlite3'):
                secret = source / name
                secret.write_text('secret')
                with self.assertRaises(ValueError):
                    package(source, root / 'package.zip')
                secret.unlink()
            ca = source / 'certifi/cacert.pem'
            ca.parent.mkdir()
            ca.write_text('public CA')
            package(source, root / 'package.zip')

    def test_preserves_legacy_environment_privately_and_rejects_conflict(self):
        from meury_app import update_install as installer
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / 'app'
            target.mkdir()
            (target / '.env').write_text('TEST_KEY=value')
            data = root / 'private-data'
            with patch.object(installer, 'executable', return_value=target / 'app.exe'), \
                 patch('meury_app.config.DEFAULT_APP_DIR', data):
                installer.preserve_environment(target)
                self.assertEqual((data / '.env').read_text(), 'TEST_KEY=value')
                import os
                if os.name != 'nt':
                    self.assertEqual((data / '.env').stat().st_mode & 0o777, 0o600)
                (data / '.env').write_text('different')
                with self.assertRaises(ValueError):
                    installer.preserve_environment(target)


class FailureAndGateTests(unittest.TestCase):
    def test_network_failure_propagates_without_partial_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'package.zip'
            with patch.object(updates, 'open_url', side_effect=TimeoutError):
                with self.assertRaises(TimeoutError):
                    updates.download({'url': 'https://github.com/a'}, target)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix('.partial').exists())

    def test_busy_application_does_not_start_installer(self):
        from meury_app.update_ui import UpdateController
        from unittest.mock import Mock
        controller = UpdateController.__new__(UpdateController)
        controller.window = Mock()
        active = Mock(is_alive=lambda: True)
        with patch('meury_app.update_ui.threading.enumerate', return_value=[active]), \
             patch('meury_app.update_ui.messagebox.showinfo') as notice, \
             patch('meury_app.update_ui.subprocess.Popen') as launch:
            controller.install()
        notice.assert_called_once()
        launch.assert_not_called()

    def test_interrupted_install_restores_before_reopening(self):
        from meury_app import update_install as installer
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / 'state'
            work = state / 'job-interrupted'
            work.mkdir(parents=True)
            target = root / 'MV Image.app'
            previous = root / 'MV Image.app.previous'
            for folder, text in ((target, 'new'), (previous, 'old')):
                binary = folder / 'Contents/MacOS/MV Image'
                binary.parent.mkdir(parents=True)
                binary.write_text(text)
            data = root / 'data'
            data.mkdir()
            (data / 'config.json').write_text('migrated')
            backup = work / 'data-backup'
            backup.mkdir()
            (backup / 'config.json').write_text('original')
            (work / 'journal.json').write_text('{"phase":"installed"}')
            (work / 'manifest.json').write_text('{}')
            (work / 'package.zip').write_bytes(b'archive')
            item = {'version': '1.1.0', 'size': 7, 'sha256': hashlib.sha256(b'archive').hexdigest()}
            with patch.object(installer, 'state_dir', return_value=state), \
                 patch.object(installer, 'target_path', return_value=target), \
                 patch.object(installer.sys, 'platform', 'darwin'), \
                 patch('meury_app.config.APP_DIR', data), \
                 patch.object(updates, 'trust_config', return_value={'public_key': 'test'}), \
                 patch.object(updates, 'verify_manifest', return_value=item), \
                 patch.object(installer.subprocess, 'Popen') as launch:
                installer.install(work)
            self.assertEqual((target / 'Contents/MacOS/MV Image').read_text(), 'old')
            self.assertEqual((data / 'config.json').read_text(), 'original')
            launch.assert_called_once()
