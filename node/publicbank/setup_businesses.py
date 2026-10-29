"""
Seed a business federation: one pool node and one node per business under it, each with an owner's card
(a `join_coord` link: the first person to tap it becomes that business's coordinator), plus one generic
link for businesses not on the list (JOIN with no code registers a new node under the pool, when the
service runs with PB_WA_POOL set to this pool).

    python -m publicbank.setup_businesses --pool goa-biz-2026 --name "Goa Business Pool" \
        --business "Mandrem Tailors" --business "Sea Breeze Cafe" --treasurer-telegram 123456789

    python -m publicbank.setup_businesses --pool goa-biz-2026 --name "Goa Business Pool" --list businesses.txt

Prints one line per business (name, node id, WhatsApp link, card image URL) and a CSV with --csv for
printing cards. No rounds are opened: the pool and business rounds open when the federation runs.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import urllib.parse

from . import core, db

TTL_WEEK = 7 * 24 * 3600


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "business"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True, help="slug, e.g. goa-biz-2026")
    ap.add_argument("--name", required=True)
    ap.add_argument("--business", action="append", default=[])
    ap.add_argument("--list", help="a text file, one business name per line")
    ap.add_argument("--treasurer-telegram", type=int, help="the treasurer's Telegram user id")
    ap.add_argument("--base-url", default=os.environ.get("PB_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--wa-number", default=os.environ.get("PB_WA_NUMBER", ""), help="the WhatsApp number people message, digits")
    ap.add_argument("--csv", help="write name,node,link,card to this file")
    ap.add_argument("--pool-vote", choices=("members", "nodes"), default="nodes", help="who votes in the pool round (new pools only)")
    ap.add_argument("--cosign-min", type=int, default=2, help="businesses that must back a proposal before it is on the ballot (new pools only)")
    ap.add_argument("--match-pct", type=int, default=0, help="the pot matches contributions at this percent, e.g. 500 = 5x (new pools only)")
    a = ap.parse_args()

    names = list(a.business)
    if a.list:
        names += [ln.strip() for ln in open(a.list, encoding="utf-8") if ln.strip() and not ln.startswith("#")]
    number = re.sub(r"\D", "", a.wa_number)
    base = a.base_url.rstrip("/")

    def link(tok: str) -> str:
        return f"https://wa.me/{number}?text={urllib.parse.quote('JOIN ' + tok)}" if number else f"{base}/j/{tok}"

    con = db.connect()
    try:
        pool = core.get_node(con, a.pool)
    except core.PBError:
        pool = core.create_node(con, a.pool, a.name, kind="pool", pool_vote=a.pool_vote, cosign_min=a.cosign_min, match_pct=a.match_pct)

    treasurer = None
    if a.treasurer_telegram:
        treasurer = core.upsert_member(con, verified_by="coordinator", telegram_id=a.treasurer_telegram, display="treasurer")
        core.join_node(con, pool["id"], treasurer["id"], role="treasurer", auto_active=True)

    rows = []
    seen: set[str] = set()
    for n in names:
        nid = f"{a.pool}/{slug(n)}"
        k = 2
        while nid in seen or con.execute("SELECT 1 FROM nodes WHERE id=?", (nid,)).fetchone():
            nid = f"{a.pool}/{slug(n)}-{k}"; k += 1
        seen.add(nid)
        core.create_node(con, nid, n, kind="group", parent_id=pool["id"])
        if treasurer:
            core.join_node(con, nid, treasurer["id"], role="treasurer", auto_active=True)
        tok = core.mint_token(con, "join_coord", node_id=nid, ttl=TTL_WEEK)
        rows.append((n, nid, link(tok), f"{base}/j/{tok}/poster.png?via=wa"))
        print(f"{n:32s} {nid:48s} {rows[-1][2]}")

    print()
    print(f"register on the web: {base}/apply/{a.pool}")
    print(f"generic link for businesses not on the list: https://wa.me/{number}?text=JOIN" if number else "set --wa-number for a generic JOIN link")
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["business", "node", "whatsapp_link", "card_png"]); w.writerows(rows)
        print(f"cards: {a.csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
