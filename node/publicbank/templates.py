"""
The WhatsApp templates: every message the Commons may start a conversation with, outside a shop's 24-hour window.

Meta approves each one by name, category and exact wording before it can be sent, so the wording lives here, once,
and three things are made from it: the preview people edit before the number is live (`preview_html`), the payload
the Business Management API takes to register them (`meta_payload`), and the send (`whatsapp.send_template`).

Categories. UTILITY: something the shop signed up for is moving (a draft from its need, the freeze, the ballot, the
plan, a payment); cheap, uncapped, and the right shape for the twin's questions. AUTHENTICATION: the sign-in code;
Meta fixes its wording. MARKETING is not used: a generic "don't forget us" is what gets a number blocked.

Rules of the form, checked by `check()`: a lowercase_name, body under 1024 characters, placeholders {{1}}..{{n}} in
order with an example for each, no placeholder at the very start or end of the body, at most three buttons, button
titles under 25 characters, a footer with the opt-out. A URL button's one variable sits at the end of the URL and
carries a one-time sign-in token, so the page opens already signed in (`/l/<token>`).

Every message ends in a tap. The tap reopens the window, and the free replies that follow are `REPLIES`.

    python -m publicbank.templates --preview out.html     # the preview page
    python -m publicbank.templates --json                 # the registration payloads
"""
from __future__ import annotations

import argparse
import json
import os
import re
from html import escape

BASE_URL = os.environ.get("PB_BASE_URL", "https://goa.cyberneticsutra.com").rstrip("/")
POOL_NAME = os.environ.get("PB_WA_POOL_NAME", "Goa Business Commons")
FOOTER = f"{POOL_NAME} · reply STOP to stop these messages"
LINK = BASE_URL + "/l/{{1}}"   # a one-time sign-in token at the end: the page opens already signed in

# (kind, title, extra): quick = a reply button whose payload the webhook gets back; url = opens the Commons signed in
def quick(title: str, payload: str) -> dict:
    return {"type": "quick", "title": title, "payload": payload}


def url(title: str) -> dict:
    return {"type": "url", "title": title, "url": LINK}


