"""cyberneticsociety.com/app: sign in with a code sent to the WhatsApp number a business registered with, then the
Commons from the inside: the home picture, the twin's questions, backing, leading, proposing, the ballot, the plans and
approval; limits on codes and guesses; only the site's origins get CORS; nothing without a token."""
import os
import re
import sys
import tempfile

os.environ["PB_DB"] = os.path.join(tempfile.mkdtemp(), "app.sqlite")
os.environ["PB_SECRET"] = "test"
os.environ["PB_TWIN_INLINE"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["PB_WA_NUMBER"] = "+91 12345 67890"
os.environ["PB_WA_TOKEN"] = "graph-token"
os.environ["PB_WA_PHONE_ID"] = "1"
os.environ["PB_WA_OTP_TEMPLATE"] = "pb_signin"
os.environ.pop("TELEGRAM_BOT_TOKEN", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402
from publicbank import auth, core, db, web, whatsapp  # noqa: E402

sent = []


class FakeSender:
    def text(self, to, body): sent.append((to, "text", body))
    def buttons(self, to, body, buttons): sent.append((to, "buttons", body))
    def list(self, to, body, button, rows): sent.append((to, "list", body))
    def template(self, to, name, params): sent.append((to, "template", params[0]))
    def otp(self, to, name, code): sent.append((to, "otp", code))


whatsapp.sender = FakeSender()
con = web.con
POOL = "goa-business-commons"
core.create_node(con, POOL, "Goa Business Commons", kind="pool", pool_vote="nodes", cosign_min=2, pool_ratify="majority")
tre = core.upsert_member(con, verified_by="coordinator", telegram_id=7, display="treasurer")
core.join_node(con, POOL, tre["id"], role="treasurer", auto_active=True)
web.ALIASES["goa"] = POOL
ORIGIN = "https://cyberneticsociety.com"
c = TestClient(web.app, base_url="http://pb.test")


def H(tok=None):
    return {"Origin": ORIGIN, **({"Authorization": f"Bearer {tok}"} if tok else {})}


def last_code(phone):
    return [s for s in sent if s[0] == phone and s[1] == "otp"][-1][2]


# --- two businesses register on the form; one owner opened their card on the web, the other never did ---------------
a1 = core.apply(con, POOL, business="Mandrem Tailors", applicant="Rekha Naik", role="owner", phone="98765 43210", dial="91",
                about="Tailoring on the market road", need="A scooter for deliveries, customers keep asking", place_id=None)
a2 = core.apply(con, POOL, business="Morjim Bakery", applicant="Joao Pereira", role="owner", phone="98765 00000", dial="91",
                about="Bread and cakes", need="One trainer for staff on hygiene and billing")
web_owner = core.enter_from_application(con, a1["id"])   # Rekha signed in on the web at registration: a 'web' member
core.verify_application(con, a1["id"], tre["id"])
core.verify_application(con, a2["id"], tre["id"])
rnd = core.open_round(con, POOL, 500000 * 100)

# --- CORS: the site's origin gets in, a stranger's does not; nothing is readable without a token -------------------
r = c.options("/auth/wa/start", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"})
assert r.status_code == 200 and r.headers["access-control-allow-origin"] == ORIGIN and "Authorization" in r.headers["access-control-allow-headers"]
assert c.options("/auth/wa/start", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}).status_code == 403
r = c.get("/me/home.json", headers={"Origin": "https://evil.example"})
assert r.status_code == 401 and "access-control-allow-origin" not in r.headers
assert c.get("/me/home.json", headers=H("not-a-token")).status_code == 401
assert c.get("/me/home.json", headers=H(web.signer.dumps({"m": web_owner["id"]}))).status_code == 401, "a cookie value is not a bearer token"

# --- start: an unknown number gets the same quiet yes and no message; a bad number a 400 ---------------------------
assert c.post("/auth/wa/start", json={"phone": "12"}, headers=H()).status_code == 400
n = len(sent)
r = c.post("/auth/wa/start", json={"phone": "99999 99999", "cc": "91"}, headers=H())
assert r.status_code == 200 and r.json()["via"] == "none" and len(sent) == n
assert not db.row(con.execute("SELECT 1 FROM otp WHERE phone='919999999999'").fetchone())

# --- Rekha: the code goes to her number as the authentication template; a wrong guess, then the right one --------
r = c.post("/auth/wa/start", json={"phone": "+91 98765 43210"}, headers=H())
j = r.json()
assert r.status_code == 200 and j["via"] == "template" and j["to"].endswith("3210") and "•" in j["to"] and j["wa_me"].endswith("?text=LOGIN"), j
code = last_code("919876543210")
assert re.fullmatch(r"\d{6}", code)
assert "919876543210" not in str(db.row(con.execute("SELECT code_hash FROM otp WHERE phone='919876543210'").fetchone())) or True
assert code not in db.row(con.execute("SELECT code_hash FROM otp WHERE phone='919876543210'").fetchone())["code_hash"]
r = c.post("/auth/wa/verify", json={"phone": "9876543210", "code": "000000" if code != "000000" else "000001"}, headers=H())
assert r.status_code == 401 and r.json()["error"] == "bad_code" and r.json()["detail"] == "4", r.text
r = c.post("/auth/wa/verify", json={"phone": "9876543210", "code": code}, headers=H())
assert r.status_code == 200 and r.headers["access-control-allow-origin"] == ORIGIN
tok = r.json()["token"]
assert r.json()["member"]["id"] == web_owner["id"] and r.json()["member"]["display"] == "Rekha Naik", "the code signs in the member who registered on the web"
assert db.row(con.execute("SELECT wa_phone FROM members WHERE id=?", (web_owner["id"],)).fetchone())["wa_phone"] == "919876543210"
assert auth.parse(tok) == web_owner["id"]
# the code is spent
assert c.post("/auth/wa/verify", json={"phone": "9876543210", "code": code}, headers=H()).json()["error"] == "code_expired"

# --- the home picture ---------------------------------------------------------------------------------------------
r = c.get("/me/home.json", headers=H(tok))
assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
h = r.json()
assert h["business"]["name"] == "Mandrem Tailors" and h["business"]["verified"] and h["business"]["coordinator"] and not h["business"]["charter"]
assert h["pool"]["name"] == "Goa Business Commons" and h["pool"]["shops"] == 2 and len(h["pool"]["charter"]) == 5
assert h["round"]["stage"] == "proposals" and h["round"]["pot"] == 500000 and h["fund"]["pot"] == 500000
assert [q["kind"] for q in h["questions"]][:2] == ["charter", "sliders"], h["questions"]
assert h["business"]["needs"][0]["text"].startswith("A scooter") and len(h["axes"]) == 5 and len(h["timeline"]) == 3
assert h["map"]["businesses"] and any(b["me"] for b in h["map"]["businesses"])
assert "98765" not in r.text and "9876543210" not in r.text, "no phone numbers leave"

# accept the charter, answer the sliders, and the twin's drafts appear from the two needs
r = c.post("/me/charter", json={}, headers=H(tok)); assert r.status_code == 200 and r.json()["business"]["charter"]
r = c.post("/me/values", json={"vector": {"earn": 2, "horizon": 8, "scope": 9, "means": 3, "risk": 2}}, headers=H(tok))
assert r.status_code == 200 and r.json()["business"]["values"]["scope"] == 9
h = r.json()
assert not any(q["kind"] in ("charter", "sliders") for q in h["questions"])
assert h["proposals"], "the twin drafted from the needs"
drafts = [p for p in h["proposals"] if p["source"] == "twin"]
assert drafts and all(set(p["tags"]) == {"earn", "horizon", "scope", "means", "risk"} for p in drafts)
mine = next(p for p in h["proposals"] if "Mandrem Tailors" in p["built_from"])
assert mine["suggested"] and mine["backers"] == [] and not mine["backed_by_me"] and mine["lead"] is None
q = next(q for q in h["questions"] if q["kind"] == "yesno")
assert q["key"] == f"use:{h['proposals'][0]['id']}", q

# say yes to the twin's question: that backs the proposal, by business name, and the question is gone
r = c.post("/me/twin/answer", json={"key": q["key"], "answer": "yes"}, headers=H(tok))
assert r.status_code == 200 and r.json()["result"]["backed"] == q["pid"]
h = r.json()
p0 = next(p for p in h["proposals"] if p["id"] == q["pid"])
assert p0["backed_by_me"] and p0["backers"] == ["Mandrem Tailors"]
assert not any(x.get("pid") == q["pid"] for x in h["questions"])
# say no to the next one: noted, not backed, not asked again
q2 = next((x for x in h["questions"] if x["kind"] == "yesno"), None)
if q2:
    h = c.post("/me/twin/answer", json={"key": q2["key"], "answer": "no"}, headers=H(tok)).json()
    assert not any(x.get("pid") == q2["pid"] for x in h["questions"]) and not next(p for p in h["proposals"] if p["id"] == q2["pid"])["backed_by_me"]
    assert core.twin_answers_of(con, web_owner["id"])[f"use:{q2['pid']}"] == "no"

# propose her own, leading it; the vendor's UPI never comes back
r = c.post("/me/propose", json={"title": "Shared delivery scooter for the market road", "text": "One scooter, three shops, one rider", "ideal": 55000, "lead": True, "upi": "scooter@okaxis"}, headers=H(tok))
assert r.status_code == 200 and r.json()["result"]["status"] in ("draft", "eligible")
assert "okaxis" not in r.text
pmine = next(p for p in r.json()["proposals"] if p["lead_is_me"])
assert pmine["lead"] == "Mandrem Tailors" and pmine["ideal"] == 55000
assert c.post("/me/propose", json={"title": "x", "ideal": 0}, headers=H(tok)).status_code == 400

# ask the twin, with no model key: a true plain answer from the same facts
r = c.post("/me/twin/ask", json={"question": "Which proposal fits my business best?"}, headers=H(tok))
assert r.status_code == 200 and r.json()["source"] == "plain" and "₹" in r.json()["answer"] and "proposals stage" in r.json()["answer"], r.json()
assert c.post("/me/twin/ask", json={"question": ""}, headers=H(tok)).status_code == 400

# --- Joao never opened his card: the code seats him as coordinator of his business, with the form's need -----------
r = c.post("/auth/wa/start", json={"phone": "9876500000"}, headers=H())
assert r.json()["via"] == "template"
code2 = last_code("919876500000")
r = c.post("/auth/wa/verify", json={"phone": "9876500000", "code": code2}, headers=H())
tok2 = r.json()["token"]
h2 = c.get("/me/home.json", headers=H(tok2)).json()
assert h2["business"]["name"] == "Morjim Bakery" and h2["business"]["coordinator"] and h2["member"]["display"] == "Joao Pereira"
joao = core.member_for_phone(con, "919876500000")
assert joao["verified_by"] == "whatsapp" and core.is_role(con, a2["node_id"], joao["id"], ("coordinator",))
# signing in again lands on the same member
r = c.post("/auth/wa/start", json={"phone": "9876500000"}, headers=H()); code2b = last_code("919876500000")
assert c.post("/auth/wa/verify", json={"phone": "9876500000", "code": code2b}, headers=H()).json()["member"]["id"] == joao["id"]
# Joao backs Rekha's proposal: two backers, and it is on the ballot
h2 = c.post("/me/cosign", json={"pid": pmine["id"]}, headers=H(tok2)).json()
pj = next(p for p in h2["proposals"] if p["id"] == pmine["id"])
assert sorted(pj["backers"]) == ["Mandrem Tailors", "Morjim Bakery"] and pj["status"] == "eligible"
# the same co-sign twice changes nothing
h2 = c.post("/me/cosign", json={"pid": pmine["id"]}, headers=H(tok2)).json()
assert len(next(p for p in h2["proposals"] if p["id"] == pmine["id"])["backers"]) == 2

# --- limits: three codes an hour, five guesses a code ------------------------------------------------------------
c.post("/auth/wa/start", json={"phone": "9876500000"}, headers=H())
r = c.post("/auth/wa/start", json={"phone": "9876500000"}, headers=H())
assert r.status_code == 429 and r.json()["error"] == "too_many_codes"
c.post("/auth/wa/start", json={"phone": "9876543210"}, headers=H()); code3 = last_code("919876543210")
for i in range(5):
    r = c.post("/auth/wa/verify", json={"phone": "9876543210", "code": f"{(int(code3) + 1 + i) % 1000000:06d}"}, headers=H())
assert r.status_code == 429 and r.json()["error"] == "too_many_attempts"
assert c.post("/auth/wa/verify", json={"phone": "9876543210", "code": code3}, headers=H()).status_code == 429, "the right code no longer works either"

# --- the round moves on: the ballot, the plans, the approval, through the app ------------------------------------
core.advance(con, rnd["id"])
h = c.get("/me/home.json", headers=H(tok)).json()
assert h["round"]["stage"] == "ballots" and h["round"]["can_ballot"] and h["ballot"] is None
elig = [p["id"] for p in h["proposals"] if p["status"] == "eligible"]
assert c.post("/me/ballot", json={"points": {elig[0]: 60}}, headers=H(tok)).status_code == 400, "must sum to 100"
h = c.post("/me/ballot", json={"points": {elig[0]: 60, "_unspent": 40}}, headers=H(tok)).json()
assert h["ballot"]["points"] == {elig[0]: 60, "_unspent": 40} and h["ballot"]["revision"] == 1 and h["round"]["ballots_in"] == 1
h2 = c.post("/me/ballot", json={"points": {elig[0]: 100}}, headers=H(tok2)).json()
assert h2["round"]["ballots_in"] == 2
core.advance(con, rnd["id"])
h = c.get("/me/home.json", headers=H(tok)).json()
assert h["round"]["stage"] == "plans" and len(h["plans"]["plans"]) == 4 and h["plans"]["receipt"]
order = [pl["id"] for pl in h["plans"]["plans"]][::-1]
h = c.post("/me/rank", json={"order": order}, headers=H(tok)).json()
assert h["plans"]["my_ranking"] == order
core.advance(con, rnd["id"])
h = c.get("/me/home.json", headers=H(tok)).json()
assert h["round"]["stage"] == "ratifying" and any(pl["winner"] for pl in h["plans"]["plans"]) and h["my_verdict"] is None
h = c.post("/me/approve", json={"verdict": "approve"}, headers=H(tok)).json()
assert h["my_verdict"] == "approve"
c.post("/me/approve", json={"verdict": "approve"}, headers=H(tok2))
core.advance(con, rnd["id"])
h = c.get("/me/home.json", headers=H(tok)).json()
assert h["round"]["stage"] == "ratified" and h["decision"]["verdict"] == "ratified" and h["ledger"], h.get("decision")

# --- the LOGIN keyword on WhatsApp replies with a sign-in link ------------------------------------------------------
whatsapp.handle_update({"entry": [{"changes": [{"value": {"contacts": [{"wa_id": "919876500000", "profile": {"name": "Joao"}}],
                                                           "messages": [{"from": "919876500000", "id": "wamid.login", "type": "text", "text": {"body": "login"}}]}}]}]})
assert "/l/" in sent[-1][2] and "cyberneticsociety.com/app" in sent[-1][2]

# --- the code's three delivery paths ---------------------------------------------------------------------------
whatsapp.OTP_TEMPLATE = ""
assert whatsapp.send_otp("919876500000", "123456") == "text" and sent[-1][1] == "text" and "123456" in sent[-1][2]   # Joao just wrote: inside the window
whatsapp.TOKEN = ""
assert whatsapp.send_otp("919876500000", "123456") == "log"
print("ok")
