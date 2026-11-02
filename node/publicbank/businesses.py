"""
The business twin: one registered business read against the schemes a business can draw on.

The place twin (places.py) says what a village or town qualifies for; it can say nothing about the business
schemes, whose conditions are on the firm: an Udyam number, Startup Goa recognition, a trade, who the owner is,
how old and how small it is. This module builds a profile from what the owner told the registration form (what
the business does, team size, where it is, and three optional answers: registrations held, owner category, age)
and gives each business-facing scheme one of the same three verdicts, with the step that would unlock it:

  eligible   nothing in the profile rules it out, and every entry ticket the scheme names is held
  plausible  an entry ticket is missing or unknown (register on Udyam, get Startup Goa certified, take GST) or the
             profile does not say (owner category, age) — with the step
  unlikely   a hard mismatch: a trade the scheme is not for, an owner category the owner is not, a size or age cap

Nothing here leaves the owner's own pages except counts: the place card may say how many schemes the verified
businesses in a place could draw, never which business holds what.
"""
from __future__ import annotations

import re
from collections import Counter

from . import core, places, schemes as S

# what a business's own words say about its trade (the form's 'what the business does' and 'needs help with')
TRADE_WORDS = [
    ("it", r"software|\bapp\b|\bIT\b|website|digital|tech|coding|saas|data"),
    ("logistics", r"logistic|warehous|courier|transport|delivery|freight|taxi|cab"),
    ("cashew", r"cashew|feni"),
    ("tourism", r"touris|hotel|homestay|guest ?house|shack|hostel|surf|yoga|travel|rental|bike|scooter rent|cafe|restaurant|bar\b|resort|villa"),
    ("artisan", r"tailor|handicraft|craft|weav|pottery|carpent|jewel|embroider|artisan|boutique|coir|print"),
    ("fisheries", r"fish|seafood|boat|aquacultur|prawn|crab"),
    ("dairy", r"dairy|milk|cattle|poultry|egg|goat|pig|livestock"),
    ("agri", r"farm|agri|coconut|spice|organic|nursery|garden|plant"),
    ("industry", r"manufactur|factory|fabricat|workshop|unit\b|engineer|machine"),
    ("food", r"bakery|cake|bread|food|catering|kitchen|sweet|juice|tiffin|lunch"),
    ("retail", r"store|shop|kirana|pharmacy|medical|stationery|grocer|mart|supermarket|boutique|salon|parlour|spa"),
]
TRADE_RX = {k: re.compile(p, re.I) for k, p in TRADE_WORDS}
STEPS = {"udyam": "register the business on the Udyam portal (free, online, takes a day)",
         "startup": "get Startup Goa certification from the Startup and IT Promotion Cell",
         "gst": "take a GST registration"}
SLOTS = (("money", "A grant, subsidy or reimbursement"), ("credit", "A loan or guarantee"), ("support", "Training, a service or a programme"))


def profile(app: dict | None, node: dict | None = None) -> dict:
    """The business as the twin sees it, from its application; None where the owner did not say."""
    a = app or {}
    text = " ".join(str(a.get(k) or "") for k in ("about", "challenge", "need", "business"))
    trades = [k for k, rx in TRADE_RX.items() if rx.search(text)]
    regs = {x for x in (a.get("registrations") or "").split(",") if x}
    known = bool(regs)
    tags = {x for x in (a.get("owner_tags") or "").split(",") if x}
    return {"node_id": a.get("node_id") or (node or {}).get("id"), "name": a.get("business") or (node or {}).get("name"),
            "trades": trades, "team_size": a.get("team_size"), "place_id": a.get("place_id") or (node or {}).get("place_id"),
            "udyam": (True if "udyam" in regs else False) if known else None, "startup": (True if "startup" in regs else False) if known else None,
            "gst": (True if "gst" in regs else False) if known else None,
            "woman": True if "woman" in tags else (False if a.get("owner_tags") is not None and a.get("owner_tags") != "" else None),
            "sc_st": True if tags & {"sc", "st"} else (False if a.get("owner_tags") else None),
            "age": a.get("age_band") or None, "verified": a.get("status") == "verified"}


