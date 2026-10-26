"""End-to-end: a residency node under a village pool, one full round, levy, payouts, export."""
import json, os, random, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PB_DB"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
from publicbank import core, db  # noqa: E402

con = db.connect()
R = random.Random(3)

village = core.create_node(con, "goa-2026", "Edge City Goa 2026 · village pool", kind="pool", ratify_pct=60)
res = core.create_node(con, "goa-2026/residency-a", "Residency A", parent_id="goa-2026", levy_pct=20, ratify_pct=60)

# 12 members join by deep link; coordinator approves
coord = core.upsert_member(con, verified_by="deeplink", telegram_id=1000, display="Coord")
core.join_node(con, res["id"], coord["id"], role="coordinator", auto_active=True)
members = [coord]
for i in range(11):
    m = core.upsert_member(con, verified_by="deeplink", telegram_id=2000 + i, display=f"p{i}")
    core.join_node(con, res["id"], m["id"]); core.approve_member(con, res["id"], m["id"], coord["id"]); members.append(m)
assert len(core.active_members(con, res["id"])) == 12

rnd = core.open_round(con, res["id"], budget_paise=150_000 * 100)   # ₹1.5L
assert core.spendable_budget(con, rnd) == 120_000 * 100              # 20% levy escrowed upward
assert con.execute("SELECT amount_paise FROM contributions").fetchone()[0] == 30_000 * 100

# proposals: 5 human, 1 twin-suggested that needs a lead
specs = [("Water filter", 30_000, 45_000), ("Kids workshop", 20_000, 35_000), ("Closing music", 15_000, 40_000),
         ("Garden", 5_000, 12_000), ("Bus to Arambol", 8_000, 8_000)]
props = [core.add_proposal(con, rnd["id"], members[i + 1]["id"], title=t, text="…", min_paise=mn * 100, ideal_paise=idl * 100, lead_id=members[i + 1]["id"])
         for i, (t, mn, idl) in enumerate(specs)]
twin = core.add_proposal(con, rnd["id"], coord["id"], title="Goa tour bus (from an offer)", text="…", min_paise=6_000 * 100, ideal_paise=10_000 * 100, source="twin")
assert twin["status"] == "draft"
twin = core.lead_proposal(con, twin["id"], members[7]["id"]); assert twin["status"] == "eligible"
props.append(twin)
frozen = core.freeze_proposals(con, rnd["id"]); assert len(frozen) == 6

# ballots: three loose blocs
for i, m in enumerate(members):
    bloc = i % 3
    w = {0: [40, 10, 10, 20, 10, 10], 1: [10, 45, 25, 5, 10, 5], 2: [15, 5, 40, 5, 20, 15]}[bloc]
    pts = {p["id"]: w[k] for k, p in enumerate(props)}
    core.submit_ballot(con, rnd["id"], m["id"], pts)
try:
    core.submit_ballot(con, rnd["id"], members[0]["id"], {props[0]["id"]: 99}); raise SystemExit("should have failed")
except core.PBError as e:
    assert e.code == "points_must_sum_100"
# re-submit valid to keep 12 ballots
core.submit_ballot(con, rnd["id"], members[0]["id"], {props[0]["id"]: 40, props[1]["id"]: 10, props[2]["id"]: 10, props[3]["id"]: 20, props[4]["id"]: 10, props[5]["id"]: 10})

plans = core.compute_plans(con, rnd["id"])
assert [p["rule"] for p in plans] == ["mes", "nash", "utilitarian", "maximin"]
for p in plans:
    amts = json.loads(p["amounts"]); assert p["valid"] == 1, p["notes"]; assert sum(amts.values()) == 120_000 * 100, sum(amts.values())
mes = next(p for p in plans if p["rule"] == "mes")
rec = core.receipt_for(con, rnd["id"], members[3]["id"])
assert abs(sum(rec.values()) - 10_000 * 100) < 200, rec     # equal share = ₹10,000 each

# rankings → Schulze winner; then 60% ratification
for i, m in enumerate(members):
    order = [p["id"] for p in plans]
    if i % 3 == 1: order = order[1:] + order[:1]
    core.submit_ranking(con, rnd["id"], m["id"], order)
winner = core.pick_winner(con, rnd["id"])
for i, m in enumerate(members):
    core.submit_ratification(con, rnd["id"], m["id"], winner["id"], "approve" if i < 8 else "reject")
d = core.close_ratification(con, rnd["id"], winner["id"])
assert d["verdict"] == "ratified" and d["approvals"] == 8 and d["eligible"] == 12, dict(d)

# payouts queued per funded proposal; pay them
q = core.payout_queue(con, rnd["id"]); assert q and all(x["status"] == "queued" for x in q)
link = core.upi_link("vendor@upi", q[0]["amount_paise"], f"{rnd['id']} {q[0]['title']}")
assert link.startswith("upi://pay?pa=vendor%40upi&") and "&am=" in link
for x in q:
    core.mark_paid(con, x["id"], f"UPI{R.randint(10**11, 10**12-1)}")
assert core.get_round(con, rnd["id"])["status"] == "paid"

# export: no identities, has tallies and hash
ex = core.export_node(con, res["id"])
assert ex["node"]["members"] == 12 and ex["decisions"][0]["verdict"] == "ratified" and len(ex["tallies"][0]["ballot_hash"]) == 64
assert "telegram" not in json.dumps(ex) and "upi" not in json.dumps(ex).lower()
print("ROUND OK ·", len(frozen), "proposals ·", len(plans), "plans · winner", winner["rule"], "· ratified", d["approvals"], "/", d["eligible"],
      "· payouts", len(q), "· levy ₹", con.execute("SELECT amount_paise FROM contributions").fetchone()[0] // 100)
print("MES plan (₹):", {core.get_proposal(con, k)["title"]: v // 100 for k, v in json.loads(mes["amounts"]).items()})
