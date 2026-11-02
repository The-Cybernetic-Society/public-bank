"""
Places: Goa's local bodies as digital twins.

A twin is a public profile of one place (a village panchayat, a town, a taluka, a district, the state) built
only from published figures, read against the scheme catalogue (`schemes.py`). For each scheme it says one of:

  eligible   the place is the kind of body the scheme funds, or has the people it is for, on the figures we hold
  plausible  the place could qualify by a step people there can take (register a group, a society, a committee),
             or the condition is one we hold no figure for yet
  unlikely   a condition the place cannot meet: an urban scheme for a village, a tribal-area scheme where the
             census counts no Scheduled Tribe households, a marine scheme inland

and `drawing` when programme data shows the scheme already running there. Every verdict carries its reasons
and the rule key it came from, so the reader can argue with the rule.

Profiles come from `publicbank/data/goa_divisions.json`: Census 2011 village figures summed per panchayat,
flags (coastal, estuarine) and whatever per-panchayat programme tables were found. Talukas, districts and the
state are roll-ups of their panchayats and towns: sums for the figures, "any" for the flags, and a count of how
many member places each verdict holds in.

    python -m publicbank.places --list [--kind village_panchayat]
    python -m publicbank.places --twin mandrem
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from functools import lru_cache

from . import schemes as S

DIVISIONS = os.path.join(S.DATA_DIR, "goa_divisions.json")
KINDS = {"village_panchayat": "Village panchayat", "municipal_council": "Municipal council", "municipal_corporation": "Municipal corporation",
         "taluka": "Taluka", "district": "District", "state": "State"}
ROLLUPS = ("taluka", "district", "state")
NUMBERS = ("households", "population", "male", "female", "sc", "st", "literates", "workers", "cultivators", "agri_labourers",
           "hh_industry", "other_workers", "non_workers")
PROGRAMMES = ("mgnrega_job_cards", "mgnrega_active_workers", "shgs", "fisher_households", "dairy_societies", "pmayg_houses")

# requirements that describe who a scheme is for without constraining the place; they are reported, not judged
DESCRIPTIVE = {"women", "seniors", "pwd", "students", "ex_servicemen", "labour", "business", "artisans", "housing", "institution"}
# forming a group is a condition only when nobody can apply alone: a scheme open to individuals that also
# mentions societies is judged on the individuals
GROUP_KEYS = {"shg", "coop", "ngo", "fra"}
INDIVIDUAL_CLASSES = {"Individual", "Household", "Farmer", "Fisher", "Livestock owner", "Business / entrepreneur", "Student", "Woman",
                      "Senior citizen", "Person with disability", "SC/ST"}
# a scheme already running in a place shows up in programme data under these keys
DRAWING = [("mgnrega_job_cards", re.compile(r"employment guarantee|mgnrega|nrega", re.I)),
           ("shgs", re.compile(r"rural livelihood|nrlm|self.help group", re.I)),
           ("pmayg_houses", re.compile(r"awaa?s yojana.*gramin|pmay-?g", re.I))]

_override: list[dict] | None = None


def set_divisions(divs: list[dict] | None) -> None:
    """Tests hand in their own places; None goes back to the data file."""
    global _override
    _override = divs
    load.cache_clear()
    statuses_of.cache_clear()
    across_goa.cache_clear()


@lru_cache(maxsize=1)
def load() -> dict:
    if _override is not None:
        return {"divisions": _override, "sources": [], "compiled": "test"}
    if not os.path.exists(DIVISIONS):
        return {"divisions": [], "sources": [], "compiled": ""}
    with open(DIVISIONS, encoding="utf-8") as f:
        return json.load(f)


def divisions(kind: str | None = None) -> list[dict]:
    ds = load()["divisions"]
    return [d for d in ds if kind is None or d["kind"] == kind]


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def get(div_id: str) -> dict | None:
    """A place by id. Talukas, districts and the state are built from their members on request."""
    for d in divisions():
        if d["id"] == div_id:
            return d
    if div_id == "goa":
        return rollup("state", "Goa", divisions())
    for kind in ("taluka", "district"):
        for name in sorted({d.get(kind) for d in divisions() if d.get(kind)}):
            if slug(f"{kind}-{name}") == div_id:
                return rollup(kind, name, [d for d in divisions() if d.get(kind) == name])
    return None


def rollups(kind: str) -> list[dict]:
    if kind == "state":
        return [rollup("state", "Goa", divisions())] if divisions() else []
    names = sorted({d.get(kind) for d in divisions() if d.get(kind)})
    return [rollup(kind, n, [d for d in divisions() if d.get(kind) == n]) for n in names]


def rollup(kind: str, name: str, members: list[dict]) -> dict:
    census: dict = {"year": 2011}
    for k in NUMBERS:
        vals = [m.get("census", {}).get(k) for m in members]
        census[k] = sum(v for v in vals if v is not None) if any(v is not None for v in vals) else None
    census["confidence"] = Counter(m.get("census", {}).get("confidence", "none") for m in members).most_common(1)[0][0] if members else "none"
    flags: dict = {}
    for k in ("coastal", "estuarine"):
        vals = [m.get("flags", {}).get(k) for m in members]
        flags[k] = True if any(v is True for v in vals) else (False if vals and all(v is False for v in vals) else None)
    prog: dict = {}
    for k in PROGRAMMES:
        vals = [m.get("programmes", {}).get(k) for m in members]
        prog[k] = sum(v for v in vals if v is not None) if any(v is not None for v in vals) else None
    wk = [m.get("programmes", {}).get("weaker_panchayat") for m in members]
    prog["weaker_panchayats"] = sum(1 for v in wk if v is True) if any(v is not None for v in wk) else None
    d = {"id": "goa" if kind == "state" else slug(f"{kind}-{name}"), "kind": kind, "name": name, "members": [m["id"] for m in members],
         "member_kinds": dict(Counter(m["kind"] for m in members)), "census": census, "flags": flags, "programmes": prog,
         "district": members[0].get("district") if kind == "taluka" and members else None, "sources": []}
    return d


# ----------------------------------------------------------------------------
# the profile: one flat view of a place, None where we hold nothing
# ----------------------------------------------------------------------------
def profile(d: dict) -> dict:
    c = d.get("census", {}) or {}
    fl = d.get("flags", {}) or {}
    pr = d.get("programmes", {}) or {}
    pop = c.get("population")
    cult, agl = c.get("cultivators"), c.get("agri_labourers")
    farmers = (cult or 0) + (agl or 0) if (cult is not None or agl is not None) else None
    p = {"kind": d["kind"], "name": d["name"], "rural": d["kind"] in ("village_panchayat", "taluka", "district", "state"),
         "has_villages": d["kind"] == "village_panchayat" or (d["kind"] in ROLLUPS and (d.get("member_kinds", {}).get("village_panchayat", 0) > 0)),
         "has_towns": d["kind"] in ("municipal_council", "municipal_corporation") or (d["kind"] in ROLLUPS and any(k.startswith("municipal") for k in d.get("member_kinds", {}))),
         "households": c.get("households"), "population": pop, "sc": c.get("sc"), "st": c.get("st"),
         "sc_share": (c["sc"] / pop) if pop and c.get("sc") is not None else None,
         "st_share": (c["st"] / pop) if pop and c.get("st") is not None else None,
         "literacy": (c["literates"] / pop) if pop and c.get("literates") is not None else None,
         "cultivators": cult, "agri_labourers": agl, "farmers": farmers, "hh_industry": c.get("hh_industry"), "other_workers": c.get("other_workers"),
         "workers": c.get("workers"), "census_confidence": c.get("confidence", "none"),
         "coastal": fl.get("coastal"), "estuarine": fl.get("estuarine"),
         "weaker_panchayat": pr.get("weaker_panchayat") if d["kind"] == "village_panchayat" else (pr.get("weaker_panchayats") or None)}
    for k in PROGRAMMES:
        p[k] = pr.get(k)
    return p


def _n(n: int | float | None) -> str:
    return f"{int(n):,}" if n is not None else "?"


# ----------------------------------------------------------------------------
# the verdict: one scheme read against one profile
# ----------------------------------------------------------------------------
def judge(key: str, p: dict) -> tuple[str, str]:
    """One requirement against the figures: ('ok'|'maybe'|'no'|'unknown', reason)."""
    if key == "st":
        if p["st"] is None: return "unknown", "no census figure for Scheduled Tribe households here"
        return ("ok", f"{_n(p['st'])} Scheduled Tribe residents (Census 2011)") if p["st"] > 0 else ("no", "the census counts no Scheduled Tribe residents here")
    if key == "sc":
        if p["sc"] is None: return "unknown", "no census figure for Scheduled Caste households here"
        return ("ok", f"{_n(p['sc'])} Scheduled Caste residents (Census 2011)") if p["sc"] > 0 else ("no", "the census counts no Scheduled Caste residents here")
    if key == "sc_or_st":
        if p["sc"] is None and p["st"] is None: return "unknown", "no census figure for SC or ST residents here"
        tot = (p["sc"] or 0) + (p["st"] or 0)
        return ("ok", f"{_n(tot)} SC or ST residents (Census 2011)") if tot > 0 else ("no", "the census counts no SC or ST residents here")
    if key == "tribal_area":
        sh = p["st_share"]
        if sh is None: return "unknown", "no census figure for the Scheduled Tribe share here"
        if sh >= 0.2: return "ok", f"Scheduled Tribes are {sh:.0%} of residents (Census 2011)"
        if sh >= 0.05: return "maybe", f"Scheduled Tribes are {sh:.0%} of residents; whether that counts as a tribal-inhabited area is the department's call"
        return "no", f"Scheduled Tribes are {sh:.1%} of residents, well short of a tribal-inhabited area"
    if key == "fishers":
        if p["fisher_households"]: return "ok", f"{_n(p['fisher_households'])} fishing households recorded"
        if p["coastal"] is True: return "ok", "a coastal place"
        if p["coastal"] is None: return "unknown", "no coastline flag for this place yet"
        if p["estuarine"]: return "maybe", "inland, but on tidal water: inland fishing and aquaculture schemes may still apply"
        return "no", "inland, with no fishing households recorded"
    if key == "brackish":
        if p["estuarine"] or p["coastal"]: return "ok", "on tidal or coastal water"
        if p["estuarine"] is None and p["coastal"] is None: return "unknown", "no water flag for this place yet"
        return "no", "no brackish or estuarine water here"
    if key == "farmers":
        if p["farmers"] is None: return "unknown", "no census figure for farming households here"
        return ("ok", f"{_n(p['cultivators'])} cultivators and {_n(p['agri_labourers'])} agricultural labourers (Census 2011)") if p["farmers"] > 0 else ("no", "the census counts nobody working in agriculture here")
    if key == "livestock":
        if p["dairy_societies"]: return "ok", f"{_n(p['dairy_societies'])} dairy societies here"
        if p["farmers"]: return "maybe", f"{_n(p['farmers'])} people in agriculture, but no herd or dairy figures for this place"
        if p["farmers"] is None: return "unknown", "no figures on livestock or farming here"
        return "no", "no farming or livestock recorded here"
    if key == "rural":
        return ("ok", "a rural local body") if p["has_villages"] else ("no", "an urban body; this scheme is for rural areas")
    if key == "urban":
        return ("ok", "an urban local body") if p["has_towns"] else ("no", "a village panchayat; this scheme is for urban bodies")
    if key == "remote":
        return "maybe", "meant for remote or unserved hamlets: a wada-level fact the figures do not show"
    if key == "bpl":
        if p["mgnrega_job_cards"]: return "ok", f"{_n(p['mgnrega_job_cards'])} MGNREGA job-card households, a proxy for the BPL and SECC lists"
        if p["households"]: return "ok", f"{_n(p['households'])} households; the BPL and ration lists are not published per place, and every local body in Goa has some"
        return "unknown", "no household figures here"
    if key == "weaker_panchayat":
        if p["weaker_panchayat"] is True: return "ok", "on the government's list of economically weaker panchayats"
        if p["weaker_panchayat"] is None: return "unknown", "the weaker-panchayat list has not been loaded"
        return "no", "not on the weaker-panchayat list"
    if key == "shg":
        if p["shgs"]: return "ok", f"{_n(p['shgs'])} self-help groups registered here"
        return "maybe", "no group registered here in the figures we hold"
    if key == "coop":
        if p["dairy_societies"]: return "ok", f"{_n(p['dairy_societies'])} cooperative societies recorded here"
        return "maybe", "no cooperative society recorded here in the figures we hold"
    if key == "fra":
        if p["st"] is None: return "unknown", "no census figure for Scheduled Tribe residents here"
        return ("maybe", "needs a Forest Rights committee; Scheduled Tribe residents live here") if p["st"] > 0 else ("no", "no Scheduled Tribe residents, so no Forest Rights committee")
    if key == "land":
        if p["cultivators"]: return "ok", f"{_n(p['cultivators'])} cultivators (Census 2011) hold land"
        if p["cultivators"] is None: return "unknown", "no census figure for cultivators here"
        return "maybe", "no cultivators in the census; land holders may still live here"
    return "info", S.label(key)


def verdict(d: dict, s: dict, p: dict | None = None) -> dict:
    """One scheme against one place."""
    p = p or profile(d)
    why: list[str] = []
    steps: list[str] = []
    results: list[str] = []
    body_ok = None
    if s["bodies"]:
        mine = {"village_panchayat": "village_panchayat", "municipal_council": "municipal", "municipal_corporation": "municipal"}.get(d["kind"])
        if d["kind"] in ROLLUPS:
            mine = None
            if "village_panchayat" in s["bodies"] and p["has_villages"]: body_ok = True; why.append("the village panchayats here can apply")
            if "municipal" in s["bodies"] and p["has_towns"]: body_ok = True; why.append("the municipal bodies here can apply")
            if "zilla_panchayat" in s["bodies"] and d["kind"] == "district": body_ok = True; why.append("the Zilla Panchayat applies")
            if body_ok is None: body_ok = False
        elif mine in s["bodies"]:
            body_ok = True; why.append(f"the {KINDS[d['kind']].lower()} itself can apply")
        elif "zilla_panchayat" in s["bodies"] and not s["people"]:
            results.append("maybe"); why.append("applied for by the Zilla Panchayat, not the village body"); steps.append("take it to the Zilla Panchayat member for this area")
            body_ok = None
        else:
            body_ok = False
        if body_ok is False and not s["people"]:
            results.append("no"); why.append(f"for {' and '.join(sorted({'village panchayats' if b == 'village_panchayat' else 'municipalities' if b == 'municipal' else 'zilla panchayats' for b in s['bodies']}))}, not a {KINDS[d['kind']].lower()}")
        elif body_ok is False and s["people"]:
            why.append(f"the {KINDS[d['kind']].lower()} cannot apply itself; people or groups here can")
    alone = bool(set(s["people"]) & INDIVIDUAL_CLASSES) or body_ok is True
    for key in s["needs"]:
        if key in DESCRIPTIVE or (key in GROUP_KEYS and alone):
            why.append("for " + S.label(key))
            continue
        if key in ("rural", "urban") and (s["bodies"] or s["scope"] == "any"):
            continue   # a body scheme's scope is which body applies; a text naming both rural and urban constrains neither
        r, reason = judge(key, p)
        results.append(r); why.append(reason)
        if r in ("maybe", "unknown") and S.step(key):
            steps.append(S.step(key))
    # the caste and tribe counts are facts the rules use, not the headline: say them last
    why = [w for w in why if "Scheduled" not in w and "SC or ST" not in w] + [w for w in why if "Scheduled" in w or "SC or ST" in w]
    drawing = any(p.get(k) for k, rx in DRAWING if rx.search(s["name"]))
    if "no" in results:
        status = "unlikely"
    elif "maybe" in results or "unknown" in results:
        status = "plausible"
    else:
        status = "eligible"
        if not results and not s["bodies"]:
            why.append("open to residents; no condition on the place itself")
    if drawing:
        status = "eligible"
    return {"scheme_id": s["id"], "status": status, "drawing": drawing, "why": why, "steps": sorted(set(steps)), "unknowns": results.count("unknown")}


def twin(d: dict) -> dict:
    p = profile(d)
    vs = [verdict(d, s, p) for s in S.schemes()]
    members = None
    if d["kind"] in ROLLUPS:
        # how many member places each scheme holds in, by status
        kids = [get(i) for i in d["members"]]
        members = {}
        for s in S.schemes():
            members[s["id"]] = dict(Counter(verdict(k, s)["status"] for k in kids if k))
    return {"division": d, "profile": p, "verdicts": vs, "summary": summarize(vs), "members": members}


def summarize(vs: list[dict]) -> dict:
    by_status = Counter(v["status"] for v in vs)
    return {"eligible": by_status.get("eligible", 0), "plausible": by_status.get("plausible", 0), "unlikely": by_status.get("unlikely", 0),
            "drawing": sum(1 for v in vs if v["drawing"]), "schemes": len(vs)}


@lru_cache(maxsize=None)
def statuses_of(div_id: str) -> list[tuple[str, bool]] | None:
    """(status, drawing) per scheme for one place, in catalogue order, cached: the data is static for the life of
    the process, and this is what the counts, the index table and the across-Goa table all need."""
    d = get(div_id)
    if not d:
        return None
    p = profile(d)
    return [(v["status"], v["drawing"]) for v in (verdict(d, s, p) for s in S.schemes())]


def summary_of(div_id: str) -> dict | None:
    st = statuses_of(div_id)
    if st is None:
        return None
    return summarize([{"status": a, "drawing": b} for a, b in st])


AUDIENCE_WEIGHT = {"body": 1.5, "groups": 1.2, "people": 1.0}   # a scheme the body itself can draw is the one it can act on


# how much of a place a requirement speaks to, when the figures cannot say: a rough share of households
REACH_DEFAULT = {"women": 0.5, "bpl": 0.3, "livestock": 0.3, "students": 0.2, "business": 0.15, "labour": 0.1, "seniors": 0.1, "housing": 0.3,
                 "artisans": 0.05, "pwd": 0.02, "ex_servicemen": 0.01, "shg": 0.3, "coop": 0.3, "ngo": 0.3, "fra": 0.3, "remote": 0.2, "land": 0.3}


def reach(s: dict, p: dict) -> float:
    """The share of the place a scheme speaks to: the whole body for a body scheme; for people, the smallest share
    among its requirements (Scheduled Tribe share for an ST scheme, farmers for a farm scheme, fishing households
    for a fisher scheme), with a rough default where no figure exists. Open to everyone → 1."""
    if section(s) == "body":
        return 1.0
    shares = []
    pop, hh = p["population"] or 0, p["households"] or 0
    for k in s["needs"]:
        if k == "st": shares.append(p["st_share"] if p["st_share"] is not None else 0.1)
        elif k == "sc": shares.append(p["sc_share"] if p["sc_share"] is not None else 0.03)
        elif k == "sc_or_st": shares.append((p["st_share"] or 0) + (p["sc_share"] or 0) or 0.1)
        elif k == "tribal_area": shares.append(p["st_share"] if p["st_share"] is not None else 0.1)
        elif k == "farmers": shares.append(min(1.0, (p["farmers"] or 0) / pop) if pop else 0.2)
        elif k in ("fishers", "brackish"): shares.append(min(1.0, (p["fisher_households"] or 0) / hh) if hh and p["fisher_households"] else (0.05 if p["coastal"] else 0.02))
        elif k in REACH_DEFAULT: shares.append(REACH_DEFAULT[k])
    return max(0.01, min(shares)) if shares else 1.0


@lru_cache(maxsize=1)
def across_goa() -> dict[str, dict]:
    """Per scheme, across every local body: `rarity`, the share of bodies where it is NOT eligible (a fish-market
    grant is rare, a library grant is everywhere), and `avg_reach`, the mean share of a body it speaks to. Computed
    once per process from the data."""
    bodies = divisions()
    if not bodies:
        return {}
    miss: Counter = Counter(); rsum: Counter = Counter()
    ss = S.schemes()
    for d in bodies:
        p = profile(d)
        for s, (status, _) in zip(ss, statuses_of(d["id"])):
            if status != "eligible":
                miss[s["id"]] += 1
            rsum[s["id"]] += reach(s, p)
    return {s["id"]: {"rarity": miss[s["id"]] / len(bodies), "avg_reach": rsum[s["id"]] / len(bodies)} for s in ss}


def rarity() -> dict[str, float]:
    return {k: v["rarity"] for k, v in across_goa().items()}


def score(s: dict, v: dict, p: dict) -> tuple[float, float, float]:
    """(score, impact, ease). Impact = money × audience × reach × distinctiveness: the money a scheme puts in one
    hand on a log scale (₹1,000 → 0.5, ₹10 lakh → 0.93, unknown → 0.6); who can act on it (the body 1.5, groups 1.2,
    people 1.0); how much of this place it speaks to (`reach`); and how much more this place than the typical body
    (rarity of eligibility across Goa, and reach here against the Goa average), so the scheme this place in particular
    qualifies for outranks the one every body gets. Ease: eligible 1.0; plausible 0.55 less 0.1 per step people
    would have to take; unlikely 0. A scheme already running here is an achievement, not an opportunity: 0."""
    import math
    mx = s.get("max_rupees")
    money = min(1.0, 0.5 + 0.5 * math.log10(max(mx, 1000)) / 7) if mx else 0.6
    detail = 1.0 if s.get("eligibility") or s.get("benefit") else 0.7
    r = reach(s, p)
    g = across_goa().get(s["id"], {"rarity": 0.0, "avg_reach": r})
    here_vs_goa = 1 + 0.5 * math.log2(max(1.0, r / max(g["avg_reach"], 0.01)))
    impact = money * AUDIENCE_WEIGHT[section(s)] * (0.3 + 0.7 * r) * (0.5 + g["rarity"]) * here_vs_goa * detail
    ease = 0.0 if v["status"] == "unlikely" or v["drawing"] else 1.0 if v["status"] == "eligible" else max(0.25, 0.55 - 0.1 * len(v["steps"]))
    return round(impact * ease, 3), round(impact, 3), ease


def top_schemes(d: dict, n: int = 3, p: dict | None = None) -> list[dict]:
    """The n schemes worth this place's attention first: impact × ease, with the verdict and a one-line reason."""
    p = p or profile(d)
    ranked = []
    for s in S.schemes():
        v = verdict(d, s, p)
        sc, impact, ease = score(s, v, p)
        if sc <= 0:
            continue
        ranked.append({"id": s["id"], "name": s["name"], "department": s["department"], "max_rupees": s.get("max_rupees"), "status": v["status"],
                       "score": sc, "impact": impact, "ease": ease, "why": v["why"][0] if v["why"] else "", "steps": v["steps"], "section": section(s),
                       "source_url": s.get("source_url", "")})
    ranked.sort(key=lambda r: (-r["score"], r["name"]))
    # one of each: the portal lists some schemes twice under slightly different titles (two Sahayata rows)
    out, seen = [], set()
    for r in ranked:
        key = " ".join(S.core_name(r["name"]).split()[:3])
        if key in seen:
            continue
        seen.add(key); out.append(r)
        if len(out) == n:
            break
    return out