TEMPLATES: list[dict] = [
    {"name": "commons_code", "category": "AUTHENTICATION", "when": "A shop asks for a sign-in code on cyberneticsociety.com/app.",
     "body": "{{1}} is your verification code. For your security, do not share this code.", "fixed": True,
     "footer": "This code expires in 10 minutes.", "buttons": [{"type": "copy", "title": "Copy code"}],
     "example": ["482913"]},

    {"name": "commons_welcome", "category": "UTILITY", "when": "Once, when the business is verified: the first message from the number.",
     "body": ("Hello {{1}}. {{2}} is in the {{3}}: a ₹{{4}} common fund that the businesses of North Goa decide together, "
              "for things they can buy or run as one. Say what you need, back what you'd use, vote on the rest. "
              "Everything happens here in this chat, or on your Commons page, whichever is easier."),
     "buttons": [url("Open my Commons"), quick("What's on the table", "cmd:status")],
     "example": ["Rekha", "Mandrem Tailors", "Goa Business Commons", "5,00,000"]},

    {"name": "commons_draft", "category": "UTILITY", "when": "The twin wrote a draft from this shop's own need.",
     "body": ("A draft for you, {{1}}. The twin wrote it from what {{2}} said it needs: “{{3}}”, about ₹{{4}}. "
              "Would your business use it? Yes backs it. No is just as useful to know."),
     "buttons": [quick("Yes, we'd use it", "use:{pid}:y"), quick("Not for us", "use:{pid}:n"), url("Read it first")],
     "example": ["Rekha", "Mandrem Tailors", "A shared delivery scooter for the market road", "55,000"]},

    {"name": "commons_one_short", "category": "UTILITY", "when": "A draft the shop would plausibly use is one backer short of the ballot.",
     "body": ("“{{1}}” is one backer short of the ballot. {{2}} back it so far. "
              "If {{3}} would use it, back it before the list freezes on {{4}}."),
     "buttons": [quick("Back it", "cs:{pid}"), quick("Not for us", "use:{pid}:n"), url("See the table")],
     "example": ["Shared trainer for staff", "Morjim Bakery and Siolim Spice Store", "Mandrem Tailors", "24 November"]},

    {"name": "commons_freeze", "category": "UTILITY", "when": "Three days before the list freezes.",
     "body": ("The list freezes on {{1}}. After that nothing new goes to the vote. "
              "{{2}} has backed {{3}} so far, and {{4}} ideas are already on the ballot. Worth one more look."),
     "buttons": [url("Open the table"), quick("I'm done", "cmd:done")],
     "example": ["24 November", "Mandrem Tailors", "2 ideas", "5"]},

    {"name": "commons_ballot", "category": "UTILITY", "when": "Ballots open.",
     "body": ("Ballots are open until {{1}}. {{2}} ideas made it to the vote. Split 100 points across them: "
              "the more points an idea gets, the more of the fund it gets. One business, one ballot."),
     "buttons": [url("Cast my ballot")],
     "example": ["29 November", "6"]},

    {"name": "commons_approve", "category": "UTILITY", "when": "The winning plan is up for approval.",
     "body": ("The votes are in. The winning plan is {{1}}. It funds {{2}}. "
              "It stands only if most businesses approve it by {{3}}. Not answering counts as not approving."),
     "buttons": [quick("Approve", "ra:{plan}:approve"), quick("Reject", "ra:{plan}:reject"), url("See the plans")],
     "example": ["Equal Shares", "the delivery scooter, the staff trainer and the packaging order", "2 December"]},

    {"name": "commons_result", "category": "UTILITY", "when": "The decision, either way.",
     "body": ("The decision is in: {{1}}, with {{2}} of {{3}} businesses saying yes. {{4}} "
              "The fund pays each vendor directly, and every rupee goes on the public board."),
     "buttons": [url("The ledger")],
     "example": ["Approved", "9", "11", "The delivery scooter, the staff trainer and the packaging order will be bought."]},

    {"name": "commons_paid", "category": "UTILITY", "when": "The treasurer records a payment.",
     "body": "Paid: ₹{{1}} to {{2}} for {{3}}. Reference {{4}}. It is on the public board now.",
     "buttons": [url("The ledger")],
     "example": ["55,000", "Goa Scooters, Mapusa", "the shared delivery scooter", "UPI 432109876543"]},

    {"name": "commons_weekly", "category": "UTILITY", "when": "Once a week while a round is open, with the shop's own line first.",
     "body": "Your Commons this week. {{1}} {{2}} Ask the twin anything here, any time.",
     "buttons": [url("Open my Commons"), quick("Ask the twin", "cmd:ask")],
     "example": ["Two ideas built from what Mandrem Tailors said are one backer short.", "Nine of thirteen businesses have backed something; the list freezes on 24 November."]},

    {"name": "commons_swap", "category": "UTILITY", "when": "The one more ask before the vote: what each business would share and would use.",
     "body": ("Before the vote, one more thing. What's one thing {{1}} could share with the businesses near it: a machine in the evenings, "
              "a van on Tuesdays, a skill, a corner of a shop? And one thing it would borrow or use from them? "
              "Reply here in your own words, or hold the mic and say it."),
     "buttons": [quick("Tell the twin", "cmd:swap"), quick("Not now", "cmd:later")],
     "example": ["Mandrem Tailors"]},

    {"name": "commons_question", "category": "UTILITY", "when": "Any other small question the twin has for this shop; a tap is enough.",
     "body": "The twin has a question for {{1}}. {{2}} A tap is enough.",
     "buttons": [quick("Yes", "q:{key}:y"), quick("No", "q:{key}:n"), url("Open my Commons")],
     "example": ["Mandrem Tailors", "If money is left after this round, should the next one go to the same lane again?"]},

    {"name": "commons_notice", "category": "UTILITY", "when": "Any round notice the adapter sends outside a shop's window (ballots opened, a plan to approve, a payment): the general template the WhatsApp adapter uses as PB_WA_TEMPLATE.",
     "body": "A note from the Commons. {{1}} Reply here any time; the twin is listening.",
     "buttons": [url("Open my Commons")],
     "example": ["Ballots are open in Goa Business Commons: split 100 points across 6 ideas by 29 November."]},

    {"name": "commons_link", "category": "UTILITY", "when": "A shop replies LOGIN outside its window, or asks for its page.",
     "body": "Here is your one-tap way into the Commons, {{1}}. It works once, on this phone, for the next three days.",
     "buttons": [url("Open my Commons")],
     "example": ["Rekha"]},
]

# What the twin says next, inside the window the tap opened. Free text, no approval needed; kept here so the
# wording is edited in one place with the templates.
REPLIES: dict[str, str] = {
    "use_yes": "Backed. “{title}” now has {n} behind it{rest}.",
    "use_no": "Noted, and useful. I'll stop suggesting it to you.",
    "swap_ask": "Go ahead: what would {business} share, and what would it use? One line each is plenty, or a voice note.",
    "swap_noted": "Got it. I'll look for a match among the other businesses and tell you when I find one.",
    "swap_match": "A match: {other} offered {offer}, which is close to what {business} said it would use. Want me to introduce you?",
    "voice_heard": "I heard: “{text}”. Is that right?",
    "voice_unclear": "I couldn't make that out. Could you say it once more, or type it?",
    "done": "Good. I'll only write again when something of yours moves.",
    "later": "No problem. I'll ask once more before the list freezes.",
    "stop": "Understood. No more messages from here. Reply START if you change your mind.",
}


