#!/usr/bin/env python3
"""User-local, signed-package installer and launcher for Steam Deck."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
DATA = Path.home() / '.local/share/chatgpt-steamdeck'
STATE = Path.home() / '.local/state/chatgpt-steamdeck'
VERSIONS = DATA / 'versions'
CURRENT = DATA / 'current'
BASE = 'https://persistent.oaistatic.com/codex-app-prod/linux/deb'
FINGERPRINT = '3BFA0E4AE8B8CC16A2D9BA684A3B4A566C4660E4'
VERSION = re.compile(r'[0-9]+(?:\.[0-9]+){2,4}')


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def output(args):
    return run(args, capture_output=True, text=True).stdout


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    with temp.open('w') as f:
        json.dump(value, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def lock(name, shared=False):
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / name).open('a') as f:
        fcntl.flock(f, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        yield


def current():
    if not CURRENT.is_symlink():
        if CURRENT.exists():
            raise RuntimeError('current must be a managed symlink')
        return None
    path = CURRENT.resolve()
    if path.parent != VERSIONS or not VERSION.fullmatch(path.name) or not path.is_dir():
        raise RuntimeError('invalid current version path')
    return path.name


def activate(version):
    if not VERSION.fullmatch(version) or not (VERSIONS / version).is_dir():
        raise RuntimeError('invalid version')
    temp = DATA / 'current.next'
    if temp.exists() or temp.is_symlink():
        temp.unlink()
    temp.symlink_to(Path('versions') / version)
    os.replace(temp, CURRENT)


def download(url, path):
    run(['curl', '--fail', '--location', '--proto', '=https', '--proto-redir', '=https',
         '--tlsv1.2', '--connect-timeout', '20', '--max-time', '1800',
         '--retry', '2', '--output', path, url])


def parse_package(text):
    for paragraph in text.split('\n\n'):
        fields = dict(line.split(': ', 1) for line in paragraph.splitlines() if ': ' in line and not line.startswith(' '))
        if fields.get('Package') == 'chatgpt' and fields.get('Architecture') == 'amd64':
            if not VERSION.fullmatch(fields.get('Version', '')):
                raise RuntimeError('unsupported package version')
            if not re.fullmatch(r'pool/main/c/chatgpt/chatgpt_[0-9.]+_amd64\.deb', fields.get('Filename', '')):
                raise RuntimeError('unsafe package URL')
            if not re.fullmatch('[a-f0-9]{64}', fields.get('SHA256', '')) or not fields.get('Size', '').isdigit():
                raise RuntimeError('invalid package hash/size')
            return fields
    raise RuntimeError('amd64 ChatGPT package not found')


def metadata(work):
    release = work / 'Release'
    download(BASE + '/dists/stable/InRelease', work / 'InRelease')
    verified = output(['gpgv', '--status-fd=1', '--keyring', ROOT / 'keys/openai.gpg',
                       '--output', release, work / 'InRelease'])
    if '[GNUPG:] VALIDSIG ' + FINGERPRINT + ' ' not in verified:
        raise RuntimeError('unexpected repository signer')
    section = False
    expected = None
    for line in release.read_text().splitlines():
        if not line.startswith(' '):
            section = line == 'SHA256:'
        parts = line.split()
        if section and len(parts) == 3 and parts[2] == 'main/binary-amd64/Packages':
            expected = parts
    if not expected:
        raise RuntimeError('signed Packages digest missing')
    packages = work / 'Packages'
    download(BASE + '/dists/stable/main/binary-amd64/Packages', packages)
    if packages.stat().st_size != int(expected[1]) or sha(packages) != expected[0]:
        raise RuntimeError('Packages verification failed')
    return parse_package(packages.read_text())


def dependencies():
    if platform.machine() != 'x86_64':
        raise RuntimeError('Only x86_64 SteamOS is supported')
    required = ['curl', 'gpgv', 'ar', 'bsdtar', 'ldd', 'xdotool', 'zenity']
    missing = [name for name in required if not shutil.which(name)]
    if missing:
        raise RuntimeError('Missing dependencies: ' + ', '.join(missing) + '. See README; no system changes were made.')


def validate_payload(path):
    for name in ['ChatGPT', 'resources/app.asar', 'resources/codex']:
        if not (path / name).is_file():
            raise RuntimeError('Payload missing: ' + name)
    result = subprocess.run(['ldd', str(path / 'ChatGPT')], text=True, capture_output=True)
    if result.returncode or 'not found' in result.stdout:
        raise RuntimeError('Missing shared libraries:\n' + result.stdout + result.stderr)
    if not os.access(path / 'ChatGPT', os.X_OK):
        raise RuntimeError('ChatGPT is not executable')


def appimage_offset(image):
    with image.open('rb') as stream:
        header = stream.read(4 * 1024 * 1024)
    if not header.startswith(b'\x7fELF') or header[8:11] != b'AI\x02':
        raise RuntimeError('Unsupported AppImage format')
    # Runtime code also contains the magic bytes. Validate each candidate rather
    # than confusing an embedded string with the real filesystem superblock.
    offset = header.find(b'hsqs')
    while offset >= 0:
        result = subprocess.run(['unsquashfs', '-s', '-o', str(offset), str(image)],
                                capture_output=True)
        if result.returncode == 0:
            return offset
        offset = header.find(b'hsqs', offset + 4)
    raise RuntimeError('No supported SquashFS payload found')


def import_appimage(image, expected_hash):
    """Migrate a previously verified local image without running its FUSE runtime.

    The caller supplies the SHA-256 from their trusted installation record.
    This command deliberately does not download or upgrade the application.
    """
    image = Path(image).resolve(strict=True)
    if not re.fullmatch('[a-f0-9]{64}', expected_hash) or sha(image) != expected_hash:
        raise RuntimeError('Source AppImage does not match the trusted SHA-256')
    if not shutil.which('unsquashfs'):
        raise RuntimeError('unsquashfs is required to migrate an AppImage')
    DATA.mkdir(parents=True, exist_ok=True)
    VERSIONS.mkdir(exist_ok=True)
    with lock('update.lock'), lock('launch.lock'):
        if current() is not None or (STATE / 'pending.json').exists():
            raise RuntimeError('An unpacked installation already exists; migration refused')
        # Locate the ELF AppImage's SquashFS payload without executing the image.
        offset = appimage_offset(image)
        with tempfile.TemporaryDirectory(prefix='import-', dir=DATA) as tmp:
            extracted = Path(tmp) / 'root'
            run(['unsquashfs', '-no-progress', '-o', str(offset), '-d', extracted,
                 image, 'opt/codex-desktop'])
            payload = extracted / 'opt/codex-desktop'
            info = json.loads((payload / '.codex-linux/build-info.json').read_text())
            version = info.get('package_version', '')
            if not VERSION.fullmatch(version):
                raise RuntimeError('Source AppImage version missing or invalid')
            validate_payload(payload)
            if sha(image) != expected_hash:
                raise RuntimeError('Source AppImage changed during extraction')
            destination = VERSIONS / version
            if destination.exists() or destination.is_symlink():
                raise RuntimeError('Version destination already exists; inspect it first')
            package = {'Version': version, 'SHA256': info.get('package_sha256'),
                       'ImportedAppImage': str(image), 'AppImageSHA256': expected_hash}
            atomic_json(payload / 'deck-package.json', package)
            os.rename(payload, destination)
            tx = {'old': None, 'new': version, 'sha256': sha(destination / 'ChatGPT'),
                  'package_sha256': info.get('package_sha256'),
                  'legacy_appimage': str(image), 'legacy_sha256': expected_hash}
            atomic_json(STATE / 'pending.json', tx)
            activate(version)
            print(f'Migrated {version} without FUSE. Original AppImage retained for recovery.')


def install_update(initial=False, yes=False, check=False):
    dependencies()
    DATA.mkdir(parents=True, exist_ok=True)
    VERSIONS.mkdir(exist_ok=True)
    with lock('update.lock'):
        with tempfile.TemporaryDirectory(prefix='metadata-', dir=STATE) as tmp:
            package = metadata(Path(tmp))
        old = current()
        new = package['Version']
        if old == new:
            print('Already current:', new)
            return
        if old and tuple(map(int, old.split('.'))) > tuple(map(int, new.split('.'))):
            raise RuntimeError('Refusing repository downgrade')
        if check:
            print('Update available:', new)
            if shutil.which('notify-send'):
                subprocess.run(['notify-send', 'ChatGPT update available', f'{new} — use ChatGPT Update to install.'])
            return
        pending = STATE / 'pending.json'
        if pending.exists():
            raise RuntimeError('Previous update still awaits successful launch; launch it or run rollback first.')
        if not initial and not yes:
            answer = subprocess.run(['zenity', '--question', '--title=ChatGPT update',
                                     f'--text=Install verified version {new}? Close ChatGPT first. Old files are removed after a successful 60-second launch.'])
            if answer.returncode:
                return
        # Shared launch locks remain held throughout each application session.
        with lock('launch.lock'):
            if shutil.disk_usage(DATA).free < int(package['Size']) * 8 + 512 * 1024**2:
                raise RuntimeError('At least 4 GiB free space is recommended for extraction')
            destination = VERSIONS / new
            if destination.exists():
                raise RuntimeError(f'Unmanaged or interrupted version exists: {destination}; inspect it first.')
            with tempfile.TemporaryDirectory(prefix='build-', dir=DATA) as tmp:
                work = Path(tmp)
                deb = work / 'package.deb'
                download(BASE + '/' + package['Filename'], deb)
                if deb.stat().st_size != int(package['Size']) or sha(deb) != package['SHA256']:
                    raise RuntimeError('Debian package verification failed')
                members = output(['ar', 't', deb]).splitlines()
                member = next((m for m in members if re.fullmatch(r'data\.tar\.(xz|gz|zst)', m)), None)
                if not member:
                    raise RuntimeError('Debian payload missing')
                archive = work / member
                with archive.open('wb') as f:
                    run(['ar', 'p', deb, member], stdout=f)
                listing = output(['bsdtar', '-tf', archive]).splitlines()
                if any(Path(p).is_absolute() or '..' in Path(p).parts for p in listing):
                    raise RuntimeError('Unsafe archive paths')
                extracted = work / 'extracted'
                extracted.mkdir()
                run(['bsdtar', '-xf', archive, '-C', extracted, '--no-same-owner'])
                payload = extracted / 'usr/lib/chatgpt'
                validate_payload(payload)
                os.rename(payload, destination)
                atomic_json(destination / 'deck-package.json', package)
                # Only record a fully staged version. Interrupted activation is recoverable.
                atomic_json(pending, {'old': old, 'new': new, 'sha256': sha(destination / 'ChatGPT'),
                                      'package_sha256': package['SHA256']})
                activate(new)
                # Keep these until launch confirmation, matching the cleanup policy.
                saved = DATA / 'artifacts' / new
                saved.parent.mkdir(exist_ok=True)
                os.rename(work, saved)
                work.mkdir()  # TemporaryDirectory cleans this now-empty directory.
            print(f'Installed {new}. Launch ChatGPT to confirm and clean update artifacts.')


def legacy_image_in_use(image):
    mounts = Path('/proc/mounts').read_text().splitlines()
    return any(len(parts := line.split()) >= 3 and parts[0] == image.name
               and parts[2] == 'fuse.' + image.name for line in mounts) or primary_running(image)


def confirm(version):
    with lock('update.lock'):
        pending = STATE / 'pending.json'
        if not pending.exists():
            return
        tx = json.loads(pending.read_text())
        if tx['new'] != version or current() != version:
            return
        if sha(VERSIONS / version / 'ChatGPT') != tx['sha256']:
            raise RuntimeError('Running version changed; cleanup refused')
        old = tx['old']
        paths = [DATA / 'artifacts' / version]
        if old:
            if not VERSION.fullmatch(old) or old == version:
                raise RuntimeError('Invalid rollback target')
            paths.append(VERSIONS / old)
        for path in paths:
            if path.is_symlink() or path.resolve() != path:
                raise RuntimeError('Cleanup target is a symlink')
        legacy = tx.get('legacy_appimage')
        if legacy:
            legacy = Path(legacy)
            expected = tx.get('legacy_sha256', '')
            if (not legacy.is_absolute() or legacy.is_symlink() or legacy.resolve() != legacy
                    or not re.fullmatch('[a-f0-9]{64}', expected)):
                raise RuntimeError('Invalid legacy AppImage cleanup target')
            if legacy.exists():
                if sha(legacy) != expected:
                    raise RuntimeError('Legacy AppImage changed; retained for inspection')
                if legacy_image_in_use(legacy):
                    raise RuntimeError('Legacy AppImage is still in use; cleanup deferred')
        atomic_json(STATE / 'last-success.json', tx)
        for path in paths:
            if path.exists():
                shutil.rmtree(path)
        if legacy:
            legacy.unlink(missing_ok=True)
        pending.unlink()
        print('Startup confirmed; installer, staging data and managed previous version removed.', flush=True)


def own_window(pid):
    result = subprocess.run(['xdotool', 'search', '--onlyvisible', '--pid', str(pid)],
                            capture_output=True, text=True, timeout=5)
    return result.stdout.split()[0] if result.returncode == 0 and result.stdout.split() else None


def primary_running(binary, proc=Path('/proc'), uid=None):
    """Match our executable and user, including Chromium's flattened cmdline."""
    uid = os.getuid() if uid is None else uid
    for process in proc.iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != uid or Path(os.readlink(process / 'exe')) != binary:
                continue
            args = (process / 'cmdline').read_bytes()
            if not args or re.search(rb'(?:^|[\x00\s])--type=', args):
                continue
            if Path(os.readlink(process / 'exe')) == binary:
                return True
        except (OSError, ValueError):
            continue
    return False


