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


# What a scheme asks of a BUSINESS (the business twin, businesses.py): regexes over the scheme text, like REQUIREMENTS
# for places. `sectors` narrows a scheme to a trade; the rest are entry tickets and who the owner must be.
BUSINESS_RULES = [
    ("udyam", r"udyam|udyog aadhaar|msme registration|registered (?:as an? |under )?(?:msme|micro|small)|EM-?II|IEM"),
    ("startup", r"registered (?:with |under )?(?:startup goa|sitpc|the cell|the start-?up)|startup (?:goa )?certif|DPIIT|recogni[sz]ed (?:as a )?start-?up|certified start-?up|start-?up certificate|start-?ups? (?:registered|certified|recogni[sz]ed)"),
    ("gst", r"\bGST\b|goods and services tax"),
    ("women_only", r"\bwomen(?:-| )(?:led|owned|entrepreneur)|\bwoman entrepreneur|promoted by (?:a )?wom[ae]n|at least 51% .{0,30}wom[ae]n"),
    ("sc_st_only", r"scheduled (?:caste|tribe)|\bSC/ST\b|\bSTs?\b|\bSCs?\b"),
    ("new_unit", r"\bnew units?\b|newly (?:set up|established)|commenc(?:ed|ing) (?:commercial )?production|within (?:one|1|two|2|three|3) years? of"),
    ("young", r"not (?:more|older) than (?:5|five|7|seven|8|eight|10|ten) years|within (?:5|five|7|seven|10|ten) years (?:of|from) (?:its )?incorporation|less than (?:10|ten) years"),
    ("small", r"\bmicro\b|\bsmall(?:er)? (?:business|enterprise|unit)|turnover (?:not exceeding|below|less than|up to)|smaller business units"),
    ("local", r"goan|resident of goa|domicile|born in goa|local (?:person|employ)"),
]
BUSINESS_RX = {k: re.compile(p, re.I) for k, p in BUSINESS_RULES}
SECTOR_RULES = [
    ("it", r"\bIT\b|information technology|software|electronics|\bITES\b|BPO|data cent"),
    ("logistics", r"logistic|warehous|cold chain|freight"),
    ("cashew", r"cashew"),
    ("tourism", r"touris|hotel|homestay|bed and breakfast|caravan|shack|guest ?house|travel"),
    ("artisan", r"artisan|handicraft|craftspeople|weaver|potter|coir|khadi|village industr|traditional (?:industr|craft|trade)"),
    ("fisheries", r"fisher|aquacultur|matsya|fish"),
    ("dairy", r"dairy|milch|cattle|poultry|piggery|goat|livestock|animal husbandry"),
    ("agri", r"farmer|agricultur|horticult|crop|kisan|krishi"),
    ("industry", r"manufactur|factory|industrial (?:estate|unit)|plant and machinery"),
    ("food", r"food process|bakery|restaurant|eatery|catering"),
]
SECTOR_RX = {k: re.compile(p, re.I) for k, p in SECTOR_RULES}
OFFER_KINDS = {"subsidy": "money", "incentive": "money", "grant": "money", "reimbursement": "money", "scheme rules": "money", "scheme": "money",
               "guidelines": "money", "loan": "credit", "service": "support", "programme": "support", "policy": "support", "notification": "support"}


