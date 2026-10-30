"""The front door: a business registers on the public form, the pool's treasurer sees it with a card to send,
the owner opens the card (web or WhatsApp) and becomes the coordinator, the form's need is the node's first need,
the charter is accepted on the web, the export carries no phone."""
import os
import sys
import tempfile

os.environ["PB_DB"] = os.path.join(tempfile.mkdtemp(), "a.sqlite")
os.environ["PB_SECRET"] = "test"
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

form = dict(business="Mandrem Tailors", applicant="Rekha Naik", role="owner", phone="98765 43210", about="Tailoring, Mandrem market road", challenge="Finding customers in the off season",
            team_size="3", need="A scooter for deliveries, customers keep asking", share="Sewing machine evenings")
r = anon.post("/apply/commons", data=form)
assert r.status_code == 200 and "Mandrem Tailors" in r.text and "3210" in r.text and "98765" not in r.text, r.text[:400]
apps = core.applications_of(con, "commons"); assert len(apps) == 1
a = apps[0]
assert a["challenge"] == "Finding customers in the off season" and a["phone"] == "919876543210" and a["node_id"] == "commons/mandrem-tailors" and a["status"] == "new"
node = core.get_node(con, a["node_id"]); assert node["parent_id"] == "commons" and node["name"] == "Mandrem Tailors"
assert core.is_role(con, a["node_id"], tre["id"], ("treasurer",))   # the pool's treasurer rides along into every business

# the same phone again: an update, not a second business; a different business with the same slug gets its own id
r = anon.post("/apply/commons", data=dict(form, business="Mandrem Tailors & Co", need="A bigger shop sign"))
assert r.status_code == 200 and len(core.applications_of(con, "commons")) == 1
a = next(x for x in core.applications_of(con, "commons") if x["node_id"] == "commons/mandrem-tailors"); assert a["business"] == "Mandrem Tailors & Co" and core.get_node(con, a["node_id"])["name"] == "Mandrem Tailors & Co"
r = anon.post("/apply/commons", data=dict(form, phone="+91 91234 56789", applicant="Sunil"))
assert {x["node_id"] for x in core.applications_of(con, "commons")} == {"commons/mandrem-tailors", "commons/mandrem-tailors-2"}
# a bot and a bad number
assert anon.post("/apply/commons", data=dict(form, phone="5551112222", website="x")).status_code == 200 and len(core.applications_of(con, "commons")) == 2
r = anon.post("/apply/commons", data=dict(form, phone="12")); assert "doesn't look like" in r.text and len(core.applications_of(con, "commons")) == 2
assert core.normalize_phone("0 98765 43210") == "919876543210" and core.normalize_phone("+1 415 555 0100") == "14155550100" and core.normalize_phone("abc") == ""

# the treasurer sees the businesses and a "Send card" link that opens WhatsApp to the owner with the card inside
T = client(tre["id"])
r = T.get("/c/commons"); assert r.status_code == 200
assert "Registered businesses" in r.text and "Mandrem Tailors &amp; Co" in r.text and "wa.me/919876543210?text=" in r.text and "Send card" in r.text
assert "/j/" + a["token"] in __import__("urllib.parse").parse.unquote(r.text)
assert client().get("/c/commons").status_code == 401

# the owner opens the card on the web: coordinator, charter shown, need recorded, display set from the form
O = client()
r = O.get(f"/j/{a['token']}")
assert r.status_code == 200 and "You run <b>Mandrem Tailors &amp; Co</b>" in r.text and "works like this" in r.text and "Your team" in r.text, r.text[:600]
owner_id = web.signer.loads(O.cookies.get("pb"))["m"]
assert core.is_role(con, a["node_id"], owner_id, ("coordinator",))
assert db.row(con.execute("SELECT display FROM members WHERE id=?", (owner_id,)).fetchone())["display"] == "Rekha Naik"
assert [n["text"] for n in core.needs_of(con, a["node_id"], include_children=False)] == ["A bigger shop sign"]
assert core.applications_of(con, "commons", status="joined")[0]["id"] == a["id"]
assert not core.get_node(con, a["node_id"]).get("charter_at")
assert O.post(f"/n/{a['node_id']}/charter").status_code == 303 and core.get_node(con, a["node_id"])["charter_at"]
r = O.get(f"/c/{a['node_id']}"); assert "works like this" not in r.text
# opening the card twice is harmless; a stranger can't accept a charter
assert O.get(f"/j/{a['token']}").status_code == 200 and len(core.needs_of(con, a["node_id"], include_children=False)) == 1
assert client().post(f"/n/{a['node_id']}/charter").status_code == 401
# the team link on the welcome page joins staff straight in; the poster link on /c/ still needs approval
import re  # noqa: E402
team = re.search(r"/j/([A-Za-z0-9_-]+)", O.get(f"/j/{a['token']}").text).group(1)
S = client(); r = S.get(f"/j/{team}"); assert r.status_code == 200 and "You're in" in r.text
poster = re.search(r"/j/([A-Za-z0-9_-]+)", O.get(f"/c/{a['node_id']}").text).group(1)
assert "coordinator will confirm" in client().get(f"/j/{poster}").text
assert len(core.active_members(con, a["node_id"])) == 3   # owner, staff, treasurer
# the treasurer's view now says joined
assert "joined" in T.get("/c/commons").text

# the second business's owner opens the card on WhatsApp: the form's need is kept and the bot doesn't ask again
sent = []
class FakeSender:
    def text(self, to, body): sent.append((to, body))
    def buttons(self, to, body, buttons): sent.append((to, body))
    def list(self, to, body, button, rows): sent.append((to, body))
    def template(self, to, name, params): sent.append((to, name))
whatsapp.sender = FakeSender()
b = core.applications_of(con, "commons", status="new")[0]
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
r = T.get("/c/commons/applications.csv"); assert r.status_code == 200 and "business,applicant,role,phone,about,challenge" in r.text and "off season" in r.text and "919876543210" in r.text
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
assert "<option value=IN data-name='India' selected>" in anon.get("/apply/commons").text and "+1</option>" in anon.get("/apply/commons").text
np = core.normalize_phone
assert np("98765 43210", "91") == "919876543210" and np("098765 43210", "91") == "919876543210" and np("+91 98765 43210", "91") == "919876543210"
assert np("415 938 0000", "1") == "14159380000" and np("+1 415 938 0000", "1") == "14159380000" and np("07911 123456", "44") == "447911123456"
assert np("91 98765 43210", "91") == "919876543210" and np("12", "91") == ""
r = anon.post("/apply/commons", data=dict(form, business="Surf Cafe", applicant="Marcus", phone="415 938 0000", cc="US"))
assert r.status_code == 200 and next(x for x in core.applications_of(con, "commons") if x["business"] == "Surf Cafe")["phone"] == "14159380000"
print("APPLY OK · country code")
