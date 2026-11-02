"""The digital twin: the scheme catalogue imported from the workbook, requirement tagging, verdicts for three
made-up places (a coastal village, an inland tribal village, a town), roll-ups, and the public pages."""
import os
import sys
import tempfile

os.environ["PB_DB"] = os.path.join(tempfile.mkdtemp(), "p.sqlite")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from publicbank import places as P, schemes as S  # noqa: E402

cat = S.load()
assert len(cat["schemes"]) > 500 and cat["grants"], "the catalogue is the imported workbook"
assert all(s["id"] and s["name"] and isinstance(s["needs"], list) for s in cat["schemes"])
assert len({s["id"] for s in cat["schemes"]}) == len(cat["schemes"]), "ids are unique"


def find(word: str) -> dict:
    return next(s for s in cat["schemes"] if word in s["id"])


# tagging: the rules read the right things off known schemes
ggsy = find("goa-gram-samrudhi-yojana")
assert ggsy["bodies"] == ["village_panchayat"] and ggsy["scope"] == "rural" and ggsy["local_body_role"] == "Funds local body directly"
nrega = find("employment-guarantee")
assert "rural" in nrega["needs"] and not nrega["bodies"] and "Household" in nrega["people"]
fish = find("construction-of-fish-markets")
assert set(fish["bodies"]) == {"village_panchayat", "municipal"} and fish["scope"] == "any" and fish["max_rupees"] == 500000
asra = find("atal-asra-yojana")
assert "st" in asra["needs"] and asra["amendments"], "amendments hang off the scheme they change"
assert S.core_name("Amendment to Atal Asra Yojana (clause 6 conditions of sanction)") == "atal asra"
nrlm = find("rural-livelihood")
assert "shg" in nrlm["needs"] and "women" in nrlm["needs"]

# three places
V = [{"id": "mandrem", "kind": "village_panchayat", "name": "Mandrem", "taluka": "Pernem", "district": "North Goa",
      "census": {"year": 2011, "households": 1200, "population": 5000, "sc": 40, "st": 10, "literates": 4000, "workers": 2000, "cultivators": 150,
                 "agri_labourers": 100, "hh_industry": 60, "other_workers": 1690, "non_workers": 3000, "confidence": "mapped"},
      "flags": {"coastal": True, "estuarine": True}, "programmes": {"mgnrega_job_cards": 120}, "sources": []},
     {"id": "netravali", "kind": "village_panchayat", "name": "Netravali", "taluka": "Sanguem", "district": "South Goa",
      "census": {"year": 2011, "households": 900, "population": 3800, "sc": 5, "st": 2600, "literates": 2500, "workers": 1800, "cultivators": 600,
                 "agri_labourers": 500, "hh_industry": 20, "other_workers": 680, "non_workers": 2000, "confidence": "mapped"},
      "flags": {"coastal": False, "estuarine": False}, "programmes": {"mgnrega_job_cards": 400, "shgs": 12}, "sources": []},
     {"id": "mapusa", "kind": "municipal_council", "name": "Mapusa", "taluka": "Bardez", "district": "North Goa",
      "census": {"year": 2011, "households": 9000, "population": 40000, "sc": 600, "st": 900, "literates": 35000, "workers": 15000, "cultivators": 100,
                 "agri_labourers": 80, "hh_industry": 500, "other_workers": 14320, "non_workers": 25000, "confidence": "mapped"},
      "flags": {"coastal": False, "estuarine": True}, "programmes": {}, "sources": []}]
P.set_divisions(V)
mandrem, netravali, mapusa = (P.get(i) for i in ("mandrem", "netravali", "mapusa"))


def status(d, s):
    return P.verdict(d, s)["status"]


