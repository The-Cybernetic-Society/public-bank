"""The webhook route: wrong secret → 404; right secret → the update reaches the bot handlers."""
import os
import sys
import tempfile

os.environ["PB_DB"] = os.path.join(tempfile.mkdtemp(), "h.sqlite")
os.environ["PB_SECRET"] = "test"
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:fake-token"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from publicbank import main  # noqa: E402
from publicbank import bot, core  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

replies = []


async def fake_send(self, chat_id, text, **kw):
    replies.append((chat_id, text))


async def fake_set_webhook(self, *a, **kw): return True
async def fake_delete_webhook(self, *a, **kw): return True
async def fake_get_me(self, *a, **kw):
    from telegram import User
    self._bot_user = User(id=1, first_name="PB", is_bot=True, username="PublicBankBot")
    return self._bot_user

from telegram import Bot  # noqa: E402
Bot.send_message = fake_send
Bot.set_webhook = fake_set_webhook
Bot.delete_webhook = fake_delete_webhook
Bot.get_me = fake_get_me
Bot._validate_token = lambda self, *a, **k: None


core.create_node(bot.con, "v/r", "Residency R")
tok = core.mint_token(bot.con, "join", node_id="v/r")

update = {"update_id": 1, "message": {"message_id": 1, "date": 0, "chat": {"id": 42, "type": "private"},
          "from": {"id": 42, "is_bot": False, "first_name": "Asha"}, "text": f"/start j_{tok}",
          "entities": [{"type": "bot_command", "offset": 0, "length": 6}]}}

with TestClient(main.app) as c:
    assert c.post("/tg/wrong", json=update).status_code == 404
    assert c.post(f"/tg/{main.HOOK_SECRET}", json=update).status_code == 404   # header missing
    r = c.post(f"/tg/{main.HOOK_SECRET}", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": main.HOOK_SECRET})
    assert r.status_code == 200, r.text
    assert c.get("/health").json() == {"ok": True}

assert replies and replies[0][0] == 42 and "asked to join" in replies[0][1], replies
m = bot.db.row(bot.con.execute("SELECT * FROM members WHERE telegram_id=42").fetchone())
assert m and m["verified_by"] == "deeplink"
print("WEBHOOK OK · 404 on bad secret/header · update dispatched · member linked")
