"""A federation round in core: three businesses under a matching pool, co-contributions and the match, proposals
from staff and owners, co-signing by two nodes, one ballot per business cast by its coordinator, plans, a
winner, ratification business by business (one failing blocks the pool), then the positive path to payouts
and an export with per-child tallies."""
import os
import sys
import tempfile

os.environ["PB_DB"] = os.path.join(tempfile.mkdtemp(), "p.sqlite")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from publicbank import core, db  # noqa: E402

con = db.connect()


def person(tag: str) -> dict:
    return core.upsert_member(con, verified_by="whatsapp", wa_phone="9190000" + tag, display=tag)


def business(nid: str, name: str, staff: int) -> tuple[dict, list[dict]]:
    core.create_node(con, nid, name, kind="group", parent_id="pool")
    with db.tx(con): con.execute("UPDATE nodes SET charter_at=? WHERE id=?", (db.now(), nid))
    owner = person(nid[-1] + "0")
    core.join_node(con, nid, owner["id"], role="coordinator", auto_active=True)
    team = []
    for i in range(1, staff + 1):
        p = person(nid[-1] + str(i)); core.join_node(con, nid, p["id"], auto_active=True); team.append(p)
    return owner, team


# the pool: one business one vote, two co-signers per proposal, the pot matches contributions 5x up to ₹1,00,000
pool = core.create_node(con, "pool", "Goa Business Pool", kind="pool", pool_vote="nodes", cosign_min=2, match_pct=500)
tre = core.upsert_member(con, verified_by="coordinator", telegram_id=1, display="treasurer")
core.join_node(con, "pool", tre["id"], role="treasurer", auto_active=True)
a_owner, a_team = business("pool/a", "Tailors", 2)     # 3 members
b_owner, b_team = business("pool/b", "Cafe", 1)        # 2 members
c_owner, c_team = business("pool/c", "Pharmacy", 2)    # 3 members
rnd = core.open_round(con, "pool", 100000 * 100)

# contributions: ₹500 each, matched 5x → ₹1,500 + ₹7,500 = ₹9,000 spendable
for nid, owner in (("pool/a", a_owner), ("pool/b", b_owner), ("pool/c", c_owner)):
    core.contribute(con, nid, 500 * 100, member_id=owner["id"])
try:
    core.contribute(con, "pool/a", 500 * 100, member_id=a_team[0]["id"]); raise AssertionError("staff contributed")
except core.PBError as e:
    assert e.code == "not_coordinator"
assert core.contributions_in(con, rnd["id"]) == 1500 * 100
assert core.spendable_budget(con, rnd) == 9000 * 100, core.spendable_budget(con, rnd)
# the match is capped at the pool's pot
with db.tx(con): con.execute("UPDATE rounds SET budget_paise=? WHERE id=?", (2000 * 100, rnd["id"]))
assert core.spendable_budget(con, core.get_round(con, rnd["id"])) == 3500 * 100
with db.tx(con): con.execute("UPDATE rounds SET budget_paise=? WHERE id=?", (100000 * 100, rnd["id"]))

# proposals: staff of A proposes and leads; it is a draft until two businesses co-sign
p1 = core.add_proposal(con, rnd["id"], a_team[0]["id"], title="Shared delivery scooter", text="For the lane.", ideal_paise=6000 * 100, min_paise=4000 * 100, lead_id=a_team[0]["id"])
assert p1["status"] == "draft" and db.j(p1["cosign_nodes"], []) == []
try:
    core.cosign_proposal(con, p1["id"], "pool/a", a_team[1]["id"]); raise AssertionError("staff co-signed")
except core.PBError as e:
    assert e.code == "not_coordinator"
p1 = core.cosign_proposal(con, p1["id"], "pool/a", a_owner["id"])
assert p1["status"] == "draft"
p1 = core.cosign_proposal(con, p1["id"], "pool/b", b_owner["id"])
assert p1["status"] == "eligible" and db.j(p1["cosign_nodes"], []) == ["pool/a", "pool/b"]
# an owner's own proposal carries their node's signature; a lead from another node's coordinator adds theirs
p2 = core.add_proposal(con, rnd["id"], b_owner["id"], title="Billing software, one licence", text="GST billing.", ideal_paise=3000 * 100, min_paise=0)
assert p2["status"] == "draft" and db.j(p2["cosign_nodes"], []) == ["pool/b"]
p2 = core.lead_proposal(con, p2["id"], c_owner["id"])
assert p2["status"] == "eligible" and db.j(p2["cosign_nodes"], []) == ["pool/b", "pool/c"]
# a stranger
stranger = person("99")
try:
    core.add_proposal(con, rnd["id"], stranger["id"], title="x", text="y", ideal_paise=100, lead_id=stranger["id"]); raise AssertionError("stranger proposed")
