"""Full web flow with a test client: join link → approval → proposals → ballots → plans → ranking → approval → payouts → export."""
import os, re, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PB_DB"] = os.path.join(tempfile.mkdtemp(), "w.sqlite"); os.environ["PB_BOT_USERNAME"] = "PublicBankBot"
from fastapi.testclient import TestClient  # noqa: E402
from publicbank import core, db, web  # noqa: E402

con = web.con
core.create_node(con, "goa-2026", "Village pool", kind="pool")
core.create_node(con, "goa-2026/res-a", "Residency A", parent_id="goa-2026", levy_pct=20)
coord = core.upsert_member(con, verified_by="coordinator", telegram_id=1); core.join_node(con, "goa-2026/res-a", coord["id"], role="coordinator", auto_active=True)
tre = core.upsert_member(con, verified_by="coordinator", telegram_id=2); core.join_node(con, "goa-2026/res-a", tre["id"], role="treasurer", auto_active=True)
rnd = core.open_round(con, "goa-2026/res-a", 100_000 * 100)

def client(mid=None):
    c = TestClient(web.app, base_url="http://pb.test", follow_redirects=False)
    if mid: c.cookies.set("pb", web.signer.dumps({"m": mid}))
    return c

C = client(coord["id"])
tok = re.search(r"/j/([A-Za-z0-9_-]+)", C.get("/c/goa-2026/res-a").text).group(1)
resid = []
for i in range(6):
    c = client(); r = c.get(f"/j/{tok}"); assert r.status_code == 200 and "t.me/PublicBankBot" in r.text
    mid = web.signer.loads(c.cookies.get("pb"))["m"]; resid.append(c)
    assert c.get("/n/goa-2026/res-a").status_code == 403
    assert C.post("/c/goa-2026/res-a/approve-member", data={"member_id": mid}).status_code == 303
assert len(core.active_members(con, "goa-2026/res-a")) == 8
for i, (t, idl, mn) in enumerate([("Water filter", 40000, 30000), ("Kids workshop", 30000, 20000), ("Closing music", 30000, 10000)]):
    assert resid[i].post("/n/goa-2026/res-a/propose", data={"title": t, "text": "…", "ideal": idl, "min": mn, "upi": f"lead{i}@upi", "lead": "on"}).status_code == 303
assert C.post("/c/goa-2026/res-a/advance", data={"step": "freeze"}).status_code == 303
props = core.eligible_proposals(con, rnd["id"]); assert len(props) == 3
for k, c in enumerate(resid + [C, client(tre["id"])]):
    w = [[60, 30, 10], [20, 60, 20], [10, 20, 70]][k % 3]
    data = {f"pt_{p['id']}": w[i] for i, p in enumerate(props)}; data["pt__unspent"] = 0
    assert c.post("/n/goa-2026/res-a/ballot", data=data).status_code == 303
assert "points_must_sum_100" in resid[0].post("/n/goa-2026/res-a/ballot", data={f"pt_{props[0]['id']}": 50}).text
assert C.post("/c/goa-2026/res-a/advance", data={"step": "compute"}).status_code == 303
r = resid[0].get("/n/goa-2026/res-a"); assert "Your receipt" in r.text and "Rank the plans" in r.text
plans = core.plans_of(con, rnd["id"])
for c in resid: assert c.post("/n/goa-2026/res-a/rank", data={f"r{i}": p["id"] for i, p in enumerate(plans)}).status_code == 303
assert C.post("/c/goa-2026/res-a/advance", data={"step": "winner"}).status_code == 303
assert "Approve the winning plan" in resid[0].get("/n/goa-2026/res-a").text
for c in resid[:5]: assert c.post("/n/goa-2026/res-a/approve", data={"verdict": "approve"}).status_code == 303
assert C.post("/c/goa-2026/res-a/advance", data={"step": "close"}).status_code == 303
d = db.row(con.execute("SELECT * FROM decisions").fetchone()); assert d["verdict"] == "ratified" and d["approvals"] == 5 and d["eligible"] == 8
T = client(tre["id"]); assert "upi://pay?pa=lead0%40upi" in T.get("/treasury").text
pid = db.row(con.execute("SELECT id FROM payouts LIMIT 1").fetchone())["id"]
assert T.post("/treasury/paid", data={"payout_id": pid, "reference": "UPI123"}).status_code == 303
ex = client().get("/export/goa-2026/res-a").json(); assert ex["node"]["members"] == 8 and ex["tallies"][0]["approvals"] == 5
assert resid[0].get("/treasury").status_code == 403 and client().get("/me").status_code == 401
print("WEB OK · stage:", core.get_round(con, rnd["id"])["status"], "· decision", d["verdict"], d["approvals"], "/", d["eligible"], "· levy ₹", ex["contributions"][0]["amount"] // 100)
