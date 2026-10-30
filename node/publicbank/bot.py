"""
Telegram bot: the on-ramp and the notification channel.

  /start j_<token>   join a node from a poster's QR or a coordinator's link (links the Telegram id to the member)
  /me                the resident's nodes and the current stage, with a link to the web page
  /status            same as the mini-app's status, for people without an agent
  /payouts           treasurer only: the queue, each item with a upi:// link and a "paid" button
  /propose           title → description → ideal ₹ → minimum ₹ → lead it? → (UPI id) → confirm
  /ballot            split 100 points across the frozen list in one reply → confirm
  /rank              tap the four plans best-first → confirm
  /approve           approve or reject the winning plan
  /cancel            drop whatever the bot was asking

The four round steps are conversations. Their state lives in the `bot_state` table, not in the
Application: on Cloud Run the next message may reach a different instance, or a fresh one. Each step
ends in the same core call the web page makes.

Telegram cannot DM a user who never started the bot, so every resident's first contact is a tap on a
deep link. After that the bot can DM them when ballots open, when the plan is ready, and when
approval opens; the coordinator's "advance" on the web page triggers those (see notify()).

Identity: the numeric Telegram user id, nothing else. It is linked to the member row the join token
created (or to an existing member who signed in with Edge City and then tapped the bot while signed in,
via a `link` token the web page mints).
"""
from __future__ import annotations

import json
import logging
import os
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from . import core, db
from .convo import ERRORS, RULE_NAMES, STAGES, err_text, parse_points, parse_rupees, proposal_summary, rupees  # noqa: F401
from . import convo
from .convo import NEXT_STEP, advanceable

log = logging.getLogger("publicbank.bot")
BASE_URL = os.environ.get("PB_BASE_URL", "http://localhost:8000").rstrip("/")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
con = db.connect()


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
    live, old = [], []
    for x in db.rows(con.execute("SELECT ms.*, n.name FROM memberships ms JOIN nodes n ON n.id=ms.node_id WHERE member_id=?", (m["id"],)).fetchall()):
        rnd = core.current_round(con, x["node_id"])
        if rnd and rnd["status"] in ("proposals", "ballots", "plans", "ratifying"):
            live.append(f"• {x['name']} — {x['role']}, {x['status']}, round: {rnd['status']}")
        else:
            old.append(x["name"])
    tok = core.mint_token(con, "login", member_id=m["id"])
    text = ("\n".join(live) if live else "No round is open where you take part.")
    if old:
        text += "\n\nEarlier: " + ", ".join(old)
    await update.message.reply_text(text + f"\n\nOpen: {BASE_URL}/l/{tok}")


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
            # Telegram allows only http(s)/tg URLs on buttons, so the button opens our /pay page, which hands off to upi://
            tok = core.mint_token(con, "login", member_id=m["id"])
            kb.insert(0, [InlineKeyboardButton("Pay in UPI app", url=f"{BASE_URL}/l/{tok}?next=/pay/{x['id']}")])
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))


# ----------------------------------------------------------------------------
# conversations: one row per Telegram user in bot_state, so any instance can pick up the next message
# ----------------------------------------------------------------------------
STATE_TTL = 24 * 3600


def get_state(tg_id: int) -> tuple[str | None, dict]:
    r = db.row(con.execute("SELECT * FROM bot_state WHERE telegram_id=?", (tg_id,)).fetchone())
    if not r or r["updated_at"] < db.now() - STATE_TTL:
        return None, {}
    return r["state"], db.j(r["data"], {})


def set_state(tg_id: int, state: str, data: dict) -> None:
    with db.tx(con):
        con.execute("""INSERT INTO bot_state (telegram_id,state,data,updated_at) VALUES (?,?,?,?)
                       ON CONFLICT(telegram_id) DO UPDATE SET state=excluded.state, data=excluded.data, updated_at=excluded.updated_at""",
                    (tg_id, state, json.dumps(data), db.now()))


def clear_state(tg_id: int) -> None:
    with db.tx(con):
        con.execute("DELETE FROM bot_state WHERE telegram_id=?", (tg_id,))


