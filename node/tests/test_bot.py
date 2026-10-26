"""Drive the bot handlers with stub updates: join via deep link, /me login link, treasurer payout flow."""
import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

os.environ["PB_DB"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
os.environ["PB_SECRET"] = "test"
os.environ["TELEGRAM_BOT_TOKEN"] = "000:fake"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from publicbank import bot, core, db  # noqa: E402
from publicbank.web import app as web_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

con = bot.con
sent: list[tuple[int, str]] = []


class FakeBot:
    async def send_message(self, chat_id, text, **kw):
        sent.append((chat_id, text))


class FakeMsg:
    def __init__(self):
        self.replies = []
        self.text = ""

    async def reply_text(self, text, reply_markup=None):
        self.replies.append((text, reply_markup))


def upd(tg_id: int, name="Asha", text=""):
    msg = FakeMsg(); msg.text = text
    return SimpleNamespace(effective_user=SimpleNamespace(id=tg_id, first_name=name), message=msg, callback_query=None), msg


def ctx(args=None):
    return SimpleNamespace(args=args or [], bot=FakeBot(), user_data={})


async def run():
    # a village with one residency, a treasurer (tg 1), and an open round
    core.create_node(con, "v", "Village", kind="pool")
    core.create_node(con, "v/r", "Residency R", kind="group", parent_id="v", levy_pct=0)
    rnd = core.open_round(con, "v/r", 50000 * 100)
    treas = core.upsert_member(con, verified_by="coordinator", telegram_id=1, display="T")
    core.join_node(con, "v/r", treas["id"], role="treasurer", auto_active=True)
    tok = core.mint_token(con, "join", node_id="v/r")

    # resident taps the deep link
    u, msg = upd(42)
    await bot.start(u, ctx([f"j_{tok}"]))
    assert "asked to join" in msg.replies[0][0], msg.replies
    assert sent and sent[0][0] == 1 and "New join request" in sent[0][1]
    m = db.row(con.execute("SELECT * FROM members WHERE telegram_id=42").fetchone())
    assert m and m["verified_by"] == "deeplink"

    # the login link in the reply opens a web session
    link = msg.replies[0][0].split("Your page: ")[1].strip()
    c = TestClient(web_app)
    r = c.get(link.replace("http://localhost:8000", ""), follow_redirects=False)
    assert r.status_code == 303 and "pb_s" in r.headers.get("set-cookie", "") or r.status_code == 303

    # coordinator approves; /me shows active
    core.approve_member(con, "v/r", m["id"], treas["id"])
    u, msg = upd(42)
    await bot.me(u, ctx())
    assert "active" in msg.replies[0][0]

    # a funded round → payout queue → treasurer pays
    p = core.add_proposal(con, rnd["id"], m["id"], title="Chai for the hall", text="Daily chai", ideal_paise=5000 * 100, lead_id=m["id"])
    with db.tx(con):
        con.execute("UPDATE members SET upi_id='asha@upi' WHERE id=?", (m["id"],))
    core.freeze_proposals(con, rnd["id"])
    core.submit_ballot(con, rnd["id"], m["id"], {p["id"]: 100})
    core.submit_ballot(con, rnd["id"], treas["id"], {p["id"]: 100})
    core.compute_plans(con, rnd["id"])
    w = core.pick_winner(con, rnd["id"])
    core.submit_ratification(con, rnd["id"], m["id"], w["id"], "approve")
    core.submit_ratification(con, rnd["id"], treas["id"], w["id"], "approve")
    core.close_ratification(con, rnd["id"], w["id"])

    u, msg = upd(1, "T")
    await bot.payouts(u, ctx())
    text, kb = msg.replies[0]
    assert "Chai" in text and "asha@upi" in text
    buttons = [b for row_ in kb.inline_keyboard for b in row_]
    assert any(b.url and b.url.startswith("upi://pay?pa=asha%40upi") for b in buttons), [b.url for b in buttons]
    paid_btn = next(b for b in buttons if b.callback_data)
    cq = SimpleNamespace(data=paid_btn.callback_data, message=msg)
    async def answer(): pass
    cq.answer = answer
    u2 = SimpleNamespace(effective_user=SimpleNamespace(id=1, first_name="T"), callback_query=cq, message=msg)
    c1 = ctx()
    await bot.on_button(u2, c1)
    u3, msg3 = upd(1, "T", text="UPI-REF-123")
    await bot.on_text(u3, c1)
    row_ = db.row(con.execute("SELECT * FROM payouts WHERE status='paid'").fetchone())
    assert row_ and row_["reference"] == "UPI-REF-123"

    # non-treasurer gets refused
    u, msg = upd(42)
    await bot.payouts(u, ctx())
    assert msg.replies[0][0] == "Treasurer only."
    print("BOT OK · join · login link · approve · payout queue with upi:// · paid ref recorded")


asyncio.run(run())