def verdict(p: dict, s: dict) -> dict:
    """One business-facing scheme against one business."""
    b = s.get("biz") or {}
    why, steps, results = [], [], []
    if b.get("sectors"):
        if p["trades"] and set(p["trades"]) & set(b["sectors"]):
            why.append("in the trade it is for"); results.append("ok")
        elif p["trades"]:
            why.append(f"for {', '.join(b['sectors'])} businesses; this one is {', '.join(p['trades'])}"); results.append("no")
        else:
            why.append(f"for {', '.join(b['sectors'])} businesses; the form does not say what trade this is"); results.append("maybe")
    needs = list(b.get("needs", []))
    if "women_only" in needs and "sc_st_only" in needs:
        # a scheme for 'women, SC/ST' entrepreneurs is for either, not both
        needs = [k for k in needs if k not in ("women_only", "sc_st_only")]
        if p["woman"] is True or p["sc_st"] is True:
            why.append("for women or SC/ST owners, as this one is"); results.append("ok")
        elif p["woman"] is False and p["sc_st"] is False:
            why.append("only for women or SC/ST owners"); results.append("no")
        else:
            why.append("only for women or SC/ST owners; the form does not say"); results.append("maybe")
    for key in needs:
        if key in ("udyam", "startup", "gst"):
            held = p.get(key)
            if held is True:
                why.append(f"holds the {key.upper() if key == 'gst' else key.capitalize()} registration it asks for"); results.append("ok")
            else:
                why.append(("no " if held is False else "needs ") + ("Udyam number" if key == "udyam" else "Startup Goa certification" if key == "startup" else "GST registration"))
                results.append("maybe"); steps.append(STEPS[key])
        elif key == "women_only":
            if p["woman"] is True: why.append("woman-owned, as it asks"); results.append("ok")
            elif p["woman"] is False: why.append("only for women-owned businesses"); results.append("no")
            else: why.append("only for women-owned businesses; the form does not say"); results.append("maybe")
        elif key == "sc_st_only":
            if p["sc_st"] is True: why.append("the owner is SC or ST, as it asks"); results.append("ok")
            elif p["sc_st"] is False: why.append("only for SC or ST owners"); results.append("no")
            else: why.append("only for SC or ST owners; the form does not say"); results.append("maybe")
        elif key in ("new_unit", "young"):
            if p["age"] == "established": why.append("for new or young businesses; this one is established"); results.append("no")
            elif p["age"]: why.append("new or young, as it asks"); results.append("ok")
            else: why.append("for new or young businesses; the form does not say how old this one is"); results.append("maybe")
        elif key == "small":
            ts = p.get("team_size")
            if ts and ts > 50: why.append("for micro and small units; this one has a large team"); results.append("no")
            else: why.append("a micro or small unit"); results.append("ok")
        elif key == "local":
            why.append("for Goan businesses or owners")
    status = "unlikely" if "no" in results else "plausible" if "maybe" in results else "eligible"
    if status == "eligible" and not results:
        why.append("open to any business; nothing in the profile rules it out")
    return {"scheme_id": s["id"], "status": status, "why": why, "steps": sorted(set(steps)), "offer": b.get("offer", "money")}


def candidates() -> list[dict]:
    """The schemes a business could draw: the portal ones, and workbook schemes whose applicants include a business."""
    return [s for s in S.schemes() if (s.get("biz") or {}).get("for_business")]


def card(con, node_id: str) -> dict:
    """The owner's view: the profile, the counts, one scheme per slot (money, credit, support) by impact × ease,
    and the steps that would unlock the most."""
    node = core.get_node(con, node_id)
    app = core.application_of_node(con, node_id)
    p = profile(app, node)
    vs = []
    for s in candidates():
        v = verdict(p, s)
        if v["status"] == "unlikely":
            vs.append((0.0, s, v)); continue
        mx = s.get("max_rupees")
        import math
        money = min(1.0, 0.5 + 0.5 * math.log10(max(mx, 1000)) / 7) if mx else 0.6
        ease = 1.0 if v["status"] == "eligible" else max(0.25, 0.55 - 0.1 * len(v["steps"]))
        vs.append((round(money * ease, 3), s, v))
    vs.sort(key=lambda x: (-x[0], x[1]["name"]))
    counts = Counter(v["status"] for _, _, v in vs)
    slots = []
    for key, label in SLOTS:
        pick = next(((sc, s, v) for sc, s, v in vs if sc > 0 and v["offer"] == key), None)
        if pick:
            sc, s, v = pick
            slots.append({"slot": key, "slot_label": label, "id": s["id"], "name": s["name"], "department": s["department"], "max_rupees": s.get("max_rupees"),
                          "status": v["status"], "why": "; ".join(v["why"]), "steps": v["steps"], "score": sc, "source_url": s.get("source_url", ""),
                          "apply_online": (s.get("apply_online") or {}).get("url", ""), "portals": [x["portal"] for x in s.get("portals", [])]})
    # which single step unlocks the most
    unlock: Counter = Counter()
    for sc, s, v in vs:
        for st in v["steps"]:
            unlock[st] += 1
    return {"node_id": node_id, "name": node["name"], "profile": p, "summary": {"eligible": counts.get("eligible", 0), "plausible": counts.get("plausible", 0),
            "unlikely": counts.get("unlikely", 0), "schemes": len(vs)}, "slots": slots,
            "unlocks": [{"step": k, "schemes": n} for k, n in unlock.most_common(3)],
            "all": [{"id": s["id"], "name": s["name"], "department": s["department"], "max_rupees": s.get("max_rupees"), "status": v["status"], "why": v["why"],
                     "steps": v["steps"], "offer": v["offer"], "source_url": s.get("source_url", ""), "apply_online": (s.get("apply_online") or {}).get("url", "")}
                    for _, s, v in vs]}


def place_draw(con, pool_id: str, place_id: str) -> dict | None:
    """For the map's card: what the verified businesses in a place could draw, as counts and the commonest step.
    Names of businesses are public already; which business holds what is not, and stays out."""
    biz = [b for b in core.verified_businesses(con, pool_id) if b["place_id"] == place_id]
    if not biz:
        return None
    elig: Counter = Counter(); unlock: Counter = Counter()
    for b in biz:
        c = card(con, b["node_id"])
        for r in c["all"]:
            if r["status"] == "eligible":
                elig[r["name"]] += 1
        for u in c["unlocks"]:
            unlock[u["step"]] += u["schemes"]
    return {"businesses": len(biz), "eligible_any": len(elig), "top": [n for n, _ in elig.most_common(3)],
            "step": unlock.most_common(1)[0][0] if unlock else None}