def yes_no(prefix: str, yes: str, no: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(yes, callback_data=f"{prefix}:y"), InlineKeyboardButton(no, callback_data=f"{prefix}:n")]])


def rounds_at(m: dict, stage: str) -> list[tuple[dict, dict]]:
    return convo.rounds_at(con, m["id"], stage)


async def begin(update: Update, ctx: ContextTypes.DEFAULT_TYPE, cmd: str, node_id: str | None = None) -> None:
    """Entry for /propose /ballot /rank /approve: find the round, asking which node if there are several."""
    msg = update.effective_message
    m = member_for(update)
    if not m:
        await msg.reply_text("I don't know you yet. Scan a residency QR code to join."); return
    stage, verb = STAGES[cmd]
    found = [(n, r) for n, r in rounds_at(m, stage) if node_id in (None, n["id"])]
    if cmd == "ballot" and found and not any(core.can_ballot(con, r, m["id"]) for _, r in found):
        await msg.reply_text(err_text(core.PBError("node_votes_by_coordinator"))); return
    found = [(n, r) for n, r in found if cmd != "ballot" or core.can_ballot(con, r, m["id"])]
    if not found:
        await msg.reply_text(f"Nothing to {verb} right now. /status shows where each round is."); return
    if len(found) > 1:
        kb = [[InlineKeyboardButton(n["name"], callback_data=f"go:{cmd}:{n['id']}")] for n, _ in found]
        await msg.reply_text("Which residency?", reply_markup=InlineKeyboardMarkup(kb)); return
    node, rnd = found[0]
    await {"propose": start_propose, "ballot": start_ballot, "rank": start_rank, "approve": start_approve}[cmd](update, m, node, rnd)


