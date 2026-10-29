"""Drive the bot handlers with stub updates: join via deep link, /me login link, treasurer payout flow, and a
whole round (propose, ballot, rank, approve) as bot conversations."""
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
    return SimpleNamespace(effective_user=SimpleNamespace(id=tg_id, first_name=name), message=msg, effective_message=msg, callback_query=None), msg


async def say(tg_id: int, text: str):
    """A typed message; returns the bot's last reply (text, keyboard)."""
    u, msg = upd(tg_id, text=text)
    await bot.on_text(u, ctx())
    return msg.replies[-1]


async def tap(tg_id: int, data: str):
    """A button press; returns the bot's last reply (text, keyboard)."""
    msg = FakeMsg()
    async def answer(): pass
    cq = SimpleNamespace(data=data, message=msg, answer=answer)
    u = SimpleNamespace(effective_user=SimpleNamespace(id=tg_id, first_name="x"), callback_query=cq, message=None, effective_message=msg)
    await bot.on_button(u, ctx())
    return msg.replies[-1] if msg.replies else (None, None)


def buttons(kb):
    return [b for row_ in kb.inline_keyboard for b in row_]


async def conversations():
    """A second residency runs its round entirely inside the bot. Coordinator steps still go through core."""
    core.create_node(con, "v/s", "Residency S", parent_id="v", levy_pct=0)
    rnd = core.open_round(con, "v/s", 20000 * 100)
    coord = core.upsert_member(con, verified_by="coordinator", telegram_id=500, display="C")
    core.join_node(con, "v/s", coord["id"], role="coordinator", auto_active=True)
    ids = []
    for tg in (501, 502, 503):
        mm = core.upsert_member(con, verified_by="deeplink", telegram_id=tg)
        core.join_node(con, "v/s", mm["id"]); core.approve_member(con, "v/s", mm["id"], coord["id"]); ids.append(mm["id"])

    # /propose: title → text → ideal → min → lead → UPI → submit
    u, msg = upd(501); await bot.cmd_propose(u, ctx())
    assert "What's it called" in msg.replies[-1][0], msg.replies
    await say(501, "Fans for the cowork")
    await say(501, "Two pedestal fans, the room is an oven after 2pm.")
    t, _ = await say(501, "ideal")
    assert "whole number" in t
    await say(501, "₹6,000")
    t, _ = await say(501, "7000")
    assert "from 0 to 6000" in t
    t, kb = await say(501, "2000")
    assert [b.callback_data for b in buttons(kb)] == ["pl:y", "pl:n"]
    t, _ = await tap(501, "pl:y")
    assert "UPI id" in t
    t, kb = await say(501, "fan.lead@okaxis")
    assert "Ideal ₹6,000, minimum ₹2,000" in t and "You lead it" in t
    t, _ = await tap(501, "pc:y")
    assert t.startswith("Submitted"), t
    fans = core.eligible_proposals(con, rnd["id"])[0]
    assert fans["title"] == "Fans for the cowork" and fans["ideal_paise"] == 600000 and fans["lead_id"] == ids[0]
    assert con.execute("SELECT upi_id FROM members WHERE id=?", (ids[0],)).fetchone()[0] == "fan.lead@okaxis"
    assert bot.get_state(501) == (None, {})

    # a second proposal, no lead → draft; /cancel drops a third
    u, msg = upd(502); await bot.cmd_propose(u, ctx())
    for x in ("Chai urn", "A 20L urn for the kitchen", "3000", "0"): await say(502, x)
    await tap(502, "pl:n")
    t, _ = await tap(502, "pc:y")
    assert "draft" in t
    u, msg = upd(502); await bot.cmd_propose(u, ctx())
    u, msg = upd(502); await bot.cancel(u, ctx())
    assert bot.get_state(502) == (None, {})
    core.add_proposal(con, rnd["id"], ids[1], title="Snorkel day", text="Gear hire", ideal_paise=8000 * 100, lead_id=ids[1])

    # ballot before freeze: nothing to vote on
    u, msg = upd(503); await bot.cmd_ballot(u, ctx())
    assert "Nothing to vote on" in msg.replies[-1][0]
    core.freeze_proposals(con, rnd["id"])

    # /ballot: bad sum → retry; positional; by item with unspent
    for tg, good in ((501, "70 30"), (502, "1:20 2:60 u:20"), (503, "50 50")):
        u, msg = upd(tg); await bot.cmd_ballot(u, ctx())
        assert "1. Fans for the cowork" in msg.replies[-1][0] and "2. Snorkel day" in msg.replies[-1][0]
        t, _ = await say(tg, "50 40")
        assert "add up to 90" in t
        t, kb = await say(tg, good)
        assert "Your ballot" in t
        t, _ = await tap(tg, "bc:y")
        assert t.startswith("Ballot saved"), t
    b = db.j(con.execute("SELECT points FROM ballots WHERE round_id=? AND member_id=?", (rnd["id"], ids[1])).fetchone()[0], {})
    assert b == {fans["id"]: 20, core.eligible_proposals(con, rnd["id"])[1]["id"]: 60, "_unspent": 20}, b
    t, _ = await tap(501, "bc:y")      # a stale button does nothing
    assert "expired" in t

    # /rank: tap plans best-first, then submit
    plans = core.compute_plans(con, rnd["id"])
    u, msg = upd(501); await bot.cmd_rank(u, ctx())
    t, kb = msg.replies[-1]
    assert "Equal Shares" in t and len(buttons(kb)) == 4
    order = [p["id"] for p in plans][::-1]
    for pid in order:
        t, kb = await tap(501, f"rk:{pid}")
    assert "Your ranking" in t
    t, _ = await tap(501, "rc:y")
    assert t.startswith("Ranking saved")
    assert db.j(con.execute("SELECT ranking FROM rankings WHERE member_id=?", (ids[0],)).fetchone()[0], []) == order

    # /approve: buttons carry the winner's plan id; 3 of 4 approve → 75% ≥ 60%
    w = core.pick_winner(con, rnd["id"])
    u, msg = upd(502); await bot.cmd_approve(u, ctx())
    t, kb = msg.replies[-1]
    assert "3 of 4 members" in t, t
    for tg in (501, 502, 503):
        t, _ = await tap(tg, f"ra:{w['id']}:approve")
        assert t.startswith("Approved"), t
    loser = next(p["id"] for p in plans if p["id"] != w["id"])
    t, _ = await tap(500, f"ra:{loser}:reject")
    assert "no longer" in t
    d = core.close_ratification(con, rnd["id"], w["id"])
    assert d["verdict"] == "ratified" and d["approvals"] == 3 and d["eligible"] == 4, d
    assert con.execute("SELECT COUNT(*) FROM bot_state").fetchone()[0] == 0


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
    assert any(b.url and "/l/" in b.url and "?next=/pay/" in b.url for b in buttons), [b.url for b in buttons]   # https button → /pay page → upi:// (Telegram refuses upi:// on buttons)
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
    await conversations()
    print("BOT OK · join · login link · approve · payout queue → /pay link · paid ref recorded · propose/ballot/rank/approve in-bot")


asyncio.run(run())