SLOTS = ("body", "groups", "people")


def slot_label(slot: str, d: dict) -> str:
    if slot == "body":
        return "For the local bodies here" if d["kind"] in ROLLUPS else f"For the {KINDS.get(d['kind'], d['kind']).lower()} itself"
    return "For groups and societies here" if slot == "groups" else "For households and people here"


def top_by_audience(d: dict, p: dict | None = None) -> list[dict]:
    """One scheme per audience: the best for the body itself, the best for groups and societies here, the best for
    households and people here, each by impact × ease. A place reads as a portrait, not a league table. A slot with
    nothing scorable is filled from the next-best of the others, so the card still shows three."""
    p = p or profile(d)
    ranked = top_schemes(d, 60, p)
    out, used = [], set()
    for slot in SLOTS:
        pick = next((r for r in ranked if r["section"] == slot and r["id"] not in used), None)
        if pick:
            out.append({**pick, "slot": slot, "slot_label": slot_label(slot, d)}); used.add(pick["id"])
    for r in ranked:
        if len(out) >= 3:
            break
        if r["id"] not in used:
            out.append({**r, "slot": r["section"], "slot_label": slot_label(r["section"], d)}); used.add(r["id"])
    return out


def card(d: dict, n: int = 3) -> dict:
    """What the map shows when a place is tapped: who it is, four figures, the counts, the top schemes, where to see all."""
    p = profile(d)
    vs = [verdict(d, s, p) for s in S.schemes()]
    sm = summarize(vs)
    kids = d.get("member_kinds", {}) if d["kind"] in ROLLUPS else {}
    return {"id": d["id"], "name": d["name"], "kind": d["kind"], "kind_word": KINDS.get(d["kind"], d["kind"]), "taluka": d.get("taluka"), "district": d.get("district"),
            "wards": d.get("wards"), "members": sum(kids.values()) if kids else None,
            "members_words": ", ".join(f"{n} {'village panchayat' if k == 'village_panchayat' else 'town'}{'s' if n != 1 else ''}" for k, n in
                                       ((k, sum(v for kk, v in kids.items() if (kk == 'village_panchayat') == (k == 'village_panchayat'))) for k in ('village_panchayat', 'municipal')) if n) if kids else None,
            "facts": {"households": p["households"], "population": p["population"], "st_share": p["st_share"], "coastal": p["coastal"],
                      "farmers": p["farmers"], "fisher_households": p["fisher_households"], "dairy_societies": p["dairy_societies"]},
            "summary": sm, "top": top_by_audience(d, p), "url": f"/twin/{d['id']}",
            "up": ("goa" if d["kind"] == "district" else slug("district-" + d["district"]) if d["kind"] == "taluka" and d.get("district") else
                   slug("taluka-" + d["taluka"]) if d.get("taluka") and d["kind"] not in ROLLUPS else None)}


