"""
The twin: turns what businesses said they need into draft proposals for the pool's round.

Needs are one-line texts recorded per node (`needs` table, asked once on WhatsApp after the charter). The twin
groups similar ones across different nodes and writes one draft per group: a title, a text that quotes every
need and names the business behind it, and an ideal amount. A draft is never on a ballot by itself: a member
must lead it and, in a pool with `cosign_min`, enough nodes must co-sign. The twin proposes; people dispose.

Grouping is a plain word-overlap clustering by default, so it runs anywhere. With ANTHROPIC_API_KEY set, the
model does the grouping and estimates amounts; if it declines or fails, the plain path runs. Nothing about
a person is sent: only the need texts and the business names.

    python -m publicbank.twin --pool goa-biz-2026 [--min-nodes 2] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
from collections import Counter

from . import core, db

log = logging.getLogger("publicbank.twin")
DEFAULT_IDEAL_RUPEES = int(os.environ.get("PB_TWIN_IDEAL_RUPEES", "10000"))   # per business in the group, before the model refines it
MODEL = "claude-opus-5-5"
BATCH = int(os.environ.get("PB_TWIN_BATCH", "60"))   # needs per model call; above this the twin drafts in batches
MAX_SHARE = float(os.environ.get("PB_TWIN_MAX_SHARE", "0.5"))   # no single draft may ask for more than this share of the pot
STOP = set("""a an the and or of for to in on at with by from is are be was were we our us i my me it its this that these those
need needs needed want wants wanted some more one two three new up down out over get got have has had also just
like would could should can will do does not no yes very really too so but if then than there here when where
what which who how all any each every many much lot lots per day week month year time""".split())


def words(text: str) -> set[str]:
    """Content words as crude stems (five-letter prefixes), so 'deliver', 'delivery' and 'deliveries' agree."""
    return {w[:5] for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP}


def cluster_plain(needs: list[dict]) -> list[list[dict]]:
    """Greedy grouping. A need joins the first group it shares two stems with, or a third of its own stems. Then a
    need left alone joins a group whose theme (stems shared by two or more of its members) it touches."""
    groups: list[tuple[set[str], list[dict]]] = []
    for n in needs:
        w = words(n["text"])
        for gw, members in groups:
            shared = len(w & gw)
            if shared >= 2 or (w and shared / len(w) >= 0.34):
                gw |= w; members.append(n); break
        else:
            groups.append((set(w), [n]))

    def theme(members: list[dict]) -> set[str]:
        c = Counter(w for m in members for w in words(m["text"]))
        return {w for w, k in c.items() if k >= 2}

    merged = True
    while merged:
        merged = False
        for i, (gw, members) in enumerate(groups):
            if len(members) != 1:
                continue
            for jdx, (hw, others) in enumerate(groups):
                if jdx != i and len(others) >= 2 and words(members[0]["text"]) & theme(others):
                    hw |= gw; others.append(members[0]); groups.pop(i); merged = True; break
            if merged:
                break
    return [m for _, m in groups]


def title_plain(group: list[dict]) -> str:
    """The two stems most members share, each shown as its commonest full word: 'Shared: delivery, customers'."""
    full = Counter(w for n in group for w in re.findall(r"[a-z0-9]+", n["text"].lower()) if len(w) > 2 and w not in STOP)
    by_stem = Counter(w[:5] for n in group for w in set(re.findall(r"[a-z0-9]+", n["text"].lower())) if len(w) > 2 and w not in STOP)
    out = []
    for stem, _ in by_stem.most_common(2):
        word = max((w for w in full if w[:5] == stem), key=lambda w: full[w])
        out.append(word)
    return ("Shared: " + ", ".join(out))[:120] if out else "Shared need"


def describe(group: list[dict]) -> str:
    biz = sorted({n["node_name"] for n in group})
    if len(biz) == 1:
        lines = [f"Suggested from what {biz[0]} said it needs, as something the lane could share. Nobody owns it yet: lead it if you'll organise it, back it if your business wants it too."]
    else:
        lines = [f"Suggested from {len(group)} needs across {len(biz)} businesses. Nobody owns it yet: lead it if you'll organise it, co-sign it if your business wants it."]
    lines += [f"- {n['node_name']}: {n['text']}" for n in group]
    return "\n".join(lines)[:2000]


def _lean(con, node_id: str) -> str:
    """The business's trade-off positions as words for the model: 'earn more (strongly), the long run, things'."""
    vec = business_vector(con, node_id)
    out = []
    for k, left, right, _ in core.VALUE_DIMENSIONS:
        v = vec.get(k, 5)
        if v == 5: continue
        side = left if v < 5 else right
        out.append(side.lower() + (" (strongly)" if v <= 1 or v >= 9 else ""))
    return ", ".join(out) or "no strong lean"


