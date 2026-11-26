"""Apple TV remote: menu bar app + local web remote built on pyatv."""

from typing import Any

__all__ = ["main"]


def __getattr__(name: str) -> Any:
    # The menu bar app needs rumps and PyObjC (macOS only). Import it on first use so
    # the headless web remote (`atv_remote.web`) also runs on Windows and Linux.
    if name == "main":
        from .menubar import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