def resolve(text: str) -> dict | None:
    """A place typed by a person → the body: 'Mandrem', 'Mandrem · Pernem', 'Mandrem (Pernem)', an id, a village's
    name or an alias. The taluka, when given, narrows it; without one, a name that only one body carries wins."""
    t = (text or "").strip()
    if not t:
        return None
    if d := get(t):
        return d if d["kind"] not in ROLLUPS else None
    m = re.match(r"^(.*?)\s*(?:·|\(|,|-)\s*([A-Za-z .]+?)\)?\s*$", t)
    name, taluka = (m.group(1), m.group(2)) if m and any(_norm(m.group(2)) == _norm(k) for k in TALUKA_WORDS | {"sattari"}) else (t, "")
    d, how = match_body(name, taluka or "", divisions(), {}, cross=True, threshold=0.9)
    return d


def choices() -> list[dict]:
    """Every body and every census village name, as the typeahead's options: {label, id}."""
    out, seen = [], set()
    for d in divisions():
        lab = f"{d['name']} · {d['taluka']}"
        out.append({"label": lab, "id": d["id"]}); seen.add(_norm(lab))
        for v in d.get("villages", []):
            nm = re.sub(r"\s*\(.*?\)", "", v["name"]).strip()
            lab = f"{nm} · {d['taluka']}"
            if nm and _norm(nm) != _norm(d["name"]) and _norm(lab) not in seen:
                out.append({"label": lab, "id": d["id"], "village": True}); seen.add(_norm(lab))
        for a in d.get("aliases", []):
            lab = f"{a} · {d['taluka']}"
            if _norm(lab) not in seen:
                out.append({"label": lab, "id": d["id"], "alias": True}); seen.add(_norm(lab))
    return out


