"""
A rehearsal: a business federation walked to a chosen stage, so the live board and the pages show a real
round. Runs against whatever database the service uses (SQLite locally, Postgres with DATABASE_URL).

    python -m publicbank.demo --pool goa-biz-demo --stage ratifying
    python -m publicbank.demo --pool goa-biz-demo --stage paid --treasurer-telegram 123456789

Stages: proposals | ballots | plans | ratifying | paid. Six businesses, each with an owner and a few staff
(fake WhatsApp numbers, 9199990xxxxx), each with a need; the pool matches ₹500 co-contributions 5x, needs
two co-signing businesses per proposal, and votes one business one vote. Re-running with the same pool id
wipes and rebuilds it.
"""
from __future__ import annotations

import argparse
import os
import time

from . import core, db, twin

BUSINESSES = [
    ("Mandrem Tailors", ["Rekha", "Sunil", "Meena"], "A scooter for deliveries, customers keep asking for home delivery"),
    ("Sea Breeze Cafe", ["Joao", "Priya"], "Someone to deliver lunch orders on the lane in the afternoon"),
    ("Ashvem Pharmacy", ["Nandini", "Ravi", "Kiran"], "Delivery of medicines to older customers at home"),
    ("Coco Surf Shack", ["Marcus", "Dev"], "GST billing software, the notebook isn't working any more"),
    ("Hadfadkar Stores", ["Tara", "Mahesh"], "Billing and stock software that works on a phone"),
    ("Junas Bakery", ["Fatima", "Anil", "Sonia"], "A bigger fridge for cakes in the season, we lose orders"),
]
STAGES = ["proposals", "ballots", "plans", "ratifying", "paid"]


def wipe(con, pool_id: str) -> None:
    ids = [r["id"] for r in db.rows(con.execute("SELECT id FROM nodes WHERE id=? OR parent_id=?", (pool_id, pool_id)).fetchall())]
    if not ids:
        return
    q = ",".join("?" * len(ids))
    rids = [r["id"] for r in db.rows(con.execute(f"SELECT id FROM rounds WHERE node_id IN ({q})", ids).fetchall())]
    rq = ",".join("?" * len(rids)) or "''"
    with db.tx(con):
        for t in ("payouts", "decisions", "ratifications", "rankings", "plans", "ballots", "proposals"):
            if rids: con.execute(f"DELETE FROM {t} WHERE round_id IN ({rq})", rids)
        con.execute(f"DELETE FROM contributions WHERE from_node_id IN ({q}) OR to_node_id IN ({q})", ids + ids)
        con.execute(f"DELETE FROM needs WHERE node_id IN ({q})", ids)
        con.execute(f"DELETE FROM offers WHERE node_id IN ({q})", ids)
        con.execute(f"DELETE FROM tokens WHERE node_id IN ({q})", ids)
        con.execute(f"DELETE FROM rounds WHERE node_id IN ({q})", ids)
        con.execute(f"DELETE FROM memberships WHERE node_id IN ({q})", ids)
        con.execute("DELETE FROM nodes WHERE parent_id=?", (pool_id,))
        con.execute("DELETE FROM nodes WHERE id=?", (pool_id,))


def slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="goa-biz-demo")
    ap.add_argument("--name", default="Goa Business Commons")
    ap.add_argument("--stage", choices=STAGES, default="ratifying")
    ap.add_argument("--treasurer-telegram", type=int)
    ap.add_argument("--base-url", default=os.environ.get("PB_BASE_URL", "http://localhost:8000"))
    a = ap.parse_args()
    con = db.connect()
    wipe(con, a.pool)
    stop = STAGES.index(a.stage)

    pool = core.create_node(con, a.pool, a.name, kind="pool", pool_vote="nodes", cosign_min=2, match_pct=0, pool_ratify="majority")
    tre = core.upsert_member(con, verified_by="coordinator", telegram_id=a.treasurer_telegram or 1, display="treasurer")
    core.join_node(con, pool["id"], tre["id"], role="treasurer", auto_active=True)
    owners, nodes = {}, []
    for i, (name, people, need) in enumerate(BUSINESSES, 1):
        nid = f"{a.pool}/{slug(name)}"
        core.create_node(con, nid, name, kind="group", parent_id=pool["id"], ratify_pct=51)
        with db.tx(con): con.execute("UPDATE nodes SET charter_at=? WHERE id=?", (db.now(), nid))
        nodes.append(nid)
        for k, person in enumerate(people):
            m = core.upsert_member(con, verified_by="whatsapp", wa_phone=f"9199990{i:02d}{k:03d}", display=person)
            core.join_node(con, nid, m["id"], role="coordinator" if k == 0 else "member", auto_active=True)
            if k == 0:
                owners[nid] = m
                with db.tx(con): con.execute("UPDATE members SET wa_last_in_at=?, wa_opt_in_at=?, upi_id=? WHERE id=?", (db.now(), db.now(), f"{slug(person)}@okaxis", m["id"]))
                core.add_need(con, nid, m["id"], need)
    rnd = core.open_round(con, pool["id"], 100000 * 100, closes_at=int(time.time()) + 7 * 86400)
    for nid in nodes:
        core.contribute(con, nid, 500 * 100, member_id=owners[nid]["id"])

    drafts = twin.draft_from_needs(con, pool["id"])
    print(f"twin drafted {len(drafts)}: " + "; ".join(d["title"] for d in drafts))
    # the delivery draft: the pharmacy leads, the tailors and cafe co-sign; the billing draft: the surf shack leads, the store co-signs
    delivery = next((d for d in drafts if "deliver" in d["title"].lower() or "scooter" in d["title"].lower()), drafts[0] if drafts else None)
    billing = next((d for d in drafts if "bill" in d["title"].lower() or "software" in d["title"].lower()), None)
    t, c, ph, surf, store, bak = nodes
    if delivery:
        core.lead_proposal(con, delivery["id"], owners[ph]["id"])
        core.cosign_proposal(con, delivery["id"], t, owners[t]["id"]); core.cosign_proposal(con, delivery["id"], c, owners[c]["id"])
    if billing:
        core.lead_proposal(con, billing["id"], owners[surf]["id"])
        core.cosign_proposal(con, billing["id"], store, owners[store]["id"])
    fridge = core.add_proposal(con, rnd["id"], owners[bak]["id"], title="Shared cold store at the bakery", text="A commercial fridge the bakery runs; the cafe and the stores keep stock in it on season weekends.",
                               ideal_paise=4000 * 100, min_paise=2500 * 100, lead_id=owners[bak]["id"])
    core.cosign_proposal(con, fridge["id"], c, owners[c]["id"])
    if stop == 0:
        return finish(a, pool["id"], nodes)
    core.advance(con, rnd["id"])   # freeze → ballots
    elig = core.eligible_proposals(con, rnd["id"])
    ids = [p["id"] for p in elig]
    splits = [[70, 20, 10], [50, 10, 40], [80, 10, 10], [10, 80, 10], [20, 70, 10], [10, 30, 60]]
    for nid, sp in zip(nodes, splits):
        pts = {pid: v for pid, v in zip(ids, sp)}
        if len(ids) < 3: pts = {ids[0]: 100}
        core.submit_ballot(con, rnd["id"], owners[nid]["id"], pts)
    if stop == 1:
        return finish(a, pool["id"], nodes)
    core.advance(con, rnd["id"])   # compute → plans
    plans = core.plans_of(con, rnd["id"])
    order = [p["id"] for p in sorted(plans, key=lambda p: p["rule"] != "mes")]
    for nid in nodes[:4]:
        core.submit_ranking(con, rnd["id"], owners[nid]["id"], order)
    if stop == 2:
        return finish(a, pool["id"], nodes)
    core.advance(con, rnd["id"])   # winner → ratifying
    rnd = core.get_round(con, rnd["id"])
    for nid in nodes:
        for m in core.active_members(con, nid)[:2]:
            core.submit_ratification(con, rnd["id"], m["id"], rnd["winner_plan_id"], "approve")
    if stop == 3:
        return finish(a, pool["id"], nodes)
    core.advance(con, rnd["id"])   # close → ratified, payouts queued
    q = core.payout_queue(con, rnd["id"])
    if q:
        core.mark_paid(con, q[0]["id"], "UTR" + str(int(time.time()))[-9:], "vendor bill 0147")
    finish(a, pool["id"], nodes)


def finish(a, pool_id: str, nodes: list[str]) -> None:
    base = a.base_url.rstrip("/")
    print(f"board:   {base}/b/{pool_id}")
    for nid in nodes:
        print(f"         {base}/b/{nid}")
    print(f"export:  {base}/export/{pool_id}")


if __name__ == "__main__":
    main()
