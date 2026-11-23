"""The WhatsApp templates: every one passes Meta's rules of form and renders; a send carries the values, the button
payloads and a one-time sign-in token; a tap on a template button comes back through the webhook and does the thing
(yes backs the draft, no is kept, the swap ask opens a free reply, STOP ends messages); the preview page renders."""
import os
import sys
import tempfile

os.environ["PB_DB"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
os.environ["PB_SECRET"] = "test"
os.environ["PB_TWIN_INLINE"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["PB_WA_NUMBER"] = "+91 12345 67890"
os.environ["PB_WA_TOKEN"] = "graph-token"
os.environ["PB_WA_PHONE_ID"] = "1"
os.environ.pop("TELEGRAM_BOT_TOKEN", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from publicbank import core, db, templates, web, whatsapp  # noqa: E402

sent = []


class FakeSender:
    def text(self, to, body): sent.append((to, "text", body, None))
    def buttons(self, to, body, buttons): sent.append((to, "buttons", body, buttons))
    def list(self, to, body, button, rows): sent.append((to, "list", body, rows))
    def template(self, to, name, params): sent.append((to, "template", name, params))
    def template_full(self, to, name, components): sent.append((to, "template_full", name, components))
    def otp(self, to, name, code): sent.append((to, "otp", name, code))


whatsapp.sender = FakeSender()
con = web.con

# --- form: every template clears the checks, has an example per placeholder, ends in a tap, and makes a payload ---
for t in templates.TEMPLATES:
    assert not templates.check(t), (t["name"], templates.check(t))
    assert t["buttons"], t["name"]
    p = templates.meta_payload(t)
    assert p["name"] == t["name"] and p["language"] == "en" and p["category"] in ("UTILITY", "AUTHENTICATION")
    kinds = [c["type"] for c in p["components"]]
    assert "BODY" in kinds and "BUTTONS" in kinds
    if t["category"] == "UTILITY":
        body = next(c for c in p["components"] if c["type"] == "BODY")
        assert body["example"]["body_text"] == [t["example"]] and "STOP" in next(c for c in p["components"] if c["type"] == "FOOTER")["text"]
assert len({t["name"] for t in templates.TEMPLATES}) == len(templates.TEMPLATES)
assert "Mandrem Tailors" in templates.fill(templates.get("commons_draft")["body"], templates.get("commons_draft")["example"])
html = templates.preview_html()
assert all(t["name"] in html for t in templates.TEMPLATES) and "<mark>" in html and "⚠" not in html

# --- a send: body values, quick-reply payloads, and a sign-in token on the URL button -------------------------------
POOL = "goa-biz"
core.create_node(con, POOL, "Goa Business Commons", kind="pool", pool_vote="nodes", cosign_min=2)
tre = core.upsert_member(con, verified_by="coordinator", telegram_id=7, display="treasurer")
core.join_node(con, POOL, tre["id"], role="treasurer", auto_active=True)
a = core.apply(con, POOL, business="Mandrem Tailors", applicant="Rekha", role="owner", phone="98765 43210", dial="91", need="A scooter for deliveries")
core.verify_application(con, a["id"], tre["id"])
rekha = core.sign_in_by_phone(con, "919876543210")
b = core.apply(con, POOL, business="Morjim Bakery", applicant="Joao", role="owner", phone="98765 00000", dial="91", need="A trainer")
core.verify_application(con, b["id"], tre["id"])
joao = core.sign_in_by_phone(con, "919876500000")
rnd = core.open_round(con, POOL, 500000 * 100)
d = core.add_twin_draft(con, rnd["id"], title="Shared delivery scooter", text="x", ideal_paise=55000 * 100)

ok = whatsapp.send_template("919876543210", "commons_draft", ["Rekha", "Mandrem Tailors", d["title"], "55,000"],
                            {"use:{pid}:y": f"use:{d['id']}:y", "use:{pid}:n": f"use:{d['id']}:n"})
assert ok and sent[-1][1] == "template_full" and sent[-1][2] == "commons_draft"
comps = sent[-1][3]
assert comps[0] == {"type": "body", "parameters": [{"type": "text", "text": x} for x in ["Rekha", "Mandrem Tailors", d["title"], "55,000"]]}
assert comps[1]["sub_type"] == "quick_reply" and comps[1]["parameters"][0]["payload"] == f"use:{d['id']}:y"
assert comps[2]["parameters"][0]["payload"] == f"use:{d['id']}:n"
tok = comps[3]["parameters"][0]["text"]
assert comps[3]["sub_type"] == "url" and core.read_token(con, tok, "login")["member_id"] == rekha["id"]
# the link the button opens is this service's sign-in route with that token at the end
assert templates.get("commons_draft")["buttons"][2]["url"].endswith("/l/{{1}}")

# --- the tap comes back as a `button` message with our payload ------------------------------------------------------
def tap(phone, payload, mid):
    whatsapp.handle_update({"entry": [{"changes": [{"value": {"contacts": [{"wa_id": phone, "profile": {"name": "x"}}],
                            "messages": [{"from": phone, "id": mid, "type": "button", "button": {"payload": payload, "text": "Yes, we'd use it"}}]}}]}]})
    return sent[-1]

r = tap("919876543210", f"use:{d['id']}:y", "wamid.1")
assert r[1] == "text" and "Backed" in r[2] and "1 behind it" in r[2], r
assert POOL + "/mandrem-tailors" in db.j(core.get_proposal(con, d["id"])["cosign_nodes"], [])
assert core.twin_answers_of(con, rekha["id"])[f"use:{d['id']}"] == "yes"
r = tap("919876500000", f"use:{d['id']}:n", "wamid.2")
assert "Noted" in r[2] and core.twin_answers_of(con, joao["id"])[f"use:{d['id']}"] == "no"
assert len(db.j(core.get_proposal(con, d["id"])["cosign_nodes"], [])) == 1
# Joao changes his mind through the one-short template's Back it button (the cs: payload)
r = tap("919876500000", f"cs:{d['id']}", "wamid.3")
assert "Co-signed" in r[2] and len(db.j(core.get_proposal(con, d["id"])["cosign_nodes"], [])) == 2   # two backers; still a draft until someone leads it

# the swap ask opens a free reply, which is kept as an answer
r = tap("919876543210", "cmd:swap", "wamid.4")
assert "what would Mandrem Tailors share" in r[2]
whatsapp.handle_update({"entry": [{"changes": [{"value": {"contacts": [], "messages": [{"from": "919876543210", "id": "wamid.5", "type": "text",
                        "text": {"body": "Our sewing machine in the evenings; we'd use a van on Tuesdays"}}]}}]}]})
assert "match" in sent[-1][2].lower()
assert "sewing machine" in core.twin_answers_of(con, rekha["id"])["swap:" + POOL + "/mandrem-tailors"]
# a question template's yes/no, and the generic done/later replies
assert "Noted" in tap("919876543210", "q:leftover:y", "wamid.6")[2] and core.twin_answers_of(con, rekha["id"])["q:leftover"] == "yes"
assert "freezes" in tap("919876543210", "cmd:later", "wamid.7")[2]
# STOP ends messages: the member loses the opt-in, so send() goes quiet; START brings it back
whatsapp.handle_update({"entry": [{"changes": [{"value": {"contacts": [], "messages": [{"from": "919876543210", "id": "wamid.8", "type": "text", "text": {"body": "STOP"}}]}}]}]})
assert "No more messages" in sent[-1][2] and whatsapp.member_for("919876543210")["wa_opt_in_at"] is None
whatsapp.handle_update({"entry": [{"changes": [{"value": {"contacts": [], "messages": [{"from": "919876543210", "id": "wamid.9", "type": "text", "text": {"body": "start"}}]}}]}]})
assert whatsapp.member_for("919876543210")["wa_opt_in_at"]

# --- the preview page on the service ---------------------------------------------------------------------------
from fastapi.testclient import TestClient  # noqa: E402
c = TestClient(web.app, base_url="http://pb.test")
r = c.get("/wa/templates")
assert r.status_code == 200 and "commons_swap" in r.text and "noindex" in r.text
print("TEMPLATES OK ·", len(templates.TEMPLATES), "templates")
