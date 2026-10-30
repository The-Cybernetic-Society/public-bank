"""
Remove a pool's businesses and everything hanging off them, keeping the pool, its current round (emptied back
to the proposals stage) and the pool's treasurer. For clearing test registrations before real ones arrive.

    python -m publicbank.wipe_businesses --pool goa-business-commons --yes
"""
from __future__ import annotations

import argparse

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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", required=True)
    ap.add_argument("--yes", action="store_true", help="actually delete; without it, only counts are shown")
    a = ap.parse_args()
    con = db.connect()
    kids = core.children(con, a.pool)
    if not a.yes:
        print(f"{a.pool}: {len(kids)} businesses would be removed: " + ", ".join(k["name"] for k in kids))
        return
    print(wipe(con, a.pool))


if __name__ == "__main__":
    main()