def business_tag(e: dict) -> dict:
    """What the scheme asks of a business: `biz` = {needs: [...], sectors: [...], offer: money|credit|support}."""
    t = text_of(e) + " " + str(e.get("documents", ""))
    needs = [k for k, rx in BUSINESS_RX.items() if rx.search(t)]
    # 'sc_st_only' from a stray 'ST' in the text is noise unless the applicant class says so too
    if "sc_st_only" in needs and "SC/ST" not in e.get("applicants", []):
        needs.remove("sc_st_only")
    if "women_only" in needs and "Woman" not in e.get("applicants", []):
        needs.remove("women_only")
    tags = set(e.get("tags", []))
    # a scheme the portal lists for start-ups only asks for start-up recognition even when its text does not say so
    if tags and tags <= {"startup", "incubator"} and "startup" not in needs:
        needs.append("startup")
    sectors = [k for k, rx in SECTOR_RX.items() if rx.search(e.get("name", "") + " " + e.get("target", "") + " " + e.get("eligibility", "")[:400])]
    e["biz"] = {"needs": needs, "sectors": sectors, "offer": OFFER_KINDS.get((e.get("kind") or "").lower(), "money"),
                # a firm, not a society or a self-help group: those are the place twin's 'groups' world
                "for_business": "Business / entrepreneur" in e.get("applicants", []) or bool(tags)}
    return e


def tag(e: dict) -> dict:
    """What the scheme asks of a place: requirement keys, the body that applies if one does, informational bits."""
    business_tag(e)
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
            if "GIPARD handbook (~2020-21)" not in hit["sources"]:
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


# ----------------------------------------------------------------------------
# the business portals: msme.goa.gov.in, startup.goa.gov.in (schemes) and goaonline.gov.in (application channels)
# ----------------------------------------------------------------------------
PORTAL_CLASSES = {"startup": ("Business / entrepreneur", "startup"), "msme": ("Business / entrepreneur", "msme"), "artisan": ("Individual", "artisan"),
                  "incubator": ("NGO / institution", "incubator")}


def _portal_applicants(s: str) -> tuple[list[str], list[str]]:
    """The portal's 'who can apply' → the catalogue's classes, plus tags for the business-specific ones."""
    classes, tags = [], []
    for part in (s or "").split(","):
        p = part.strip()
        if not p:
            continue
        low = p.lower()
        if low in PORTAL_CLASSES:
            cls, tg = PORTAL_CLASSES[low]
            classes.append(cls); tags.append(tg)
        else:
            classes.extend(a for a in APPLICANTS if norm(a) == norm(p))
    return sorted(set(classes), key=APPLICANTS.index), sorted(set(tags))


def find_scheme(name: str, by_norm: dict[str, dict], department: str = "") -> dict | None:
    """A portal row's scheme in the catalogue, if the workbook already has it: the same name, or the same core name."""
    k = norm(name)
    if k in by_norm:
        return by_norm[k]
    core = core_name(name)
    if len(core) < 10:
        return None
    # the same core name only: 'Employment Subsidy Scheme for Cashew Industry' is not 'Employment Subsidy Scheme, 2017'
    return next((e for e in by_norm.values() if core_name(e["name"]) == core), None)


