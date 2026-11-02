"""The front door: a business registers on the public form, the pool's treasurer sees it with a card to send,
the owner opens the card (web or WhatsApp) and becomes the coordinator, the form's need is the node's first need,
the charter is accepted on the web, the export carries no phone."""
import os
import sys
import tempfile

os.environ["PB_DB"] = os.path.join(tempfile.mkdtemp(), "a.sqlite")
os.environ["PB_SECRET"] = "test"
os.environ["PB_TWIN_INLINE"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("PB_WA_NUMBER", None)
os.environ["PB_WA_PHONE_ID"] = "1"; os.environ["PB_WA_TOKEN"] = "t"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402
from publicbank import core, db, web, whatsapp  # noqa: E402

con = web.con
pool = core.create_node(con, "commons", "Goa Business Commons", kind="pool", pool_vote="nodes", cosign_min=2, match_pct=500)
tre = core.upsert_member(con, verified_by="coordinator", telegram_id=7, display="treasurer")
core.join_node(con, "commons", tre["id"], role="treasurer", auto_active=True)


def client(mid=None):
    c = TestClient(web.app, base_url="http://pb.test", follow_redirects=False)
    if mid: c.cookies.set("pb", web.signer.dumps({"m": mid}))
    return c


anon = client()
assert anon.get("/privacy").status_code == 200 and "privacy policy" in anon.get("/apply/commons").text
r = anon.get("/"); assert r.status_code == 200 and "Goa Business Commons" in r.text and "/apply/commons" in r.text
r = anon.get("/apply/commons"); assert r.status_code == 200 and "Register my business" in r.text and "name=phone" in r.text
assert anon.get("/apply/nope").status_code == 404
web.ALIASES["g"] = "commons"
assert anon.get("/apply/g").status_code == 200 and "action='/apply/g'" in anon.get("/apply/g").text and anon.get("/b/g").status_code == 200

form = dict(business="Mandrem Tailors", applicant="Rekha Naik", role="owner", phone="98765 43210", about="Tailoring, Mandrem market road", challenge=["marketing", "finance"], challenge_other="Finding customers in the off season",
            team_size="3", need="A scooter for deliveries, customers keep asking", share=["equipment"], share_other="Sewing machine evenings",
            address="Shop 3, market road, Mandrem", place="",   # no place picked: the server reads it off the address
            registrations=["none"], age_band="established", pin="403 527")
# registering signs the applicant in as the business's coordinator, sends them to step 2, and tells the treasurer on Telegram
os.environ["TELEGRAM_BOT_TOKEN"] = "t"
tg_sent = []
web.httpx.post = lambda url, json=None, timeout=None, **kw: tg_sent.append((url.split("/bot")[0], json)) or type("R", (), {"status_code": 200})()
R = client()
r = R.post("/apply/commons", data=form)
assert r.status_code == 303 and r.headers["location"] == "/start/commons/mandrem-tailors/values", (r.status_code, r.text[:300])
owner_id = web.signer.loads(R.cookies.get("pb"))["m"]
assert len(tg_sent) == 1 and tg_sent[0][1]["chat_id"] == 7 and "Mandrem Tailors" in tg_sent[0][1]["text"] and "/c/commons" in tg_sent[0][1]["text"], tg_sent
apps = core.applications_of(con, "commons"); assert len(apps) == 1
a = apps[0]
assert a["challenge"] == "marketing, finance, Finding customers in the off season" and a["share"] == "equipment, Sewing machine evenings" and a["phone"] == "919876543210"
assert a["node_id"] == "commons/mandrem-tailors" and a["status"] == "joined"
node = core.get_node(con, a["node_id"]); assert node["parent_id"] == "commons" and node["name"] == "Mandrem Tailors"
assert core.is_role(con, a["node_id"], owner_id, ("coordinator",)) and not core.is_role(con, a["node_id"], tre["id"], ("treasurer",))   # the treasurer is not a member of each business
assert core.get_node(con, a["node_id"])["ratify_pct"] == 51
assert db.row(con.execute("SELECT display, verified_by FROM members WHERE id=?", (owner_id,)).fetchone()) == {"display": "Rekha Naik", "verified_by": "web"}
assert [n["text"] for n in core.needs_of(con, a["node_id"], include_children=False)] == ["A scooter for deliveries, customers keep asking"]
# step 2: sliders; a stranger can't see them
r = R.get("/start/commons/mandrem-tailors/values"); assert r.status_code == 200 and r.text.count("type=range") == 5 and "What matters" in r.text
assert client().get("/start/commons/mandrem-tailors/values").status_code == 401
r = R.post("/start/commons/mandrem-tailors/values", data={"earn": "9", "horizon": "3", "scope": "7", "means": "5", "risk": "10"})
assert r.status_code == 303 and r.headers["location"] == "/start/commons/mandrem-tailors/done"
vv = core.values_of(con, "commons"); assert len(vv) == 1 and vv[0]["vector"] == {"earn": 9, "horizon": 3, "scope": 7, "means": 5, "risk": 10} and vv[0]["node_name"] == "Mandrem Tailors"
R.post("/start/commons/mandrem-tailors/values", data={"earn": "0"}); assert core.values_of(con, "commons")[0]["vector"]["earn"] == 0 and len(core.values_of(con, "commons")) == 1
# step 3: straight to the table; the sliders counted as joining
assert core.get_node(con, a["node_id"])["charter_at"]
r = R.get("/start/commons/mandrem-tailors/done"); assert r.status_code == 200 and "Five rules" not in r.text and "is in Goa Business Commons" in r.text
assert "Your team" not in r.text and "/j/" not in r.text and "What happens next" in r.text and "Finish" in r.text
r = R.get("/start/commons/mandrem-tailors/finished"); assert r.status_code == 200 and "That's it" in r.text and "confetti" in r.text and "/j/" in r.text
assert client().get("/start/commons/mandrem-tailors/finished").status_code == 401
import re  # noqa: E402
team = re.search(r"/j/([A-Za-z0-9_-]+)", r.text).group(1)
S = client(); r = S.get(f"/j/{team}"); assert r.status_code == 200 and "You're in" in r.text
assert len(core.active_members(con, a["node_id"])) == 2   # owner, staff
assert client(tre["id"]).get(f"/c/{a['node_id']}").status_code == 200   # the pool's treasurer can still open each business's page
# the same phone again: already registered, the new need is kept, no second business
r = anon.post("/apply/commons", data=dict(form, need="A bigger shop sign"))
assert r.status_code == 200 and "Already registered" in r.text and len(core.applications_of(con, "commons")) == 1
assert [n["text"] for n in core.needs_of(con, a["node_id"], include_children=False)] == ["A scooter for deliveries, customers keep asking", "A bigger shop sign"]
# the shop's location is required: no lane line, or no place anywhere, and the form comes back
r = client().post("/apply/commons", data=dict(form, business="No Address Co", phone="+91 90000 22222", address="", place=""))
assert r.status_code == 200 and "Tell us where the shop is" in r.text and not any(x["business"] == "No Address Co" for x in core.applications_of(con, "commons"))
# another business, same slug, its own id; nothing ticked, a bot, a bad number
r = client().post("/apply/commons", data=dict(form, phone="+91 91234 56789", applicant="Sunil")); assert r.status_code == 303
assert {x["node_id"] for x in core.applications_of(con, "commons")} == {"commons/mandrem-tailors", "commons/mandrem-tailors-2"}
assert "Tick at least one" in anon.post("/apply/commons", data=dict(form, phone="5551112222", share=[], share_other="")).text and len(core.applications_of(con, "commons")) == 2
assert anon.post("/apply/commons", data=dict(form, phone="5551112222", website="x")).status_code == 200 and len(core.applications_of(con, "commons")) == 2
r = anon.post("/apply/commons", data=dict(form, phone="12")); assert "doesn't look like" in r.text and len(core.applications_of(con, "commons")) == 2
assert core.normalize_phone("0 98765 43210") == "919876543210" and core.normalize_phone("+1 415 555 0100") == "14155550100" and core.normalize_phone("abc") == ""

# the treasurer sees both, verifies one post-hoc; the un-continued one still has a Send-card link
T = client(tre["id"])
r = T.get("/c/commons"); assert r.status_code == 200 and "Registered businesses" in r.text and "Verify" in r.text and "Download CSV" in r.text
assert client().get("/c/commons").status_code == 401
assert T.post("/c/commons/verify", data={"application_id": a["id"]}).status_code == 303
assert core.applications_of(con, "commons", status="verified")[0]["id"] == a["id"] and "verified" in T.get("/c/commons").text
# the address stays with the application; the place it resolved to travels to the node and, once verified, to the map
assert a["address"] == "Shop 3, market road, Mandrem" and a["place_id"] == "mandrem", (a["address"], a["place_id"])
assert core.get_node(con, a["node_id"])["place_id"] == "mandrem"
assert [(b["name"], b["place_id"]) for b in core.verified_businesses(con, "commons")] == [("Mandrem Tailors", "mandrem")]
assert "Mandrem" in T.get("/c/commons").text and "address" in T.get("/c/commons/applications.csv").text
assert "name=address" in anon.get("/apply/commons").text and "Mandrem · Pernem" in anon.get("/apply/commons").text and "Use my location" in anon.get("/apply/commons").text
# the business twin: the three optional answers are kept, the owner sees their schemes, nobody else does
assert a["registrations"] == "none" and not a["owner_tags"] and a["age_band"] == "established" and a["pin"] == "403527"
assert "The owner is" not in anon.get("/apply/commons").text and "owner_tags" not in anon.get("/apply/commons").text
r = R.get("/schemes/commons/mandrem-tailors")
assert r.status_code == 200 and "Udyam not held" in r.text and "register the business on the Udyam portal" in r.text and "unlikely for this business" in r.text, r.text[:400]
assert client().get("/schemes/commons/mandrem-tailors").status_code == 401 and T.get("/schemes/commons/mandrem-tailors").status_code == 403
assert "What the government offers Mandrem Tailors" in R.get("/start/commons/mandrem-tailors/done").text
from publicbank import businesses  # noqa: E402
bc = businesses.card(con, "commons/mandrem-tailors")
assert bc["summary"]["schemes"] > 100 and bc["slots"] and bc["profile"]["woman"] is None and bc["unlocks"]
pd = businesses.place_draw(con, "commons", "mandrem")
assert pd and pd["businesses"] == 1 and pd["eligible_any"] > 0 and "Mandrem Tailors" not in str(pd), "counts only; no business is named"
assert R.post("/c/commons/verify", data={"application_id": a["id"]}).status_code == 403   # a business can't verify itself
# only verified businesses count: the second business is registered and chartered but unverified, so it cannot cast the pool's ballot
S3 = client(); S3.post("/apply/commons", data=dict(form, business="Lane Cafe", phone="+91 90000 11111", applicant="Joao"))
pid2 = next(x["node_id"] for x in core.applications_of(con, "commons") if x["business"] == "Lane Cafe")
S3.post(f"/n/{pid2}/charter?next=/start/{pid2}/done")
assert {k["id"] for k in core.affected_children(con, "commons")} == {"commons/mandrem-tailors"}
# no round at proposals right now (the first one was closed above), so the twin had nothing to draft into; the admin button says so
assert T.post("/c/commons/twin").status_code == 400
rc = core.open_round(con, "commons", 100000 * 100)
assert {v["id"] for v in core.voters(con, rc)} == {owner_id}
cafe_owner = web.signer.loads(S3.cookies.get("pb"))["m"]
assert not core.can_ballot(con, rc, cafe_owner) and core.can_ballot(con, rc, owner_id)
T.post("/c/commons/verify", data={"application_id": next(x["id"] for x in core.applications_of(con, "commons") if x["business"] == "Lane Cafe")})
assert core.can_ballot(con, rc, cafe_owner) and len(core.voters(con, rc)) == 2
with db.tx(con): con.execute("UPDATE rounds SET status='closed' WHERE id=?", (rc["id"],))
b2 = next(x for x in core.applications_of(con, "commons") if x["node_id"] == "commons/mandrem-tailors-2")
with db.tx(con): con.execute("UPDATE applications SET status='new' WHERE id=?", (b2["id"],))   # as if Sunil had closed the tab before step 2
assert "Send card" in T.get("/c/commons").text and "/j/" + b2["token"] in __import__("urllib.parse").parse.unquote(T.get("/c/commons").text)
# that card still works on the web for a second person
O = client(); r = O.get(f"/j/{b2['token']}"); assert r.status_code == 200 and "You run <b>Mandrem Tailors" in r.text
assert client().post(f"/n/{a['node_id']}/charter").status_code == 401
poster = re.search(r"/j/([A-Za-z0-9_-]+)", R.get(f"/c/{a['node_id']}").text).group(1)
assert "coordinator will confirm" in client().get(f"/j/{poster}").text

# the second business's owner opens the card on WhatsApp: the form's need is kept and the bot doesn't ask again
sent = []
class FakeSender:
    def text(self, to, body): sent.append((to, body))
    def buttons(self, to, body, buttons): sent.append((to, body))
    def list(self, to, body, button, rows): sent.append((to, body))
    def template(self, to, name, params): sent.append((to, name))
whatsapp.sender = FakeSender()
b = b2
with db.tx(con): con.execute("DELETE FROM needs WHERE node_id=?", (b["node_id"],)); con.execute("UPDATE applications SET status='new' WHERE id=?", (b["id"],))
def wa(phone, text):
    whatsapp.handle_update({"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "contacts": [{"profile": {"name": "Sunil"}, "wa_id": phone}],
        "messages": [{"from": phone, "id": "wamid." + text, "timestamp": "0", "type": "text", "text": {"body": text}}]}}]}]})
