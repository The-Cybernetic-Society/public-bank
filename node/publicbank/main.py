"""
One process, stateless: the web app at /, the mini-app MCP endpoint at /mcp, the Telegram bot fed by
webhook at /tg/<secret> (set when TELEGRAM_BOT_TOKEN is present), and the WhatsApp webhook at /wa
(GET: Meta's subscription handshake against PB_WA_VERIFY_TOKEN; POST: messages, signed with PB_WA_APP_SECRET).

    uvicorn publicbank.main:app --host 0.0.0.0 --port 8000

On startup the bot registers its webhook as PB_BASE_URL + /tg/<secret>, where the secret is derived
from the bot token, so no extra configuration is needed. Set PB_BOT_POLLING=1 for local development
without a public URL (long polling instead).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route

from . import whatsapp
from .mcp_server import app as mcp_app
from .web import app as web_app

log = logging.getLogger("publicbank")
logging.getLogger("httpx").setLevel(logging.WARNING)   # its INFO lines carry the full request URL, bot token included
BASE_URL = os.environ.get("PB_BASE_URL", "http://localhost:8000").rstrip("/")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
HOOK_SECRET = hashlib.sha256(("hook:" + BOT_TOKEN).encode()).hexdigest()[:32] if BOT_TOKEN else ""
_bot_app = None


@contextlib.asynccontextmanager
async def lifespan(app):
    global _bot_app
    if BOT_TOKEN:
        from . import bot
        _bot_app = bot.build()
        await _bot_app.initialize()
        await _bot_app.start()
        if os.environ.get("PB_BOT_POLLING"):
            await _bot_app.bot.delete_webhook(drop_pending_updates=False)
            await _bot_app.updater.start_polling(drop_pending_updates=True)
            log.info("telegram bot: long polling (dev)")
        else:
            url = f"{BASE_URL}/tg/{HOOK_SECRET}"
            await _bot_app.bot.set_webhook(url, secret_token=HOOK_SECRET, allowed_updates=["message", "callback_query"])
            log.info("telegram bot: webhook %s", url)
    else:
        log.info("TELEGRAM_BOT_TOKEN unset; bot not started")
    if os.environ.get("PB_TWIN_WARM", "1") != "0":
        # the digital twin's counts for every place, before the first visitor: a few seconds here, none later
        from . import places
        done = await run_in_threadpool(places.warm)
        log.info("twin warmed: %s", done)
    async with mcp_app.router.lifespan_context(mcp_app):
        try:
            yield
        finally:
            if _bot_app:
                if os.environ.get("PB_BOT_POLLING"):
                    await _bot_app.updater.stop()
                await _bot_app.stop()
                await _bot_app.shutdown()


async def telegram_hook(request: Request):
    if not _bot_app or request.path_params["secret"] != HOOK_SECRET \
            or request.headers.get("X-Telegram-Bot-Api-Secret-Token") != HOOK_SECRET:
        return PlainTextResponse("no", status_code=404)
    from telegram import Update
    data = await request.json()
    await _bot_app.process_update(Update.de_json(data, _bot_app.bot))
    return PlainTextResponse("ok")


async def wa_verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and whatsapp.VERIFY_TOKEN and q.get("hub.verify_token") == whatsapp.VERIFY_TOKEN:
        return PlainTextResponse(q.get("hub.challenge", ""))
    return PlainTextResponse("no", status_code=403)


async def wa_hook(request: Request):
    """Always 200 once the signature checks out: Meta retries anything else, and a handler error is ours to log."""
    body = await request.body()
    if not whatsapp.verify_signature(body, request.headers.get("X-Hub-Signature-256")):
        return PlainTextResponse("no", status_code=403)
    try:
        payload = json.loads(body)
    except ValueError:
        return PlainTextResponse("ok")
    await run_in_threadpool(whatsapp.handle_update, payload)
    return PlainTextResponse("ok")


async def _dispatch(scope, receive, send):
    """/mcp (exactly) goes to the MCP app, which answers at that path itself; everything else to the web app."""
    if scope["type"] == "http" and scope["path"].rstrip("/") == "/mcp":
        await mcp_app(scope, receive, send)
    else:
        await web_app(scope, receive, send)


_inner = Starlette(routes=[Route("/tg/{secret}", telegram_hook, methods=["POST"]),
                           Route("/wa", wa_verify, methods=["GET"]), Route("/wa", wa_hook, methods=["POST"]),
                           Mount("/", app=_dispatch)], lifespan=lifespan)

# Old hostnames (PB_OLD_HOSTS, comma-separated, e.g. bank.cyberneticsutra.com) forward to PB_BASE_URL with the same
# path and query, so cards, posters and links already out in the world keep landing. Webhooks and the health check
# are answered wherever they arrive: Telegram and Meta post to the URL they were given, and Cloud Run probes the
# service's own address.
OLD_HOSTS = {h.strip().lower() for h in os.environ.get("PB_OLD_HOSTS", "").split(",") if h.strip()}
KEEP_PATHS = ("/health", "/tg/", "/wa")


async def app(scope, receive, send):
    if scope["type"] == "http" and OLD_HOSTS and not scope["path"].startswith(KEEP_PATHS):
        host = next((v.decode().split(":")[0].lower() for k, v in scope.get("headers", []) if k == b"host"), "")
        if host in OLD_HOSTS:
            from starlette.responses import RedirectResponse
            q = scope.get("query_string", b"").decode()
            await RedirectResponse(BASE_URL + scope["path"] + (f"?{q}" if q else ""), status_code=301)(scope, receive, send)
            return
    await _inner(scope, receive, send)


app.router = _inner.router   # TestClient and uvicorn look here for the lifespan