def merge_business(cat: dict, path: str, portal: str) -> dict:
    """Rows from a business portal's CSV: a scheme the workbook already lists gains the portal as a source, its
    page link and any blanks filled; a new one joins the catalogue tagged with the portal. Returns counts."""
    import csv
    by_norm = {norm(s["name"]): s for s in cat["schemes"]}
    seen_ids = {s["id"] for s in cat["schemes"]}
    added = matched = 0
    for r in csv.DictReader(open(path, encoding="utf-8")):
        name = (r.get("name") or "").strip()
        if not name:
            continue
        classes, tags = _portal_applicants(r.get("who_can_apply", ""))
        hit = find_scheme(name, by_norm, r.get("agency", ""))
        if hit is not None:
            matched += 1
            if portal not in hit["sources"]:
                hit["sources"].append(portal)
            hit.setdefault("portals", []).append({"portal": portal, "url": r.get("source_url", ""), "section": r.get("portal_section", ""), "name": name})
            for f in ("eligibility", "benefit", "how_to_apply", "target"):
                src = "target_group" if f == "target" else f
                if not hit.get(f) and r.get(src):
                    hit[f] = r[src].strip()
            if hit.get("max_rupees") is None and _int(r.get("max_rupees", "")) is not None:
                hit["max_rupees"] = _int(r["max_rupees"])
            hit["applicants"] = sorted(set(hit["applicants"]) | set(classes), key=APPLICANTS.index)
            hit["tags"] = sorted(set(hit.get("tags", [])) | set(tags))
            tag(hit)
            continue
        e = {"name": name, "kind": (r.get("kind") or "Scheme rules").strip(), "department": (r.get("agency") or "").strip(), "applicants": classes,
             "target": (r.get("target_group") or "").strip(), "eligibility": (r.get("eligibility") or "").strip(), "benefit": (r.get("benefit") or "").strip(),
             "max_rupees": _int(r.get("max_rupees", "")), "how_to_apply": (r.get("how_to_apply") or "").strip(), "local_body_role": "None stated", "funding": "State",
             "valid_period": (r.get("valid_period") or "").strip(), "notified_on": (r.get("notified_on") or "").strip(), "later_amendments": "",
             "detail": "Portal" if (r.get("eligibility") or r.get("benefit")) else "Name only", "notes": (r.get("notes") or "").strip(),
             "source_url": (r.get("source_url") or "").strip(), "sources": [portal], "amendments": [], "tags": tags,
             "portals": [{"portal": portal, "url": r.get("source_url", ""), "section": r.get("portal_section", ""), "name": name}],
             "documents": (r.get("documents") or "").strip(), "date_seen": (r.get("date_seen") or "").strip()}
        tag(e)
        s = slug(e["name"]); n = 2
        while s in seen_ids:
            s = f"{slug(e['name'])[:76]}-{n}"; n += 1
        e["id"] = s; seen_ids.add(s)
        cat["schemes"].append(e); by_norm[norm(name)] = e
        added += 1
    cat.setdefault("portals", {})[portal] = {"file": os.path.basename(path), "added": added, "matched": matched}
    return {"added": added, "matched": matched}


def merge_services(cat: dict, path: str) -> dict:
    """Goa Online's services: kept as a list, and a service that is the application channel for a named scheme
    puts its link on that scheme as `apply_online`."""
    import csv
    by_norm = {norm(s["name"]): s for s in cat["schemes"]}
    rows = []
    linked = 0
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if not (r.get("service_name") or "").strip():
            continue
        row = {k: (v or "").strip() for k, v in r.items()}
        rows.append(row)
        ln = row.get("linked_scheme_name", "")
        if ln and (hit := find_scheme(ln, by_norm, row.get("department", ""))) is not None:
            hit["apply_online"] = {"service": row["service_name"], "url": row.get("source_url", ""), "department": row.get("department", "")}
            row["linked_scheme_id"] = hit["id"]; linked += 1
    cat["services"] = rows
    cat.setdefault("portals", {})["goaonline.gov.in"] = {"file": os.path.basename(path), "services": len(rows), "linked": linked}
    return {"services": len(rows), "linked": linked}


# ----------------------------------------------------------------------------
# co-funding: the scheme that could carry part of a proposal
# ----------------------------------------------------------------------------
# (pattern over a proposal's title and text, the scheme's core name in the catalogue, what it pays, in words).
# A hand-kept table, deliberately short: each line is a scheme a Goan business could actually use for the kind of
# thing the Commons funds. The catalogue supplies the link, the department and the Goa Online channel.
CO_FUNDING = [
    (r"train|trainer|workshop|course|coach|upskill|skill", "incentives to industries for training prospective employees", "reimburses the training cost of new staff for a registered unit"),
    (r"audit|energy|solar|electric|water|green", "ramp goa: 50% subsidy for water and energy audits", "pays half of a water or energy audit, up to ₹50,000"),
    (r"common facilit|cluster|shared .{0,24}(?:equipment|machine|oven|cold|chiller|fridge|store|kitchen|workshop|printer|lift)|cold (?:room|store)", "micro small enterprises cluster development programme", "funds common facility centres for a cluster of units, through a registered SPV"),
    (r"certif|quality mark|\biso\b|hygiene rating", "zero defect zero effect", "subsidises ZED quality certification for registered MSMEs"),
    (r"exhibition|trade fair|stall at|buyer-?seller|trade show|expo\b", "procurement and marketing support", "reimburses stall and travel costs at fairs for registered MSMEs"),
    (r"(?:artisan|craft|tailor|embroider|potter|weav).{0,40}(?:tool|machine|kit|equipment)|toolkit", "pm vishwakarma", "a toolkit grant and a collateral-free loan for artisans in listed trades"),
    (r"local supplier|buy local|from goan|sourcing", "incentives to encourage purchases from local suppliers", "an incentive on purchases from local Goan suppliers"),
    (r"signage|signboard|\bbins\b|clean-?up|street ?light|public toilet|footpath|drain|crematori|community hall", "goa gram samrudhi yojana", "funds village assets through the panchayat: a proposal here could ask the panchayat to carry it"),
]
CO_FUNDING_RX = [(re.compile(p, re.I), core, share) for p, core, share in CO_FUNDING]


