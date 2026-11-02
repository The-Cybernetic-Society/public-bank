"""
The scheme catalogue: what the Goa government offers, and what a place must be or have to draw on each scheme.

Source: the "Goa schemes — first pass" workbook (goa.gov.in scheme PDFs read by machine, the older GIPARD
handbook, the 2026-27 Demands for Grants). It lives outside the repo; `--import` turns it into
`publicbank/data/goa_schemes.json`, which is what the service reads. The workbook's own caveat stands: nothing
in it has been verified by a department, and this module adds a second layer of machine reading on top
(`REQUIREMENTS`: regular expressions over the scheme text that say what a scheme asks of a place). Every verdict
the twin gives names the rule it came from, so a person can disagree with the rule rather than the answer.

    python -m publicbank.schemes --import "/path/to/Goa_schemes_first_pass.xlsx"   # rebuild the JSON
    python -m publicbank.schemes --stats                                            # what the catalogue holds
"""
from __future__ import annotations

import argparse
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from functools import lru_cache

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CATALOGUE = os.path.join(DATA_DIR, "goa_schemes.json")

# who a scheme is for, as the workbook classified it (its "Who can apply" column)
APPLICANTS = ["Individual", "Household", "Farmer", "Fisher", "Livestock owner", "SHG / group", "Business / entrepreneur",
              "Cooperative society", "NGO / institution", "Village panchayat", "Municipality", "Zilla panchayat",
              "Government department", "Student", "Woman", "Senior citizen", "Person with disability", "SC/ST"]
BODIES = {"Village panchayat": "village_panchayat", "Municipality": "municipal", "Zilla panchayat": "zilla_panchayat"}

