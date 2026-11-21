# atv-remote

A small Apple TV remote for macOS: a menu bar item plus a floating remote panel,
built on [pyatv](https://pyatv.dev).

```
uv sync
uv run atv-remote        # menu bar app (remote panel: menu bar icon → Open Remote…, ⌘R)
uv run atv-remote-web    # headless: just the web remote at http://127.0.0.1:48765/
```

## Start at login

```
scripts/login-item.sh install     # LaunchAgent local.atv-remote; restarts on crash, not on Quit
scripts/login-item.sh uninstall
```

Logs go to `~/Library/Logs/atv-remote.log`. After changing code, run
`launchctl kickstart -k gui/$(id -u)/local.atv-remote` to restart it.

## Pairing

Pick a TV from the menu bar's **Apple TV** submenu (or the panel's dropdown) and
press **Start pairing**. The TV shows two 4-digit PINs in a row:

1. **AirPlay**: now-playing info and artwork
2. **Companion**: remote keys, power, apps and text input

Credentials go in `~/.pyatv.conf` (shared with `atvremote`). The last-used TV is
saved in `~/.config/atv-remote/settings.json`.

## Keys (remote panel)

`← ↑ → ↓` move · `⏎` select · `esc` back · `space` play/pause · `H` home ·
`+`/`−` volume · `[`/`]` skip · `P` power. The clickpad accepts clicks (edges or
centre), drags, and two-finger trackpad swipes; hold the centre for the context menu.

## Known issues

- `srptools` is pinned below 2.0: pyatv 0.18.0 calls it in a way 2.0 rejects,
  which breaks PIN pairing (see `pyproject.toml`).

## Layout

- `core.py`: `Controller` owns the pyatv connection on one asyncio loop
  (scan, connect/reconnect, pairing, commands, push updates)
- `web.py`: aiohttp API + page on 127.0.0.1. POSTs need an `X-ATV-Remote: 1`
  header and the Host header is checked, so other web pages can't drive the TV.
- `menubar.py`: rumps menu on the main thread, plus an `NSPanel` hosting a
  `WKWebView` of the web remote
- `static/index.html`: the remote UI

Set `ATV_REMOTE_PORT` to change the port.
