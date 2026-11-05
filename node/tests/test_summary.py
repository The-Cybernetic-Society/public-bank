"""The feed cyberneticsociety.com reads as it loads, and the map picture beside it: the fund in rupees, the board in the live
board's order, readable from any origin, with nothing the public board does not already show."""
import os
import sys
import tempfile

os.environ["PB_DB"] = os.path.join(tempfile.mkdtemp(), "s.sqlite")
os.environ["PB_SECRET"] = "test"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402
from publicbank import core, db, web  # noqa: E402

con = web.con
POOL = "goa-commons"
core.create_node(con, POOL, "Goa Commons", kind="pool", pool_vote="nodes", cosign_min=2)
web.ALIASES["goa"] = POOL
c = TestClient(web.app, base_url="http://pb.test")

# unknown pool, and a pool with no round: nothing to show, and the page keeps its snapshot
assert c.get("/commons/nobody/summary.json").status_code == 404
assert c.get("/commons/nobody/map.svg").status_code == 404
empty = c.get("/commons/goa/summary.json").json()
assert empty["stage"] is None and empty["board"] == [] and empty["shops"] == 0

NAME = "Tom's <script>alert(1)</script> & Sons"
owner = core.upsert_member(con, verified_by="coordinator", telegram_id=7, display="owner")
for i, (nid, name, place, status) in enumerate((("a", NAME, "torxem", "verified"), ("b", "Bee Shop", "torxem", "new"), ("c", "Sea Shop", None, "verified"))):
    core.create_node(con, f"{POOL}/{nid}", name, parent_id=POOL)
    con.execute("""INSERT INTO applications (id,pool_id,node_id,business,applicant,role,phone,consent,place_id,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (f"ap{i}", POOL, f"{POOL}/{nid}", name, "Secret Applicant", "owner", "919800000000", 1, place, status, db.now(), db.now()))
con.commit()
core.join_node(con, POOL, owner["id"], role="coordinator", auto_active=True)
rnd = core.open_round(con, POOL, 500000 * 100)

core.add_twin_draft(con, rnd["id"], title="Twin, unbacked", text="x", ideal_paise=10000 * 100)
d2 = core.add_twin_draft(con, rnd["id"], title="Twin, backed", text="x", ideal_paise=20000 * 100)
core.add_proposal(con, rnd["id"], owner["id"], title="By a person <b>", text="x", ideal_paise=30000 * 100, lead_id=owner["id"])
con.execute("UPDATE proposals SET cosign_nodes=? WHERE id=?", (f'["{POOL}/a","{POOL}/b"]', d2["id"]))
con.commit()

r = c.get("/commons/goa/summary.json")
assert r.status_code == 200
assert r.headers["access-control-allow-origin"] == "*" and "max-age=60" in r.headers["cache-control"]
s = r.json()
assert s["stage"] == "proposals" and s["pot"] == 500000 and s["paid"] == 0 and s["approved"] == 0
assert s["asked"] == 60000 and s["remaining"] == 500000
assert s["shops"] == 3 and s["verified"] == 2 and s["proposals"] == 3
# a person's proposal first, then the twin's drafts by backing, the same order as the live board
assert [p["title"] for p in s["board"]] == ["By a person <b>", "Twin, backed", "Twin, unbacked"]
assert [p["backers"] for p in s["board"]] == [0, 2, 0] and [p["source"] for p in s["board"]] == ["human", "twin", "twin"]
assert s["board"][1]["ideal"] == 20000
assert set(s["board"][0]) == {"title", "ideal", "backers", "source"}
assert "919800000000" not in r.text and "Secret Applicant" not in r.text

home = c.get("/").text if web.HOME_POOL in ("goa", POOL) else ""
if home:
    i = [home.find(t) for t in ("By a person", "Twin, backed", "Twin, unbacked")]
    assert -1 not in i and i == sorted(i), i

# the picture: placed businesses only, dashed until verified, and a typed name can never become markup
m = c.get("/commons/goa/map.svg")
assert m.status_code == 200 and m.headers["content-type"].startswith("image/svg+xml")
assert "default-src 'none'" in m.headers["content-security-policy"]
assert "<script" not in m.text and "&lt;script&gt;" in m.text
assert m.text.count('class="on"') == 1 and m.text.count('class="on unv"') == 1, "the verified dot and the unverified one"
assert "Sea Shop" not in m.text and "919800000000" not in m.text
assert m.text.count("<line") == 2 and '<text' in m.text

# once voting starts nothing more is being asked for, but the list stands; when the round ends the board empties
con.execute("UPDATE rounds SET status='ballots' WHERE id=?", (rnd["id"],))
con.commit()
v = c.get("/commons/goa/summary.json").json()
assert v["stage"] == "ballots" and v["asked"] == 0 and len(v["board"]) == 3
con.execute("UPDATE rounds SET status='closed' WHERE id=?", (rnd["id"],))
con.commit()
v = c.get("/commons/goa/summary.json").json()
assert v["board"] == [] and v["proposals"] == 0
print("ok")