def locate(lat: float, lon: float) -> dict | None:
    """The body a point falls in, by ray casting over the map's polygons (map units, same projection). None
    outside Goa or when the map is not built."""
    from . import twinmap
    if not twinmap.available() or not (14.5 <= lat <= 16.0 and 73.5 <= lon <= 74.5):
        return None
    x, y = twinmap.project(lon, lat)
    for f in twinmap.load()["features"]:
        if f["level"] != "body":
            continue
        inside = False
        for ring in f["rings"]:
            n = len(ring)
            for i in range(n):
                x1, y1 = ring[i]; x2, y2 = ring[(i + 1) % n]
                if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                    inside = not inside
        if inside:   # even-odd over all rings, so a hole cancels the shell
            return get(f["id"])
    return None


def warm() -> dict:
    """Compute everything the twin pages need before the first visitor asks: the per-place counts for every body
    and roll-up (`summary_of`), the across-Goa rarity table behind the card's ranking, and the map file. Called at
    startup; about three seconds on one CPU, after which every twin page answers from cache."""
    import time
    t0 = time.time()
    ids = [d["id"] for d in divisions()] + [r["id"] for k in ROLLUPS for r in rollups(k)]
    for i in ids:
        summary_of(i)
    across_goa()
    try:
        from . import twinmap
        if twinmap.available():
            twinmap.load()
    except Exception:
        pass
    return {"places": len(ids), "seconds": round(time.time() - t0, 2)}


