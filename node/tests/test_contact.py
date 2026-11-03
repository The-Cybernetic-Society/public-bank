"""The Write-to-us form on cyberneticsociety.com: a cross-origin POST to /contact becomes one Telegram message to the
people in PB_CONTACT_TELEGRAM (else the default pool's treasurers); bots and floods get nothing."""
import os
import sys
import tempfile

os.environ["PB_DB"] = os.path.join(tempfile.mkdtemp(), "c.sqlite")
os.environ["PB_SECRET"] = "test"
os.environ["PB_TWIN_INLINE"] = "1"
os.environ["TELEGRAM_BOT_TOKEN"] = "tok"
os.environ.pop("PB_CONTACT_TELEGRAM", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402
from publicbank import core, web  # noqa: E402

con = web.con
core.create_node(con, "goa-business-commons", "Goa Business Commons", kind="pool", pool_vote="nodes", cosign_min=2, match_pct=500)
tre = core.upsert_member(con, verified_by="coordinator", telegram_id=4242, display="treasurer")
core.join_node(con, "goa-business-commons", tre["id"], role="treasurer", auto_active=True)
web.ALIASES["goa"] = "goa-business-commons"
sent = []
web.httpx.post = lambda url, json=None, timeout=None, **kw: sent.append(json) or type("R", (), {"status_code": 200})()

c = TestClient(web.app, base_url="http://pb.test")
good = dict(name="Rekha Naik", org="Mandrem panchayat", place="Pernem, North Goa", want="round", contact="98765 43210",
            message="We would like to run a round for the market road shops.", website="", t="9000")

# preflight from the static site, then the message itself
r = c.options("/contact", headers={"Origin": "https://cyberneticsociety.com"})
assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*" and "POST" in r.headers["access-control-allow-methods"]
r = c.post("/contact", json=good, headers={"Origin": "https://cyberneticsociety.com"})
assert r.status_code == 200 and r.json() == {"ok": True} and r.headers["access-control-allow-origin"] == "*"
assert len(sent) == 1 and sent[0]["chat_id"] == 4242
assert "Rekha Naik, Mandrem panchayat" in sent[0]["text"] and "run one round in one place" in sent[0]["text"] and "market road" in sent[0]["text"]

# form-encoded works too (no JS fallback), and PB_CONTACT_TELEGRAM overrides the treasurers
os.environ["PB_CONTACT_TELEGRAM"] = "11, 22"
r = c.post("/contact", data=good)
assert r.status_code == 200 and [m["chat_id"] for m in sent[1:]] == [11, 22]
del os.environ["PB_CONTACT_TELEGRAM"]

# honeypot filled or submitted within three seconds: a quiet yes, nothing sent
n = len(sent)
assert c.post("/contact", json=dict(good, website="http://spam")).json() == {"ok": True}
assert c.post("/contact", json=dict(good, t="800")).json() == {"ok": True}
assert len(sent) == n

# too short: a 400 with a reason
r = c.post("/contact", json=dict(good, message="hi"))
assert r.status_code == 400 and "few words" in r.json()["error"] and len(sent) == n

# five an hour per address, then 429
for _ in range(5):
    c.post("/contact", json=good)
assert c.post("/contact", json=good).status_code == 429

# no token: still ok to the visitor, nothing sent
del os.environ["TELEGRAM_BOT_TOKEN"]
web._CONTACT_SEEN.clear()
n = len(sent)
assert c.post("/contact", json=good).json() == {"ok": True} and len(sent) == n
print("ok")