wa("919123456789", "JOIN " + b["token"])
assert any("coordinator for Mandrem Tailors" in s[1] for s in sent), sent
assert [n["text"] for n in core.needs_of(con, b["node_id"], include_children=False)] == ["A scooter for deliveries, customers keep asking"]
whatsapp.handle_update({"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
    "messaging_product": "whatsapp", "contacts": [{"profile": {"name": "Sunil"}, "wa_id": "919123456789"}],
    "messages": [{"from": "919123456789", "id": "wamid.ch", "timestamp": "0", "type": "interactive",
                  "interactive": {"type": "button_reply", "button_reply": {"id": f"ch:{b['node_id']}:y", "title": "Accept"}}}]}}]}]})
assert core.get_node(con, b["node_id"])["charter_at"] and not any("one thing your business needs" in s[1] for s in sent)
assert core.applications_of(con, "commons", status="new") == []

# nothing private leaves: the export has no phone, no applicant, no token
ex = str(core.export_node(con, "commons"))
assert "9876543210" not in ex and "9123456789" not in ex and "Rekha" not in ex and a["token"] not in ex
assert "9876543210" not in client().get("/b/commons").text

print("APPLY OK · landing · form → node + card · same phone updates · honeypot · bad number · treasurer sees businesses + Send card · card on web → coordinator, need kept, charter · team link · card on WhatsApp → need kept, not asked twice · export clean")