def section(s: dict) -> str:
    """Which part of the twin page a scheme belongs to: the body itself, groups here, or people here. 'SC/ST' names
    who a scheme is for, not who applies: a tribal community hall a panchayat builds is still the body's."""
    alone = set(s["people"]) & INDIVIDUAL_CLASSES
    if (s["bodies"] or (set(s["people"]) - INDIVIDUAL_CLASSES)) and alone <= {"SC/ST"}:
        return "body" if s["bodies"] else "groups"
    if s["people"] and not alone:
        return "groups"
    return "people"


# ----------------------------------------------------------------------------
# building the divisions file from the published sources in data/sources/
# ----------------------------------------------------------------------------
SOURCES = os.path.join(S.DATA_DIR, "sources")
PCA = os.path.join(SOURCES, "goa_pca_villages.csv")              # Census 2011 PCA, village and town rows, Goa
VILLAGE_MAP = os.path.join(SOURCES, "goa_village_to_panchayat.csv")   # LGD: census village code → local body
CENSUS_FIELDS = {"households": "households", "population": "pop_total", "male": "pop_male", "female": "pop_female", "sc": "pop_sc", "st": "pop_st",
                 "literates": "literates", "workers": "total_workers", "cultivators": "cultivators_total", "agri_labourers": "agri_labourers_total",
                 "hh_industry": "hh_industry_total", "other_workers": "other_workers_total", "non_workers": "non_workers"}
TALUKA = {"Satari": "Sattari"}   # the census spelling → the one on the panchayat's own letterhead


LOCAL_BODIES = os.path.join(SOURCES, "goa_local_bodies.csv")   # LGD: every panchayat and town, with codes, wards, aliases
LGD_SOURCE = "Local Government Directory (lgdirectory.gov.in, snapshot of 2026-11-01)"


