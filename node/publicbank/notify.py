"""
Re-send the treasurers' Telegram notice for every business on a pool that nobody has verified yet, each with a fresh
per-treasurer login link to the verify page. The notices sent before 3 Nov 2026 carried a bare /c/ link, which in
Telegram's in-app browser only ever showed "Sign in first".

    python -m publicbank.notify --resend-unverified goa-business-commons

On prod: through the publicbank-seed job, with the service's current image.
"""
from __future__ import annotations

import argparse

from . import core
from . import web


def resend_unverified(con, pool_id: str) -> list[str]:
    """One message per unverified business, oldest first. Returns the business names sent."""
    pool = core.get_node(con, web._resolve(pool_id))
    if not pool:
        raise SystemExit(f"no pool {pool_id}")
    sent = []
    for a in reversed(core.applications_of(con, pool["id"])):
        if a["status"] == "verified":
            continue
        web._notify_treasurers(pool["id"], web._business_notice("Not yet verified on", pool, a), open_path=f"/c/{pool['id']}", label="Verify")
        sent.append(a["business"])
    return sent


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resend-unverified", metavar="POOL", help="pool id (or alias) whose unverified businesses to re-announce")
    args = ap.parse_args()
    if args.resend_unverified:
        names = resend_unverified(web.con, args.resend_unverified)
        print(f"sent {len(names)}: " + ", ".join(names))