# the CSV is the treasurer's, and only theirs
r = T.get("/c/commons/applications.csv"); assert r.status_code == 200 and "business,applicant,role,phone,about,place_id,pin,address,challenge" in r.text and "off season" in r.text and "919876543210" in r.text
assert client().get("/c/commons/applications.csv").status_code == 401 and O.get("/c/commons/applications.csv").status_code == 403
print("APPLY OK · csv")

# rupees in Indian grouping; a matching pool's board shows the ceiling and the rule before any money is in
from publicbank.convo import rupees  # noqa: E402
assert rupees(500000 * 100) == "₹5,00,000" and rupees(18000 * 100) == "₹18,000" and rupees(12345678 * 100) == "₹1,23,45,678" and rupees(500) == "₹5" and rupees(0) == "₹0"
rnd = core.open_round(con, "commons", 500000 * 100)
bt = client().get("/b/commons").text
assert "up to ₹5,00,000" in bt and "5×" in bt and "Nothing in yet" in bt, bt[:800]
core.contribute(con, a["node_id"], 1000 * 100, member_id=owner_id)
bt = client().get("/b/commons").text
assert "₹6,000" in bt and "= ₹1,000 from 1 nodes + match 5× (up to ₹5,00,000)" in bt, bt[:900]
print("APPLY OK · rupees · board ceiling")
core.create_node(con, "empty-pool", "Empty Pool", kind="pool", match_pct=500); core.open_round(con, "empty-pool", 500000 * 100)
assert "up to ₹5,00,000" in client().get("/b/empty-pool").text   # even before any business exists
print("APPLY OK · empty matching pool")
# the country selector: India by default; the number is national, the code comes from the box
assert "<option value=IN data-short='🇮🇳 +91' selected>🇮🇳 +91 India</option>" in anon.get("/apply/commons").text and "+383 Kosovo</option>" in anon.get("/apply/commons").text
np = core.normalize_phone
assert np("98765 43210", "91") == "919876543210" and np("098765 43210", "91") == "919876543210" and np("+91 98765 43210", "91") == "919876543210"
assert np("415 938 0000", "1") == "14159380000" and np("+1 415 938 0000", "1") == "14159380000" and np("07911 123456", "44") == "447911123456"
assert np("91 98765 43210", "91") == "919876543210" and np("12", "91") == ""
r = anon.post("/apply/commons", data=dict(form, business="Surf Cafe", applicant="Marcus", phone="415 938 0000", cc="US"))
assert r.status_code == 303 and next(x for x in core.applications_of(con, "commons") if x["business"] == "Surf Cafe")["phone"] == "14159380000"
print("APPLY OK · country code")