def build_divisions(bodies_path: str = LOCAL_BODIES, pca_path: str = PCA, map_path: str = VILLAGE_MAP) -> dict:
    """Every local body on the LGD list, with Census 2011 figures attached. A village panchayat's figures are the
    sum of its census villages (through the LGD village → local body mapping, by code); a town's are its census
    town row. A census unit that LGD places in two bodies is split half and half (six cases, all on the edge of a
    census town). A body whose villages have no census row of their own (six panchayats inside Panaji's urban
    outgrowth, and Harvalem, made after 2011) keeps the LGD facts and no figures. Flags and programme figures
    start empty; `merge_sources` adds them."""
    import csv
    pca_rows = list(csv.DictReader(open(pca_path, encoding="utf-8")))
    pca = {}
    for r in pca_rows:
        # Panaji has two rows under one code: the corporation and the corporation with its outgrowths. The outgrowths
        # are the villages of the neighbouring panchayats, so the corporation itself is the row to use.
        if r["village_town_code"] not in pca or "+ OG" in pca[r["village_town_code"]]["name"]:
            pca[r["village_town_code"]] = r
    links = list(csv.DictReader(open(map_path, encoding="utf-8")))
    per_code = Counter(r["census_village_code"] for r in links)
    by_body: dict[str, list[dict]] = {}
    for r in links:
        if r["local_body_code"]:
            by_body.setdefault(r["local_body_code"], []).append(r)
    out = []
    for b in csv.DictReader(open(bodies_path, encoding="utf-8")):
        d = {"id": None, "kind": b["kind"], "name": b["name"].strip(), "lgd_code": int(b["lgd_code"]) if b["lgd_code"] else None,
             "taluka": TALUKA.get(b["taluka"], b["taluka"]), "district": b["district"], "district_census_2011": b["district_census_2011"],
             "wards": int(b["wards"]) if b["wards"] else None, "aliases": [a for a in b["aliases"].split("|") if a],
             "villages": [], "census": {"year": 2011, **{k: 0 for k in CENSUS_FIELDS}, "confidence": "none"},
             "flags": {"coastal": None, "estuarine": None}, "programmes": {}, "sources": [LGD_SOURCE]}
        units: list[tuple[str, float]] = []
        if b["kind"] == "village_panchayat":
            seen = set()
            for r in by_body.get(b["lgd_code"], []):
                if r["census_village_code"] in pca and r["census_village_code"] not in seen:
                    units.append((r["census_village_code"], 1 / per_code[r["census_village_code"]])); seen.add(r["census_village_code"])
            for code in b["village_census_codes"].split("|"):   # LGD's own village list, where the mapping file has nothing
                if code and code in pca and code not in seen:
                    units.append((code, 1 / max(per_code.get(code, 1), 1))); seen.add(code)
        else:
            if b["census_town_code"] in pca:
                units.append((b["census_town_code"], 1))
            for r in links:   # a village LGD places inside this municipality
                if r["local_body_code"] == b["lgd_code"] and r["level"] == "VILLAGE" and r["census_village_code"] in pca:
                    units.append((r["census_village_code"], 1 / per_code[r["census_village_code"]]))
        for code, share in units:
            row = pca[code]
            d["villages"].append({"code": int(code), "name": row["name"].strip(), "share": share})
            for k, col in CENSUS_FIELDS.items():
                d["census"][k] += int(row[col]) * share
        missing = [c for c in b["village_census_codes"].split("|") if c and c not in pca] if b["kind"] == "village_panchayat" else []
        if units:
            for k in CENSUS_FIELDS:
                d["census"][k] = int(round(d["census"][k]))
            d["census"]["confidence"] = "partial" if missing else "mapped"
            if missing:
                d["census"]["note"] = f"{len(missing)} of the body's villages have no census row of their own (counted inside Panaji's urban outgrowth in 2011); the figures cover the rest"
            d["sources"].append("Census 2011 PCA, village and town rows summed per body")
        else:
            for k in CENSUS_FIELDS:
                d["census"][k] = None
            d["census"]["note"] = "no census row of its own: its villages were counted inside Panaji's urban outgrowth in 2011" if b["taluka"] == "Tiswadi" else "no Census 2011 unit maps to this body"
        out.append(d)
    # ids: the name, with the taluka when two bodies share one (Navelim, Querim, Sanvordem)
    names = Counter(slug(d["name"]) for d in out)
    for d in out:
        d["id"] = slug(d["name"]) if names[slug(d["name"])] == 1 else slug(f"{d['name']}-{d['taluka']}")
    out.sort(key=lambda d: (d["district"], d["taluka"], d["name"]))
    return {"compiled": "2026-11-02", "divisions": out,
            "sources": [LGD_SOURCE,
                        "Census of India 2011, Primary Census Abstract, village and town level, Goa (censusindia.gov.in NADA catalogue 6776, 6777)",
                        "Local Government Directory, village to local body mapping for Goa (via the India Data Portal snapshot of 2023-10-20)"]}


FLAGS = os.path.join(SOURCES, "goa_place_flags.csv")           # id or lgd_code, coastal, estuarine, source
PROGRAMMES_CSV = os.path.join(SOURCES, "goa_programmes.csv")    # id or lgd_code, key, value, as_of, source
NAMES = os.path.join(SOURCES, "goa_place_names.csv")            # lgd_code, name, wards, aliases, source


def _find(divs: list[dict], r: dict) -> dict | None:
    """A row's place: by LGD code when the row has one, else by id, else by slug of the name (with taluka)."""
    code = (r.get("lgd_code") or "").strip()
    if code:
        for d in divs:
            if str(d.get("lgd_code") or "") == code:
                return d
    key = (r.get("id") or "").strip() or slug(r.get("name", ""))
    for d in divs:
        if d["id"] == key or (slug(d["name"]) == key and (not r.get("taluka") or d["taluka"] == r.get("taluka"))):
            return d
    return None


def _bool(s: str) -> bool | None:
    s = (s or "").strip().lower()
    return True if s in ("1", "true", "yes", "y") else False if s in ("0", "false", "no", "n") else None