async def cmd_propose(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None: await begin(update, ctx, "propose")
async def cmd_ballot(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None: await begin(update, ctx, "ballot")
async def cmd_rank(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None: await begin(update, ctx, "rank")
async def cmd_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None: await begin(update, ctx, "approve")


async def nxt(update: Update, ctx: ContextTypes.DEFAULT_TYPE, node_id: str | None = None) -> None:
    """/next: a coordinator moves their round to its next stage, as the web page's button does."""
    msg = update.effective_message
    m = member_for(update)
    if not m:
        await msg.reply_text("I don't know you yet."); return
    opts = [(n, r) for n, r in advanceable(con, m["id"]) if node_id in (None, n["id"])]
    if not opts:
        await msg.reply_text("Nothing to move on right now."); return
    if len(opts) > 1:
        kb = [[InlineKeyboardButton(f"{n['name']}: {NEXT_STEP[r['status']]}", callback_data=f"nx:{n['id']}")] for n, r in opts]
        await msg.reply_text("Which round?", reply_markup=InlineKeyboardMarkup(kb)); return
    node, rnd = opts[0]
    try:
        step = core.advance(con, rnd["id"])
    except core.PBError as e:
        await msg.reply_text(err_text(e)); return
    from . import web
    web._announce(node["id"], step)
    rnd = core.get_round(con, rnd["id"])
    await msg.reply_text(f"Done. {node['name']} is now at: {rnd['status']}.")


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    clear_state(update.effective_user.id)
    await update.effective_message.reply_text("Dropped. Nothing was saved.")


# --- propose ----------------------------------------------------------------
async def start_propose(update: Update, m: dict, node: dict, rnd: dict) -> None:
    set_state(update.effective_user.id, "p_title", {"round_id": rnd["id"], "node": node["name"]})
    await update.effective_message.reply_text(f"A proposal for {node['name']}. What's it called? (/cancel to stop)")


async def propose_text(update: Update, m: dict, state: str, d: dict, text: str) -> None:
    tg, reply = update.effective_user.id, update.message.reply_text
    if state == "p_title":
        if not text: await reply("Send a short title."); return
        d["title"] = text[:120]; set_state(tg, "p_text", d)
        await reply("Describe it in a few lines: what it is, who it's for, what the money buys.")
    elif state == "p_text":
        d["text"] = text[:2000]; set_state(tg, "p_ideal", d)
        await reply("Ideal amount, in rupees?")
    elif state == "p_ideal":
        v = parse_rupees(text)
        if not v: await reply("A whole number of rupees above 0, e.g. 5000."); return
        d["ideal"] = v; set_state(tg, "p_min", d)
        await reply("The least that would still make it worth doing, in rupees? Send 0 if any amount helps.")
    elif state == "p_min":
        v = parse_rupees(text)
        if v is None or v > d["ideal"]: await reply(f"A whole number of rupees from 0 to {d['ideal']}."); return
        d["min"] = v; set_state(tg, "p_lead", d)
        await reply("Will you lead it? The lead organises it and names the vendor; the fund pays them directly.",
                    reply_markup=yes_no("pl", "Yes, I'll lead", "No"))
    elif state == "p_upi":
        if text.lower() != "skip":
            if not re.fullmatch(r"[\w.\-]{2,256}@[\w.\-]{2,64}", text): await reply("That doesn't look like a UPI id (name@bank). Or send skip."); return
            d["upi"] = text
        set_state(tg, "p_confirm", d)
        await reply(proposal_summary(d), reply_markup=yes_no("pc", "Submit", "Cancel"))


async def propose_button(update: Update, m: dict, key: str, ans: str, d: dict) -> None:
    tg, reply = update.effective_user.id, update.callback_query.message.reply_text
    if key == "pl":
        d["lead"] = ans == "y"
        if d["lead"] and not m["upi_id"]:
            set_state(tg, "p_upi", d)
            await reply("The vendor's UPI id, if you have a quote (e.g. shop@okaxis)? Only the treasurer sees it. Send skip to add it later."); return
        set_state(tg, "p_confirm", d)
        await reply(proposal_summary(d), reply_markup=yes_no("pc", "Submit", "Cancel"))
    elif key == "pc":
        clear_state(tg)
        if ans != "y":
            await reply("Dropped. Nothing was saved."); return
        if d.get("upi"):
            with db.tx(con): con.execute("UPDATE members SET upi_id=? WHERE id=?", (d["upi"], m["id"]))
        try:
            p = core.add_proposal(con, d["round_id"], m["id"], title=d["title"], text=d["text"], ideal_paise=d["ideal"] * 100,
                                  min_paise=d["min"] * 100, lead_id=m["id"] if d["lead"] else None)
        except core.PBError as e:
            await reply(err_text(e)); return
        await reply("Submitted. It's on the list." if p["status"] == "eligible" else "Saved as a draft. It reaches the ballot once someone leads it.")


# --- ballot -----------------------------------------------------------------
async def start_ballot(update: Update, m: dict, node: dict, rnd: dict) -> None:
    props = core.eligible_proposals(con, rnd["id"])
    cur = db.j((db.row(con.execute("SELECT points FROM ballots WHERE round_id=? AND member_id=?", (rnd["id"], m["id"])).fetchone()) or {}).get("points"), {})
    lines = [f"{node['name']} · budget {rupees(core.spendable_budget(con, rnd))}. Split 100 points across:"]
    for i, p in enumerate(props, 1):
        lines.append(f"{i}. {p['title']} (ideal {rupees(p['ideal_paise'])}, min {rupees(p['min_paise'])})" + (f" · now {cur[p['id']]}" if p["id"] in cur else ""))
    lines.append("\nReply with one number per item, in order, adding up to 100, e.g. " + " ".join(["0"] * len(props)).replace("0", "100", 1)
                 + f"\nOr by item: 1:60 3:40. Add one more number (or u:N) to leave points unspent." + (f" Your current unspent: {cur['_unspent']}." if "_unspent" in cur else ""))
    set_state(update.effective_user.id, "b_points", {"round_id": rnd["id"], "ids": [p["id"] for p in props], "titles": [p["title"] for p in props]})
    await update.effective_message.reply_text("\n".join(lines))


async def ballot_text(update: Update, m: dict, d: dict, text: str) -> None:
    pts = parse_points(text, d["ids"])
    if isinstance(pts, str):
        await update.message.reply_text(pts + " Try again, or /cancel."); return
    d["points"] = pts; set_state(update.effective_user.id, "b_confirm", d)
    names = dict(zip(d["ids"], d["titles"])) | {"_unspent": "left unspent"}
    await update.message.reply_text("Your ballot:\n" + "\n".join(f"{v:>3}  {names[k]}" for k, v in pts.items()),
                                    reply_markup=yes_no("bc", "Submit", "Cancel"))


async def ballot_button(update: Update, m: dict, ans: str, d: dict) -> None:
    reply = update.callback_query.message.reply_text
    clear_state(update.effective_user.id)
    if ans != "y":
        await reply("Dropped. Nothing was saved."); return
    try:
        b = core.submit_ballot(con, d["round_id"], m["id"], d["points"])
    except core.PBError as e:
        await reply(err_text(e)); return
    await reply("Ballot saved." + (" (This replaces your earlier one.)" if b["revision"] > 1 else "") + " You can change it with /ballot until ballots close.")


# --- rank -------------------------------------------------------------------
def plan_lines(rnd: dict, plan: dict) -> list[str]:
    return convo.plan_lines(con, rnd, plan)


async def start_rank(update: Update, m: dict, node: dict, rnd: dict) -> None:
    plans = core.plans_of(con, rnd["id"])
    lines = [f"{node['name']}: four ways to spend {rupees(core.spendable_budget(con, rnd))}."]
    for pl in plans:
        lines += [f"\n{RULE_NAMES.get(pl['rule'], pl['rule'])}" + ("" if pl["valid"] else " (not valid)")] + plan_lines(rnd, pl)
    rec = core.receipt_for(con, rnd["id"], m["id"])
    if rec:
        tot = sum(rec.values()) or 1; titles = {p["id"]: p["title"] for p in core.eligible_proposals(con, rnd["id"])}
        lines.append("\nUnder Equal Shares your share went to: " + ", ".join(f"{titles.get(k, k)} {v * 100 // tot}%" for k, v in sorted(rec.items(), key=lambda kv: -kv[1])))
    lines.append("\nTap the plans from best to worst.")
    d = {"round_id": rnd["id"], "plans": {pl["id"]: RULE_NAMES.get(pl["rule"], pl["rule"]) for pl in plans}, "order": []}
    set_state(update.effective_user.id, "r_pick", d)
    await update.effective_message.reply_text("\n".join(lines), reply_markup=rank_keyboard(d))


def rank_keyboard(d: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(name, callback_data=f"rk:{pid}")] for pid, name in d["plans"].items() if pid not in d["order"]])


async def rank_button(update: Update, m: dict, key: str, val: str, d: dict) -> None:
    tg, reply = update.effective_user.id, update.callback_query.message.reply_text
    if key == "rk":
        if val not in d["plans"] or val in d["order"]: return
        d["order"].append(val)
        so_far = "\n".join(f"{i}. {d['plans'][p]}" for i, p in enumerate(d["order"], 1))
        if len(d["order"]) < len(d["plans"]):
            set_state(tg, "r_pick", d)
            await reply(f"{so_far}\n\nNext?", reply_markup=rank_keyboard(d)); return
        set_state(tg, "r_confirm", d)
        await reply(f"Your ranking:\n{so_far}", reply_markup=yes_no("rc", "Submit", "Start over"))
    elif key == "rc":
        if val != "y":
            d["order"] = []; set_state(tg, "r_pick", d)
            await reply("From the top: which plan is best?", reply_markup=rank_keyboard(d)); return
        clear_state(tg)
        try:
            core.submit_ranking(con, d["round_id"], m["id"], d["order"])
        except core.PBError as e:
            await reply(err_text(e)); return
        await reply("Ranking saved. You can change it with /rank until the winner is picked.")


# --- approve (no typed input, so no stored state: the plan id rides in the button) ------------------
async def start_approve(update: Update, m: dict, node: dict, rnd: dict) -> None:
    plan = db.row(con.execute("SELECT * FROM plans WHERE id=?", (rnd["winner_plan_id"],)).fetchone())
    eligible = len(core.active_members(con, node["id"]))
    need = -(-node["ratify_pct"] * eligible // 100)
    text = (f"{node['name']}: the winning plan is {RULE_NAMES.get(plan['rule'], plan['rule'])}.\n" + "\n".join(plan_lines(rnd, plan))
            + f"\n\nIt is funded only if {need} of {eligible} members ({node['ratify_pct']}%) approve. Not answering counts as not approving.")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Approve", callback_data=f"ra:{plan['id']}:approve"),
                                InlineKeyboardButton("Reject", callback_data=f"ra:{plan['id']}:reject")]])
    await update.effective_message.reply_text(text, reply_markup=kb)


