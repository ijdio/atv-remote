"""Menu bar app: rumps on the main thread, pyatv + web server on a worker loop."""

import asyncio
import contextlib
import logging
import threading
import webbrowser
from typing import Any

import rumps
from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSFloatingWindowLevel,
    NSImage,
    NSImageSymbolConfiguration,
    NSMakeRect,
    NSPanel,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSURL, NSURLRequest
from WebKit import WKWebView, WKWebViewConfiguration

from .core import Controller
from .web import URL, serve

log = logging.getLogger("atv_remote")


class Backend:
    """Runs the asyncio loop (pyatv + aiohttp) on a daemon thread."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.ctl = Controller(self.loop)
        self.error: Exception | None = None
        ready = threading.Event()

        def run() -> None:
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(serve(self.ctl))
            except Exception as exc:  # noqa: BLE001 - e.g. port already in use
                self.error = exc
                ready.set()
                return
            ready.set()
            self.loop.create_task(self.ctl.start())
            self.loop.run_forever()

        threading.Thread(target=run, name="atv-loop", daemon=True).start()
        ready.wait()

    def command(self, name: str) -> None:
        fut = self.ctl.submit(self.ctl.command(name))
        fut.add_done_callback(
            lambda f: f.exception() and log.warning("%s: %s", name, f.exception())
        )

    def snapshot(self) -> dict[str, Any]:
        async def snap() -> dict[str, Any]:
            return self.ctl.snapshot()

        return self.ctl.submit(snap()).result(timeout=1)


class RemotePanel:
    """Floating native window hosting the web remote in a WKWebView."""

    def __init__(self) -> None:
        self.panel = None
        self.web = None

    def show(self) -> None:
        if self.panel is None:
            style = (
                NSWindowStyleMaskTitled
                | NSWindowStyleMaskClosable
                | NSWindowStyleMaskResizable
            )
            panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, 340, 690), style, NSBackingStoreBuffered, False
            )
            panel.setTitle_("Apple TV Remote")
            panel.setReleasedWhenClosed_(False)
            panel.setHidesOnDeactivate_(False)
            panel.setLevel_(NSFloatingWindowLevel)
            panel.setContentMinSize_((300, 420))
            web = WKWebView.alloc().initWithFrame_configuration_(
                panel.contentView().bounds(), WKWebViewConfiguration.alloc().init()
            )
            web.setAutoresizingMask_(2 | 16)  # width + height sizable
            web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(URL)))
            panel.setContentView_(web)
            panel.center()
            self.panel, self.web = panel, web
        NSApp.activateIgnoringOtherApps_(True)
        self.panel.makeKeyAndOrderFront_(None)
        self.panel.makeFirstResponder_(self.web)

    def toggle(self) -> None:
        if self.panel is not None and self.panel.isVisible():
            self.panel.orderOut_(None)
        else:
            self.show()


def _status_icon() -> Any:
    for name in ("appletvremote.gen4.fill", "appletv.fill", "tv"):
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            name, "Apple TV"
        )
        if img is not None:
            cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(14, 0)
            img = img.imageWithSymbolConfiguration_(cfg)
            img.setTemplate_(True)
            return img
    return None


class MenuApp(rumps.App):
    def __init__(self, backend: Backend) -> None:
        super().__init__("Apple TV Remote", title="TV", quit_button=None)
        self.backend = backend
        self.panel = RemotePanel()
        self._icon_set = False
        self._devices_sig: tuple | None = None

        def cmd(name: str):
            return lambda _: backend.command(name)

        self.now_item = rumps.MenuItem("Starting…")
        self.power_item = rumps.MenuItem("Power", callback=cmd("power"))
        self.devices_item = rumps.MenuItem("Apple TV")
        self.menu = [
            self.now_item,
            None,
            rumps.MenuItem(
                "Open Remote…", callback=lambda _: self.panel.show(), key="r"
            ),
            None,
            rumps.MenuItem("Play / Pause", callback=cmd("play_pause"), key="p"),
            rumps.MenuItem("Skip Back", callback=cmd("skip_backward")),
            rumps.MenuItem("Skip Forward", callback=cmd("skip_forward")),
            None,
            rumps.MenuItem("Volume Up", callback=cmd("volume_up"), key="="),
            rumps.MenuItem("Volume Down", callback=cmd("volume_down"), key="-"),
            None,
            rumps.MenuItem("Home", callback=cmd("home")),
            rumps.MenuItem("Back", callback=cmd("menu")),
            self.power_item,
            None,
            self.devices_item,
            rumps.MenuItem("Rescan", callback=self.rescan),
            rumps.MenuItem("Open in Browser", callback=lambda _: webbrowser.open(URL)),
            None,
            rumps.MenuItem("Quit", callback=self.quit, key="q"),
        ]
        self.timer = rumps.Timer(self.refresh, 1.5)
        self.timer.start()

    # ---- periodic UI sync (main thread) ----
    def refresh(self, _=None) -> None:
        if not self._icon_set:
            icon = _status_icon()
            button = getattr(getattr(self, "_nsapp", None), "nsstatusitem", None)
            if icon is not None and button is not None:
                button = button.button()
                button.setImage_(icon)
                button.setTitle_("")
                self._icon_set = True
        try:
            s = self.backend.snapshot()
        except Exception:  # noqa: BLE001 - loop busy; try next tick
            return
        self.now_item.title = self._now_text(s)
        self.power_item.title = "Turn Off" if s["power"] == "on" else "Turn On"
        self._sync_devices(s)

    @staticmethod
    def _now_text(s: dict[str, Any]) -> str:
        name = s["current_name"] or "No Apple TV"
        status = s["status"]
        if status != "connected":
            label = {"unpaired": "not paired — open remote to pair"}.get(status, status)
            return f"{name}: {label}"
        p = s["playing"]
        if p and p.get("title"):
            icon = "▶︎" if p["state"] == "playing" else "❚❚"
            who = p.get("artist") or p.get("series") or s.get("app") or ""
            text = f"{icon} {p['title']}" + (f" — {who}" if who else "")
            return text if len(text) <= 48 else text[:47] + "…"
        if s["power"] == "off":
            return f"{name}: asleep"
        return f"{name}: {s.get('app') or 'idle'}"

    def _sync_devices(self, s: dict[str, Any]) -> None:
        sig = tuple((d["id"], d["name"], d["paired"]) for d in s["devices"]) + (
            s["current"],
        )
        if sig == self._devices_sig:
            return
        self._devices_sig = sig
        if len(self.devices_item):
            self.devices_item.clear()
        for d in s["devices"]:
            label = d["name"] + ("" if d["paired"] else " (not paired)")
            item = rumps.MenuItem(label, callback=self._picker(d["id"], d["paired"]))
            item.state = d["id"] == s["current"]
            self.devices_item.add(item)
        if not s["devices"]:
            self.devices_item.add(rumps.MenuItem("None found"))

    def _picker(self, identifier: str, paired: bool):
        def pick(_) -> None:
            self.backend.ctl.submit(self.backend.ctl.select_device(identifier))
            if not paired:
                self.panel.show()  # pairing happens in the remote UI

        return pick

    def rescan(self, _) -> None:
        self.backend.ctl.submit(self.backend.ctl.scan())

    def quit(self, _) -> None:
        with contextlib.suppress(Exception):
            self.backend.ctl.submit(self.backend.ctl.stop()).result(timeout=3)
        rumps.quit_application()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory
    )
    backend = Backend()
    if backend.error is not None:
        rumps.alert(
            "Apple TV Remote",
            f"Couldn't start the local server on {URL}\n\n{backend.error}\n\n"
            "Is another copy already running?",
        )
        return
    MenuApp(backend).run()
