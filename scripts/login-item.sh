#!/usr/bin/env bash
# Install or remove the LaunchAgent that starts the menu bar app at login.
#   scripts/login-item.sh install     # write plist, (re)start now
#   scripts/login-item.sh uninstall   # stop and remove
# The agent restarts the app if it crashes, but not after "Quit" from the menu.
set -euo pipefail

LABEL="local.atv-remote"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Prefer this checkout's venv; fall back to a `uv tool install` on PATH.
BIN="${ATV_REMOTE_BIN:-$ROOT/.venv/bin/atv-remote}"
if [[ ! -x "$BIN" ]] && command -v atv-remote >/dev/null; then
  BIN="$(command -v atv-remote)"
fi
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/atv-remote.log"
DOMAIN="gui/$(id -u)"

unload() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || return 0
  # bootout returns before the job is gone; bootstrapping too early fails (EIO).
  for _ in {1..50}; do
    launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || return 0
    sleep 0.2
  done
}

case "${1:-}" in
  install)
    [[ -x "$BIN" ]] || { echo "atv-remote not found: run 'uv sync' here or 'uv tool install'" >&2; exit 1; }
    mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$BIN</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>LimitLoadToSessionType</key><string>Aqua</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
    plutil -lint "$PLIST" >/dev/null
    unload
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "installed $PLIST (log: $LOG)"
    ;;
  uninstall)
    unload
    rm -f "$PLIST"
    echo "removed $LABEL"
    ;;
  *)
    echo "usage: $0 install|uninstall" >&2
    exit 2
    ;;
esac