FISHING = os.path.join(SOURCES, "goa_marine_fishing_villages_cmfri_2016.csv")   # CMFRI Marine Fisheries Census 2016: 41 villages
DAIRY = os.path.join(SOURCES, "goa_dairy_cooperative_societies_2008.csv")        # Registrar of Cooperative Societies, dairy societies by place, 2008
ST_TABLE = os.path.join(SOURCES, "goa_st_population_by_panchayat.csv")          # Directorate of Tribal Welfare: ST population per body (Census 2011 basis)
NAME_FIXES = os.path.join(SOURCES, "goa_name_fixes.csv")                        # hand-kept: a source's place name → the body it lies in, with the basis
INLAND_TALUKAS = {"Bicholim", "Sattari", "Ponda", "Dharbandora", "Sanguem"}       # no coastline: a map fact
TALUKA_SPELLINGS = {"marmagoa": "Mormugao", "marmugao": "Mormugao", "pedne": "Pernem", "satari": "Sattari", "vailpoi": "Sattari"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


TALUKA_WORDS = {"goa", "tiswadi", "bardez", "sattari", "satari", "pedne", "pernem", "bicholim", "quepem", "sanguem", "canacona", "salcete", "ponda", "mormugao", "dharbandora"}


def _parts(s: str) -> list[str]:
    """The words of a place name worth matching: 'Sao Lourence(Agassaim)' → ['saolourence', 'agassaim']; a taluka
    name is dropped when it only says where the place is ('Bhati-Sanguem'), kept when it is the place ('Sanguem')."""
    s = re.sub(r"\b(?:mci|m\.? ?cl|m\.? ?corp\.?|og|ct)\b", " ", s, flags=re.I)
    parts = [p for p in (_norm(x) for x in re.split(r"[-,/&()+]|\s+e\s+|\s+and\s+|\s+", s)) if len(p) > 2]
    kept = [p for p in parts if p not in TALUKA_WORDS]
    return kept or [p for p in parts if p != "goa"]


def match_body(name: str, taluka: str, divs: list[dict], fixes: dict, cross: bool = True, threshold: float = 0.86) -> tuple[dict | None, str]:
    """A source's place name → the body it belongs to: a hand-kept fix first, then a body in the source's taluka
    whose name, alias or village has the same word, then a close spelling (difflib ≥ threshold) there, then, when
    `cross` allows (whole-body names, which sources file under the taluka they think right), the same word anywhere
    in the state, as long as only one body has it. Returns (body, how)."""
    import difflib
    t = TALUKA_SPELLINGS.get(_norm(taluka), taluka)
    key = (_norm(name), _norm(t))
    if key in fixes:
        return next((d for d in divs if d["id"] == fixes[key]), None), "fix"
    q = _parts(name)
    if not q:
        return None, "none"
    whole = _norm(name)

    def hits(pool):
        full, part, best, score = [], [], None, 0.0
        for d in pool:
            names = [d["name"]] + d.get("aliases", []) + [v["name"] for v in d["villages"]]
            for nm in names:
                if _norm(re.sub(r"\(.*?\)", "", nm)) == whole:
                    full.append(d)
                for p in _parts(nm):
                    for qp in q:
                        if p == qp:
                            part.append(d)
                        else:
                            r = difflib.SequenceMatcher(None, p, qp).ratio()
                            if r > score:
                                best, score = d, r
        return full, part, best, score

    full, part, best, score = hits([d for d in divs if _norm(d["taluka"]) == _norm(t)])
    if full:
        return full[0], "exact"
    if part:
        return part[0], "exact"
    if score >= threshold:
        return best, f"fuzzy {score:.2f}"
    if cross:
        full, part, _, _ = hits(divs)
        ids = {d["id"] for d in full} or {d["id"] for d in part}
        if len(ids) == 1:
            return (full or part)[0], "exact, other taluka"
        if len(ids) > 1:
            return None, "ambiguous: " + ", ".join(sorted(ids))
    return None, "none"


def derive_tables(data: dict) -> dict:
    """Flags and programme figures from the three published tables, matched to bodies by name. Coastline: a body with
    a CMFRI marine fishing village is coastal; everything in an inland taluka is not; the rest stays unknown.
    Each match records how it was made, and every unmatched row is listed so a person can add a fix."""
    import csv
    divs = data["divisions"]
    fixes = {}
    if os.path.exists(NAME_FIXES):
        by_id = {d["id"]: d for d in divs}
        for r in csv.DictReader(open(NAME_FIXES, encoding="utf-8")):
            fixes[(_norm(r["name"]), _norm(TALUKA_SPELLINGS.get(_norm(r["taluka"]), r["taluka"])))] = r["body_id"]
            # a hand-kept name is also a name people use: make it an alias, so the form and the typeahead know it
            d = by_id.get(r["body_id"])
            nm = re.sub(r"\s*\(.*?\)", "", r["name"]).strip()
            if d is not None and nm and _norm(nm) != _norm(d["name"]) and nm not in d.setdefault("aliases", []):
                d["aliases"].append(nm)
    report: dict[str, list] = {"fishing_unmatched": [], "dairy_unmatched": [], "st_unmatched": [], "st_disagree": []}
    for d in divs:
        if d["taluka"] in INLAND_TALUKAS:
            d["flags"]["coastal"] = False
            d["flags"]["coastal_basis"] = "inland taluka"
    if os.path.exists(FISHING):
        for r in csv.DictReader(open(FISHING, encoding="utf-8")):
            d, how = match_body(r["village"], r["taluk"], divs, fixes, cross=False, threshold=0.9)
            if not d:
                report["fishing_unmatched"].append(f"{r['village']} ({r['taluk']})"); continue
            d["flags"]["coastal"] = True
            d["flags"]["coastal_basis"] = f"marine fishing village {r['village']} (CMFRI 2016, matched by {how})"
            d["programmes"]["fisher_households"] = d["programmes"].get("fisher_households", 0) + int(r["fishermen_families"] or 0)
            d["programmes"]["fisher_bpl_households"] = d["programmes"].get("fisher_bpl_households", 0) + int(r["bpl_families"] or 0)
            d["programmes"].setdefault("fishing_villages", []).append(r["village"])
            src = "CMFRI Marine Fisheries Census 2016, Goa (fisher families per fishing village)"
            if src not in d["sources"]: d["sources"].append(src)
    if os.path.exists(DAIRY):
        for r in csv.DictReader(open(DAIRY, encoding="utf-8")):
            d, how = match_body(r["place_from_name"], r["block"], divs, fixes, cross=False, threshold=0.9)
            if not d:
                report["dairy_unmatched"].append(f"{r['place_from_name']} ({r['block']})"); continue
            d["programmes"]["dairy_societies"] = d["programmes"].get("dairy_societies", 0) + 1
            src = "Registrar of Cooperative Societies, dairy societies by place (as on 31 May 2008; hamlets that match no village are left out)"
            if src not in d["sources"]: d["sources"].append(src)
    if os.path.exists(ST_TABLE):
        for r in csv.DictReader(open(ST_TABLE, encoding="utf-8")):
            d, how = match_body(r["panchayat_or_municipality"], r["taluka"], divs, fixes)
            if not d:
                report["st_unmatched"].append(f"{r['panchayat_or_municipality']} ({r['taluka']})"); continue
            st, tot = int(float(r["st_population"] or 0)), int(float(r["total_population"] or 0))
            c = d["census"]
            if c["confidence"] in ("none", "partial") and tot:
                # the directorate's table assigns the Panaji outgrowth villages to their panchayats; use it where the census file could not
                c["population"], c["st"], c["confidence"] = tot, st, "directorate"
                c["note"] = "population and ST count from the Directorate of Tribal Welfare's panchayat-wise table (Census 2011 basis); other figures unavailable"
                d["sources"].append("Directorate of Tribal Welfare, ST population panchayat-wise (tribalwelfare.goa.gov.in, Census 2011 basis)")
            elif c.get("st") is not None and tot and abs(c["st"] - st) > max(50, 0.2 * max(c["st"], st)):
                report["st_disagree"].append(f"{d['name']}: census {c['st']} vs directorate {st}")
    data["derivation"] = {k: v for k, v in report.items()}
    return data


def merge_sources(data: dict) -> dict:
    """Optional files in data/sources/ add to the census build: official names and ward counts, coastline and
    tidal-water flags, per-place programme figures. Each row names its source, which the page then shows."""
    import csv
    divs = data["divisions"]
    unmatched: dict[str, list[str]] = {}
    if os.path.exists(NAMES):
        for r in csv.DictReader(open(NAMES, encoding="utf-8")):
            d = _find(divs, r)
            if not d:
                unmatched.setdefault("names", []).append(r.get("name") or r.get("lgd_code")); continue
            if r.get("name"):
                if d["name"] != r["name"].strip():
                    d.setdefault("aliases", []).append(d["name"]); d["name"] = r["name"].strip()
            if r.get("wards"):
                d["wards"] = int(r["wards"])
            if r.get("aliases"):
                d.setdefault("aliases", []).extend(a.strip() for a in r["aliases"].split("|") if a.strip())
            if r.get("source") and r["source"] not in d["sources"]:
                d["sources"].append(r["source"])
    if os.path.exists(FLAGS):
        for r in csv.DictReader(open(FLAGS, encoding="utf-8")):
            d = _find(divs, r)
            if not d:
                unmatched.setdefault("flags", []).append(r.get("name") or r.get("id") or r.get("lgd_code")); continue
            for k in ("coastal", "estuarine"):
                if r.get(k, "") != "":
                    d["flags"][k] = _bool(r[k])
            if r.get("source") and r["source"] not in d["sources"]:
                d["sources"].append(r["source"])
    if os.path.exists(PROGRAMMES_CSV):
        for r in csv.DictReader(open(PROGRAMMES_CSV, encoding="utf-8")):
            d = _find(divs, r)
            if not d:
                unmatched.setdefault("programmes", []).append(r.get("name") or r.get("id") or r.get("lgd_code")); continue
            key = r["key"].strip()
            val = r["value"].strip()
            d["programmes"][key] = _bool(val) if key == "weaker_panchayat" else int(float(val))
            src = (r.get("source") or "") + (f" ({r['as_of']})" if r.get("as_of") else "")
            if src and src not in d["sources"]:
                d["sources"].append(src)
    # ids may have changed with the names: recompute, keeping them unique
    names = Counter(slug(d["name"]) for d in divs)
    for d in divs:
        d["id"] = slug(d["name"]) if names[slug(d["name"])] == 1 else slug(f"{d['name']}-{d['taluka']}")
    data["unmatched"] = unmatched
    return data


def save(data: dict) -> None:
    with open(DIVISIONS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    load.cache_clear(); statuses_of.cache_clear()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true", help="rebuild data/goa_divisions.json from data/sources/")
    ap.add_argument("--build-map", action="store_true", help="rebuild data/goa_map.json from the village polygons in data/sources/ (needs shapely)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--kind")
    ap.add_argument("--twin")
    a = ap.parse_args()
    if a.build:
        data = merge_sources(derive_tables(build_divisions()))
        save(data)
        for k, v in data["derivation"].items():
            print(f"{k}: {len(v)}" + (f" · {v[:12]}" if v else ""))
        kinds = Counter(d["kind"] for d in data["divisions"])
        print(f"wrote {DIVISIONS}: {dict(kinds)}; population {sum(d['census']['population'] or 0 for d in data['divisions']):,}; "
              f"{sum(1 for d in data['divisions'] if d['census']['confidence'] == 'none')} bodies without census figures")
        flagged = sum(1 for d in data["divisions"] if d["flags"].get("coastal") is not None)
        withprog = sum(1 for d in data["divisions"] if d["programmes"])
        print(f"flags on {flagged} places, programme figures on {withprog}; unmatched rows: { {k: len(v) for k, v in data['unmatched'].items()} or 'none'}")
        for k, v in data["unmatched"].items():
            print(f"  {k}: {v[:20]}")
    if a.build_map:
        from . import twinmap
        m = twinmap.build_map()
        twinmap.save_map(m)
        levels = Counter(f["level"] for f in m["features"])
        pts = sum(len(r) for f in m["features"] for r in f["rings"])
        print(f"wrote {twinmap.MAP_FILE}: {dict(levels)}, {pts:,} points, {os.path.getsize(twinmap.MAP_FILE) // 1024} KB")
        print(f"bodies without a shape: {m['no_shape']}")
        print(f"polygons placed nowhere: {m['unplaced_polygons']}")
    if a.list:
        for d in divisions(a.kind):
            p = profile(d)
            st = f"{p['st_share']:.0%}" if p["st_share"] is not None else "?"
            print(f"{d['id']:40} {KINDS.get(d['kind'], d['kind']):22} {d.get('taluka') or '':14} pop {_n(p['population']):>8} ST {st:>5} coastal {p['coastal']}")
    if a.twin:
        d = get(a.twin)
        if not d:
            raise SystemExit(f"no place {a.twin!r}")
        t = twin(d)
        print(json.dumps(t["profile"], indent=1, ensure_ascii=False))
        print(t["summary"])
        for v in t["verdicts"]:
            if v["status"] != "unlikely":
                s = S.by_id(v["scheme_id"])
                print(f"[{v['status']:9}] {s['name'][:70]:70} · {'; '.join(v['why'])[:120]}")


if __name__ == "__main__":
    main()