def co_funding(title: str, text: str = "") -> dict | None:
    """The first matching scheme for a proposal, as {id, name, department, share, url, apply_url}; None when nothing fits.
    Reads the title and the prose, not the quoted needs (a business called 'Tailors' is not a craft proposal)."""
    t = f"{title} {(text or '').split(chr(10) + '- ')[0]}"
    by_core = {}
    for s in schemes():
        by_core.setdefault(core_name(s["name"]), s)
        by_core.setdefault(norm(s["name"]), s)
    for rx, core, share in CO_FUNDING_RX:
        if not rx.search(t):
            continue
        s = by_core.get(core) or next((x for k, x in by_core.items() if k.startswith(core)), None)
        if s is None:
            continue
        return {"id": s["id"], "name": s["name"], "department": s["department"], "share": share, "url": s.get("source_url", ""),
                "apply_url": (s.get("apply_online") or {}).get("url", "")}
    return None


def attach_co_funding(con) -> int:
    """Put a co-funding scheme on every open proposal that has none yet; returns how many changed."""
    from . import db
    n = 0
    for p in db.rows(con.execute("SELECT id, title, text, scheme_json FROM proposals WHERE status IN ('draft','eligible')").fetchall()):
        if p.get("scheme_json"):
            continue
        s = co_funding(p["title"], p["text"] or "")
        if s:
            with db.tx(con):
                con.execute("UPDATE proposals SET scheme_json=? WHERE id=?", (json.dumps(s, ensure_ascii=False), p["id"]))
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--import", dest="path", help="the xlsx to turn into publicbank/data/goa_schemes.json")
    ap.add_argument("--business", action="append", default=[], metavar="PORTAL=CSV", help="a business portal's CSV to merge, e.g. msme.goa.gov.in=data/sources/goa_msme_schemes.csv (repeatable)")
    ap.add_argument("--services", metavar="CSV", help="Goa Online's services CSV to attach as application channels")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--attach-cofunding", action="store_true", help="put a co-funding scheme on every open proposal in the service's database that has none")
    a = ap.parse_args()
    if a.attach_cofunding:
        from . import db
        print(f"co-funding attached to {attach_co_funding(db.connect())} proposals")
    if a.path:
        cat = build_catalogue(a.path)
        for spec in a.business:
            portal, _, csv_path = spec.partition("=")
            print(f"{portal}: {merge_business(cat, csv_path, portal)}")
        if a.services:
            print(f"goaonline.gov.in: {merge_services(cat, a.services)}")
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CATALOGUE, "w", encoding="utf-8") as f:
            json.dump(cat, f, ensure_ascii=False, indent=1)
        load.cache_clear()
        print(f"wrote {CATALOGUE}: {len(cat['schemes'])} schemes, {len(cat['grants'])} grant lines, {len(cat.get('services', []))} services")
    if a.stats or a.path:
        for k, v in stats().items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