# the last step shows the business's own need, lets it propose, and shows drafts from others with lead/back buttons
rnd2 = core.current_round(con, "commons")
# with a round at proposals, re-accepting a charter triggers the twin: the two scooter needs become one draft
core.add_need(con, pid2, cafe_owner, "A delivery scooter for lunch orders to customers on the lane")
S3.post(f"/n/{pid2}/charter?next=/start/{pid2}/done")
tw = db.rows(con.execute("SELECT * FROM proposals WHERE round_id=? AND source='twin'", (rnd2["id"],)).fetchall())
scooter = [p for p in tw if "scooter" in p["title"].lower() or "deliver" in p["title"].lower()]
assert len(scooter) == 1 and "Mandrem Tailors" in scooter[0]["text"] and "Lane Cafe" in scooter[0]["text"], tw   # one draft for the scooter needs, the cafe attached
assert all(n["status"] == "drafted" for n in core.needs_of(con, "commons", status=None))   # every need, including lone ones, is drafted
r = T.get("/c/commons"); assert "The twin" in r.text and "drafts in this round" in r.text and "Draft proposals from needs" in r.text
assert T.post("/c/commons/twin").headers["location"].endswith("?drafted=0")   # nothing left to group
r = R.get("/start/commons/mandrem-tailors/done"); assert "On the table" in r.text and "Propose your own" in r.text and r.text.index("Propose your own") < r.text.index("fit your business best"), r.text[:300]
# the page asks the server to draft from an open need; the fresh card comes back as HTML with the draft on it
with db.tx(con): con.execute("UPDATE needs SET status='open' WHERE node_id='commons/mandrem-tailors'")
r = R.get("/start/commons/mandrem-tailors/done"); assert "Drafting a proposal from" in r.text and "data-draft='/start/commons/mandrem-tailors/draft'" in r.text
r = R.post("/start/commons/mandrem-tailors/draft", headers={"X-Requested-With": "fetch"})
assert r.status_code == 200 and "id=table" in r.text and "Drafting a proposal" not in r.text and "suggested from what businesses" in r.text, r.text[:400]
assert all(n["status"] == "drafted" for n in core.needs_of(con, "commons/mandrem-tailors", include_children=False, status=None))
assert client().post("/start/commons/mandrem-tailors/draft").status_code == 401
r = R.post("/start/commons/mandrem-tailors/done", data={})  # nothing: pages are GET
r = R.post("/n/commons/propose?next=/start/commons/mandrem-tailors/done", data={"title": "A shared delivery scooter", "ideal": "60000", "text": "", "lead": "on"})
assert r.status_code == 303 and r.headers["location"] == "/start/commons/mandrem-tailors/done"
r = R.get("/start/commons/mandrem-tailors/done"); assert "A shared delivery scooter" in r.text and "backed by Mandrem Tailors" in r.text and "you lead this ✓" in r.text
# the second business backs it from its own last step
S2 = S3
r = S2.get(f"/start/{pid2}/done"); assert "Back this" in r.text
pid = db.row(con.execute("SELECT id FROM proposals WHERE round_id=? AND title='A shared delivery scooter'", (rnd2["id"],)).fetchone())["id"]
assert f"value='{pid}'" in r.text
assert S2.post(f"/n/commons/cosign?next=/start/{pid2}/done", data={"pid": pid}).headers["location"] == f"/start/{pid2}/done"
assert "backed ✓" in S2.get(f"/start/{pid2}/done").text and db.row(con.execute("SELECT status FROM proposals WHERE id=?", (pid,)).fetchone())["status"] == "eligible"
print("APPLY OK · on the table")

