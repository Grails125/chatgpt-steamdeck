#!/usr/bin/env bash
set -euo pipefail
release=v0.1.2
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ! -f "$source_dir/lib/deck.py" ]]; then
  work=$(mktemp -d -t chatgpt-steamdeck-install.XXXXXXXX)
  trap 'rm -rf -- "$work"' EXIT
  curl --fail --location --proto '=https' --proto-redir '=https' \
    "https://github.com/Grails125/chatgpt-steamdeck/archive/refs/tags/${release}.tar.gz" -o "$work/source.tar.gz"
  tar -xzf "$work/source.tar.gz" -C "$work"
  bash "$work/chatgpt-steamdeck-${release#v}/install.sh" "$@"
  exit
fi
command -v python3 >/dev/null || { echo 'Python 3.11+ is required.' >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
[[ $(id -u) != 0 ]] || { echo 'Run as your normal Deck user, not root.' >&2; exit 1; }
python3 "$source_dir/lib/deck.py" doctor
if [[ ${1:-} == --check ]]; then exit; fi
if [[ $# != 0 ]]; then echo 'Usage: bash install.sh [--check]' >&2; exit 2; fi
python3 "$source_dir/lib/install.py"
"$HOME/.local/bin/chatgpt-deck" install
systemctl --user daemon-reload
systemctl --user enable --now chatgpt-deck-update.timer
printf '\nInstalled. Open ChatGPT (Steam Deck) from the application menu.\nAdd that shortcut to Steam; leave Proton compatibility disabled.\n'