# ----------------------------------------------------------------------------
# checks, rendering, payloads
# ----------------------------------------------------------------------------
def check(t: dict) -> list[str]:
    """Meta's rules of form, as far as they can be checked here. An empty list means it should pass review."""
    out = []
    if not re.fullmatch(r"[a-z0-9_]{1,512}", t["name"]):
        out.append("name must be lowercase letters, digits and underscores")
    body = t["body"]
    if len(body) > 1024:
        out.append("body over 1024 characters")
    nums = [int(x) for x in re.findall(r"\{\{(\d+)\}\}", body)]
    if nums != list(range(1, len(nums) + 1)):
        out.append("placeholders must run {{1}}, {{2}}, … in order")
    if len(t.get("example", [])) != len(nums):
        out.append(f"{len(nums)} placeholders but {len(t.get('example', []))} examples")
    if not t.get("fixed") and (body.startswith("{{") or body.rstrip().endswith("}}")):
        out.append("body may not start or end with a placeholder")
    btns = t.get("buttons", [])
    if len(btns) > 3:
        out.append("more than three buttons")
    for b in btns:
        if len(b["title"]) > 25:
            out.append(f"button title over 25 characters: {b['title']}")
    if len(t.get("footer", FOOTER)) > 60:
        out.append("footer over 60 characters")
    return out


def get(name: str) -> dict:
    for t in TEMPLATES:
        if t["name"] == name:
            return t
    raise KeyError(name)


def fill(body: str, params: list[str]) -> str:
    for i, v in enumerate(params, 1):
        body = body.replace("{{%d}}" % i, str(v))
    return body


def meta_payload(t: dict) -> dict:
    """The body the Business Management API takes: POST /{WABA_ID}/message_templates."""
    if t["category"] == "AUTHENTICATION":
        return {"name": t["name"], "category": "AUTHENTICATION", "language": "en",
                "components": [{"type": "BODY", "add_security_recommendation": True},
                               {"type": "FOOTER", "code_expiration_minutes": 10},
                               {"type": "BUTTONS", "buttons": [{"type": "OTP", "otp_type": "COPY_CODE", "text": "Copy code"}]}]}
    comps: list[dict] = [{"type": "BODY", "text": t["body"], "example": {"body_text": [t["example"]]}},
                         {"type": "FOOTER", "text": t.get("footer", FOOTER)}]
    btns = []
    for b in t.get("buttons", []):
        if b["type"] == "quick":
            btns.append({"type": "QUICK_REPLY", "text": b["title"]})
        elif b["type"] == "url":
            btns.append({"type": "URL", "text": b["title"], "url": b["url"], "example": [b["url"].replace("{{1}}", "Ab3dE9fG")]})
    if btns:
        comps.append({"type": "BUTTONS", "buttons": btns})
    return {"name": t["name"], "category": t["category"], "language": "en", "components": comps}


def send_components(t: dict, params: list[str], payloads: dict[str, str] | None = None, token: str | None = None) -> list[dict]:
    """The components of one send: body values, each quick-reply's payload (so the webhook gets `use:p_1:y`, not the
    button's words), and the sign-in token at the end of the URL."""
    comps: list[dict] = [{"type": "body", "parameters": [{"type": "text", "text": str(p)[:1024]} for p in params]}]
    for i, b in enumerate(t.get("buttons", [])):
        if b["type"] == "quick":
            pl = (payloads or {}).get(b["payload"], b["payload"])
            comps.append({"type": "button", "sub_type": "quick_reply", "index": str(i), "parameters": [{"type": "payload", "payload": pl[:128]}]})
        elif b["type"] == "url":
            comps.append({"type": "button", "sub_type": "url", "index": str(i), "parameters": [{"type": "text", "text": token or ""}]})
    return comps


