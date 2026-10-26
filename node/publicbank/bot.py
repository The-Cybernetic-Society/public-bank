"""
Telegram bot: the on-ramp and the notification channel.

  /start j_<token>   join a node from a poster's QR or a coordinator's link (links the Telegram id to the member)
  /me                the resident's nodes and the current stage, with a link to the web page
  /status            same as the mini-app's status, for people without an agent
  /payouts           treasurer only: the queue, each item with a upi:// link and a "paid" button

Telegram cannot DM a user who never started the bot, so every resident's first contact is a tap on a
deep link. After that the bot can DM them when ballots open, when the plan is ready, and when
approval opens; the coordinator's "advance" on the web page triggers those (see notify()).

Identity: the numeric Telegram user id, nothing else. It is linked to the member row the join token
created (or to an existing member who signed in with Edge City and then tapped the bot while signed in,
via a `link` token the web page mints).
"""
from __future__ import annotations

import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from . import core, db

log = logging.getLogger("publicbank.bot")
BASE_URL = os.environ.get("PB_BASE_URL", "http://localhost:8000").rstrip("/")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
con = db.connect()


def rupees(p: int) -> str:
    return f"₹{p // 100:,}"


def member_for(update: Update) -> dict | None:
    u = update.effective_user
    return db.row(con.execute("SELECT * FROM members WHERE telegram_id=?", (u.id,)).fetchone()) if u else None


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    arg = (ctx.args or [""])[0]
    if arg.startswith("j_"):
        try:
            t = core.read_token(con, arg[2:], "join")
        except core.PBError:
            await update.message.reply_text("That link has expired. Ask your coordinator for a fresh one."); return
        m = core.upsert_member(con, verified_by="deeplink", telegram_id=u.id, display=u.first_name)
        ms = core.join_node(con, t["node_id"], m["id"])
        node = core.get_node(con, t["node_id"])
        await update.message.reply_text(
            f"You've asked to join {node['name']}. The coordinator will confirm you, and I'll message you when there's something to do.\n\n"
            f"Your page: {BASE_URL}/l/{core.mint_token(con, 'login', member_id=m['id'])}")
        for c in coordinators(t["node_id"]):
            if c["telegram_id"]:
                try: await ctx.bot.send_message(c["telegram_id"], f"New join request for {node['name']}: {u.first_name or u.id}. Approve: {BASE_URL}/c/{node['id']}")
                except Exception: pass
        return
    if arg.startswith("l_"):   # link this Telegram to a web session's member (minted by the web page while signed in)
        try:
            t = core.read_token(con, arg[2:], "link", consume=True)
        except core.PBError:
            await update.message.reply_text("That link has expired."); return
        core.upsert_member(con, verified_by="deeplink", telegram_id=u.id)   # ensures a row exists for this tg id
        with db.tx(con):
            # move the telegram id onto the signed-in member if it was a separate bare row
            con.execute("UPDATE members SET telegram_id=NULL WHERE telegram_id=? AND id<>?", (u.id, t["member_id"]))
            con.execute("UPDATE members SET telegram_id=? WHERE id=?", (u.id, t["member_id"]))
        await update.message.reply_text("Linked. I'll message you here when there's something to do."); return
    await update.message.reply_text("Hi. Scan your residency's QR code, or ask your coordinator for a join link.")


def coordinators(node_id: str) -> list[dict]:
    return db.rows(con.execute("SELECT m.* FROM members m JOIN memberships ms ON ms.member_id=m.id WHERE ms.node_id=? AND ms.role IN ('coordinator','treasurer') AND ms.status='active'", (node_id,)).fetchall())


async def me(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    m = member_for(update)
    if not m:
        await update.message.reply_text("I don't know you yet. Scan a residency QR code to join."); return
    lines = []
    for x in db.rows(con.execute("SELECT ms.*, n.name FROM memberships ms JOIN nodes n ON n.id=ms.node_id WHERE member_id=?", (m["id"],)).fetchall()):
        rnd = core.current_round(con, x["node_id"])
        lines.append(f"• {x['name']} — {x['status']}" + (f", round: {rnd['status']}" if rnd else ""))
    tok = core.mint_token(con, "login", member_id=m["id"])
    await update.message.reply_text("\n".join(lines) + f"\n\nOpen: {BASE_URL}/l/{tok}")


async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    m = member_for(update)
    if not m:
        await update.message.reply_text("Scan a residency QR code first."); return
    out = []
    for x in db.rows(con.execute("SELECT node_id FROM memberships WHERE member_id=? AND status='active'", (m["id"],)).fetchall()):
        rnd = core.current_round(con, x["node_id"])
        if not rnd: continue
        node = core.get_node(con, x["node_id"])
        props = core.eligible_proposals(con, rnd["id"])
        out.append(f"{node['name']} · {rnd['status']} · budget {rupees(core.spendable_budget(con, rnd))}")
        for p in props: out.append(f"  – {p['title']} (ideal {rupees(p['ideal_paise'])})")
        rec = core.receipt_for(con, rnd["id"], m["id"])
        if rec:
            tot = sum(rec.values()) or 1; titles = {p["id"]: p["title"] for p in props}
            out.append("  your share went: " + ", ".join(f"{titles.get(k, k)} {v * 100 // tot}%" for k, v in sorted(rec.items(), key=lambda kv: -kv[1])))
    await update.message.reply_text("\n".join(out) or "Nothing open right now.")


async def payouts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    m = member_for(update)
    if not m or not db.row(con.execute("SELECT 1 FROM memberships WHERE member_id=? AND role='treasurer' AND status='active'", (m["id"],)).fetchone()):
        await update.message.reply_text("Treasurer only."); return
    q = core.payout_queue(con)
    if not q:
        await update.message.reply_text("Nothing to pay."); return
    for x in q:
        pa = x["payee_upi"] or x["lead_upi"]
        note = f"{x['round_id']} {x['title']}"
        text = f"{x['title']} · {rupees(x['amount_paise'])} · {x['kind']}\nto: {pa or 'no UPI id yet'}\nremark: {note}"
        kb = [[InlineKeyboardButton("Mark paid", callback_data=f"paid:{x['id']}")]]
        if pa:
            kb.insert(0, [InlineKeyboardButton("Pay in UPI app", url=core.upi_link(pa, x["amount_paise"], note))])
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    m = member_for(update)
    if q.data.startswith("paid:"):
        if not m or not db.row(con.execute("SELECT 1 FROM memberships WHERE member_id=? AND role='treasurer' AND status='active'", (m["id"],)).fetchone()):
            return
        ctx.user_data["awaiting_ref"] = q.data[5:]
        await q.message.reply_text("Reply with the UPI/IMPS reference number.")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    pid = ctx.user_data.pop("awaiting_ref", None)
    if pid:
        core.mark_paid(con, pid, update.message.text.strip())
        await update.message.reply_text("Recorded. It's on the ledger.")


async def notify(app: Application, node_id: str, text: str) -> int:
    """DM every active member of a node who has started the bot. Returns how many were reached."""
    n = 0
    for m in core.active_members(con, node_id):
        if not m["telegram_id"]: continue
        try:
            await app.bot.send_message(m["telegram_id"], text); n += 1
        except Exception as e:  # blocked the bot, never started it, etc.
            log.info("notify skipped %s: %s", m["id"][:8], type(e).__name__)
    return n


def build() -> Application:
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("me", me))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("payouts", payouts))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build().run_polling()