except core.PBError as e:
    assert e.code == "not_a_member"

# ballots: one per business, by its coordinator
core.freeze_proposals(con, rnd["id"])
try:
    core.submit_ballot(con, rnd["id"], a_team[0]["id"], {p1["id"]: 100}); raise AssertionError("staff voted in a nodes pool")
except core.PBError as e:
    assert e.code == "node_votes_by_coordinator"
assert {v["id"] for v in core.voters(con, rnd)} == {a_owner["id"], b_owner["id"], c_owner["id"]}
core.submit_ballot(con, rnd["id"], a_owner["id"], {p1["id"]: 80, p2["id"]: 20})
core.submit_ballot(con, rnd["id"], b_owner["id"], {p1["id"]: 30, p2["id"]: 70})
core.submit_ballot(con, rnd["id"], c_owner["id"], {p2["id"]: 100})
assert len(core.ballots_of(con, rnd["id"])) == 3
plans = core.compute_plans(con, rnd["id"])
assert any(p["valid"] for p in plans)
core.submit_ranking(con, rnd["id"], a_owner["id"], [p["id"] for p in plans])          # a child's coordinator ranks in the pool round
core.submit_ranking(con, rnd["id"], c_team[0]["id"], [p["id"] for p in reversed(plans)])  # and so does a child's staff member
try:
    core.submit_ranking(con, rnd["id"], stranger["id"], [p["id"] for p in plans]); raise AssertionError("stranger ranked")
except core.PBError as e:
    assert e.code == "not_a_member"
core.pick_winner(con, rnd["id"])
rnd = core.get_round(con, rnd["id"])
win = rnd["winner_plan_id"]
assert sum(db.j(next(p for p in plans if p["id"] == win)["amounts"], {}).values()) == 9000 * 100

# ratification per business: A 2 of 3 (ok), B 1 of 2 (50% < 60%: fails), C 2 of 3 (ok) → the pool fails
for m in (a_owner, a_team[0], b_owner, c_owner, c_team[0]):
    core.submit_ratification(con, rnd["id"], m["id"], win, "approve")
core.submit_ratification(con, rnd["id"], c_team[1]["id"], win, "reject")
t = core.ratification_tally(con, rnd["id"], win)
assert t["rule"] == "every_child_ratifies" and not t["ok"] and t["detail"]["pool/b"]["ok"] is False and t["detail"]["pool/a"]["ok"], t
# B's second member approves → every child passes
core.submit_ratification(con, rnd["id"], b_team[0]["id"], win, "approve")
dec = core.close_ratification(con, rnd["id"], win)
assert dec["verdict"] == "ratified" and dec["eligible"] == 8 and dec["approvals"] == 6 and dec["rejections"] == 1, dec
detail = db.j(dec["detail"], {})
assert set(detail) == {"pool/a", "pool/b", "pool/c"} and detail["pool/c"] == {"name": "Pharmacy", "eligible": 3, "approvals": 2, "rejections": 1, "pct": 60, "ok": True, "voted": True}
q = core.payout_queue(con, rnd["id"])
assert q and sum(x["amount_paise"] for x in q) == 9000 * 100 and {x["payee_member_id"] for x in q} <= {a_team[0]["id"], c_owner["id"]}

# export: per-child tallies, charter fields, contributions into the round, no identities
ex = core.export_node(con, "pool")
assert ex["node"]["pool_vote"] == "nodes" and ex["node"]["match_pct"] == 500 and len(ex["node"]["children"]) == 3
assert {k: v for k, v in ex["tallies"][0]["children"]["pool/b"].items() if k != "voted"} == {"eligible": 2, "approvals": 2, "rejections": 0, "pct": 60, "ok": True}
assert sum(c["amount"] for c in ex["contributions"] if c["to_round"] == rnd["id"]) == 1500 * 100
assert "wa_phone" not in str(ex) and "9190000" not in str(ex)
assert all(c["registered_at_ist"].endswith(" IST") and c["registered_at"] > 0 for c in ex["node"]["children"])   # when each business joined is public

