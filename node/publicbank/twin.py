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
    lines = [f"Suggested from {len(group)} needs across {len(biz)} businesses. Nobody owns it yet: lead it if you'll organise it, co-sign it if your business wants it."]
    lines += [f"- {n['node_name']}: {n['text']}" for n in group]
    return "\n".join(lines)[:2000]


def cluster_llm(needs: list[dict]) -> list[dict] | None:
    """Ask the model for groups with titles and rupee estimates. None when no key, a refusal, or any failure."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        items = [{"id": n["id"], "business": n["node_name"], "need": n["text"]} for n in needs]
        system = ("You group the needs of small businesses in Goa, India into things they could buy or run together: a shared "
                  "delivery scooter, one software licence for several shops, a shared cold store, a paid trainer. Group only needs "
                  "that one shared purchase or service would meet. Each group must span at least two different businesses. "
                  "Answer with JSON only: a list of objects with keys title (short, plain), text (one or two sentences on what "
                  "is bought and how it is shared), ideal_rupees (integer, what would let it work well), min_rupees (integer, the "
                  "least that still makes sense, 0 if any amount helps), need_ids (list of the ids in the group). Prices in rupees, "
                  "realistic for Goa in 2026. Leave out needs that stand alone.")
        r = client.beta.messages.create(
            model=MODEL, max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "medium"}, system=system,
            messages=[{"role": "user", "content": json.dumps(items, ensure_ascii=False)}])
        if r.stop_reason == "refusal":
            log.info("twin: model declined (%s)", getattr(getattr(r, "stop_details", None), "category", None)); return None
        text = "".join(b.text for b in r.content if b.type == "text")
        m = re.search(r"\[.*\]", text, re.S)
        out = json.loads(m.group(0) if m else text)
        ok = []
        known = {n["id"] for n in needs}
        for g in out:
            ids = [i for i in g.get("need_ids", []) if i in known]
            if len(ids) < 2: continue
            ok.append({"title": str(g.get("title", ""))[:120], "text": str(g.get("text", ""))[:1500],
                       "ideal": max(1, int(g.get("ideal_rupees", 0) or 0)), "min": max(0, int(g.get("min_rupees", 0) or 0)), "need_ids": ids})
        return ok
    except Exception as e:  # network, key, parse: the plain path is always there
        log.info("twin: model path skipped: %s", type(e).__name__)
        return None


def draft_from_needs(con, pool_id: str, min_nodes: int = 2, dry_run: bool = False) -> list[dict]:
    """Group open needs across the pool's children and write one draft proposal per group into the pool's round."""
    rnd = core.current_round(con, pool_id)
    if not rnd or rnd["status"] != "proposals":
        raise core.PBError("proposals_closed")
    needs = core.needs_of(con, pool_id, include_children=True, status="open")
    if not needs:
        return []
    by_id = {n["id"]: n for n in needs}
    drafts = []
    groups = cluster_llm(needs)
    if groups is None:
        groups = []
        for g in cluster_plain(needs):
            if len({n["node_id"] for n in g}) < min_nodes:
                continue
            groups.append({"title": title_plain(g), "text": describe(g), "ideal": DEFAULT_IDEAL_RUPEES * len({n["node_id"] for n in g}),
                           "min": 0, "need_ids": [n["id"] for n in g]})
    for g in groups:
        members = [by_id[i] for i in g["need_ids"] if i in by_id]
        if len({n["node_id"] for n in members}) < min_nodes:
            continue
        text = g["text"] if g["text"] and g["text"].startswith("Suggested") else (g["text"] + "\n\n" + describe(members)).strip()
        ideal = max(1, int(g["ideal"])); mn = min(max(0, int(g["min"])), ideal)
        if dry_run:
            drafts.append({"title": g["title"], "text": text, "ideal_paise": ideal * 100, "min_paise": mn * 100, "needs": len(members)})
            continue
        drafts.append(core.add_twin_draft(con, rnd["id"], title=g["title"], text=text, ideal_paise=ideal * 100, min_paise=mn * 100,
                                          need_ids=[n["id"] for n in members]))
    return drafts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--min-nodes", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    con = db.connect()
    for d in draft_from_needs(con, a.pool, a.min_nodes, a.dry_run):
        print(f"{d['title']:50s} ideal ₹{d['ideal_paise'] // 100:,}  {d.get('needs', '')}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()


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
