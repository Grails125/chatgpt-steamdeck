#!/usr/bin/env python3
"""Install only this project's scripts and desktop integration, never user data."""
from pathlib import Path
import shutil

source = Path(__file__).resolve().parents[1]
home = Path.home()
target = home / '.local/lib/chatgpt-steamdeck'
for directory in ['lib', 'keys']:
    (target / directory).mkdir(parents=True, exist_ok=True)
    for path in (source / directory).iterdir():
        if path.is_file() and path.suffix in ('.py', '.gpg'):
            shutil.copy2(path, target / directory / path.name)


def write(path, text, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if executable:
        path.chmod(0o755)


write(home / '.local/bin/chatgpt-deck', '''#!/usr/bin/env bash
set -euo pipefail
exec python3 "$HOME/.local/lib/chatgpt-steamdeck/lib/deck.py" "$@"
''', True)

# Desktop Exec has its own quoting syntax (it is not shell syntax).
executable = str(home / '.local/bin/chatgpt-deck')
escaped = executable.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
for filename, label, action in [('chatgpt-steamdeck', 'ChatGPT (Steam Deck)', 'launch'),
                                 ('chatgpt-steamdeck-update', 'ChatGPT Update (Steam Deck)', 'update')]:
    write(home / f'.local/share/applications/{filename}.desktop', f'''[Desktop Entry]
Type=Application
Name={label}
Comment=User-local ChatGPT launcher for Steam Deck
Exec="{escaped}" {action}
Icon=applications-internet
Terminal=false
Categories=Network;
StartupWMClass=codex-desktop
''')
write(home / '.config/systemd/user/chatgpt-deck-update.service', '''[Unit]
Description=Check signed upstream ChatGPT package metadata
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart="%h/.local/bin/chatgpt-deck" check
NoNewPrivileges=yes
PrivateTmp=yes
''')
write(home / '.config/systemd/user/chatgpt-deck-update.timer', '''[Unit]
Description=Daily ChatGPT update check

[Timer]
OnBootSec=15min
OnCalendar=daily
Persistent=true
RandomizedDelaySec=15min

[Install]
WantedBy=timers.target
''')
print('Installed launcher and desktop entries:', target)
