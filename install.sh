#!/usr/bin/env bash
# CAE Node one-line installer for Linux / macOS — works on a fresh machine.
# It installs Python if missing, installs dependencies, downloads the node,
# opens the local interface in a browser, and starts the node.
#
#   curl -fsSL https://raw.githubusercontent.com/omarperso/cae/main/install.sh | bash -s -- "My Laptop"
#
# The first launch prints (and shows in the browser) a 6-digit pairing code —
# read it out to your main Claude.
set -e

NAME="${1:-$(hostname)}"
RAW="https://raw.githubusercontent.com/omarperso/cae/main"
DIR="$HOME/.cae"
mkdir -p "$DIR"

echo ">> CAE Node install for: $NAME"

# --- 1. Make sure Python 3 exists; install it if not -------------------------
need_python() {
  command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1
}
if ! need_python; then
  echo ">> Python 3 not found — installing it..."
  if   command -v apt-get >/dev/null 2>&1; then sudo apt-get update -y && sudo apt-get install -y python3 python3-pip
  elif command -v dnf     >/dev/null 2>&1; then sudo dnf install -y python3 python3-pip
  elif command -v pacman  >/dev/null 2>&1; then sudo pacman -Sy --noconfirm python python-pip
  elif command -v zypper  >/dev/null 2>&1; then sudo zypper install -y python3 python3-pip
  elif command -v brew    >/dev/null 2>&1; then brew install python
  else echo "Could not auto-install Python. Please install Python 3 and re-run." >&2; exit 1
  fi
fi
PY="$(command -v python3 || command -v python)"

# --- 2. Dependencies ---------------------------------------------------------
echo ">> Installing Python packages..."
"$PY" -m pip install --quiet --user --upgrade pip 2>/dev/null || true
"$PY" -m pip install --quiet --user zeroconf mss pyautogui requests

# --- 3. Download the node ----------------------------------------------------
echo ">> Downloading node..."
curl -fsSL "$RAW/node/cae_node.py" -o "$DIR/cae_node.py"

# --- 4. Open the local interface in a browser --------------------------------
URL="http://localhost:8765/"
( sleep 2
  if   command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
  elif command -v open     >/dev/null 2>&1; then open "$URL"
  fi ) >/dev/null 2>&1 &

# --- 5. Run ------------------------------------------------------------------
echo ">> Starting CAE Node. Interface: $URL"
echo "   (Stop: Ctrl+C.  New pairing code later: press Enter in this window.)"
exec "$PY" "$DIR/cae_node.py" --name "$NAME" --pair
