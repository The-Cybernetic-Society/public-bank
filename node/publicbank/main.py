"""
One process, stateless: the web app at /, the mini-app MCP endpoint at /mcp, and the Telegram bot
fed by webhook at /tg/<secret> (set when TELEGRAM_BOT_TOKEN is present).

    uvicorn publicbank.main:app --host 0.0.0.0 --port 8000

On startup the bot registers its webhook as PB_BASE_URL + /tg/<secret>, where the secret is derived
from the bot token, so no extra configuration is needed. Set PB_BOT_POLLING=1 for local development
without a public URL (long polling instead).
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route

from .mcp_server import app as mcp_app
from .web import app as web_app

log = logging.getLogger("publicbank")
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


async def _dispatch(scope, receive, send):
    """/mcp (exactly) goes to the MCP app, which answers at that path itself; everything else to the web app."""
    if scope["type"] == "http" and scope["path"].rstrip("/") == "/mcp":
        await mcp_app(scope, receive, send)
    else:
        await web_app(scope, receive, send)


app = Starlette(routes=[Route("/tg/{secret}", telegram_hook, methods=["POST"]), Mount("/", app=_dispatch)], lifespan=lifespan)