# ----------------------------------------------------------------------------
# the preview: each message as it would look on the shop's phone
# ----------------------------------------------------------------------------
PREVIEW_CSS = """
.wat{--paper:#f3e9d6;--card:#fbf6e9;--ink:#20201d;--text:#534e41;--muted:#7a7465;--gold:#d9a33d;--teal:#197c83;--line:#20201d33;--chat:#e8dfcf;--bubble:#ffffff;--tick:#53bdeb;--btn:#0a7cff}
.wat{font:300 17px/1.55 Newsreader,Georgia,serif;color:var(--text)}
.wat h2{font:500 26px/1.15 Newsreader,Georgia,serif;color:var(--ink);margin:0 0 4px}
.wat .lead{max-width:40em;margin:0 0 22px}
.wat .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:22px}
.wat .tpl{border:2px solid var(--ink);border-radius:3px;background:var(--card);box-shadow:5px 5px 0 #20201dcc;display:flex;flex-direction:column;min-width:0}
.wat .tpl .hd{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;padding:10px 14px;border-bottom:2px solid var(--ink);font:400 10px Silkscreen,'IBM Plex Mono',monospace;text-transform:uppercase;color:var(--ink)}
.wat .tpl .hd .cat{margin-left:auto;background:var(--gold);padding:3px 6px;border-radius:6px;font-size:8px}
.wat .tpl .hd .cat.auth{background:var(--teal);color:var(--card)}
.wat .when{padding:10px 14px 0;font-size:14px;color:var(--muted);line-height:1.45}
.wat .phone{margin:12px 14px 0;background:var(--chat);border-radius:14px;padding:14px 12px 12px}
.wat .bub{background:var(--bubble);border-radius:0 10px 10px 10px;padding:8px 10px;max-width:100%;box-shadow:0 1px 1px rgba(0,0,0,.08);font:400 15px/1.45 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#111;white-space:pre-wrap;word-wrap:break-word}
.wat .bub mark{background:#fff3c4;color:#111;border-radius:3px;padding:0 2px}
.wat .bub .ft{display:block;margin-top:6px;font-size:12px;color:#8696a0}
.wat .bub .tm{display:block;text-align:right;font-size:11px;color:#8696a0;margin-top:2px}
.wat .btns{margin-top:4px}
.wat .btns div{background:var(--bubble);color:var(--btn);text-align:center;padding:10px;margin-top:3px;border-radius:8px;font:500 15px -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;box-shadow:0 1px 1px rgba(0,0,0,.08)}
.wat .btns div.u:before{content:'↗ ';font-size:12px}.wat .btns div.c:before{content:'⧉ ';font-size:12px}
.wat .raw{padding:12px 14px 14px;font:400 12.5px/1.6 'IBM Plex Mono',monospace;color:var(--muted);border-top:1px solid var(--line);margin-top:12px}
.wat .raw b{font-weight:500;color:var(--ink)}
.wat .raw .ex{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;margin-top:6px}
.wat .raw .bad{color:#bd4c3c}
.wat .reps{margin-top:34px}
.wat .reps dl{display:grid;grid-template-columns:auto 1fr;gap:8px 16px;font-size:15px}
.wat .reps dt{font:400 11px 'IBM Plex Mono',monospace;color:var(--muted);padding-top:3px}
.wat .reps dd{margin:0;color:var(--ink)}
.wat .note{margin-top:26px;padding:14px 16px;border-left:6px solid var(--gold);background:var(--card);font-size:15px;line-height:1.5}
@media(max-width:640px){.wat .grid{grid-template-columns:1fr}}
"""


def _bubble(t: dict) -> str:
    body = escape(t["body"])
    for i, v in enumerate(t.get("example", []), 1):
        body = body.replace("{{%d}}" % i, f"<mark>{escape(str(v))}</mark>")
    footer = escape(t.get("footer", FOOTER))
    btns = "".join(f"<div class='{ 'u' if b['type'] == 'url' else 'c' if b['type'] == 'copy' else 'q'}'>{escape(b['title'])}</div>" for b in t.get("buttons", []))
    return (f"<div class=phone><div class=bub>{body}<span class=ft>{footer}</span><span class=tm>10:42</span></div>"
            + (f"<div class=btns>{btns}</div>" if btns else "") + "</div>")


