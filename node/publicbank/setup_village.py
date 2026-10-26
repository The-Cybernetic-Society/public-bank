"""
Seed a village: one pool node, N residency nodes under it, a round in each, one treasurer, and a join
link per residency (print as QR posters with --qr).

    python -m publicbank.setup_village --village goa-2026 --name "Edge City Goa 2026" \
        --residency "Residency A" --residency "Residency B" \
        --budget-per-residency 100000 --levy 20 --treasurer-telegram 123456789 --qr out/

Budgets are rupees on the command line and stored as paise. The levy is the % of each residency pot
that goes up to the village pool when its round opens; the pool spends what it receives plus
--pool-budget, if any.
"""
from __future__ import annotations

import argparse
import os
import re
import time

from . import core, db

TTL_WEEK = 7 * 24 * 3600


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--village", required=True, help="slug, e.g. goa-2026")
    ap.add_argument("--name", required=True)
    ap.add_argument("--residency", action="append", default=[])
    ap.add_argument("--budget-per-residency", type=int, default=0, help="rupees")
    ap.add_argument("--pool-budget", type=int, default=0, help="rupees")
    ap.add_argument("--levy", type=int, default=0, help="% of each residency pot sent to the pool")
    ap.add_argument("--treasurer-telegram", type=int, help="your Telegram user id (from @userinfobot)")
    ap.add_argument("--base-url", default=os.environ.get("PB_BASE_URL", "http://localhost:8000"))
    ap.add_argument("--qr", help="directory to write one PNG poster per residency")
    a = ap.parse_args()

    con = db.connect()
    pool = core.create_node(con, a.village, a.name, kind="pool")
    if a.pool_budget:
        core.open_round(con, pool["id"], a.pool_budget * 100, closes_at=int(time.time()) + TTL_WEEK)

    treasurer = None
    if a.treasurer_telegram:
        treasurer = core.upsert_member(con, verified_by="coordinator", telegram_id=a.treasurer_telegram, display="treasurer")
        core.join_node(con, pool["id"], treasurer["id"], role="treasurer", auto_active=True)

    links = []
    for r in a.residency:
        nid = f"{a.village}/{slug(r)}"
        core.create_node(con, nid, r, kind="group", parent_id=pool["id"], levy_pct=a.levy)
        if a.budget_per_residency:
            core.open_round(con, nid, a.budget_per_residency * 100, closes_at=int(time.time()) + TTL_WEEK)
        if treasurer:
            core.join_node(con, nid, treasurer["id"], role="treasurer", auto_active=True)
        tok = core.mint_token(con, "join", node_id=nid, ttl=TTL_WEEK)
        url = f"{a.base_url.rstrip('/')}/j/{tok}"
        links.append((r, nid, url))
        print(f"{r:30s} {nid:40s} {url}")

    if a.qr:
        import qrcode
        from PIL import Image, ImageDraw
        os.makedirs(a.qr, exist_ok=True)
        for r, nid, url in links:
            q = qrcode.make(url).convert("RGB").resize((600, 600))
            img = Image.new("RGB", (700, 820), "white")
            img.paste(q, (50, 40))
            d = ImageDraw.Draw(img)
            d.text((50, 660), f"{a.name}", fill="black")
            d.text((50, 690), f"{r} · budget round", fill="black")
            d.text((50, 740), "Scan to join. Propose, vote, approve. Money moves only after 60% approve.", fill="black")
            img.save(os.path.join(a.qr, f"{slug(r)}.png"))
        print(f"posters in {a.qr}/")


if __name__ == "__main__":
    main()
