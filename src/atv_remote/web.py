"""Localhost HTTP API + static remote page, served on the controller's loop."""

import asyncio
import contextlib
import logging
import os
from importlib.resources import files

from aiohttp import web

from .core import Controller

log = logging.getLogger("atv_remote")

HOST = "127.0.0.1"
PORT = int(os.environ.get("ATV_REMOTE_PORT", "48765"))
URL = f"http://{HOST}:{PORT}/"

# Any page in the browser can fire requests at localhost. Requiring a custom
# header forces a CORS preflight (which we never answer), and checking Host
# blocks DNS-rebinding, so only our own page can drive the TV.
CSRF_HEADER = "X-ATV-Remote"
ALLOWED_HOSTS = {f"{HOST}:{PORT}", f"localhost:{PORT}"}


@web.middleware
async def guard(request: web.Request, handler):
    if request.host not in ALLOWED_HOSTS:
        raise web.HTTPForbidden(text="bad host")
    if request.method != "GET" and request.headers.get(CSRF_HEADER) != "1":
        raise web.HTTPForbidden(text="missing header")
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface errors to the UI
        log.warning("%s %s failed: %s", request.method, request.path, exc)
        return web.json_response({"error": str(exc)}, status=400)


def build_app(ctl: Controller) -> web.Application:
    routes = web.RouteTableDef()
    page = files("atv_remote").joinpath("static/index.html")

    @routes.get("/")
    async def index(_: web.Request) -> web.Response:
        return web.Response(
            text=page.read_text(encoding="utf-8"), content_type="text/html"
        )

    @routes.get("/api/state")
    async def state(_: web.Request) -> web.Response:
        return web.json_response(ctl.snapshot())

    @routes.post("/api/command/{name}")
    async def command(request: web.Request) -> web.Response:
        await ctl.command(request.match_info["name"])
        return web.json_response({"ok": True})

    @routes.post("/api/device")
    async def device(request: web.Request) -> web.Response:
        await ctl.select_device((await request.json())["id"])
        return web.json_response(ctl.snapshot())

    @routes.post("/api/scan")
    async def scan(_: web.Request) -> web.Response:
        await ctl.scan()
        return web.json_response(ctl.snapshot())

    @routes.post("/api/pair/start")
    async def pair_start(request: web.Request) -> web.Response:
        return web.json_response(await ctl.pair_start((await request.json())["id"]))

    @routes.post("/api/pair/pin")
    async def pair_pin(request: web.Request) -> web.Response:
        pin = str((await request.json())["pin"]).strip()
        return web.json_response(await ctl.pair_pin(pin))

    @routes.post("/api/pair/cancel")
    async def pair_cancel(_: web.Request) -> web.Response:
        await ctl.pair_cancel()
        return web.json_response({"ok": True})

    @routes.get("/api/apps")
    async def apps(_: web.Request) -> web.Response:
        return web.json_response(await ctl.app_list())

    @routes.post("/api/apps/launch")
    async def launch(request: web.Request) -> web.Response:
        await ctl.launch_app((await request.json())["id"])
        return web.json_response({"ok": True})

    @routes.post("/api/text")
    async def text(request: web.Request) -> web.Response:
        await ctl.send_text((await request.json())["text"])
        return web.json_response({"ok": True})

    @routes.get("/api/artwork")
    async def artwork(_: web.Request) -> web.Response:
        art = await ctl.artwork()
        if art is None:
            raise web.HTTPNotFound()
        body, mime = art
        return web.Response(
            body=body, content_type=mime, headers={"Cache-Control": "max-age=3600"}
        )

    app = web.Application(middlewares=[guard])
    app.add_routes(routes)
    return app


async def serve(ctl: Controller) -> web.AppRunner:
    runner = web.AppRunner(build_app(ctl), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, HOST, PORT).start()
    log.info("remote UI at %s", URL)
    return runner


def main() -> None:
    """Headless mode: web remote only, no menu bar."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    async def run() -> None:
        ctl = Controller(asyncio.get_running_loop())
        runner = await serve(ctl)
        try:
            await ctl.start()
            await asyncio.Event().wait()
        finally:
            await ctl.stop()
            await runner.cleanup()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())