@contextlib.contextmanager
def startup_gate(binary, timeout=10):
    """Yield True for IPC reuse, False for the sole cold-start supervisor.

    Hold the gate for the primary session. A concurrent caller can reuse the
    visible primary, or retry acquiring the gate if the first startup exits.
    Never silently discard URI arguments when a startup remains unobservable.
    """
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'startup.lock').open('a') as gate:
        deadline = time.monotonic() + timeout
        while True:
            acquired = False
            try:
                fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                pass
            if primary_running(binary):
                yield True
                return
            if acquired:
                yield False
                return
            if time.monotonic() >= deadline:
                raise RuntimeError('ChatGPT is still starting; retry opening the link shortly')
            time.sleep(0.1)


def launch(args):
    with lock('launch.lock', shared=True):
        version = current()
        if not version:
            raise RuntimeError('Not installed; run install.sh first')
        env = os.environ.copy()
        for name in ['LD_PRELOAD', 'LD_LIBRARY_PATH', 'STEAM_RUNTIME', 'STEAM_RUNTIME_LIBRARY_PATH']:
            env.pop(name, None)
        env.update(ELECTRON_OZONE_PLATFORM_HINT='x11', CODEX_LINUX_RENDERING_MODE='x11-gpu',
                   CHROME_DESKTOP='chatgpt-steamdeck.desktop')
        # Steam's GTK IM module can double Electron text events in Game Mode.
        # Composition remains in Steam's keyboard; desktop IME settings are kept.
        if env.get('GAMESCOPE_WAYLAND_DISPLAY') or env.get('XDG_CURRENT_DESKTOP') == 'gamescope':
            env['GTK_IM_MODULE'] = 'simple'
        binary = VERSIONS / version / 'ChatGPT'
        log = STATE / 'launcher.log'
        if log.exists() and log.stat().st_size > 5 * 1024**2:
            os.replace(log, log.with_suffix('.log.1'))
        with log.open('a') as f, startup_gate(binary) as reuse:
            print(f'[{time.strftime("%Y-%m-%dT%H:%M:%S%z")}] launch version={version} '
                  f'route={"native-ipc" if reuse else "cold"} argument_count={len(args)}',
                  file=f, flush=True)
            if reuse:
                # Direct payloads have no FUSE lifecycle. Use native IPC without
                # a second fullscreen helper or startup/cleanup observer.
                return subprocess.run(
                    [str(binary), '--class=codex-desktop', '--ozone-platform=x11', *args],
                    env=env, stdout=f, stderr=f).returncode
            process = subprocess.Popen([str(binary), '--class=codex-desktop', '--ozone-platform=x11', *args],
                                       env=env, stdout=f, stderr=f)
            stable = None
            fullscreen = False
            deadline = time.monotonic() + 180
            while process.poll() is None and time.monotonic() < deadline:
                window = own_window(process.pid)
                if window:
                    stable = stable or time.monotonic()
                    if not fullscreen and env.get('GAMESCOPE_WAYLAND_DISPLAY'):
                        subprocess.run(['xdotool', 'key', '--window', window, 'F11'], stdout=f, stderr=f)
                        fullscreen = True
                    if time.monotonic() - stable >= 60:
                        try:
                            confirm(version)
                        except Exception as error:
                            print(f'Cleanup deferred: {error}', file=f, flush=True)
                        break
                else:
                    stable = None
                time.sleep(2)
            code = process.wait()
            print(f'[{time.strftime("%Y-%m-%dT%H:%M:%S%z")}] main-exit version={version} code={code}',
                  file=f, flush=True)
            if code:
                print(f'ChatGPT exited with {code}. See {log}; rollback is available if startup was not confirmed.')
            return code