# advance() walks a plain node's round the same way the web button does
core.create_node(con, "solo", "Solo", kind="group")
s_m = [person("s" + str(i)) for i in range(3)]
for m in s_m: core.join_node(con, "solo", m["id"], auto_active=True)
r2 = core.open_round(con, "solo", 1000 * 100)
core.add_proposal(con, r2["id"], s_m[0]["id"], title="Tea", text="Tea.", ideal_paise=1000 * 100, lead_id=s_m[0]["id"])
assert core.advance(con, r2["id"]) == "freeze"
for m in s_m: core.submit_ballot(con, r2["id"], m["id"], {core.eligible_proposals(con, r2["id"])[0]["id"]: 100})
assert core.advance(con, r2["id"]) == "compute" and core.advance(con, r2["id"]) == "winner"
r2 = core.get_round(con, r2["id"])
for m in s_m[:2]: core.submit_ratification(con, r2["id"], m["id"], r2["winner_plan_id"], "approve")
assert core.advance(con, r2["id"]) == "close" and core.get_round(con, r2["id"])["status"] == "ratified"
try:
    core.advance(con, r2["id"]); raise AssertionError("advanced past the end")
except core.PBError as e:
    assert e.code == "wrong_stage"

# needs: recorded per business, listed across the pool
core.add_need(con, "pool/a", a_owner["id"], "A scooter for deliveries, customers keep asking")
core.add_need(con, "pool/b", b_owner["id"], "Someone to deliver lunch orders on the lane")
assert [n["node_name"] for n in core.needs_of(con, "pool")] == ["Tailors", "Cafe"]
assert core.needs_of(con, "pool/a", include_children=False)[0]["text"].startswith("A scooter")

# the twin (plain path, no API key): two delivery needs from two businesses become one draft; the lone need does not
os.environ.pop("ANTHROPIC_API_KEY", None)
from publicbank import twin  # noqa: E402
core.create_node(con, "pool2", "Pool 2", kind="pool", cosign_min=2)
for nid, who, text in (("pool2/x", "x0", "A scooter for deliveries to customers"), ("pool2/y", "y0", "Delivery person for lunch orders, customers ask for delivery"), ("pool2/z", "z0", "A bigger fridge for the season")):
    core.create_node(con, nid, nid[-1].upper(), parent_id="pool2")
    pm = person(who); core.join_node(con, nid, pm["id"], role="coordinator", auto_active=True); core.add_need(con, nid, pm["id"], text)
r3 = core.open_round(con, "pool2", 10000 * 100)
dry = twin.draft_from_needs(con, "pool2", min_nodes=2, dry_run=True)
assert len(dry) == 1 and dry[0]["needs"] == 2 and dry[0]["ideal_paise"] == min(2 * twin.DEFAULT_IDEAL_RUPEES, int(10000 * twin.MAX_SHARE)) * 100, dry   # capped at half the ₹10,000 pot
assert all(n["status"] == "open" for n in core.needs_of(con, "pool2"))   # a dry run marks nothing
made = twin.draft_from_needs(con, "pool2", min_nodes=2)
assert len(made) == 1 and made[0]["status"] == "draft" and made[0]["source"] == "twin" and "- X: A scooter" in made[0]["text"] and "- Y: Delivery" in made[0]["text"], made[0]["text"]
assert [n["text"][:8] for n in core.needs_of(con, "pool2")] == ["A bigger"]   # only the fridge is still open
# one need is enough by default: the lone fridge becomes a draft written as the lane's shared version
made2 = twin.draft_from_needs(con, "pool2")
assert len(made2) == 1 and made2[0]["source"] == "twin" and "Z said it needs" in made2[0]["text"] and core.needs_of(con, "pool2") == []
assert twin.draft_from_needs(con, "pool2") == []   # nothing left
# a later business with a similar need is attached to the existing scooter draft, not given a second one
core.create_node(con, "pool2/q", "Q", parent_id="pool2"); qm = person("q0"); core.join_node(con, "pool2/q", qm["id"], role="coordinator", auto_active=True)
core.add_need(con, "pool2/q", qm["id"], "A scooter to deliver orders to customers")
res = twin.draft_from_needs(con, "pool2")
assert len(res) == 1 and res[0]["id"] == made[0]["id"] and "- Q: A scooter" in res[0]["text"] and len(twin.existing_drafts(con, r3["id"])) == 2, res