# What a scheme needs of a place. (key, pattern over the scheme's text, applicant classes that imply it, label,
# the step a place could take to meet it). A requirement with no step is a fact about the place (coastal, tribal)
# that no step changes; one with a step is something people there could set up (a group, a society, a committee).
REQUIREMENTS = [
    ("st", r"scheduled tribe|\bSTs?\b|tribal|adivasi|forest.?dwell", (), "Scheduled Tribe households", None),
    ("sc", r"scheduled caste|\bSCs?\b", (), "Scheduled Caste households", None),
    ("tribal_area", r"areas? inhabited by trib|tribal(?:-| )(?:area|village|habitation|population|dominated|inhabited)|substantial tribal|remote tribal", (), "a tribal-inhabited area", None),
    ("fishers", r"fisher|fishing|marine|fish vendor|fish farm|fish market|aquacultur|canoe|trawler|\bboats?\b|\bnets?\b", ("Fisher",), "fishing households", None),
    ("brackish", r"brackish|estuar|shrimp|khazan|saline", (), "brackish or estuarine water", None),
    ("farmers", r"farmer|cultivat|crop|horticult|agricultur|paddy|coconut|cashew|arecanut|kharif|rabi|orchard|plantation|kisan|krishi", ("Farmer",), "farming households", None),
    ("livestock", r"cattle|milch|dairy|poultry|goat|\bpigs?\b|livestock|animal husbandry|fodder|veterinar|kamdhenu|cow", ("Livestock owner",), "livestock keepers", None),
    ("rural", r"rural (?:household|area|famil|poor|communit|infrastructure|road|local bod)|in rural|gramin|grameen|village panchayat|gram sabha|\bwadas?\b", (), "a rural area", None),
    ("urban", r"urban (?:local|poor|area|household|development)|municipal (?:area|council|corporation)|shehari|\bULBs?\b|\btowns?\b", (), "an urban local body", None),
    ("remote", r"remote|inaccessible|un-?electrified|not feasible", (), "a remote or unserved hamlet", None),
    ("bpl", r"\bBPL\b|below poverty|antyodaya|\bAAY\b|priority household|\bSECC\b|destitute", (), "households on the BPL or SECC lists", None),
    ("weaker_panchayat", r"weaker (?:village )?panchayat|economically weaker panchayat", (), "a panchayat on the weaker-panchayat list", None),
    ("shg", r"self.help|\bSHGs?\b|mahila mandal|youth club|sports club|sanskrutik mandal|group of (?:minimum|at least|min\.? )?\d|(?:minimum|min\.?) (?:of )?\d+ (?:unrelated )?members|registered (?:community )?group", ("SHG / group",), "a registered group",
     "form a group (a self-help group, mahila mandal or youth club) and register it"),
    ("coop", r"co-?operative societ|registered societ|fishermen societ|farmers?'? (?:co-?operative|societ)|milk (?:producers'?|co-?operative)|dairy societ|primary agricultural credit", ("Cooperative society",), "a registered cooperative society",
     "register a cooperative society with the Registrar of Cooperative Societies"),
    ("fra", r"forest rights|\bFRA\b|community forest", (), "a Forest Rights Act committee", "constitute a Forest Rights committee through the Gram Sabha"),
    ("ngo", r"\bNGOs?\b|non.?government(?:al)? (?:organi[sz]ation|institution)|voluntary organi[sz]ation|registered (?:organi[sz]ation|institution|trust)", ("NGO / institution",), "a registered organisation",
     "a registered society or trust in the place applies, or the panchayat partners with one"),
    ("women", r"\bwomen\b|\bwoman\b|widow|mothers?\b|\bgirls?\b|pregnant|lactating|mahila|ladki|laadli", ("Woman",), "women", None),
    ("seniors", r"senior citizen|aged 60|60 years|old age|elderly", ("Senior citizen",), "senior citizens", None),
    ("pwd", r"disabilit|divyang|handicap", ("Person with disability",), "persons with disabilities", None),
    ("students", r"\bstudents?\b|\bschools?\b|\bcolleges?\b|scholarship|\bpupils?\b|higher education", ("Student",), "students", None),
    ("ex_servicemen", r"ex-?servicem|sainik|armed forces|defence personnel|war widow|gallantry|\bjawans?\b", (), "ex-servicemen and their families", None),
    ("labour", r"labour welfare fund|registered (?:workers?|labour)|construction workers?|unorganised|unorganized|\bwage earners?\b", (), "workers registered with the Labour Welfare Board", None),
    ("business", r"entrepreneur|enterprise|industr|start-?up|\bunits?\b|\bbusiness|\bMSME|manufactur|\bshops?\b", ("Business / entrepreneur",), "businesses", None),
    ("artisans", r"artisan|handicraft|weaver|\bcoir\b|khadi|traditional (?:occupation|product|craft)|potter|carpenter|goldsmith|bamboo", (), "artisans in traditional trades", None),
    ("land", r"owning (?:agricultural )?land|own(?:s|ing)? .{0,20}land|land(?:-| )holding|hectares?|\bacres?\b|irrigated land|\bkhazan\b", (), "households holding land", None),
    ("housing", r"\bhouses?\b|housing|dwelling|katcha|pucca|\bshelter", (), "households needing a house or repairs", None),
    ("institution", r"librar|gymnasi|community hall|anganwadi|shelter home|child committee|crematori|fish market|panchayat ghar", (), "a facility the body runs or will build", None),
]
REQ = {k: (re.compile(p, re.I), imp, label, step) for k, p, imp, label, step in REQUIREMENTS}
INCOME = re.compile(r"income (?:limit|ceiling|not exceed|less than|below|up to)|annual (?:family )?income", re.I)
RESIDENT = re.compile(r"resident of goa for (?:a period of |a minimum of |not less than )?(?:at ?least |minimum |last |preceding )?(?:the )?(?:(?:last|preceding) )?(\d+|fifteen|ten|five|three)", re.I)
WORDS = {"fifteen": 15, "ten": 10, "five": 5, "three": 3}


