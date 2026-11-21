"""HTTP API tests against a fake controller (no Apple TV or network needed)."""

import asyncio
import re
from importlib.resources import files
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from atv_remote import web
from atv_remote.core import COMMANDS

OK_HOST = {"Host": f"127.0.0.1:{web.PORT}"}
POST = {**OK_HOST, web.CSRF_HEADER: "1"}


class FakeController:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def snapshot(self) -> dict[str, Any]:
        return {"status": "connected", "devices": []}

    async def command(self, name: str) -> None:
        if name not in COMMANDS:
            raise ValueError(f"unknown command {name}")
        self.sent.append(name)


def call(method: str, path: str, headers: dict[str, str]) -> tuple[int, Any, list]:
    ctl = FakeController()

    async def go() -> tuple[int, Any]:
        async with TestClient(TestServer(web.build_app(ctl))) as client:
            resp = await client.request(method, path, headers=headers)
            is_json = resp.content_type == "application/json"
            body = await (resp.json() if is_json else resp.text())
            return resp.status, body

    status, body = asyncio.run(go())
    return status, body, ctl.sent


def test_state_is_served_to_localhost() -> None:
    status, body, _ = call("GET", "/api/state", OK_HOST)
    assert status == 200
    assert body["status"] == "connected"


def test_foreign_host_is_rejected() -> None:
    # DNS-rebinding guard: a page on evil.example resolving to 127.0.0.1
    status, _, _ = call("GET", "/api/state", {"Host": f"evil.example:{web.PORT}"})
    assert status == 403


def test_post_without_header_is_rejected() -> None:
    # A cross-site <form> or fetch() can't add the custom header without CORS.
    status, _, sent = call("POST", "/api/command/play_pause", OK_HOST)
    assert status == 403
    assert sent == []


def test_command_is_dispatched() -> None:
    status, body, sent = call("POST", "/api/command/play_pause", POST)
    assert status == 200 and body == {"ok": True}
    assert sent == ["play_pause"]


def test_controller_errors_become_json_400() -> None:
    status, body, _ = call("POST", "/api/command/nope", POST)
    assert status == 400
    assert "unknown command" in body["error"]


def test_every_ui_command_exists() -> None:
    html = files("atv_remote").joinpath("static/index.html").read_text()
    buttons = set(re.findall(r'data-(?:cmd|hold)="([a-z_]+)"', html))
    keymap = re.search(r"const KEYS = \{(.*?)\};", html, re.S)
    assert keymap, "KEYS map not found in index.html"
    keys = set(re.findall(r':\s*"([a-z_]+)"', keymap.group(1)))
    pad = {"up", "down", "left", "right", "select", "select_hold"}
    missing = (buttons | keys | pad) - COMMANDS.keys()
    assert not missing, f"UI sends commands the controller lacks: {missing}"