print("POOL OK · twin plain clustering · match 5x capped · cosign ×2 · one ballot per business · per-child ratification (one child blocks) · payouts · export with child tallies · advance · needs")

# the majority rule: more than half of the businesses that voted pass; silence is neither; the treasurer is not a member
core.create_node(con, "m", "Majority Pool", kind="pool", pool_vote="nodes", pool_ratify="majority")
mt = person("mt"); core.join_node(con, "m", mt["id"], role="treasurer", auto_active=True)
mo = {}
for nid in ("m/x", "m/y", "m/z"):
    core.create_node(con, nid, nid[-1].upper(), parent_id="m", ratify_pct=51)
    with db.tx(con): con.execute("UPDATE nodes SET charter_at=? WHERE id=?", (db.now(), nid))
    o = person(nid[-1] + "m"); core.join_node(con, nid, o["id"], role="coordinator", auto_active=True); mo[nid] = o
    st = person(nid[-1] + "s"); core.join_node(con, nid, st["id"], auto_active=True); mo[nid + "/s"] = st
    core.join_node(con, nid, mt["id"], role="treasurer", auto_active=True)   # as old seeds did: must not count
assert len(core.eligible_members(con, "m/x")) == 2 and len(core.active_members(con, "m/x")) == 3
rm = core.open_round(con, "m", 1000 * 100)
pm = core.add_proposal(con, rm["id"], mo["m/x"]["id"], title="Tea", text="Tea.", ideal_paise=1000 * 100, lead_id=mo["m/x"]["id"])
core.freeze_proposals(con, rm["id"])
for nid in ("m/x", "m/y", "m/z"): core.submit_ballot(con, rm["id"], mo[nid]["id"], {pm["id"]: 100})
core.compute_plans(con, rm["id"]); core.pick_winner(con, rm["id"]); rm = core.get_round(con, rm["id"]); w = rm["winner_plan_id"]
# X: both approve (passes 51%); Y: one approves, one rejects (50% < 51%: fails); Z: silent
core.submit_ratification(con, rm["id"], mo["m/x"]["id"], w, "approve"); core.submit_ratification(con, rm["id"], mo["m/x/s"]["id"], w, "approve")
core.submit_ratification(con, rm["id"], mo["m/y"]["id"], w, "approve"); core.submit_ratification(con, rm["id"], mo["m/y/s"]["id"], w, "reject")
t = core.ratification_tally(con, rm["id"], w)
assert t["rule"] == "majority_of_voting_businesses" and t["businesses_voted"] == 2 and t["businesses_passed"] == 1 and not t["ok"], t   # 1 of 2 is not more than half
core.submit_ratification(con, rm["id"], mo["m/y/s"]["id"], w, "approve")   # Y changes its mind: 2 of 2 voted pass; Z's silence is not a no
t = core.ratification_tally(con, rm["id"], w); assert t["ok"] and t["businesses_voted"] == 2 and t["detail"]["m/z"]["voted"] is False, t
dec = core.close_ratification(con, rm["id"], w); assert dec["verdict"] == "ratified" and dec["rule"] == "majority_of_voting_businesses"

# the freeze guard: more than 16 eligible proposals cannot go to the ballot
core.create_node(con, "big", "Big", kind="group")
bm = [person("b" + str(i)) for i in range(2)]
for m_ in bm: core.join_node(con, "big", m_["id"], auto_active=True)
rb = core.open_round(con, "big", 1000 * 100)
for i in range(17): core.add_proposal(con, rb["id"], bm[0]["id"], title=f"P{i}", text="x", ideal_paise=100 * 100, lead_id=bm[0]["id"])
try:
    core.freeze_proposals(con, rb["id"]); raise AssertionError("froze 17")
except core.PBError as e:
    assert e.code == "too_many_proposals" and "17" in e.detail
assert core.get_round(con, rb["id"])["status"] == "proposals"

