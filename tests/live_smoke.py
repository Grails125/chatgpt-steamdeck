"""Opt-in fresh-profile GUI smoke test; run only on a Deck with DISPLAY.

Requires a prior install under repository/.test-home. Never uses the real profile.
Terminates only the child application that this test starts.
"""
import os
from pathlib import Path
import subprocess
import sys
import time

repo = Path(__file__).resolve().parents[1]
home = repo / '.test-home'
assert (home / '.local/state/chatgpt-steamdeck/pending.json').is_file()
env = os.environ.copy()
env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'),
           XDG_CACHE_HOME=str(home / '.cache'), XDG_DATA_HOME=str(home / '.local/share'),
           XDG_STATE_HOME=str(home / '.local/state'), CODEX_HOME=str(home / '.codex'))
env.pop('GAMESCOPE_WAYLAND_DISPLAY', None)  # Do not fullscreen over the working session.
process = subprocess.Popen([sys.executable, str(repo / 'lib/deck.py'), 'launch',
                            '--user-data-dir=' + str(home / '.config/smoke-profile')], env=env)
child = None
try:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Launcher exited early: {process.returncode}')
        children = Path(f'/proc/{process.pid}/task/{process.pid}/children').read_text().split()
        for pid in children:
            try:
                executable = Path(f'/proc/{pid}/exe').resolve()
                if executable.is_relative_to(home) and executable.name == 'ChatGPT':
                    child = int(pid)
            except OSError:
                pass
        if not (home / '.local/state/chatgpt-steamdeck/pending.json').exists():
            assert (home / '.local/state/chatgpt-steamdeck/last-success.json').is_file()
            print('PASS: fresh-profile window stayed visible for 60 seconds; artifacts cleaned.', flush=True)
            break
        time.sleep(2)
    else:
        raise RuntimeError('Startup was not confirmed within 120 seconds; inspect isolated launcher.log')
finally:
    if child:
        try:
            os.kill(child, 15)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=5)
