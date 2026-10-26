"""
One process: the web app at /, the mini-app MCP endpoint at /mcp, and the Telegram bot polling
in the background (only when TELEGRAM_BOT_TOKEN is set).

    uvicorn publicbank.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import contextlib
import logging
import os

from starlette.applications import Starlette
from starlette.routing import Mount

from .mcp_server import app as mcp_app
from .web import app as web_app

log = logging.getLogger("publicbank")


@contextlib.asynccontextmanager
async def lifespan(app):
    bot_app = None
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        from . import bot
        bot_app = bot.build()
        await bot_app.initialize()
        await bot_app.start()
        await bot_app.updater.start_polling(drop_pending_updates=True)
        log.info("telegram bot polling")
    else:
        log.info("TELEGRAM_BOT_TOKEN unset; bot not started")
    async with mcp_app.router.lifespan_context(mcp_app):
        try:
            yield
        finally:
            if bot_app:
                await bot_app.updater.stop()
                await bot_app.stop()
                await bot_app.shutdown()


async def _dispatch(scope, receive, send):
    """/mcp (exactly) goes to the MCP app, which answers at that path itself; everything else to the web app."""
    if scope["type"] == "http" and scope["path"].rstrip("/") == "/mcp":
        await mcp_app(scope, receive, send)
    else:
        await web_app(scope, receive, send)


app = Starlette(routes=[Mount("/", app=_dispatch)], lifespan=lifespan)