# personalised order: a business leaning "earn more / things" sees the scooter first, marked for them;
# one leaning "spend less / proven" sees billing first
from publicbank import twin  # noqa: E402
t = twin.tags_for("A shared delivery scooter for the market lane, orders to customers at home"); assert t["earn"] < 0 and t["means"] < 0, t
t = twin.tags_for("One GST billing and stock software licence for several shops"); assert t["earn"] > 0 and t["risk"] < 0, t
t = twin.tags_for("Try an AI tool for Instagram posts"); assert t["risk"] > 0 and t["earn"] < 0, t
t = twin.tags_for("zzz"); assert all(v == 0 for k, v in t.items() if k != "_conf") and not any(t["_conf"].values())
R.post("/n/commons/propose?next=/start/commons/mandrem-tailors/done", data={"title": "GST billing software licence for several shops", "ideal": "20000", "text": "One licence, shared.", "lead": "on"})
R.post("/n/commons/propose?next=/start/commons/mandrem-tailors/done", data={"title": "Beach clean-up kit and bins for the lane", "ideal": "15000", "text": "", "lead": "on"})
allp = db.rows(con.execute("SELECT * FROM proposals WHERE round_id=? AND status IN ('draft','eligible') ORDER BY created_at", (rnd2["id"],)).fetchall())
R.post("/start/commons/mandrem-tailors/values", data={"earn": "1", "horizon": "5", "scope": "2", "means": "1", "risk": "5"})
ranked = twin.rank_for(con, "commons/mandrem-tailors", allp); order = [p["title"] for p in ranked]
assert order[0].startswith("Shared:") and ranked[0]["suggested"] and "A shared delivery scooter" in order[:3], order   # the twin's drafts from its own needs first, the scooter close behind
S2.post(f"/start/{pid2}/values", data={"earn": "10", "horizon": "5", "scope": "2", "means": "1", "risk": "1"})
order2 = [p["title"] for p in twin.rank_for(con, pid2, allp)]
assert order2.index(next(t for t in order2 if t.startswith("GST billing"))) < order2.index("A shared delivery scooter"), order2
r = S2.get(f"/start/{pid2}/done"); lst = r.text[r.text.index("fit your business best"):]; assert lst.index("GST billing") < lst.index("A shared delivery scooter")
assert "for you" in R.get("/start/commons/mandrem-tailors/done").text
# a business with no sliders yet gets the neutral vector and still a stable order
assert len(twin.rank_for(con, b2["node_id"], allp)) == len(allp)
# the slider page shows poles, not a 1-10 scale
r = R.get("/start/commons/mandrem-tailors/values"); assert "Earn more" in r.text and "Spend less" in r.text and "The whole lane" in r.text and "not at all" not in r.text
print("APPLY OK · personalised order")
# backing from the page script answers JSON and the row updates without a reload
pid3 = next(p["id"] for p in allp if p["title"].startswith("GST billing"))
r = client(tre["id"]).post("/n/commons/cosign", data={"pid": pid3}, headers={"X-Requested-With": "fetch"})
assert r.status_code == 400 and r.json()["error"] in ("not_coordinator", "not_a_child", "not_a_member"), r.text
r = S2.post("/n/commons/cosign", data={"pid": pid3}, headers={"X-Requested-With": "fetch"})
assert r.status_code == 200 and r.json()["status"] == "eligible" and "Lane Cafe" in r.json()["backers"] and pid2 in r.json()["signs"], r.text
r = S2.post("/n/commons/cosign", data={"pid": pid3}, headers={"X-Requested-With": "fetch"}); assert r.status_code in (200, 400)
print("APPLY OK · in-place backing")

