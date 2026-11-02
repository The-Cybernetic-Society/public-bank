"""
Remove a pool's businesses and everything hanging off them, keeping the pool, its current round (emptied back
to the proposals stage) and the pool's treasurer. For clearing test registrations before real ones arrive.

    python -m publicbank.wipe_businesses --pool goa-business-commons --yes
    python -m publicbank.wipe_businesses --pool goa-business-commons --business goa-business-commons/test-shop --yes
"""
from __future__ import annotations

import argparse
import json

from . import core, db


def wipe(con, pool_id: str) -> dict:
    kids = [r["id"] for r in db.rows(con.execute("SELECT id FROM nodes WHERE parent_id=?", (pool_id,)).fetchall())]
    rnd = core.current_round(con, pool_id)
    rid = rnd["id"] if rnd else None
    q = ",".join("?" * len(kids)) or "''"
    tre = {r["member_id"] for r in db.rows(con.execute("SELECT member_id FROM memberships WHERE node_id=? AND role='treasurer'", (pool_id,)).fetchall())}
    owners = {r["member_id"] for r in db.rows(con.execute(f"SELECT member_id FROM memberships WHERE node_id IN ({q})", kids).fetchall())} - tre
    elsewhere = {r["member_id"] for r in db.rows(con.execute(f"SELECT member_id FROM memberships WHERE node_id NOT IN ({q}) AND node_id!=?", [*kids, pool_id]).fetchall())}
    gone = sorted(owners - elsewhere)
    mq = ",".join("?" * len(gone)) or "''"
    with db.tx(con):
        if rid:
            con.execute("UPDATE rounds SET status='proposals', winner_plan_id=NULL WHERE id=?", (rid,))
            for t in ("ratifications", "rankings", "payouts", "decisions", "plans", "ballots", "proposals"):   # children before parents
                con.execute(f"DELETE FROM {t} WHERE round_id=?", (rid,))
            con.execute("DELETE FROM contributions WHERE to_round_id=?", (rid,))
        con.execute("DELETE FROM applications WHERE pool_id=?", (pool_id,))
        if kids:
            con.execute(f"DELETE FROM contributions WHERE from_node_id IN ({q}) OR to_node_id IN ({q})", kids + kids)
            for t in ("needs", "value_vectors", "tokens", "offers", "memberships", "rounds"):
                con.execute(f"DELETE FROM {t} WHERE node_id IN ({q})", kids)
            con.execute(f"DELETE FROM nodes WHERE id IN ({q})", kids)
        con.execute("DELETE FROM wa_state")
        if gone:
            for t in ("value_vectors", "tokens"):
                con.execute(f"DELETE FROM {t} WHERE member_id IN ({mq})", gone)
            # older rounds may still reference these people, so the rows stay but hold nothing personal
            con.execute(f"UPDATE members SET display=NULL, wa_phone=NULL, telegram_id=NULL, upi_id=NULL, edgeos_human_id=NULL, verified_by='removed' WHERE id IN ({mq})", gone)
    return {"businesses": len(kids), "members_anonymised": len(gone), "round": rid, "treasurers_kept": len(tre), "children_now": len(core.children(con, pool_id))}


def remove_business(con, node_id: str) -> dict:
    """Remove one business from its pool: its membership, application, needs, slider answers and tokens; its
    backing is taken off any proposal, proposals it led lose their lead, and its people are anonymised unless they
    belong elsewhere. The pool, the round and other businesses are untouched."""
    node = core.get_node(con, node_id)
    pool_id = node.get("parent_id")
    if not pool_id:
        raise core.PBError("not_a_child")
    people = {r["member_id"] for r in db.rows(con.execute("SELECT member_id FROM memberships WHERE node_id=? AND role!='treasurer'", (node_id,)).fetchall())}
    elsewhere = {r["member_id"] for r in db.rows(con.execute("SELECT member_id FROM memberships WHERE node_id!=?", (node_id,)).fetchall())}
    gone = sorted(people - elsewhere)
    mq = ",".join("?" * len(gone)) or "''"
    rnd = core.current_round(con, pool_id)
    with db.tx(con):
        if rnd:
            for p in db.rows(con.execute("SELECT id, cosign_nodes, lead_id FROM proposals WHERE round_id=?", (rnd["id"],)).fetchall()):
                signs = [x for x in db.j(p["cosign_nodes"], []) if x != node_id]
                lead = None if p["lead_id"] in people else p["lead_id"]
                if signs != db.j(p["cosign_nodes"], []) or lead != p["lead_id"]:
                    status = core._proposal_status(con, pool_id, lead, signs) if lead else "draft"
                    con.execute("UPDATE proposals SET cosign_nodes=?, lead_id=?, status=? WHERE id=? AND status IN ('draft','eligible')", (json.dumps(signs), lead, status, p["id"]))
            for t in ("ballots", "rankings", "ratifications"):
                if gone:
                    con.execute(f"DELETE FROM {t} WHERE round_id=? AND member_id IN ({mq})", [rnd["id"], *gone])
        con.execute("DELETE FROM applications WHERE node_id=?", (node_id,))
        con.execute("DELETE FROM contributions WHERE from_node_id=? OR to_node_id=?", (node_id, node_id))
        for t in ("needs", "value_vectors", "tokens", "offers", "memberships", "rounds"):
            con.execute(f"DELETE FROM {t} WHERE node_id=?", (node_id,))
        con.execute("DELETE FROM nodes WHERE id=?", (node_id,))
        if gone:
            for t in ("value_vectors", "tokens"):
                con.execute(f"DELETE FROM {t} WHERE member_id IN ({mq})", gone)
            con.execute(f"UPDATE members SET display=NULL, wa_phone=NULL, telegram_id=NULL, upi_id=NULL, edgeos_human_id=NULL, verified_by='removed' WHERE id IN ({mq})", gone)
    return {"removed": node_id, "members_anonymised": len(gone), "children_now": len(core.children(con, pool_id))}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", required=True)
    ap.add_argument("--business", help="remove only this business (node id) from the pool")
    ap.add_argument("--yes", action="store_true", help="actually delete; without it, only counts are shown")
    a = ap.parse_args()
    con = db.connect()
    if a.business:
        node = core.get_node(con, a.business)
        if not a.yes:
            print(f"would remove {node['name']} ({a.business}) from {a.pool}"); return
        print(remove_business(con, a.business)); return
    kids = core.children(con, a.pool)
    if not a.yes:
        print(f"{a.pool}: {len(kids)} businesses would be removed: " + ", ".join(k["name"] for k in kids))
        return
    print(wipe(con, a.pool))


if __name__ == "__main__":
    main()