# ----------------------------------------------------------------------------
# the workbook
# ----------------------------------------------------------------------------
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def read_workbook(path: str) -> dict[str, list[dict]]:
    """Every sheet as a list of rows, each row {column letter: value}, hyperlinks resolved into `{col}_url`.
    Standard library only, so the import runs where openpyxl is not installed."""
    z = zipfile.ZipFile(path)
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    strings = ["".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS)] if "xl/sharedStrings.xml" in z.namelist() else []
    out: dict[str, list[dict]] = {}
    for s in wb.find("m:sheets", NS):
        target = rels[s.get("{%s}id" % NS["r"])]
        sheet_path = "xl/" + target if not target.startswith("/") else target[1:]
        root = ET.fromstring(z.read(sheet_path))
        links: dict[str, str] = {}
        rel_path = "xl/worksheets/_rels/" + sheet_path.split("/")[-1] + ".rels"
        if rel_path in z.namelist():
            rid = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read(rel_path))}
            hl = root.find("m:hyperlinks", NS)
            for h in (hl if hl is not None else []):
                links[h.get("ref")] = rid.get(h.get("{%s}id" % NS["r"]), "")
        rows = []
        for row in root.iter("{%s}row" % NS["m"]):
            cells: dict[str, str] = {}
            for c in row.findall("m:c", NS):
                ref = c.get("r"); col = re.match(r"[A-Z]+", ref).group(0)
                v = c.find("m:v", NS); t = c.get("t")
                if t == "s" and v is not None: val = strings[int(v.text)]
                elif t == "inlineStr": val = "".join(x.text or "" for x in c.iter("{%s}t" % NS["m"]))
                else: val = v.text if v is not None else ""
                cells[col] = (val or "").strip()
                if ref in links: cells[col + "_url"] = links[ref]
            if any(v for k, v in cells.items() if not k.endswith("_url")):
                rows.append(cells)
        out[s.get("name")] = rows
    return out


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s[:80].rstrip("-") or "scheme"


AMEND_WORDS = re.compile(r"\b(?:amendment|amendments|amended|amending|addendum|corrigendum|modification|modified|revised|revision|extension|validity|of|to|the|and|for|under|scheme|schemes|yojana|rules|guidelines|notification|clause|sub|dated|no|ref|state|sector|goa|\d+(?:st|nd|rd|th)?|[ivx]+)\b")


def core_name(name: str) -> str:
    """What is left of a scheme or amendment name once the amendment words are gone: 'Amendment to Atal Asra
    Yojana (clause 6 conditions of sanction)' → 'atal asra'. The part the two names share."""
    n = norm(re.sub(r"\(.*?\)|\[.*?\]|[-–—:].*$", " ", name))
    return re.sub(r"\s+", " ", AMEND_WORDS.sub(" ", n)).strip()


def parent_of(name: str, by_norm: dict[str, dict], department: str = "") -> dict | None:
    """The scheme an amendment changes, by name: the catalogue scheme whose name holds the amendment's core, or
    whose own core the amendment holds, preferring the same department and the longer shared core."""
    core = core_name(name)
    if len(core.split()) < 2 and len(core) < 10:
        return None
    best, score = None, (0, 0)
    for k, e in by_norm.items():
        if e.get("kind") == "Amendment":
            continue
        kc = core_name(e["name"])
        shared = core if core in k else (kc if kc and len(kc.split()) >= 2 and kc in core else "")
        if not shared:
            continue
        sc = (len(shared), 1 if e.get("department") == department else 0)
        if sc > score:
            best, score = e, sc
    return best


def _int(s: str) -> int | None:
    try:
        return int(float(s)) if s not in ("", None) else None
    except ValueError:
        return None


def _applicants(s: str) -> list[str]:
    return [a for a in APPLICANTS if a in (s or "")]


def _entry_from_portal(r: dict) -> dict:
    return {"name": r.get("A", ""), "kind": r.get("B", ""), "department": r.get("D", ""), "applicants": _applicants(r.get("E", "")),
            "target": r.get("F", ""), "eligibility": r.get("G", ""), "benefit": r.get("H", ""), "max_rupees": _int(r.get("I", "")),
            "how_to_apply": r.get("J", ""), "local_body_role": r.get("K", "") or "None stated", "funding": r.get("L", ""),
            "valid_period": r.get("M", ""), "notified_on": r.get("N", ""), "later_amendments": r.get("P", ""),
            "detail": r.get("Q", ""), "notes": r.get("R", ""), "source_url": r.get("S_url", ""), "sources": ["goa.gov.in"],
            "amendments": []}


def _entry_from_handbook(r: dict) -> dict:
    return {"name": r.get("A", ""), "kind": "Scheme rules", "department": r.get("B", ""), "applicants": _applicants(r.get("C", "")),
            "target": r.get("D", ""), "eligibility": r.get("E", ""), "benefit": r.get("F", ""), "max_rupees": _int(r.get("G", "")),
            "how_to_apply": r.get("H", ""), "local_body_role": r.get("I", "") or "None stated", "funding": r.get("J", ""),
            "valid_period": "", "notified_on": "", "later_amendments": "", "detail": "Handbook", "notes": r.get("L", ""),
            "source_url": "https://gipard.goa.gov.in/gipard/storage/2023/04/Hand-Book-On-Govt-Schemes.pdf",
            "sources": ["GIPARD handbook (~2020-21)"], "handbook_pages": r.get("K", ""), "amendments": []}