# a login link survives a chat app's preview fetch: GET shows Continue and spends nothing; POST signs in
lt = core.mint_token(con, "login", member_id=tre["id"])
c0 = client(); r = c0.get(f"/l/{lt}?next=/c/commons"); assert r.status_code == 200 and "Continue" in r.text and "treasurer" in r.text and not c0.cookies.get("pb")
r = c0.get(f"/l/{lt}"); assert r.status_code == 200 and "Continue" in r.text   # a second preview fetch is still fine
r = c0.post(f"/l/{lt}", data={"next": "/c/commons"}); assert r.status_code == 303 and r.headers["location"] == "/c/commons" and c0.cookies.get("pb")
assert c0.get("/c/commons").status_code == 200
assert "Link expired" in client().post(f"/l/{lt}", data={}).text   # spent
# /me: live rounds first, earlier nodes folded into one line
core.create_node(con, "oldpool", "Edge City 2025", kind="pool"); core.join_node(con, "oldpool", tre["id"], role="treasurer", auto_active=True)
ro = core.open_round(con, "oldpool", 1000 * 100)
with db.tx(con): con.execute("UPDATE rounds SET status='paid' WHERE id=?", (ro["id"],))
r = client(tre["id"]).get("/me"); assert "Earlier: " in r.text and r.text.index("Goa Business Commons") < r.text.index("Earlier: ") and "Edge City 2025" in r.text[r.text.index("Earlier: "):]
print("APPLY OK · login link two-step · treasurer told · /me folded")

