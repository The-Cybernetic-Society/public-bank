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
    summary_of.cache_clear()


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
def summary_of(div_id: str) -> dict | None:
    """The counts for one place, cached: the data is static for the life of the process."""
    d = get(div_id)
    if not d:
        return None
    p = profile(d)
    return summarize([verdict(d, s, p) for s in S.schemes()])


def section(s: dict) -> str:
    """Which part of the twin page a scheme belongs to: the body itself, groups here, or people here."""
    if s["bodies"] and not (set(s["people"]) & INDIVIDUAL_CLASSES):
        return "body"
    if s["people"] and not (set(s["people"]) & INDIVIDUAL_CLASSES):
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


def title(s: str) -> str:
    """'KERI TERECOL' → 'Keri Terecol'; keeps 'de', 'do', 'e' small inside Portuguese-era names."""
    small = {"de", "do", "da", "e", "dos", "das"}
    out = []
    for w in re.split(r"(\s+|-)", s.strip()):
        if w.strip() in ("", "-"): out.append(w); continue
        out.append(w.lower() if w.lower() in small and out else w.capitalize())
    return "".join(out)


def build_divisions(pca_path: str = PCA, map_path: str = VILLAGE_MAP) -> dict:
    """Village panchayats and towns with Census 2011 figures. A census village that LGD places in two local bodies
    is split half and half between them (six cases in Goa, all on the edge of a census town). Flags and programme
    figures start empty; `merge_*` steps add them from other sources."""
    import csv
    pca = {r["village_town_code"]: r for r in csv.DictReader(open(pca_path, encoding="utf-8"))}
    links = list(csv.DictReader(open(map_path, encoding="utf-8")))
    per_code = Counter(r["census_village_code"] for r in links)
    bodies: dict[str, dict] = {}
    for r in links:
        if r["local_body_type"] != "Village Panchayat":
            continue   # a village inside a municipality counts in the town's own census row
        code, b = r["census_village_code"], r["local_body_code"]
        if code not in pca:
            continue
        d = bodies.setdefault(b, {"id": None, "kind": "village_panchayat", "name": title(r["local_body_name"]), "lgd_code": int(b),
                                  "taluka": TALUKA.get(r["lgd_subdistrict_name"], r["lgd_subdistrict_name"]), "district": pca[code]["district_name"],
                                  "villages": [], "census": {"year": 2011, **{k: 0 for k in CENSUS_FIELDS}, "confidence": "mapped"},
                                  "flags": {"coastal": None, "estuarine": None}, "programmes": {}, "sources": []})
        share = 1 / per_code[code]
        d["villages"].append({"code": int(code), "name": pca[code]["name"].strip(), "share": share})
        for k, col in CENSUS_FIELDS.items():
            d["census"][k] += int(pca[code][col]) * share
    out = []
    for d in bodies.values():
        for k in CENSUS_FIELDS:
            d["census"][k] = int(round(d["census"][k]))
        d["sources"] = ["Local Government Directory (village to local body, 2023 snapshot)"]
        out.append(d)
    # the fourteen statutory towns are their own census rows, plus any village LGD now places inside one
    towns = {}
    for code, r in pca.items():
        if r["level"] == "TOWN" and ("(M Cl)" in r["name"] or "(M Corp" in r["name"]):
            name = re.sub(r"\s*\(.*$", "", r["name"]).strip()
            towns[slug(name)] = {"id": None, "kind": "municipal_corporation" if "Corp" in r["name"] else "municipal_council", "name": name, "lgd_code": None,
                                 "taluka": TALUKA.get(r["subdistrict_name"], r["subdistrict_name"]), "district": r["district_name"],
                                 "villages": [{"code": int(code), "name": r["name"].strip(), "share": 1}],
                                 "census": {"year": 2011, **{k: int(r[col]) for k, col in CENSUS_FIELDS.items()}, "confidence": "mapped"},
                                 "flags": {"coastal": None, "estuarine": None}, "programmes": {}, "sources": []}
    for r in links:
        if r["local_body_type"].startswith("Municipal") and r["census_village_code"] in pca and r["level"] == "VILLAGE":
            t = next((t for k, t in towns.items() if k in slug(r["local_body_name"]) or slug(r["local_body_name"]) in k), None)
            if t:
                share = 1 / per_code[r["census_village_code"]]
                t["villages"].append({"code": int(r["census_village_code"]), "name": pca[r["census_village_code"]]["name"].strip(), "share": share})
                for k, col in CENSUS_FIELDS.items():
                    t["census"][k] += int(round(int(pca[r["census_village_code"]][col]) * share))
    out.extend(towns.values())
    # ids: the name, with the taluka when two bodies share one (Navelim, Querim, Sanvordem)
    names = Counter(slug(d["name"]) for d in out)
    for d in out:
        d["id"] = slug(d["name"]) if names[slug(d["name"])] == 1 else slug(f"{d['name']}-{d['taluka']}")
    out.sort(key=lambda d: (d["district"], d["taluka"], d["name"]))
    return {"compiled": "2026-11-02", "divisions": out,
            "sources": ["Census of India 2011, Primary Census Abstract, village and town level, Goa (censusindia.gov.in NADA catalogue 6776, 6777)",
                        "Local Government Directory, village to local body mapping for Goa (lgdirectory.gov.in, via the India Data Portal snapshot of 2023-10-20)"]}


def save(data: dict) -> None:
    with open(DIVISIONS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    load.cache_clear(); summary_of.cache_clear()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true", help="rebuild data/goa_divisions.json from data/sources/")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--kind")
    ap.add_argument("--twin")
    a = ap.parse_args()
    if a.build:
        data = build_divisions()
        save(data)
        kinds = Counter(d["kind"] for d in data["divisions"])
        print(f"wrote {DIVISIONS}: {dict(kinds)}; population {sum(d['census']['population'] for d in data['divisions']):,}")
    if a.list:
        for d in divisions(a.kind):
            p = profile(d)
            print(f"{d['id']:40} {KINDS.get(d['kind'], d['kind']):22} {d.get('taluka') or '':14} pop {_n(p['population']):>8} ST {p['st_share'] if p['st_share'] is None else f'{p['st_share']:.0%}':>5} coastal {p['coastal']}")
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