def text_of(e: dict) -> str:
    return " ".join(str(e.get(k, "")) for k in ("name", "target", "eligibility", "benefit", "how_to_apply", "local_body_role"))


def tag(e: dict) -> dict:
    """What the scheme asks of a place: requirement keys, the body that applies if one does, informational bits."""
    t = text_of(e)
    who = set(e.get("applicants", []))
    needs = [k for k, (rx, implied, _, _) in REQ.items() if rx.search(t) or (who & set(implied))]
    # the SC/ST class without a word for either side means either
    if "SC/ST" in who and "st" not in needs and "sc" not in needs:
        needs.append("sc_or_st")
    # a scheme for an individual class says nothing about women/students/etc. unless the text does; 'women' from a
    # stray 'girls' in a sainik scheme is still a fact about who it is for, so keep it, but the body rule wins below
    bodies = [BODIES[a] for a in e.get("applicants", []) if a in BODIES]
    people = [a for a in e.get("applicants", []) if a not in BODIES and a != "Government department"]
    m = RESIDENT.search(t)
    years = (int(m.group(1)) if m and m.group(1).isdigit() else WORDS.get(m.group(1).lower())) if m else None
    e["needs"] = needs
    e["bodies"] = bodies
    e["people"] = people
    e["income_ceiling"] = bool(INCOME.search(t))
    e["resident_years"] = years
    if bodies and not people:
        # a scheme a body applies for: its scope is which body, not the words in its text
        e["scope"] = "rural" if set(bodies) <= {"village_panchayat", "zilla_panchayat"} else "urban" if set(bodies) == {"municipal"} else "any"
    else:
        e["scope"] = "urban" if ("urban" in needs and "rural" not in needs) else "rural" if ("rural" in needs and "urban" not in needs) else "any"
    return e