# the notes
r = anon.get("/notes/simulating-a-thousand-businesses"); assert r.status_code == 200 and "Method of Equal Shares" in r.text and "equalshares.net" in r.text
assert anon.get("/notes/nope").status_code == 404 and "/notes/simulating-a-thousand-businesses" in anon.get("/").text
r = anon.get("/notes/what-is-a-digital-twin"); assert r.status_code == 200 and "Porto Alegre" in r.text and "Varanasi" in r.text and "World Development" in r.text and "href='/twin'" in r.text
print("APPLY OK · notes")

# removing one business from the pool leaves the rest, strips its backing, anonymises its owner
from publicbank import wipe_businesses  # noqa: E402
before = len(core.children(con, "commons"))
res = wipe_businesses.remove_business(con, pid2)
assert res["children_now"] == before - 1 and not db.row(con.execute("SELECT 1 FROM nodes WHERE id=?", (pid2,)).fetchone())
assert db.row(con.execute("SELECT display, wa_phone, verified_by FROM members WHERE id=?", (cafe_owner,)).fetchone()) == {"display": None, "wa_phone": None, "verified_by": "removed"}
assert all(pid2 not in db.j(p["cosign_nodes"], []) for p in db.rows(con.execute("SELECT cosign_nodes FROM proposals WHERE round_id=?", (rnd2["id"],)).fetchall()))
assert "Business" in client().get("/b/commons").text and "businesses · one vote each" in client().get("/b/commons").text
print("APPLY OK · remove one business")

# the front page is the home pool: intro, the pot with what's paid, approved, asked and remaining, a way in, the record
web.HOME_POOL = "commons"
r = anon.get("/"); assert r.status_code == 200
assert "Register your business" in r.text and "action='/apply/commons'" in r.text and "class=track" in r.text and "remaining ₹" in r.text and "asked for ₹" in r.text, r.text[:500]
assert "How it works" in r.text and r.text.count("<li class=") == 11 and r.text.count("<li class='now'>") == 2 and "Funding proposals" in r.text and "class='seal" in r.text and "/notes/simulating-a-thousand-businesses" in r.text
assert r.text.index("class='card hero'") < r.text.index("class=track") < r.text.index("Register your business") < r.text.index("Who's in") < r.text.index("Funding proposals") < r.text.index("How it works") < r.text.index("Timeline") < r.text.index("For the record")
assert "/notes/what-is-a-digital-twin'>Learn more</a>" in r.text and "<ol class=timeline" in r.text and "First round closes" in r.text and "Final report" in r.text and "id=ctwin" not in r.text and "What is an AI digital twin?" in r.text
assert "href='/apply/commons?propose=1'>Propose your own</a>" in r.text and r.text.index("Funding proposals") < r.text.index("Propose your own") < r.text.index("What is an AI digital twin?")
ra = anon.get("/apply/commons?propose=1"); assert ra.status_code == 200 and "class=came" in ra.text and "proposal form is on the page after" in ra.text
assert "class=came" not in anon.get("/apply/commons").text
assert web._timeline(web.LAUNCH).count("<li class='now'>") == 1 and web._timeline(web.LAUNCH + web._timedelta(days=400)).count("class='done'") == 3
assert "class=primary" in r.text
assert "class=thumb" in r.text and "href='/apply/commons?for=" in r.text   # the thumb count on each proposal leads into registering
b = web._budget(core.get_node(con, "commons"), core.current_round(con, "commons"))
assert b["pot"] == core.spendable_budget(con, core.current_round(con, "commons")) and b["paid"] == 0 and b["approved"] == 0 and b["asked"] > 0 and b["remaining"] == b["pot"] and b["proposals"] >= 3, b
print("APPLY OK · home page")