def preview_html() -> str:
    """The catalogue as a page: each template as a phone bubble with its examples filled in, the raw wording and
    examples Meta sees, the checks, and the free replies that follow a tap."""
    cards = []
    for t in TEMPLATES:
        probs = check(t)
        ex = "".join(f"<span>{{{{{i}}}}}</span><span>{escape(str(v))}</span>" for i, v in enumerate(t.get("example", []), 1))
        raw = (f"<div class=raw><b>Wording sent to Meta</b><br>{escape(t['body'])}" + (" <i>(fixed by Meta for authentication templates)</i>" if t.get("fixed") else "")
               + (f"<div class=ex>{ex}</div>" if ex else "")
               + "".join(f"<br><span class=bad>⚠ {escape(p)}</span>" for p in probs) + "</div>")
        cards.append(f"<article class=tpl id='{t['name']}'><div class=hd><span>{escape(t['name'])}</span><span class='cat{' auth' if t['category'] == 'AUTHENTICATION' else ''}'>{t['category'].lower()}</span></div>"
                     f"<p class=when>{escape(t['when'])}</p>{_bubble(t)}{raw}</article>")
    reps = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in REPLIES.items())
    return (f"<div class=wat><h2>The messages the Commons may start with</h2>"
            f"<p class=lead>{len(TEMPLATES)} templates, every one tied to something the shop signed up for, every one ending in a tap. "
            "Highlighted words are filled in per shop; the grey line under each is the footer with the opt-out. A tap on a button opens the chat for a day, "
            "and the replies at the end are what the twin says next, free of charge and free of review.</p>"
            f"<div class=grid>{''.join(cards)}</div>"
            f"<section class=reps><h2>What the twin says after the tap</h2><p class=lead>Inside the open window. Curly names are filled in.</p><dl>{reps}</dl></section>"
            "<div class=note>Meta reviews each template's category and wording before the first send; utility templates usually clear within a day. "
            "The authentication template's wording is Meta's own and cannot be edited. Button titles are capped at 25 characters, bodies at 1,024.</div></div>")


GRAPH = os.environ.get("PB_WA_GRAPH", "https://graph.facebook.com/v21.0").rstrip("/")


def register(waba_id: str, token: str, names: list[str] | None = None) -> list[dict]:
    """Submit every template (or the named ones) to Meta for review: POST /{WABA_ID}/message_templates. One that
    already exists under the same name is reported as such, not duplicated. Returns one line per template."""
    import httpx
    out = []
    with httpx.Client(timeout=30) as c:
        for t in TEMPLATES:
            if names and t["name"] not in names:
                continue
            r = c.post(f"{GRAPH}/{waba_id}/message_templates", headers={"Authorization": f"Bearer {token}"}, json=meta_payload(t))
            body = r.json() if r.content else {}
            if r.status_code < 300:
                out.append({"name": t["name"], "ok": True, "id": body.get("id"), "status": body.get("status")})
            else:
                err = (body.get("error") or {})
                out.append({"name": t["name"], "ok": False, "error": err.get("error_user_msg") or err.get("message") or r.text[:200]})
    return out


def status(waba_id: str, token: str) -> list[dict]:
    """What Meta says about each of our templates: APPROVED, PENDING, REJECTED (with the reason), or missing."""
    import httpx
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{GRAPH}/{waba_id}/message_templates", headers={"Authorization": f"Bearer {token}"},
                  params={"fields": "name,status,category,rejected_reason,quality_score", "limit": 100})
        r.raise_for_status()
        theirs = {x["name"]: x for x in r.json().get("data", [])}
    return [{"name": t["name"], "status": theirs.get(t["name"], {}).get("status", "MISSING"), "category": theirs.get(t["name"], {}).get("category"),
             "reason": theirs.get(t["name"], {}).get("rejected_reason")} for t in TEMPLATES]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", help="write the preview page to this file")
    ap.add_argument("--json", action="store_true", help="print the registration payloads")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--register", action="store_true", help="submit the templates to Meta (needs PB_WA_WABA_ID and PB_WA_TOKEN)")
    ap.add_argument("--status", action="store_true", help="Meta's review status of each template (same two variables)")
    ap.add_argument("--only", nargs="*", help="with --register: only these template names")
    a = ap.parse_args()
    if a.register or a.status:
        waba, tok = os.environ.get("PB_WA_WABA_ID", ""), os.environ.get("PB_WA_TOKEN", "")
        if not waba or not tok:
            raise SystemExit("set PB_WA_WABA_ID (the WhatsApp Business Account id) and PB_WA_TOKEN (a system-user token)")
        rows = register(waba, tok, a.only) if a.register else status(waba, tok)
        for r in rows:
            print(f"{r['name']:20s} " + (("submitted" if r["ok"] else "FAILED: " + r["error"]) if "ok" in r else f"{r['status']:10s} {r.get('reason') or ''}"))
        return
    if a.json:
        print(json.dumps([meta_payload(t) for t in TEMPLATES], ensure_ascii=False, indent=1))
    if a.preview:
        with open(a.preview, "w") as f:
            f.write(f"<style>{PREVIEW_CSS}</style>" + preview_html())
        print("wrote", a.preview)
    if a.check or not (a.json or a.preview):
        for t in TEMPLATES:
            print(f"{t['name']:20s} {t['category']:15s} {'ok' if not check(t) else '; '.join(check(t))}")


if __name__ == "__main__":
    main()