def build_catalogue(path: str) -> dict:
    wb = read_workbook(path)
    portal = wb["Current schemes (portal)"][1:]
    handbook = wb["Schemes (detailed)"][1:]
    grants_rows = wb["Local body grants 2026-27"]

    by_norm: dict[str, dict] = {}
    order: list[str] = []
    amendments = []
    for r in portal:
        e = _entry_from_portal(r)
        if not e["name"]:
            continue
        if e["kind"] == "Amendment":
            amendments.append((r.get("C", ""), e)); continue
        if e["kind"] == "Notification" and not (e["eligibility"] or e["benefit"]):
            continue   # a notification with no scheme text in it
        k = norm(e["name"])
        if k in by_norm:
            cur = by_norm[k]
            if len(e["eligibility"]) + len(e["benefit"]) > len(cur["eligibility"]) + len(cur["benefit"]):
                e["sources"] = cur["sources"]; e["also_url"] = cur.get("source_url", ""); by_norm[k] = e
            else:
                cur["also_url"] = e.get("source_url", "")
            continue
        by_norm[k] = e; order.append(k)
    # amendments hang off the scheme they change: by the workbook's 'Amends' column, else by the longest scheme
    # name contained in the amendment's name; an orphan stays as its own entry so nothing is lost
    for amends, e in amendments:
        parent = by_norm.get(norm(amends)) if amends else None
        if parent is None and amends:
            parent = parent_of(amends, by_norm, e["department"])
        if parent is None:
            parent = parent_of(e["name"], by_norm, e["department"])
        item = {"name": e["name"], "notified_on": e["notified_on"], "benefit": e["benefit"], "notes": e["notes"], "source_url": e["source_url"]}
        if parent is not None:
            parent["amendments"].append(item)
        else:
            k = norm(e["name"])
            if k not in by_norm:
                by_norm[k] = e; order.append(k)
    # the handbook: fills gaps in a portal scheme of the same name, else a scheme of its own
    for r in handbook:
        e = _entry_from_handbook(r)
        if not e["name"]:
            continue
        k = norm(e["name"])
        hit = by_norm.get(k)
        if hit is None:
            cands = [kk for kk in by_norm if len(kk) > 12 and (kk in k or k in kk)]
            hit = by_norm[max(cands, key=len)] if cands else None
        if hit is not None:
            hit["sources"].append("GIPARD handbook (~2020-21)")
            hit["handbook_pages"] = e.get("handbook_pages", "")
            for f in ("eligibility", "benefit", "how_to_apply", "funding", "target"):
                if not hit.get(f) and e.get(f): hit[f] = e[f]
            if hit.get("max_rupees") is None and e.get("max_rupees") is not None: hit["max_rupees"] = e["max_rupees"]
            if hit["local_body_role"] == "None stated" and e["local_body_role"] != "None stated": hit["local_body_role"] = e["local_body_role"]
            hit["applicants"] = sorted(set(hit["applicants"]) | set(e["applicants"]), key=APPLICANTS.index)
        else:
            by_norm[k] = e; order.append(k)
    schemes = []
    seen = set()
    for k in order:
        e = tag(by_norm[k])
        s = slug(e["name"]); n = 2
        while s in seen:
            s = f"{slug(e['name'])[:76]}-{n}"; n += 1
        seen.add(s); e["id"] = s
        schemes.append(e)

    grants, totals, in_table = [], [], False
    for r in grants_rows:
        a = r.get("A", "")
        if a == "Demand" and r.get("B") == "Budget line":
            in_table = True; continue
        if a == "Demand" and r.get("C") == "Revenue":
            continue
        if not in_table and a.startswith("Demand ") and r.get("E"):
            totals.append({"demand": a, "revenue_lakh": float(r.get("C") or 0), "capital_lakh": float(r.get("D") or 0), "total_lakh": float(r.get("E") or 0), "source_url": r.get("G_url", "")})
        elif in_table and a.startswith("Demand "):
            grants.append({"demand": a, "line": r.get("B", ""), "plan": r.get("C", ""), "recipient": r.get("D", ""),
                           "amount_lakh_unverified": float(r.get("E") or 0) if r.get("E") else None, "source_url": r.get("G_url", "")})
    return {"compiled": (wb["Read me"][1].get("B") if len(wb["Read me"]) > 1 else ""), "imported_from": os.path.basename(path),
            "caveat": "Machine-read from goa.gov.in PDFs and the GIPARD handbook; not verified by any department. Amounts and rules may have changed.",
            "schemes": schemes, "grants": grants, "grant_totals": totals}


# ----------------------------------------------------------------------------
# reading the catalogue
# ----------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load() -> dict:
    with open(CATALOGUE, encoding="utf-8") as f:
        return json.load(f)


def schemes() -> list[dict]:
    return load()["schemes"]


def by_id(sid: str) -> dict | None:
    return next((s for s in schemes() if s["id"] == sid), None)


def label(key: str) -> str:
    if key == "sc_or_st":
        return "Scheduled Caste or Scheduled Tribe households"
    return REQ[key][2] if key in REQ else key


def step(key: str) -> str | None:
    return REQ[key][3] if key in REQ else None


def stats(cat: dict | None = None) -> dict:
    cat = cat or load()
    ss = cat["schemes"]
    return {"schemes": len(ss), "with_amendments": sum(1 for s in ss if s["amendments"]), "amendments": sum(len(s["amendments"]) for s in ss),
            "for_bodies": sum(1 for s in ss if s["bodies"]), "departments": len({s["department"] for s in ss}),
            "needs": Counter(k for s in ss for k in s["needs"]).most_common(), "scope": Counter(s["scope"] for s in ss),
            "grants": len(cat["grants"]), "sources": Counter(src for s in ss for src in s["sources"])}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--import", dest="path", help="the xlsx to turn into publicbank/data/goa_schemes.json")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    if a.path:
        cat = build_catalogue(a.path)
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CATALOGUE, "w", encoding="utf-8") as f:
            json.dump(cat, f, ensure_ascii=False, indent=1)
        load.cache_clear()
        print(f"wrote {CATALOGUE}: {len(cat['schemes'])} schemes, {len(cat['grants'])} grant lines")
    if a.stats or a.path:
        for k, v in stats().items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
