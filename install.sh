#!/bin/bash
# Registers the SpoofDPI Toggle native messaging host for Firefox (current user only).
# Optional environment:
#   SPOOFDPI_BIN   absolute path to spoofdpi (default: found in PATH, then /opt/homebrew/bin/spoofdpi)
#   SPOOFDPI_ARGS  spoofdpi tuning flags that replace the built-in ones, e.g. "--https-split-mode sni"
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT_DIR="$HOME/Library/Application Support/spoofdpi-toggle"
LAUNCHER="$SUPPORT_DIR/host.sh"
MANIFEST_DIR="$HOME/Library/Application Support/Mozilla/NativeMessagingHosts"
MANIFEST="$MANIFEST_DIR/spoofdpi_toggle.json"
EXTENSION_ID="spoofdpi-toggle@oleonov"

if [ -x /usr/bin/python3 ]; then
    PYTHON=/usr/bin/python3
else
    PYTHON="$(command -v python3 || true)"
fi
if [ -z "$PYTHON" ]; then
    echo "error: python3 not found (expected /usr/bin/python3)" >&2
    exit 1
fi

SPOOFDPI="${SPOOFDPI_BIN:-}"
if [ -n "$SPOOFDPI" ]; then
    if [ "${SPOOFDPI#/}" = "$SPOOFDPI" ] || [ ! -x "$SPOOFDPI" ]; then
        echo "error: SPOOFDPI_BIN must be an absolute path to an executable: $SPOOFDPI" >&2
        exit 1
    fi
else
    SPOOFDPI="$(command -v spoofdpi || true)"
    if [ -z "$SPOOFDPI" ] && [ -x /opt/homebrew/bin/spoofdpi ]; then
        SPOOFDPI=/opt/homebrew/bin/spoofdpi
    fi
fi
if [ -z "$SPOOFDPI" ]; then
    echo "error: spoofdpi not found in PATH or at /opt/homebrew/bin/spoofdpi (brew install spoofdpi)" >&2
    exit 1
fi
ARGS="${SPOOFDPI_ARGS:-}"

mkdir -p "$SUPPORT_DIR" "$MANIFEST_DIR"

{
    echo '#!/bin/bash'
    echo "export SPOOFDPI_BIN=$(printf '%q' "$SPOOFDPI")"
    if [ -n "$ARGS" ]; then
        echo "export SPOOFDPI_ARGS=$(printf '%q' "$ARGS")"
    fi
    echo "exec $(printf '%q' "$PYTHON") -I $(printf '%q' "$REPO_DIR/host/spoofdpi_toggle_host.py")"
} > "$LAUNCHER"
chmod 755 "$LAUNCHER"

"$PYTHON" -I -c '
import json, sys
manifest = {
    "name": "spoofdpi_toggle",
    "description": "Starts and stops SpoofDPI for the SpoofDPI Toggle extension",
    "path": sys.argv[2],
    "type": "stdio",
    "allowed_extensions": [sys.argv[3]],
}
with open(sys.argv[1], "w") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
' "$MANIFEST" "$LAUNCHER" "$EXTENSION_ID"

echo "Wrote $LAUNCHER (python: $PYTHON, spoofdpi: $SPOOFDPI, args: ${ARGS:-built-in})"
echo "Wrote $MANIFEST"
