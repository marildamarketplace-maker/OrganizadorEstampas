"""Per-user installer and crash-safe executable replacement."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import zipfile


def target_path():
    if sys.platform == 'win32':
        return Path(os.environ['LOCALAPPDATA']) / 'Programs' / 'MV Image'
    if sys.platform == 'darwin':
        return Path.home() / 'Applications' / 'MV Image.app'
    raise ValueError('Sistema não suportado.')


def executable(target):
    return target / ('MV Image.exe' if sys.platform == 'win32' else 'Contents/MacOS/MV Image')


def state_dir():
    root = Path.home() / '.mv-image-updates'
    root.mkdir(mode=0o700, exist_ok=True)
    return root


def instance_lock():
    import portalocker
    lock = portalocker.Lock(str(state_dir() / 'instances.lock'), mode='a', flags=portalocker.LOCK_SH | portalocker.LOCK_NB)
    lock.acquire()
    return lock


def write_state(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(value, output)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def extract(package, destination):
    destination = Path(destination).resolve()
    total = 0
    links = []
    with zipfile.ZipFile(package) as archive:
        if len(archive.infolist()) > 100000:
            raise ValueError('Pacote contém arquivos demais.')
        for info in archive.infolist():
            name = info.filename
            parts = name.split('/')
            mode = info.external_attr >> 16
            if '\\' in name or name.startswith('/') or any(p in ('..', '.') or ':' in p for p in parts):
                raise ValueError('Caminho inseguro no pacote.')
            total += info.file_size
            if total > 12 * 1024**3:
                raise ValueError('Pacote expandido excede o limite.')
            output = destination / name
            if any(parent.is_symlink() for parent in output.parents):
                raise ValueError('Caminho atravessa link.')
            if stat.S_ISLNK(mode):
                link = archive.read(info).decode('utf-8')
                resolved = (output.parent / link).resolve()
                root = (destination / parts[0]).resolve()
                if sys.platform != 'darwin' or Path(link).is_absolute() or (resolved != root and root not in resolved.parents):
                    raise ValueError('Link inseguro no pacote.')
                links.append((output, link))
                continue
            if info.is_dir():
                output.mkdir(parents=True, exist_ok=True)
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, output.open('xb') as sink:
                    shutil.copyfileobj(source, sink)
                output.chmod(0o755 if mode & 0o111 else 0o644)
        # No archive writes occur after links are created.
        for output, link in links:
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists() or any(parent.is_symlink() for parent in output.parents):
                raise ValueError('Link conflitante no pacote.')
            output.symlink_to(link)


def backup_data(data, backup):
    backup.mkdir()
    for name in ('estado_indexador.sqlite3', 'config.json'):
        source = data / name
        if not source.exists():
            continue
        if name.endswith('.sqlite3'):
            from contextlib import closing
            with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(backup / name)) as dst:
                src.backup(dst)
        else:
            shutil.copy2(source, backup / name)


def _install_locked(work):
    import portalocker
    from .updates import trust_config, verify_manifest
    from . import __version__
    work = Path(work).resolve()
    if work.parent != state_dir().resolve() or not work.name.startswith('job-'):
        raise ValueError('Diretório de instalação inválido.')
    current = __version__
    installed_record = state_dir() / 'installed-version.json'
    if installed_record.exists() and not (work / 'journal.json').exists():
        from .updates import version
        installed = json.loads(installed_record.read_text())['version']
        if version(installed) > version(current):
            current = installed
    item = verify_manifest((work / 'manifest.json').read_bytes(), trust_config()['public_key'], current)
    if item is None:
        raise ValueError('Atualização não é mais recente.')
    package = work / 'package.zip'
    digest = hashlib.sha256()
    with package.open('rb') as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    if package.stat().st_size != item['size'] or digest.hexdigest() != item['sha256']:
        raise ValueError('Pacote inválido.')
    target = target_path()
    from .config import APP_DIR
    data = APP_DIR.resolve()
    if data == target.resolve() or target.resolve() in data.parents:
        raise ValueError('Dados estão dentro da instalação.')
    old = target.with_name(target.name + '.previous')
    journal = work / 'journal.json'
    if journal.exists():
        with portalocker.Lock(str(state_dir() / 'instances.lock'), mode='a', timeout=120):
            if (work / 'healthy').exists():
                if old.exists():
                    shutil.rmtree(old)
                write_state(journal, {'phase': 'healthy'})
                write_state(installed_record, {'version': item['version']})
            elif old.exists():
                if target.exists():
                    shutil.rmtree(target)
                old.rename(target)
                restore_data(data, work / 'data-backup')
                write_state(journal, {'phase': 'rolled_back'})
        subprocess.Popen([str(executable(target))])
        return
    # Every packaged app holds a shared lock from before login until process exit.
    with portalocker.Lock(str(state_dir() / 'instances.lock'), mode='a', timeout=120):
        staging = work / 'staging'
        staging.mkdir()
        extract(package, staging)
        candidate = staging / target.name
        if not executable(candidate).is_file():
            raise ValueError('Pacote não contém o aplicativo.')
        if shutil.disk_usage(target.parent).free < package.stat().st_size * 2:
            raise ValueError('Espaço insuficiente.')
        # Stage on the destination filesystem so the final rename is atomic.
        incoming = target.with_name(target.name + '.incoming-' + work.name)
        if incoming.exists():
            raise ValueError('Há uma preparação de instalação pendente.')
        shutil.copytree(candidate, incoming, symlinks=True)
        preserve_environment(target)
        backup_data(data, work / 'data-backup')
        old = target.with_name(target.name + '.previous')
        if old.exists():
            raise ValueError('Há uma recuperação pendente.')
        journal = work / 'journal.json'
        write_state(journal, {'phase': 'prepared'})
        target.rename(old)
        try:
            incoming.rename(target)
            write_state(journal, {'phase': 'installed'})
        except BaseException:
            if target.exists():
                shutil.rmtree(target)
            old.rename(target)
            raise
    env = os.environ.copy()
    env['MV_IMAGE_UPDATE_HEALTH'] = str(work)
    process = subprocess.Popen([str(executable(target))], env=env)
    for _ in range(120):
        if (work / 'healthy').exists():
            write_state(journal, {'phase': 'healthy'})
            write_state(installed_record, {'version': item['version']})
            shutil.rmtree(old)
            shutil.rmtree(staging, ignore_errors=True)
            package.unlink(missing_ok=True)
            return
        if process.poll() is not None:
            with portalocker.Lock(str(state_dir() / 'instances.lock'), mode='a', timeout=10):
                # No accepted user operations are possible before the health marker.
                shutil.rmtree(target)
                old.rename(target)
                restore_data(data, work / 'data-backup')
                write_state(journal, {'phase': 'rolled_back'})
            subprocess.Popen([str(executable(target))])
            return
        time.sleep(1)
    # Never replace a running app or restore a database after an ambiguous timeout.
    write_state(journal, {'phase': 'needs_recovery'})


def mark_healthy():
    work = os.environ.pop('MV_IMAGE_UPDATE_HEALTH', '')
    if work:
        path = Path(work).resolve()
        if path.parent != state_dir().resolve() or not path.name.startswith('job-'):
            raise ValueError('Handshake inválido.')
        (path / 'healthy').touch()
        hold_data_lock()


def recover_interrupted():
    """Run before opening databases. An exclusive instance lock forbids concurrent writes."""
    import portalocker
    if os.environ.get('MV_IMAGE_UPDATE_HEALTH'):
        return
    from .config import APP_DIR
    target = target_path()
    old = target.with_name(target.name + '.previous')
    for job in state_dir().glob('job-*'):
        journal = job / 'journal.json'
        if not journal.exists():
            continue
        state = json.loads(journal.read_text())['phase']
        if state in ('healthy', 'rolled_back', 'recovered'):
            continue
        with portalocker.Lock(str(state_dir() / 'instances.lock'), mode='a', timeout=1):
            if (job / 'healthy').exists():
                if old.exists():
                    shutil.rmtree(old)
                write_state(journal, {'phase': 'healthy'})
                continue
            if old.exists():
                # Do not replace the executable of this running process: restart the helper.
                helper = job / ('MV Image Updater.exe' if sys.platform == 'win32' else 'MV Image Updater')
                if not helper.is_file():
                    raise RuntimeError('Atualizador de recuperação ausente: ' + str(job))
                subprocess.Popen([str(helper), str(job)], cwd=job)
                raise SystemExit(0)
            write_state(journal, {'phase': 'recovered'})


def restore_data(data, backup):
    for name in ('estado_indexador.sqlite3', 'config.json'):
        source = backup / name
        if name.endswith('.sqlite3'):
            for suffix in ('-wal', '-shm'):
                (data / (name + suffix)).unlink(missing_ok=True)
        if source.exists():
            shutil.copy2(source, data / name)
        else:
            (data / name).unlink(missing_ok=True)


_DATA_GUARD = None


def hold_data_lock():
    global _DATA_GUARD
    import portalocker
    from .config import APP_DIR
    APP_DIR.mkdir(parents=True, exist_ok=True)
    _DATA_GUARD = portalocker.Lock(str(APP_DIR / '.update-data.lock'), mode='a',
                                  timeout=130, flags=portalocker.LOCK_SH | portalocker.LOCK_NB)
    _DATA_GUARD.acquire()


def install(work):
    import portalocker
    from .config import APP_DIR
    APP_DIR.mkdir(parents=True, exist_ok=True)
    # Covers shared custom data folders as well as local installations.
    with portalocker.Lock(str(state_dir() / 'installer.lock'), mode='a', timeout=1):
        with portalocker.Lock(str(APP_DIR / '.update-data.lock'), mode='a', timeout=120):
            _install_locked(work)



def preserve_environment(target):
    """Keep legacy secrets outside signed application bundles, never in public ZIPs."""
    from .config import DEFAULT_APP_DIR
    source = executable(target).parent / '.env'
    if not source.exists():
        return
    destination = DEFAULT_APP_DIR / '.env'
    DEFAULT_APP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_bytes() != source.read_bytes():
            raise ValueError('Configurações locais conflitantes; unifique os arquivos .env antes de atualizar.')
        return
    # Set permissions before writing secrets (independent of the process umask).
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(source.read_bytes())
        output.flush()
        os.fsync(output.fileno())