# a scheme a village panchayat applies for: the village can, the town cannot
assert status(mandrem, ggsy) == "eligible" and status(mapusa, ggsy) == "unlikely"
# a rural household scheme: both villages, not the town; MGNREGA data marks it as already drawing
assert status(mandrem, nrega) == "eligible" and P.verdict(mandrem, nrega)["drawing"] is True and status(mapusa, nrega) == "unlikely"
# fish markets: coastal village eligible, inland village unlikely, estuarine town plausible
assert status(mandrem, fish) == "eligible" and status(netravali, fish) == "unlikely" and status(mapusa, fish) == "plausible"
# a Scheduled Tribe housing scheme is for people, wherever they live, where the census counts them
assert status(netravali, asra) == "eligible" and status(mandrem, asra) == "eligible" and status(mapusa, asra) == "eligible"
# a scheme open to households as well as groups is judged on the households; SHG data marks it as running
assert status(mandrem, nrlm) == "eligible" and status(netravali, nrlm) == "eligible" and P.verdict(netravali, nrlm)["drawing"] is True
# a scheme only a society can apply for: plausible with the step where none is recorded, unlikely inland
soc = find("registered-fishermen-societies")
v = P.verdict(mandrem, soc)
assert v["status"] == "plausible" and v["steps"] and "cooperative" in v["steps"][0], v
assert status(netravali, soc) == "unlikely"
# an individual fisher relief scheme is not made plausible by the societies it mentions
relief = find("saving-cum-relief")
assert status(mandrem, relief) == "eligible", P.verdict(mandrem, relief)
# a tribal-area scheme: Netravali (68% ST) yes, Mandrem (0.2%) no
tribal = next(s for s in cat["schemes"] if "tribal_area" in s["needs"])
assert status(netravali, tribal) != "unlikely" and status(mandrem, tribal) == "unlikely", P.verdict(mandrem, tribal)
# every verdict explains itself
for d in (mandrem, netravali, mapusa):
    for s in cat["schemes"]:
        v = P.verdict(d, s)
        assert v["status"] in ("eligible", "plausible", "unlikely") and v["why"], (d["id"], s["id"], v)

# roll-ups: sums, any-flags, member counts
pernem = P.get("taluka-pernem")
assert pernem["census"]["population"] == 5000 and pernem["members"] == ["mandrem"]
goa = P.get("goa")
assert goa["census"]["population"] == 48800 and goa["flags"]["coastal"] is True and goa["programmes"]["shgs"] == 12
t = P.twin(goa)
assert t["members"][ggsy["id"]] == {"eligible": 2, "unlikely": 1}, t["members"][ggsy["id"]]
assert t["summary"]["unlikely"] == 0, "the state as a whole fails no territorial condition"
assert P.summary_of("mandrem")["eligible"] > 0 and P.summary_of("nowhere") is None
assert P.rollups("district") and [r["name"] for r in P.rollups("district")] == ["North Goa", "South Goa"]

# the card: the three schemes worth a place's attention first, impact × ease; already-running and unlikely never rank
top = P.top_schemes(mandrem, 3)
assert len(top) == 3 and top[0]["score"] >= top[1]["score"] >= top[2]["score"] > 0
assert all(t["status"] in ("eligible", "plausible") for t in top)
pm, pmap = P.profile(mandrem), P.profile(mapusa)
assert P.score(ggsy, P.verdict(mandrem, ggsy), pm)[2] == 1.0 and P.score(ggsy, P.verdict(mapusa, ggsy), pmap)[0] == 0
assert P.score(nrega, P.verdict(mandrem, nrega), pm)[0] == 0, "a scheme already running here is not an opportunity"
soc_v = P.verdict(mandrem, soc)
assert 0 < P.score(soc, soc_v, pm)[2] < 1, "plausible with a step: eased down"
assert P.reach(ggsy, pm) == 1.0 and P.reach(asra, P.profile(netravali)) > P.reach(asra, pm), "an ST scheme reaches more of a tribal village"
assert P.rarity()[fish["id"]] > P.rarity()[ggsy["id"]], "a fish-market grant is eligible in fewer bodies than a panchayat infrastructure grant"
c = P.card(mandrem)
assert c["name"] == "Mandrem" and c["facts"]["coastal"] is True and c["url"] == "/twin/mandrem" and c["up"] == "taluka-pernem" and len(c["top"]) == 3
assert P.card(P.get("taluka-pernem"))["up"] == "district-north-goa" and P.card(P.get("district-north-goa"))["up"] == "goa"
assert P.section(ggsy) == "body" and P.section(nrega) == "people" and P.section(find("registered-fishermen-societies")) == "groups"

