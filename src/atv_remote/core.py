"""Apple TV controller: owns the pyatv connection on a single asyncio loop.

Everything here runs on the asyncio loop thread. Other threads (the menu bar)
talk to it via `Controller.submit()` and read `Controller.snapshot()`.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pyatv
from pyatv import exceptions
from pyatv.const import (
    DeviceState,
    InputAction,
    OperatingSystem,
    PowerState,
    Protocol,
)
from pyatv.interface import (
    AppleTV,
    BaseConfig,
    DeviceListener,
    Playing,
    PowerListener,
    PushListener,
)
from pyatv.storage.file_storage import FileStorage

log = logging.getLogger("atv_remote")

SETTINGS_PATH = Path.home() / ".config" / "atv-remote" / "settings.json"

# Protocols we pair, in order. AirPlay gives now-playing metadata (MRP is
# tunnelled over it); Companion gives remote keys, power, apps and text input.
PAIR_PROTOCOLS = (Protocol.AirPlay, Protocol.Companion)


def _rc(name: str, *args: Any) -> Callable[[AppleTV], Awaitable[None]]:
    return lambda atv: getattr(atv.remote_control, name)(*args)


async def _power_toggle(atv: AppleTV) -> None:
    if atv.power.power_state == PowerState.On:
        await atv.power.turn_off()
    else:
        await atv.power.turn_on()


COMMANDS: dict[str, Callable[[AppleTV], Awaitable[None]]] = {
    "up": _rc("up"),
    "down": _rc("down"),
    "left": _rc("left"),
    "right": _rc("right"),
    "select": _rc("select"),
    "select_hold": _rc("select", InputAction.Hold),
    "menu": _rc("menu"),
    "home": _rc("home"),
    "home_double": _rc("home", InputAction.DoubleTap),  # app switcher
    "home_hold": _rc("home", InputAction.Hold),  # control centre
    "top_menu": _rc("top_menu"),
    "play_pause": _rc("play_pause"),
    "next": _rc("next"),
    "previous": _rc("previous"),
    "skip_forward": _rc("skip_forward"),
    "skip_backward": _rc("skip_backward"),
    "volume_up": lambda atv: atv.audio.volume_up(),
    "volume_down": lambda atv: atv.audio.volume_down(),
    "power": _power_toggle,
    "turn_on": lambda atv: atv.power.turn_on(),
    "turn_off": lambda atv: atv.power.turn_off(),
}


def _is_apple_tv(config: BaseConfig) -> bool:
    return config.device_info.operating_system == OperatingSystem.TvOS


def _has_creds(config: BaseConfig, protocol: Protocol) -> bool:
    service = config.get_service(protocol)
    return bool(service and service.credentials)


class Controller(PushListener, PowerListener, DeviceListener):
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.storage = FileStorage.default_storage(loop)
        self.devices: dict[str, BaseConfig] = {}
        self.current_id: str | None = None
        self.atv: AppleTV | None = None
        self.status = (
            "starting"  # starting|scanning|connecting|connected|unpaired|offline|error
        )
        self.error: str | None = None
        self.playing: dict[str, Any] | None = None
        self.app_name: str | None = None
        self.artwork_id: str | None = None
        self._artwork: tuple[str, bytes, str] | None = None  # (id, bytes, mime)
        self._pairing = None
        self._pairing_queue: list[Protocol] = []
        self._connect_lock = asyncio.Lock()
        self._reconnect_task: asyncio.Task | None = None

    # ---- thread bridge -------------------------------------------------
    def submit(self, coro: Awaitable[Any]) -> Future:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def snapshot(self) -> dict[str, Any]:
        """Plain-data view of state; safe to hand to other threads / JSON."""
        current = self.devices.get(self.current_id) if self.current_id else None
        power = None
        if self.atv is not None:
            try:
                power = self.atv.power.power_state.name.lower()
            except Exception:  # noqa: BLE001 - not supported on some setups
                power = None
        return {
            "status": self.status,
            "error": self.error,
            "current": self.current_id,
            "current_name": current.name if current else None,
            "power": power,
            "playing": self.playing,
            "app": self.app_name,
            "artwork_id": self.artwork_id,
            "pairing": (
                {"protocol": self._pairing_queue[0].name} if self._pairing else None
            ),
            "devices": [
                {
                    "id": dev_id,
                    "name": cfg.name,
                    "model": cfg.device_info.model_str,
                    "address": str(cfg.address),
                    "paired": _has_creds(cfg, Protocol.Companion),
                    "metadata_paired": _has_creds(cfg, Protocol.AirPlay),
                }
                for dev_id, cfg in sorted(
                    self.devices.items(), key=lambda kv: kv[1].name
                )
            ],
        }

    # ---- lifecycle -----------------------------------------------------
    async def start(self) -> None:
        await self.storage.load()
        self.current_id = self._load_settings().get("current")
        await self.scan()

    async def stop(self) -> None:
        await self._cancel_pairing()
        await self._disconnect()

    async def scan(self) -> None:
        self.status = "scanning"
        configs = await pyatv.scan(self.loop, timeout=5, storage=self.storage)
        self.devices = {c.identifier: c for c in configs if _is_apple_tv(c)}
        log.info(
            "found %d Apple TV(s): %s",
            len(self.devices),
            ", ".join(c.name for c in self.devices.values()),
        )
        if self.current_id not in self.devices:
            by_name = sorted(self.devices, key=lambda i: self.devices[i].name)
            paired = [
                i for i in by_name if _has_creds(self.devices[i], Protocol.Companion)
            ]
            self.current_id = (paired or by_name or [None])[0]
        if self.atv is None:
            await self.connect()
        elif self.status == "scanning":
            self.status = "connected"

    async def select_device(self, identifier: str) -> None:
        if identifier not in self.devices:
            raise ValueError(f"unknown device {identifier}")
        await self._cancel_pairing()
        if identifier != self.current_id:
            await self._disconnect()
            self.current_id = identifier
            self._save_settings({"current": identifier})
        await self.connect()

    async def connect(self) -> None:
        async with self._connect_lock:
            if self.atv is not None:
                return
            config = self.devices.get(self.current_id) if self.current_id else None
            if config is None:
                self.status = "offline"
                return
            if not _has_creds(config, Protocol.Companion):
                self.status = "unpaired"
                return
            self.status = "connecting"
            self.error = None
            try:
                atv = await pyatv.connect(config, self.loop, storage=self.storage)
            except Exception as exc:  # noqa: BLE001
                log.warning("connect to %s failed: %s", config.name, exc)
                self.status, self.error = "offline", str(exc)
                self._schedule_reconnect(rescan=True)
                return
            self.atv = atv
            atv.listener = self
            atv.power.listener = self
            atv.push_updater.listener = self
            try:
                atv.push_updater.start()
            except exceptions.NotSupportedError:
                log.info("push updates not supported (pair AirPlay for metadata)")
            self.status = "connected"
            log.info("connected to %s", config.name)
            await self._refresh_app()

    async def _disconnect(self) -> None:
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        atv, self.atv = self.atv, None
        self.playing = self.app_name = self.artwork_id = None
        self._artwork = None
        if atv is not None:
            atv.listener = None
            with contextlib.suppress(Exception):
                atv.push_updater.stop()
            await asyncio.gather(*atv.close(), return_exceptions=True)

    def _schedule_reconnect(self, rescan: bool = False, delay: float = 5.0) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            return

        async def _later() -> None:
            await asyncio.sleep(delay)
            if rescan and self.current_id:
                found = await pyatv.scan(
                    self.loop,
                    timeout=5,
                    identifier=self.current_id,
                    storage=self.storage,
                )
                for cfg in found:
                    self.devices[cfg.identifier] = cfg
            self._reconnect_task = None
            await self.connect()

        self._reconnect_task = self.loop.create_task(_later())

    # ---- commands ------------------------------------------------------
    async def command(self, name: str) -> None:
        func = COMMANDS.get(name)
        if func is None:
            raise ValueError(f"unknown command {name}")
        if self.atv is None:
            await self.connect()
        if self.atv is None:
            raise RuntimeError(f"not connected ({self.status})")
        await func(self.atv)

    async def app_list(self) -> list[dict[str, str]]:
        if self.atv is None:
            return []
        apps = await self.atv.apps.app_list()
        return sorted(
            ({"id": a.identifier, "name": a.name} for a in apps),
            key=lambda a: a["name"].lower(),
        )

    async def launch_app(self, bundle_id: str) -> None:
        if self.atv is None:
            raise RuntimeError("not connected")
        await self.atv.apps.launch_app(bundle_id)

    async def send_text(self, text: str) -> None:
        if self.atv is None:
            raise RuntimeError("not connected")
        await self.atv.keyboard.text_set(text)

    async def artwork(self) -> tuple[bytes, str] | None:
        if self.atv is None or not self.artwork_id:
            return None
        if self._artwork and self._artwork[0] == self.artwork_id:
            return self._artwork[1], self._artwork[2]
        art = await self.atv.metadata.artwork(width=400, height=400)
        if art is None:
            return None
        self._artwork = (self.artwork_id, art.bytes, art.mimetype)
        return art.bytes, art.mimetype

    # ---- pairing -------------------------------------------------------
    async def pair_start(self, identifier: str) -> dict[str, Any]:
        """Begin pairing: the Apple TV shows a PIN. Returns the protocol paired."""
        await self._cancel_pairing()
        if identifier != self.current_id:
            await self.select_device(identifier)
        config = self.devices[identifier]
        self._pairing_queue = [
            p
            for p in PAIR_PROTOCOLS
            if config.get_service(p) and not _has_creds(config, p)
        ]
        if not self._pairing_queue:
            return {"done": True}
        await self._begin_next(config)
        return {"done": False, "protocol": self._pairing_queue[0].name}

    async def _begin_next(self, config: BaseConfig) -> None:
        protocol = self._pairing_queue[0]
        self._pairing = await pyatv.pair(
            config, protocol, self.loop, storage=self.storage, name="Mac Remote"
        )
        await self._pairing.begin()

    async def pair_pin(self, pin: str) -> dict[str, Any]:
        if self._pairing is None:
            raise RuntimeError("no pairing in progress")
        protocol = self._pairing_queue[0]
        self._pairing.pin(pin)
        try:
            await self._pairing.finish()
            ok = self._pairing.has_paired
        finally:
            await self._pairing.close()
            self._pairing = None
        if not ok:
            self._pairing_queue = []
            raise RuntimeError(f"{protocol.name} pairing failed — wrong PIN?")
        await self.storage.save()
        log.info("paired %s", protocol.name)
        self._pairing_queue.pop(0)
        # Re-scan this device so its config picks up the new credentials.
        found = await pyatv.scan(
            self.loop, timeout=5, identifier=self.current_id, storage=self.storage
        )
        for cfg in found:
            self.devices[cfg.identifier] = cfg
        config = self.devices[self.current_id]
        if self._pairing_queue:
            await self._begin_next(config)
            return {"done": False, "protocol": self._pairing_queue[0].name}
        await self._disconnect()
        await self.connect()
        return {"done": True}

    async def pair_cancel(self) -> None:
        await self._cancel_pairing()

    async def _cancel_pairing(self) -> None:
        pairing, self._pairing = self._pairing, None
        self._pairing_queue = []
        if pairing is not None:
            await pairing.close()

    # ---- pyatv listener callbacks (called on the loop) -----------------
    def playstatus_update(self, updater, playstatus: Playing) -> None:
        idle = playstatus.device_state in (DeviceState.Idle, DeviceState.Stopped)
        self.playing = (
            None
            if idle and not playstatus.title
            else {
                "state": playstatus.device_state.name.lower(),
                "title": playstatus.title,
                "artist": playstatus.artist,
                "album": playstatus.album,
                "series": playstatus.series_name,
                "position": playstatus.position,
                "total": playstatus.total_time,
                "media_type": playstatus.media_type.name.lower(),
            }
        )
        try:
            self.artwork_id = self.atv.metadata.artwork_id if self.atv else None
        except Exception:  # noqa: BLE001
            self.artwork_id = None
        self.loop.create_task(self._refresh_app())

    def playstatus_error(self, updater, exception: Exception) -> None:
        log.debug("push update error: %s", exception)

    def powerstate_update(self, old_state: PowerState, new_state: PowerState) -> None:
        log.info("power %s -> %s", old_state.name, new_state.name)

    def connection_lost(self, exception: Exception) -> None:
        log.warning("connection lost: %s", exception)
        self.atv = None
        self.status = "offline"
        self._schedule_reconnect(rescan=True, delay=2)

    def connection_closed(self) -> None:
        log.info("connection closed")

    async def _refresh_app(self) -> None:
        try:
            app = self.atv.metadata.app if self.atv else None
            self.app_name = app.name if app else None
        except Exception:  # noqa: BLE001 - needs AirPlay/MRP
            self.app_name = None

    # ---- settings ------------------------------------------------------
    @staticmethod
    def _load_settings() -> dict[str, Any]:
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _save_settings(data: dict[str, Any]) -> None:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(data))
