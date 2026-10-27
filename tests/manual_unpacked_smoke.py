"""Opt-in native launch/IPC/exit smoke test with a mount-isolated home.

Requires bwrap and an X11 session. Never reads the real user's profile or stops
the working client. Uses the installed unpacked payload read-only.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time


def main():
    payload = (Path.home() / '.local/share/chatgpt-steamdeck/current').resolve(strict=True)
    state = Path.home() / '.local/state/chatgpt-steamdeck'
    with tempfile.TemporaryDirectory(prefix='unpacked-smoke-', dir=state) as temporary:
        scratch = Path(temporary)
        (scratch / 'tmp').mkdir()
        identity = scratch.stat()
        target = '/tmp/chatgpt-smoke-payload'
        command = ['bwrap', '--unshare-user', '--unshare-pid', '--ro-bind', '/', '/',
                   '--proc', '/proc', '--dev-bind', '/dev', '/dev',
                   '--bind', str(scratch / 'tmp'), '/tmp',
                   '--ro-bind', '/tmp/.X11-unix', '/tmp/.X11-unix',
                   '--bind', str(scratch), str(Path.home()),
                   '--ro-bind', str(payload), target, '--chdir', str(Path.home()),
                   '--', target + '/ChatGPT', '--class=chatgpt-migration-smoke', '--ozone-platform=x11']
        env = os.environ.copy()
        env['CODEX_LINUX_RENDERING_MODE'] = 'x11-gpu'
        env['ELECTRON_OZONE_PLATFORM_HINT'] = 'x11'
        for key in ('LD_PRELOAD', 'LD_LIBRARY_PATH', 'STEAM_RUNTIME', 'STEAM_RUNTIME_LIBRARY_PATH'):
            env.pop(key, None)
        # Normal HOME and Codex defaults resolve inside the isolated mount tree.
        for key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME'):
            env.pop(key, None)
        log = (state / 'unpacked-smoke.log').open('w')
        process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
        primary = None

        def isolated_pids():
            found = []
            for directory in Path('/proc').iterdir():
                if not directory.name.isdigit():
                    continue
                try:
                    if directory.stat().st_uid != os.getuid():
                        continue
                    home = (directory / 'root' / Path.home().relative_to('/')).stat()
                    if (home.st_dev, home.st_ino) == (identity.st_dev, identity.st_ino):
                        found.append(int(directory.name))
                except OSError:
                    pass
            return found

        try:
            deadline = time.monotonic() + 90
            stable = None
            duplicate_checked = False
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'Native startup exited early: {process.returncode}')
                result = subprocess.run(['xdotool', 'search', '--onlyvisible', '--class',
                                         'codex-desktop|chatgpt-migration-smoke'], capture_output=True, text=True)
                mapping = {}
                for pid in isolated_pids():
                    try:
                        status = Path(f'/proc/{pid}/status').read_text()
                        nspid = next(line.split()[-1] for line in status.splitlines() if line.startswith('NSpid:'))
                        mapping[int(nspid)] = pid
                        mapping[pid] = pid
                    except (OSError, StopIteration):
                        pass
                primary = None
                for window in result.stdout.split():
                    try:
                        inner_pid = int(subprocess.check_output(['xdotool', 'getwindowpid', window], text=True))
                        if inner_pid in mapping:
                            primary = mapping[inner_pid]
                            break
                    except (subprocess.CalledProcessError, ValueError):
                        pass
                if primary:
                    stable = stable or time.monotonic()
                    if not duplicate_checked and time.monotonic() - stable >= 8:
                        second = subprocess.run(command, env=env, stdout=log, stderr=log, timeout=20)
                        if second.returncode != 0 or primary not in isolated_pids():
                            raise RuntimeError('Native second-instance forwarding failed')
                        duplicate_checked = True
                    if duplicate_checked and time.monotonic() - stable >= 60:
                        break
                else:
                    stable = None
                time.sleep(2)
            else:
                raise RuntimeError('No stable window for 60 seconds')
            os.kill(primary, signal.SIGTERM)
            process.wait(timeout=15)
            time.sleep(8)  # Observe helper exit without removing mapped files.
            print(json.dumps({'stable_window_seconds': 60, 'duplicate_ipc': 'passed',
                              'primary_pid': primary, 'primary_exit': process.returncode,
                              'remaining_test_processes': isolated_pids()}), flush=True)
        finally:
            for pid in isolated_pids():
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
            for pid in isolated_pids():
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            log.close()


if __name__ == '__main__':
    main()
