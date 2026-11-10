#!/bin/bash
# Removes exactly what install.sh wrote.
set -euo pipefail

SUPPORT_DIR="$HOME/Library/Application Support/spoofdpi-toggle"
LAUNCHER="$SUPPORT_DIR/host.sh"
MANIFEST="$HOME/Library/Application Support/Mozilla/NativeMessagingHosts/spoofdpi_toggle.json"

for file in "$MANIFEST" "$LAUNCHER"; do
    if [ -e "$file" ]; then
        rm -f "$file"
        echo "Removed $file"
    fi
done
if [ -d "$SUPPORT_DIR" ] && rmdir "$SUPPORT_DIR" 2>/dev/null; then
    echo "Removed $SUPPORT_DIR"
fi
