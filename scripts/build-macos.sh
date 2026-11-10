#!/bin/bash
# macOS build: windowed .app bundle (→ dist/YT-DLP Downloader.app).
# Same pyinstaller resolution as build-linux.sh (venv → ~/.local/bin → PATH):
# no venv activation required, works from any shell.
#
# The bundle ships no .git, so it cannot run "git rev-parse" at runtime. Stamp
# the commit into BUILD_COMMIT and bundle it with assets/; the app reads it
# through resource_path() and shows it in the output-panel header. Without this
# the packaged app would report a version with no commit.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || exit 1

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

# Stamp the commit. Written before pyinstaller runs so it is picked up as data;
# removed afterwards so it cannot go stale in the working tree. A missing or
# malformed hash is left blank rather than failing the build: the app falls
# back to a version-only header, and a broken git must not block packaging.
BUILD_COMMIT="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null)"
if [ -n "$BUILD_COMMIT" ]; then
    printf '%s\n' "$BUILD_COMMIT" > "$REPO_ROOT/BUILD_COMMIT"
    echo "Bundling commit: $BUILD_COMMIT"
else
    echo "warning: no git commit found — the app will report the version only"
    : > "$REPO_ROOT/BUILD_COMMIT"
fi
trap 'rm -f "$REPO_ROOT/BUILD_COMMIT"' EXIT

"$PYINSTALLER" --name "YT-DLP Downloader" --windowed --icon assets/AppIcon.icns --add-data "assets:assets" --add-data "BUILD_COMMIT:." main.py --clean --noconfirm