# Contributing

Thanks for helping. This is a small project, so the process is light.

## Reporting a bug

[Open an issue](https://github.com/ijdio/atv-remote/issues/new/choose) using the
bug report form. Include your macOS version, Apple TV model and tvOS version, and
the log output (terminal, or `~/Library/Logs/atv-remote.log` for the login item).
Many problems come from pyatv or the network rather than this app, so the log helps
a lot.

For security problems, see [SECURITY.md](SECURITY.md) and don't open a public issue.

## Suggesting a feature

Open an issue describing what you want to do and why before writing a large
change, so we can agree on the approach first.

## Making a change

You'll need macOS and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/ijdio/atv-remote.git
cd atv-remote
uv sync
uv run atv-remote        # or: uv run atv-remote-web for the server only
```

Before opening a pull request:

```sh
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

CI runs the same checks on macOS.

### Guidelines

- Keep pull requests focused: one fix or feature each.
- Add or update a test in `tests/` when you change the HTTP API or add a command.
  Tests use a fake controller, so they don't need an Apple TV.
- If you change something you can only check on a real Apple TV, say in the PR
  which model and tvOS version you tried it on.
- The UI is a single file (`src/atv_remote/static/index.html`) with no build step.
  Please keep it that way.
- Python style follows ruff's settings in `pyproject.toml` (line length 88).

## Where things live

| Path | What |
|---|---|
| `src/atv_remote/core.py` | Controller: pyatv scanning, pairing, connection, commands |
| `src/atv_remote/web.py` | HTTP API and request guards |
| `src/atv_remote/menubar.py` | Menu bar item and floating panel |
| `src/atv_remote/static/index.html` | Remote UI |
| `scripts/login-item.sh` | Start-at-login LaunchAgent |
| `tests/` | pytest suite |

## Code of conduct

Everyone taking part is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).