async def approve_button(update: Update, m: dict, plan_id: str, verdict: str) -> None:
    reply = update.callback_query.message.reply_text
    rnd = db.row(con.execute("SELECT r.* FROM rounds r JOIN plans p ON p.round_id=r.id WHERE p.id=?", (plan_id,)).fetchone())
    if not rnd or rnd["winner_plan_id"] != plan_id:
        await reply("That plan is no longer the one up for approval. Send /approve again."); return
    try:
        core.submit_ratification(con, rnd["id"], m["id"], plan_id, verdict)
    except core.PBError as e:
        await reply(err_text(e)); return
    await reply(("Approved." if verdict == "approve" else "Rejected.") + " You can change your answer with /approve until approval closes.")


# --- dispatch ---------------------------------------------------------------
async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    m = member_for(update)
    if not m:
        return
    key, _, val = q.data.partition(":")
    if key == "paid":
        if not db.row(con.execute("SELECT 1 FROM memberships WHERE member_id=? AND role='treasurer' AND status='active'", (m["id"],)).fetchone()):
            return
        set_state(update.effective_user.id, "t_ref", {"payout_id": val})
        await q.message.reply_text("Reply with the UPI/IMPS reference number."); return
    if key == "go":
        cmd, _, node_id = val.partition(":")
        if cmd in STAGES: await begin(update, ctx, cmd, node_id)
        return
    if key == "nx":
        await nxt(update, ctx, val); return
    if key == "ra":
        plan_id, _, verdict = val.partition(":")
        await approve_button(update, m, plan_id, verdict); return
    state, d = get_state(update.effective_user.id)
    expects = {"pl": "p_lead", "pc": "p_confirm", "bc": "b_confirm", "rk": "r_pick", "rc": "r_confirm"}
    if not state or expects.get(key) != state:
        await q.message.reply_text("That question has expired. Start again with the command."); return
    if key in ("pl", "pc"): await propose_button(update, m, key, val, d)
    elif key == "bc": await ballot_button(update, m, val, d)
    else: await rank_button(update, m, key, val, d)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tg = update.effective_user.id
    text = (update.message.text or "").strip()
    state, d = get_state(tg)
    m = member_for(update)
    if not state or not m:
        await update.message.reply_text("Try /status, /propose, /ballot, /rank or /approve."); return
    if state == "t_ref":
        clear_state(tg)
        core.mark_paid(con, d["payout_id"], text)
        await update.message.reply_text("Recorded. It's on the ledger.")
    elif state.startswith("p_") and state not in ("p_lead", "p_confirm"):
        await propose_text(update, m, state, d, text)
    elif state == "b_points":
        await ballot_text(update, m, d, text)
    else:
        await update.message.reply_text("Use the buttons above, or /cancel.")


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
    app.add_handler(CommandHandler("propose", cmd_propose))
    app.add_handler(CommandHandler("ballot", cmd_ballot))
    app.add_handler(CommandHandler("rank", cmd_rank))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("next", nxt))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    return app


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A handler raised. Log it with the update's shape (no text) and tell the person, instead of silence."""
    log.exception("handler failed on %s", type(update).__name__, exc_info=context.error)
    msg = getattr(update, "effective_message", None)
    if msg is not None:
        try:
            await msg.reply_text("Something went wrong on our side. Please send that again.")
        except Exception:
            pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build().run_polling()
