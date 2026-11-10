#!/bin/bash
# Build for Linux — MUST run on a Linux machine (PyInstaller cannot cross-compile
# from macOS). Output: dist/YT-DLP Downloader/ (onedir bundle; add --onefile for
# a single binary).
#
# Build host requirements (Debian/Ubuntu example):
#   python3 -m venv .venv-linux && source .venv-linux/bin/activate
#   pip install -r requirements.txt pyinstaller
#   sudo apt install -y libxcb-cursor0 libxcb-xinerama0 libxkbcommon0 \
#       libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
#       libxcb-render-util0 libxcb-shape0 libgl1 libglib2.0-0 \
#       libfontconfig1 libdbus-1-3          # Qt6/X11 runtime deps (xcb plugin)
#
# External binaries on the TARGET machine (not bundled by PyInstaller):
#   yt-dlp, ffmpeg, deno  — e.g. sudo apt install ffmpeg; pip install yt-dlp
#
# NOTE: assets/ (icons + styles.qss) is bundled via --add-data; the ':' separator
# is correct on Linux (it would be ';' on Windows). BUILD_COMMIT is bundled the
# same way — the app has no .git to read inside the package, so the build stamps
# the commit in and it appears in the output-panel header.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || exit 1

# Locate pyinstaller: project venv first, then pip --user (~/.local/bin),
# then PATH. The script itself runs under bash regardless of the launching
# shell (fish, zsh, ...), so no venv activation is required.
PYINSTALLER=""
for candidate in \
    "$REPO_ROOT/.venv/bin/pyinstaller" \
    "$HOME/.local/bin/pyinstaller" \
    "$(command -v pyinstaller 2>/dev/null)"
do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        PYINSTALLER="$candidate"
        break
    fi
done
if [ -z "$PYINSTALLER" ]; then
    echo "pyinstaller not found — install it into the project venv:"
    echo "    .venv/bin/pip install pyinstaller"
    exit 1
fi

# Stamp the commit before pyinstaller runs, so it is collected as data, and
# remove it afterwards so it cannot go stale in the tree. A missing hash is
# left blank rather than failing the build: the app then reports the version
# only, and a broken git must not block packaging.
BUILD_COMMIT="$(git rev-parse --short HEAD 2>/dev/null)"
if [ -n "$BUILD_COMMIT" ]; then
    printf '%s\n' "$BUILD_COMMIT" > BUILD_COMMIT
    echo "Bundling commit: $BUILD_COMMIT"
else
    echo "warning: no git commit found — the app will report the version only"
    : > BUILD_COMMIT
fi
trap 'rm -f BUILD_COMMIT' EXIT

"$PYINSTALLER" --name "YT-DLP Downloader" --windowed --icon assets/AppIcon.png \
    --add-data "assets:assets" --add-data "BUILD_COMMIT:." main.py --clean --noconfirm