def cluster_llm(con, needs: list[dict], drafts: list[dict], min_nodes: int, rnd: dict | None = None) -> dict | None:
    """Ask the model to attach needs to existing drafts or write new ones, with titles and rupee estimates.
    Returns {"attach": [(draft_id, need_id)], "groups": [...]} or None when no key, a refusal, or any failure."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        items = [{"id": n["id"], "business": n["node_name"], "need": n["text"], "leans_towards": _lean(con, n["node_id"])} for n in needs]
        existing = [{"draft_id": d["id"], "title": d["title"], "text": d["text"][:300]} for d in drafts]
        pot = int(rnd["budget_paise"] // 100) if rnd else 0
        n_biz = len(core.children(con, rnd["node_id"])) if rnd else len({n["node_id"] for n in needs})
        money = (f" The whole fund is ₹{pot:,} for about {max(n_biz, 1)} businesses, so one proposal should rarely ask for more than a "
                 f"tenth of it and never more than ₹{int(pot * MAX_SHARE):,}. Give ideal_rupees only; minimums are not used. Price what one shared item actually costs in Goa: a second-hand "
                 "delivery scooter ₹40,000–60,000, a billing software licence ₹5,000–15,000 a year, a commercial fridge ₹30,000–80,000, "
                 "a trainer for two days ₹10,000–25,000, bins and signage for a lane ₹10,000–30,000.") if pot else ""
        system = ("You turn the stated needs of small businesses in Goa, India into draft proposals for a shared fund: things "
                  "businesses could buy or run together, such as a delivery scooter shared along a lane, one software licence for "
                  "several shops, a shared cold store, a paid trainer. A need that stands alone still becomes a draft, written as the "
                  "shared version of it (one shop's scooter becomes a scooter shared by the shops on its lane). Group needs that one "
                  "shared purchase or service would meet. When an existing draft already covers a need, attach the need to it instead "
                  "of writing a new one. Each business says what it leans towards on five trade-offs (earn more or spend less; this "
                  "season or the long run; my business or the whole lane; things or people; proven or new): let that shape the draft, "
                  "for example a cheaper, proven option for a business leaning spend less and proven. Prices in rupees, realistic for "
                  "Goa in 2026." + money + " Answer with JSON only: an object with keys attach (list of {draft_id, need_id}) and groups (list of "
                  "{title (short, plain), text (one or two sentences on what is bought and how it is shared), ideal_rupees (integer), "
                  "min_rupees (integer, the least that still makes sense, 0 if any amount helps), need_ids (list)}).")
        r = client.beta.messages.create(
            model=MODEL, max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "medium"}, system=system,
            messages=[{"role": "user", "content": json.dumps({"needs": items, "existing_drafts": existing}, ensure_ascii=False)}])
        if r.stop_reason == "refusal":
            log.warning("twin: model declined (%s), falling back to plain clustering", getattr(getattr(r, "stop_details", None), "category", None)); return None
        text = "".join(b.text for b in r.content if b.type == "text")
        m = re.search(r"\{.*\}", text, re.S)
        out = json.loads(m.group(0) if m else text)
        known = {n["id"] for n in needs}
        known_drafts = {d["id"] for d in drafts}
        attach = [(a.get("draft_id"), a.get("need_id")) for a in out.get("attach", []) if a.get("draft_id") in known_drafts and a.get("need_id") in known]
        taken = {nid for _, nid in attach}
        groups = []
        for g in out.get("groups", []):
            ids = [i for i in g.get("need_ids", []) if i in known and i not in taken]
            if not ids: continue
            groups.append({"title": str(g.get("title", ""))[:120], "text": str(g.get("text", ""))[:1500],
                           "ideal": max(1, int(g.get("ideal_rupees", 0) or 0)), "min": max(0, int(g.get("min_rupees", 0) or 0)), "need_ids": ids})
        return {"attach": attach, "groups": groups}
    except Exception as e:  # network, key, parse: the plain path is always there
        log.warning("twin: model path failed, falling back to plain clustering: %s: %s", type(e).__name__, str(e)[:200])
        return None


def existing_drafts(con, round_id: str) -> list[dict]:
    return db.rows(con.execute("SELECT * FROM proposals WHERE round_id=? AND source='twin' AND status IN ('draft','eligible') ORDER BY created_at", (round_id,)).fetchall())


def draft_from_needs(con, pool_id: str, min_nodes: int = 1, dry_run: bool = False) -> list[dict]:
    """Turn the pool's open needs into draft proposals in its round. A need that reads like an existing twin draft
    is attached to it; the rest are grouped and written, one draft per group. One need is enough for a draft, so
    the first business to register sees something on the table; `min_nodes=2` restores the old behaviour."""
    rnd = core.current_round(con, pool_id)
    if not rnd or rnd["status"] != "proposals":
        raise core.PBError("proposals_closed")
    needs = core.needs_of(con, pool_id, include_children=True, status="open")
    if not needs:
        return []
    by_id = {n["id"]: n for n in needs}
    drafts: list[dict] = []
    if len(needs) > BATCH and not dry_run:
        # a thousand needs do not fit one prompt: draft in batches, each seeing the drafts made so far so later
        # needs attach to them instead of duplicating
        for i in range(0, len(needs), BATCH):
            drafts += _draft_batch(con, rnd, needs[i:i + BATCH], min_nodes)
        return drafts
    return _draft_batch(con, rnd, needs, min_nodes, dry_run)


def _draft_batch(con, rnd: dict, needs: list[dict], min_nodes: int, dry_run: bool = False) -> list[dict]:
    by_id = {n["id"]: n for n in needs}
    current = existing_drafts(con, rnd["id"])
    drafts: list[dict] = []
    res = cluster_llm(con, needs, current, min_nodes, rnd)
    if res is None:
        # plain: a need joins a draft it shares two stems with (title + text); the rest cluster as before
        attach = []
        for n in needs:
            w = words(n["text"])
            for d in current:
                if len(w & words(d["title"] + " " + d["text"])) >= 2:
                    attach.append((d["id"], n["id"])); break
        taken = {nid for _, nid in attach}
        rest = [n for n in needs if n["id"] not in taken]
        groups = []
        for g in cluster_plain(rest):
            groups.append({"title": title_plain(g), "text": describe(g), "ideal": DEFAULT_IDEAL_RUPEES * len({n["node_id"] for n in g}),
                           "min": 0, "need_ids": [n["id"] for n in g]})
        res = {"attach": attach, "groups": groups}
    for did, nid in res["attach"]:
        if dry_run:
            drafts.append({"attach_to": did, "need_id": nid}); continue
        try:
            drafts.append(core.attach_need_to_draft(con, did, nid))
        except core.PBError:
            pass
    for g in res["groups"]:
        members = [by_id[i] for i in g["need_ids"] if i in by_id]
        if len({n["node_id"] for n in members}) < min_nodes:
            continue
        text = g["text"] if g["text"] and g["text"].startswith("Suggested") else (g["text"] + "\n\n" + describe(members)).strip()
        cap = max(1, int(rnd["budget_paise"] // 100 * MAX_SHARE))   # a hard ceiling whatever the model or the default said
        ideal = min(max(1, int(g["ideal"])), cap); mn = 0   # twin drafts carry no minimum: any amount helps, the lead decides later
        if dry_run:
            drafts.append({"title": g["title"], "text": text, "ideal_paise": ideal * 100, "min_paise": mn * 100, "needs": len(members)})
            continue
        drafts.append(core.add_twin_draft(con, rnd["id"], title=g["title"], text=text, ideal_paise=ideal * 100, min_paise=mn * 100,
                                          need_ids=[n["id"] for n in members]))
    return drafts


def schedule(pool_id: str) -> None:
    """Draft from the pool's open needs without holding up a request: a thread with its own connection. Harmless
    when there is nothing to group (fewer than two businesses with needs, or no round at proposals). Inline when
    PB_TWIN_INLINE is set (tests)."""
    import os
    import threading

    def run():
        try:
            con = db.connect()
            try:
                draft_from_needs(con, pool_id)
            finally:
                try: con.close()
                except Exception: pass
        except core.PBError:
            pass
        except Exception as e:  # never surface to a user; the next trigger tries again
            log.info("twin: background draft skipped: %s", type(e).__name__)

    if os.environ.get("PB_TWIN_INLINE"):
        run()
    else:
        threading.Thread(target=run, name="twin", daemon=True).start()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--min-nodes", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    con = db.connect()
    for d in draft_from_needs(con, a.pool, a.min_nodes, a.dry_run):
        print(f"{d['title']:50s} ideal ₹{d['ideal_paise'] // 100:,}  {d.get('needs', '')}")



# ----------------------------------------------------------------------------
# what a draft is about, and which drafts a business should see first
# ----------------------------------------------------------------------------
TAG_WORDS = {   # per axis: (stems pulling to the left pole, stems pulling to the right pole); five-letter stems like words()
    "earn": (["custo", "order", "deliv", "sale", "sell", "marke", "promo", "reach", "onlin", "insta", "websi", "touri", "book", "incom", "reven", "footf"],
             ["billi", "invoi", "gst", "accou", "stock", "inven", "softw", "licen", "payme", "paper", "cost", "save", "cheap", "time", "admin", "elect", "solar", "rent"]),
    "horizon": (["seaso", "peak", "decem", "chris", "this", "now", "month", "week", "event", "festi", "diwal", "summe"],
                ["fridg", "cold", "store", "scoot", "van", "machi", "equip", "build", "roof", "solar", "year", "asset", "perma", "invest", "websi"]),
    "scope": (["my", "our", "shop", "own", "licen", "billi", "accou", "fridg", "sign", "stock"],
              ["lane", "beach", "road", "villa", "all", "every", "share", "commo", "clean", "waste", "plast", "green", "traff", "parki", "water", "commu", "touri", "goa", "mandr", "ashve", "morji", "aramb"]),
    "means": (["scoot", "fridg", "cold", "machi", "equip", "softw", "licen", "bins", "kit", "van", "oven", "print", "solar", "sign", "websi", "phone", "table", "chair", "tent"],
              ["train", "trainer", "cours", "teach", "staff", "hire", "worke", "cook", "chef", "guide", "assis", "skill", "learn", "coach", "consu", "perso", "someo", "rider"]),
    "risk": (["billi", "gst", "fridg", "scoot", "cold", "stock", "sign", "bins", "clean", "tally", "van", "oven", "print"],
             ["ai", "chatg", "app", "onlin", "insta", "digit", "new", "exper", "try", "pilot", "autom", "robot", "websi", "drone", "qr", "crypt"]),
}


def tags_for(text: str) -> dict[str, float]:
    """Where a text sits on each axis, -1 (left pole) .. +1 (right pole); 0 when it says nothing on that axis.
    Also returns `_conf`: which axes it spoke to, so the fit can ignore the silent ones."""
    w = words(text)
    low = text.lower()
    out, conf = {}, {}
    for axis, (left, right) in TAG_WORDS.items():
        def hits(stems):
            return sum(1 for st in stems if (len(st) >= 4 and any(x.startswith(st) or st.startswith(x) for x in w)) or (len(st) < 4 and re.search(r"\b" + re.escape(st) + r"\b", low)))
        l, r = hits(left), hits(right)
        out[axis] = round((r - l) / (r + l), 3) if (r + l) else 0.0
        conf[axis] = 1 if (r + l) else 0
    out["_conf"] = conf
    return out


def position(v: int | float) -> float:
    """A slider value 0..10 as -1..+1, 5 being the centre."""
    return (float(v) - 5) / 5


def business_vector(con, node_id: str) -> dict[str, int]:
    vv = core.values_of(con, node_id, include_children=False)
    return vv[-1]["vector"] if vv else {k: 5 for k, *_ in core.VALUE_DIMENSIONS}


def rank_for(con, node_id: str, proposals: list[dict], limit: int = 10) -> list[dict]:
    """The proposals a business should see first: scored by its slider vector against each proposal's tags, with
    a boost when the proposal was built from, or reads like, the business's own need. Each returned proposal
    carries `score` and `suggested` (it matches their need). Ties keep the given order."""
    vec = business_vector(con, node_id)
    needs = core.needs_of(con, node_id, include_children=False, status=None)
    need_w = words(" ".join(n["text"] for n in needs)) if needs else set()
    node = core.get_node(con, node_id)
    out = []
    for i, p in enumerate(proposals):
        t = tags_for(p["title"] + " " + (p.get("text") or ""))
        spoke = [d for d, c in t["_conf"].items() if c]
        # closeness on the axes the proposal speaks to: 1 when it sits where the business leans, 0 at the opposite pole
        fit = (sum(1 - abs(position(vec.get(d, 5)) - t[d]) / 2 for d in spoke) / len(spoke)) if spoke else 0.5
        overlap = len(need_w & words(p["title"] + " " + (p.get("text") or "")))
        mine = node["name"] in (p.get("text") or "")   # the twin names the businesses whose needs it grouped
        suggested = mine or overlap >= 2
        q = dict(p); q["score"] = round(fit + (0.6 if mine else 0) + 0.15 * min(overlap, 4), 3); q["suggested"] = suggested; q["_i"] = i
        out.append(q)
    out.sort(key=lambda q: (-q["score"], q["_i"]))
    for q in out: q.pop("_i")
    return out[:limit]

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
