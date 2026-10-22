#!/usr/bin/env bash
# Example --notify-cmd target: reads the alert text on stdin and prints it with a prefix.
# Swap in your own sender (mail, a chat webhook, a push service).
set -euo pipefail
while IFS= read -r line; do printf '[heartbeat-alert] %s\n' "$line"; done