# a vendor named on the proposal is paid directly; otherwise the lead is
core.create_node(con, "v", "Vendor", kind="group")
vm = [person("v" + str(i)) for i in range(3)]
for m_ in vm: core.join_node(con, "v", m_["id"], auto_active=True)
rv = core.open_round(con, "v", 1000 * 100)
pv1 = core.add_proposal(con, rv["id"], vm[0]["id"], title="Fridge", text="x", ideal_paise=600 * 100, lead_id=vm[0]["id"], payee_upi="coldco@okaxis", payee_name="Cold Co")
pv2 = core.add_proposal(con, rv["id"], vm[1]["id"], title="Sign", text="x", ideal_paise=400 * 100, lead_id=vm[1]["id"])
try:
    core.set_proposal_payee(con, pv2["id"], vm[0]["id"], upi="x@y"); raise AssertionError("not the lead")
except core.PBError as e:
    assert e.code == "not_yours"
core.freeze_proposals(con, rv["id"])
for m_ in vm: core.submit_ballot(con, rv["id"], m_["id"], {pv1["id"]: 60, pv2["id"]: 40})
core.compute_plans(con, rv["id"]); core.pick_winner(con, rv["id"]); rv = core.get_round(con, rv["id"])
for m_ in vm: core.submit_ratification(con, rv["id"], m_["id"], rv["winner_plan_id"], "approve")
core.close_ratification(con, rv["id"], rv["winner_plan_id"])
q = {x["title"]: x for x in core.payout_queue(con, rv["id"])}
assert q["Fridge"]["kind"] == "vendor" and q["Fridge"]["payee_upi"] == "coldco@okaxis" and q["Sign"]["kind"] == "reimbursement" and q["Sign"]["payee_upi"] is None, q

# db.tx rolls back on SystemExit too
try:
    with db.tx(con):
        con.execute("INSERT INTO needs (id,node_id,member_id,text,status,created_at) VALUES ('n_exit','v',?, 'x','open',0)", (vm[0]["id"],))
        raise SystemExit("allocator bailed")
except SystemExit:
    pass
assert con.execute("SELECT 1 FROM needs WHERE id='n_exit'").fetchone() is None
print("POOL OK · majority of voting businesses · treasurer not counted · freeze guard · vendor payee · tx rollback on exit")

# many needs are drafted in batches, later batches attaching to earlier drafts rather than duplicating
os.environ["PB_TWIN_BATCH"] = "3"
import importlib; importlib.reload(twin)
core.create_node(con, "pool3", "Pool 3", kind="pool", cosign_min=2)
for i in range(7):
    nid = f"pool3/n{i}"; core.create_node(con, nid, f"N{i}", parent_id="pool3")
    pm = person(f"p3{i}"); core.join_node(con, nid, pm["id"], role="coordinator", auto_active=True)
    core.add_need(con, nid, pm["id"], "A scooter for deliveries to customers" if i % 2 == 0 else "A bigger fridge for the season")
r4 = core.open_round(con, "pool3", 10000 * 100)
out = twin.draft_from_needs(con, "pool3")
ds = twin.existing_drafts(con, r4["id"])
assert len(ds) == 2 and core.needs_of(con, "pool3") == [], [d["title"] for d in ds]   # one scooter draft, one fridge draft, every need drafted
assert sum(1 for d in ds if d["text"].count("- N") >= 3) == 2, [d["text"] for d in ds]   # both gathered needs from later batches
os.environ.pop("PB_TWIN_BATCH")
print("POOL OK · batched drafting")

# no draft may ask for more than half the pot, whatever the default or the model said; twin drafts carry no minimum
core.create_node(con, "pool4", "Pool 4", kind="pool")
core.create_node(con, "pool4/a", "A4", parent_id="pool4"); am = person("a4"); core.join_node(con, "pool4/a", am["id"], role="coordinator", auto_active=True)
core.add_need(con, "pool4/a", am["id"], "A scooter for deliveries to customers")
r5 = core.open_round(con, "pool4", 8000 * 100)   # a small pot: the default ₹10,000 per business is above half of it
d5 = twin.draft_from_needs(con, "pool4")
assert len(d5) == 1 and d5[0]["ideal_paise"] == 4000 * 100 and d5[0]["min_paise"] == 0, d5
print("POOL OK · draft price cap")