def rollback():
    with lock('update.lock'), lock('launch.lock'):
        pending = STATE / 'pending.json'
        tx = json.loads(pending.read_text())
        if not tx.get('old'):
            raise RuntimeError('This was the first install; no managed previous version exists')
        activate(tx['old'])
        os.replace(pending, STATE / ('rolled-back-' + tx['new'] + '.json'))
        print('Restored', tx['old'], '; failed version and artifacts retained for inspection.')


def main():
    # Launch arguments belong to Electron, including --help/--yes and URIs.
    if len(sys.argv) > 1 and sys.argv[1] == 'launch':
        return launch(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == 'import-appimage':
        importer = argparse.ArgumentParser(description=import_appimage.__doc__)
        importer.add_argument('image', type=Path)
        importer.add_argument('--sha256', required=True)
        options = importer.parse_args(sys.argv[2:])
        return import_appimage(options.image, options.sha256)
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['install', 'update', 'check', 'launch', 'rollback', 'doctor'])
    parser.add_argument('--yes', action='store_true')
    options, args = parser.parse_known_args()
    if options.command == 'launch':
        return launch(args)
    if args:
        parser.error('unknown arguments: ' + ' '.join(args))
    if options.command == 'doctor':
        dependencies()
        version = current()
        if version:
            validate_payload(VERSIONS / version)
        print('Dependencies OK; installed version:', version or 'none')
    elif options.command == 'rollback':
        rollback()
    else:
        install_update(initial=options.command == 'install', yes=options.yes, check=options.command == 'check')


if __name__ == '__main__':
    try:
        sys.exit(main() or 0)
    except Exception as error:
        print(f'chatgpt-steamdeck: {error}', file=sys.stderr)
        sys.exit(1)