# the real build: every body on the LGD list, census attached, coast and fishing from the published tables
P.set_divisions(None)
real = P.divisions()
assert len(real) == 205 and sum(1 for d in real if d["kind"] == "village_panchayat") == 191
md = P.get("mandrem")
assert md and md["census"]["population"] == 8336 and md["flags"]["coastal"] is True and md["programmes"]["fisher_households"] > 0
assert P.get("cotigao")["flags"]["coastal"] is None and P.get("neturlim")["flags"]["coastal"] is False, "inland talukas are not coastal; coastal talukas without a fishing village are unknown"
assert P.get("taleigao")["census"]["population"] and P.get("taleigao")["census"]["confidence"] == "directorate"
assert {r["name"] for r in P.rollups("district")} == {"North Goa", "South Goa", "Kushavati"}
fixes = {(P._norm("Harmal"), P._norm("Pernem")): "arambol"}
assert P.match_body("Harmal", "Pernem", real, fixes)[1] == "fix"
assert P.match_body("Querim", "Pernem", real, {})[0]["id"] == "keri-terecol", "a village name inside the taluka"
assert P.match_body("Allorna", "Pernem", real, {})[0]["id"] == "alorna", "a close spelling inside the taluka"
assert P.match_body("Nagoa", "Mormugao", real, {})[1].startswith("ambiguous"), "two bodies elsewhere share the word: no guess"
assert P.match_body("Nuvem", "Canacona", real, {}, cross=False)[0] is None, "a hamlet never matches another taluka's body"
# the registration form's place: typed names, labels, villages and aliases resolve to a body; the typeahead lists them
assert P.resolve("Mandrem")["id"] == "mandrem" and P.resolve("Mandrem · Pernem")["id"] == "mandrem" and P.resolve("mandrem (Pernem)")["id"] == "mandrem"
assert P.resolve("Harmal")["id"] == "arambol", "a hand-kept fix becomes an alias the form understands"
assert P.resolve("Querim · Sattari")["id"] != P.resolve("Querim · Pernem")["id"], "the taluka tells the two Querims apart"
assert P.resolve("taluka-pernem") is None and P.resolve("") is None and P.resolve("Nowhere at all") is None
ch = P.choices()
assert any(c["label"] == "Mandrem · Pernem" for c in ch) and len(ch) > 300 and len({c["label"] for c in ch}) == len(ch)
from publicbank import twinmap  # noqa: E402
if twinmap.available():
    assert P.locate(15.65, 73.72)["id"] == "mandrem", P.locate(15.65, 73.72)   # Mandrem village; 73.71 is already in the sea
    assert P.locate(15.495, 73.83)["id"] == "city-corporation-panaji", P.locate(15.495, 73.83)
    assert P.locate(15.0, 74.9) is None, "outside Goa"
P.set_divisions(V)

# the pages
from fastapi.testclient import TestClient  # noqa: E402
from publicbank import web  # noqa: E402

c = TestClient(web.app)
r = c.get("/twin")
assert r.status_code == 200 and "Mandrem" in r.text and "Netravali" in r.text
r = c.get("/twin?level=municipal")
assert r.status_code == 200 and "Mapusa" in r.text and "Netravali" not in r.text
r = c.get("/twin?level=taluka")
assert r.status_code == 200 and "Pernem" in r.text
r = c.get("/twin/mandrem")
assert r.status_code == 200 and "Goa Gram Samrudhi" in r.text and "Census 2011" in r.text and "coastal" in r.text.lower()
r = c.get("/twin/taluka-pernem")
assert r.status_code == 200 and "of 1" in r.text
r = c.get("/twin/goa")
assert r.status_code == 200
r = c.get("/twin/mandrem.json")
assert r.status_code == 200 and r.json()["summary"]["schemes"] == len(cat["schemes"]) and r.json()["profile"]["coastal"] is True
assert c.get("/twin/nowhere").status_code == 404
r = c.get("/twin/mandrem/card.json")
assert r.status_code == 200 and r.json()["top"] and r.json()["summary"]["schemes"] == len(cat["schemes"])
assert c.get("/twin/nowhere/card.json").status_code == 404
if twinmap.available():
    m = c.get("/twin/map.json").json()
    assert {f["level"] for f in m["features"]} == {"district", "taluka", "body"} and all(f["rings"] and f["c"] for f in m["features"])
    assert "id=tmap" in c.get("/twin").text
    P.set_divisions(None)
    r = c.get("/twin/locate?lat=15.65&lon=73.72")
    assert r.status_code == 200 and r.json()["id"] == "mandrem" and r.json()["label"] == "Mandrem · Pernem"
    assert c.get("/twin/locate?lat=10&lon=70").status_code == 404
    P.set_divisions(V)
r = c.get("/twin/businesses.json")
assert r.status_code == 200 and "businesses" in r.json()
assert "/twin" in c.get("/").text
P.set_divisions(None)
print("TWIN OK")
