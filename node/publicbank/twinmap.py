"""
The map: Goa drawn as its local bodies, tappable, with a card under it for the place tapped.

Three scales (districts, talukas, village panchayats and towns) on one segmented control; tapping a district or
taluka selects it and offers to zoom into its members; a breadcrumb climbs back out. The card shows who the place
is, four figures, the verdict counts, the three schemes worth its attention first (impact × ease, `places.score`)
and a button to the full twin page. Plain SVG and plain JS: no map library, nothing loaded from outside the site.

Geometry comes from `data/goa_map.json`, built by `python -m publicbank.places --build-map` from the census
village polygons in `data/sources/` dissolved per body, taluka and district (see data/sources/README.md).
"""
from __future__ import annotations

import json
import os
from collections import Counter

from . import schemes as S

MAP_FILE = os.path.join(S.DATA_DIR, "goa_map.json")

CSS = """<style>
.tmap{margin:8px 0 0}.tmap .lvl{display:flex;gap:8px;margin:0 0 10px}.tmap .lvl button{flex:1;margin:0;padding:12px 6px;font-size:11px;box-shadow:3px 3px 0 var(--shadow)}
.tmap .lvl button[aria-pressed=true]{background:var(--gold);box-shadow:1px 1px 0 var(--shadow);transform:translate(2px,2px)}
.tmap .crumb{font:400 14px 'IBM Plex Mono',monospace;color:var(--muted);margin:0 0 6px;min-height:20px}.tmap .crumb a{text-decoration:none;border-bottom:1px solid var(--line);color:var(--ink)}.tmap .crumb a:after{content:' › ';border:0;color:var(--muted)}
.tmap svg{width:100%;height:auto;max-height:68vh;display:block;background:var(--card);border:2px solid var(--ink);border-radius:3px;box-shadow:6px 6px 0 #20201dcc;touch-action:manipulation}
.tmap path{fill:#efe3c9;fill-rule:evenodd;stroke:var(--ink);stroke-width:1px;vector-effect:non-scaling-stroke;stroke-linejoin:round;cursor:pointer;transition:fill .15s}.tmap path:hover,.tmap path:focus{fill:#e8cf8f;outline:none}
.tmap path.sel{fill:var(--gold);stroke-width:2px}.tmap path.dim{fill:#f3e9d6;stroke:#20201d44;pointer-events:none}.tmap path.ctx{fill:none;stroke:var(--ink);stroke-width:2.5px;pointer-events:none}
.tmap .hint{font:400 14px 'IBM Plex Mono',monospace;color:var(--muted);margin:8px 0 0;min-height:20px}
.tmap g.marks circle{fill:var(--ink);stroke:var(--card);stroke-width:2px;vector-effect:non-scaling-stroke;pointer-events:none}.tmap g.marks text{font:500 11px 'IBM Plex Mono',monospace;fill:var(--card);text-anchor:middle;dominant-baseline:central;pointer-events:none}
.tmap .legend{font:400 13px 'IBM Plex Mono',monospace;color:var(--muted);margin:6px 0 0}.tmap .legend i{display:inline-block;width:12px;height:12px;border-radius:50%;background:var(--ink);vertical-align:-1px;margin-right:6px}
.tcard .biz{margin:12px 0 0;font-size:15px}.tcard .biz .chip{display:inline-block;border:1px solid var(--line);border-radius:8px;padding:3px 8px;margin:4px 6px 0 0;font:400 13px 'IBM Plex Mono',monospace;color:var(--ink)}
.tcard{margin-top:16px}.tcard h3{font:500 26px/1.1 Newsreader,Georgia,serif;letter-spacing:-.015em;color:var(--ink);margin:0 0 2px}.tcard .kind{color:var(--muted);font-size:15px;margin:0 0 12px}
.tcard .facts{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.tcard .facts div{font:400 12px 'IBM Plex Mono',monospace;color:var(--muted)}.tcard .facts b{display:block;font:500 20px 'IBM Plex Mono',monospace;color:var(--ink)}
.tcard .counts{font:400 13px 'IBM Plex Mono',monospace;color:var(--muted);margin:12px 0 4px}.tcard .counts .tag{margin-right:2px}
.tcard ol.top{list-style:none;padding:0;margin:8px 0 0}.tcard ol.top li{display:flex;gap:10px;padding:10px 0;border-top:1px solid var(--line)}.tcard ol.top .n{font:400 11px Silkscreen,'IBM Plex Mono',monospace;color:var(--ink);background:var(--gold);border-radius:8px;padding:5px 7px;height:fit-content;flex:0 0 auto}
.tcard ol.top b{font-weight:500;color:var(--ink)}.tcard ol.top .m{display:block;color:var(--muted);font-size:14px;line-height:1.4}.tcard ol.top .s{display:block;color:var(--ink);font-size:14px}.tcard ol.top .s:before{content:'→ '}
.tcard .score{font:400 12px 'IBM Plex Mono',monospace;color:var(--muted);margin-left:auto;flex:0 0 auto;align-self:flex-start}
.tcard a.btn{margin-top:14px}.tcard a.btn.quiet{box-shadow:none;margin-top:8px;border-color:var(--line)}.tcard .empty{color:var(--muted)}
@media(max-width:480px){.tcard .facts{grid-template-columns:repeat(2,1fr)}}
</style>"""

JS = r"""<script>
(function(){
var root=document.getElementById('tmap');if(!root)return;
var svg=root.querySelector('svg'),g=svg.querySelector('g.shapes'),ctx=svg.querySelector('g.ctx'),marks=svg.querySelector('g.marks'),crumb=root.querySelector('.crumb'),hint=root.querySelector('.hint'),legend=root.querySelector('.legend'),card=document.getElementById('tcard');
var DATA=null,level='district',focus=null,selected=root.dataset.selected||null,BIZ=[];
function bizIn(f){if(!BIZ.length)return [];if(f.level==='body')return BIZ.filter(function(b){return b.place_id===f.id});var key=f.level==='taluka'?'taluka':'district';var ids={};DATA.features.forEach(function(x){if(x.level==='body'&&x[key]===f.name)ids[x.id]=1});return BIZ.filter(function(b){return ids[b.place_id]})}
function drawMarks(){marks.innerHTML='';if(!BIZ.length||!DATA)return;var vb=svg.viewBox.baseVal,r=vb.width/60;features().filter(inFocus).forEach(function(f){var n=bizIn(f).length;if(!n||!f.c)return;
  var c=document.createElementNS('http://www.w3.org/2000/svg','circle');c.setAttribute('cx',f.c[0]);c.setAttribute('cy',f.c[1]);c.setAttribute('r',r);marks.appendChild(c);
  var t=document.createElementNS('http://www.w3.org/2000/svg','text');t.setAttribute('x',f.c[0]);t.setAttribute('y',f.c[1]);t.setAttribute('font-size',r*1.2);t.textContent=n;marks.appendChild(t)});
  legend.innerHTML=BIZ.length?'<i></i>verified businesses in the Commons, by place':''}
var LEVELS={district:'district',taluka:'taluka',body:'body'},WORDS={district:'districts',taluka:'talukas',body:'panchayats and towns'};
function rupees(n){var s=String(n),t=s.slice(-3),h=s.slice(0,-3);return '₹'+(h?h.replace(/\B(?=(\d{2})+(?!\d))/g,',')+',':'')+t}
function num(n){return n==null?'?':Number(n).toLocaleString('en-IN')}
function pct(x){return x==null?'?':Math.round(x*100)+'%'}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function path(rings){return rings.map(function(r){return 'M'+r.map(function(p){return p[0].toFixed(1)+' '+p[1].toFixed(1)}).join('L')+'Z'}).join('')}
function features(){return DATA.features.filter(function(f){return f.level===level})}
function inFocus(f){if(!focus)return true;var fo=byId(focus);if(fo.level==='district')return f.district===fo.name;if(fo.level==='taluka')return f.taluka===fo.name;return true}
function byId(id){return DATA.features.filter(function(f){return f.id===id})[0]}
function bbox(fs){var b=[1e9,1e9,-1e9,-1e9];fs.forEach(function(f){f.rings.forEach(function(r){r.forEach(function(p){if(p[0]<b[0])b[0]=p[0];if(p[1]<b[1])b[1]=p[1];if(p[0]>b[2])b[2]=p[0];if(p[1]>b[3])b[3]=p[1]})})});return b}
function draw(){
  var fs=features(),shown=fs.filter(inFocus),box=bbox(shown.length?shown:fs),pad=Math.max(box[2]-box[0],box[3]-box[1])*0.04;
  svg.setAttribute('viewBox',[box[0]-pad,box[1]-pad,box[2]-box[0]+2*pad,box[3]-box[1]+2*pad].join(' '));
  g.innerHTML='';ctx.innerHTML='';
  fs.forEach(function(f){var p=document.createElementNS('http://www.w3.org/2000/svg','path');p.setAttribute('d',path(f.rings));p.dataset.id=f.id;
    var vis=inFocus(f);p.setAttribute('class',(f.id===selected?'sel ':'')+(vis?'':'dim'));
    if(vis){p.setAttribute('tabindex','0');p.setAttribute('role','button');p.setAttribute('aria-label',f.name);var t=document.createElementNS('http://www.w3.org/2000/svg','title');t.textContent=f.name;p.appendChild(t);
      p.addEventListener('click',function(){select(f.id)});p.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();select(f.id)}});
      p.addEventListener('mouseenter',function(){hint.textContent=f.name+(f.taluka&&level==='body'?' · '+f.taluka:'')});p.addEventListener('mouseleave',function(){hint.textContent=defaultHint()})}
    g.appendChild(p)});
  if(focus){var fo=byId(focus),o=document.createElementNS('http://www.w3.org/2000/svg','path');o.setAttribute('d',path(fo.rings));o.setAttribute('class','ctx');ctx.appendChild(o)}
  root.querySelectorAll('.lvl button').forEach(function(b){b.setAttribute('aria-pressed',b.dataset.level===level?'true':'false')});
  crumbs();hint.textContent=defaultHint();drawMarks()}
function defaultHint(){var n=features().filter(inFocus).length;return 'Tap one of the '+n+' '+WORDS[level]}
function crumbs(){var parts=['<a href="#" data-go="goa">Goa</a>'];if(focus){var fo=byId(focus);if(fo.level==='taluka'&&fo.district){var d=DATA.features.filter(function(f){return f.level==='district'&&f.name===fo.district})[0];if(d)parts.push('<a href="#" data-go="'+d.id+'">'+esc(d.name)+'</a>')}parts.push('<a href="#" data-go="'+fo.id+'">'+esc(fo.name)+'</a>')}
  crumb.innerHTML=parts.join('')+'<span>'+WORDS[level]+'</span>';
  crumb.querySelectorAll('a').forEach(function(a){a.addEventListener('click',function(e){e.preventDefault();var id=a.dataset.go;if(id==='goa'){focus=null;level='district'}else{var f=byId(id);focus=f.id;level=f.level==='district'?'taluka':'body'}selected=id==='goa'?'goa':id;draw();load(selected);remember()})})}
function setLevel(l){level=l;if(focus){var fo=byId(focus);if(l==='district'||(l==='taluka'&&fo.level==='taluka'))focus=null}draw()}
function remember(){try{var h='place='+encodeURIComponent(selected||'')+'&level='+level+(focus?'&focus='+encodeURIComponent(focus):'');history.replaceState(null,'','#'+h)}catch(e){}}
function recall(){var m=/place=([^&]*)/.exec(location.hash),l=/level=(district|taluka|body)/.exec(location.hash),f=/focus=([^&]*)/.exec(location.hash);
  if(m&&m[1])selected=decodeURIComponent(m[1]);if(l)level=l[1];if(f&&f[1])focus=decodeURIComponent(f[1])}
function select(id){selected=id;draw();load(id);remember()}
function load(id){card.innerHTML='<p class=empty>…</p>';fetch('/twin/'+encodeURIComponent(id)+'/card.json').then(function(r){return r.ok?r.json():null}).then(function(c){if(!c){card.innerHTML='<p class=empty>Nothing known about this place yet.</p>';return}render(c)}).catch(function(){card.innerHTML='<p class=empty>Could not load this place.</p>'})}
function render(c){var f=c.facts,coast=f.coastal===true?'yes':f.coastal===false?'no':'?';
  var where=[c.kind_word,c.taluka&&c.kind!=='taluka'?c.taluka:null,c.district&&c.kind!=='district'&&c.kind!=='taluka'?c.district:(c.kind==='taluka'?c.district:null),c.wards?c.wards+' wards':null,c.members?c.members+' local bodies':null].filter(Boolean).join(' · ');
  var h='<h3>'+esc(c.name)+'</h3><p class=kind>'+esc(where)+'</p>';
  h+='<div class=facts><div>Households<b>'+num(f.households)+'</b></div><div>People<b>'+num(f.population)+'</b></div><div>Scheduled Tribes<b>'+pct(f.st_share)+'</b></div><div>Coast<b>'+coast+'</b></div></div>';
  var s=c.summary;h+='<p class=counts><span class="tag ok">eligible</span> '+s.eligible+' &nbsp; <span class="tag maybe">plausible</span> '+s.plausible+' &nbsp; <span class="tag no">unlikely</span> '+s.unlikely+' &nbsp;· of '+s.schemes+' schemes</p>';
  if(c.top.length){h+='<p class=muted style="margin:10px 0 0">Worth looking at first, by impact × ease:</p><ol class=top>'+c.top.map(function(t,i){var cls=t.status==='eligible'?'ok':'maybe';
    return '<li><span class=n>'+(i+1)+'</span><div><b>'+(t.source_url?'<a href="'+esc(t.source_url)+'">'+esc(t.name)+'</a>':esc(t.name))+'</b> <span class="tag '+cls+'">'+t.status+'</span><span class=m>'+esc(t.department)+(t.max_rupees?' · up to '+rupees(t.max_rupees):'')+(t.why?' · '+esc(t.why):'')+'</span>'+(t.steps||[]).map(function(x){return '<span class=s>'+esc(x)+'</span>'}).join('')+'</div><span class=score title="impact '+t.impact+' × ease '+t.ease+'">'+t.score.toFixed(2)+'</span></li>'}).join('')+'</ol>'}
  var f0=byId(c.id),here=f0?bizIn(f0):[];
  if(here.length){h+='<p class=biz><b>'+here.length+' verified business'+(here.length>1?'es':'')+' in the Commons here:</b><br>'+here.map(function(b){return '<span class=chip>'+esc(b.name)+'</span>'}).join('')+'</p>'}
  h+='<a class=btn href="'+esc(c.url)+'">All '+s.schemes+' schemes for '+esc(c.name)+'</a>';if(f0&&f0.level!=='body'){h+='<a class="btn quiet" href="#" id=tzoom>See its '+WORDS[f0.level==='district'?'taluka':'body']+'</a>'}
  card.innerHTML=h;var z=document.getElementById('tzoom');if(z)z.addEventListener('click',function(e){e.preventDefault();focus=c.id;level=f0.level==='district'?'taluka':'body';draw();remember();svg.scrollIntoView({behavior:'smooth',block:'start'})})}
root.querySelectorAll('.lvl button').forEach(function(b){b.addEventListener('click',function(){setLevel(b.dataset.level);remember()})});
recall();
fetch('/twin/businesses.json').then(function(r){return r.ok?r.json():{businesses:[]}}).then(function(b){BIZ=b.businesses||[];if(DATA)drawMarks()}).catch(function(){});
fetch('/twin/map.json?v='+(root.dataset.v||'')).then(function(r){return r.json()}).then(function(d){DATA=d;if(selected&&!/level=/.test(location.hash)){var f=byId(selected);if(f){level=f.level;if(f.level==='body'){var t=DATA.features.filter(function(x){return x.level==='taluka'&&x.name===f.taluka})[0];focus=t?t.id:null}}}
  if(focus&&!byId(focus))focus=null;draw();if(selected)load(selected)}).catch(function(){hint.textContent='The map could not load.'});
})();
</script>"""


VILLAGES = os.path.join(S.DATA_DIR, "sources", "goa_villages_2011.geojson")   # DataMeet village polygons with Census 2011 codes (ODbL)
LON0, LAT0, K = 73.6, 15.9, 1000.0   # map units: x east of 73.6°E, y south of 15.9°N, 1 unit ≈ 111 m


def project(lon: float, lat: float) -> tuple[float, float]:
    import math
    return round((lon - LON0) * math.cos(math.radians(15.4)) * K, 1), round((LAT0 - lat) * K, 1)


def rings_of(geom) -> list[list[list[float]]]:
    polys = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    out = []
    for poly in polys:
        for ring in [poly.exterior, *poly.interiors]:
            pts = [list(project(x, y)) for x, y in ring.coords]
            if len(pts) > 3:
                out.append(pts)
    return out


def build_map(tolerance: float = 0.0004) -> dict:
    """Dissolve the census village polygons into local bodies (through the LGD village → body mapping, by code;
    the thirteen statutory towns by name), then bodies into talukas and districts. Simplified after the union so
    neighbours still share their edges to within the tolerance (≈ 40 m). Needs shapely at build time only."""
    import csv
    from shapely.geometry import shape
    from shapely.ops import unary_union
    from . import places
    gj = json.load(open(VILLAGES, encoding="utf-8"))
    divs = places.divisions()
    by_id = {d["id"]: d for d in divs}
    candidates: dict[str, list[str]] = {}
    for r in csv.DictReader(open(places.VILLAGE_MAP, encoding="utf-8")):
        code = r["census_village_code"]
        if not r["local_body_code"]:
            continue
        d = next((d for d in divs if str(d.get("lgd_code") or "") == r["local_body_code"]), None)
        if d is None and r["local_body_type"].startswith("Municipal"):
            d, _ = places.match_body(r["local_body_name"], r["lgd_subdistrict_name"], divs, {})
        if d and d["id"] not in candidates.setdefault(code, []):
            candidates[code].append(d["id"])
    # a polygon can be drawn once: a village in two bodies goes to the body that would otherwise have no shape, else
    # to the one named after it, else to the first (its panchayat)
    body_of_code = {c: ids[0] for c, ids in candidates.items() if len(ids) == 1}
    drawn = Counter(body_of_code.values())
    names = {f["properties"].get("village_code_2011"): f["properties"]["NAME"] for f in gj["features"]}
    for code, ids in candidates.items():
        if len(ids) == 1:
            continue
        bare = [i for i in ids if not drawn.get(i)]
        named = [i for i in ids if places._norm(names.get(code, "")) and places._norm(names.get(code, "")) in places._norm(by_id[i]["name"])]
        pick = bare[0] if len(bare) == 1 else (bare and named and next((i for i in named if i in bare), None)) or (named[0] if named else ids[0])
        body_of_code[code] = pick; drawn[pick] += 1
    geoms: dict[str, list] = {}
    unplaced = []
    for f in gj["features"]:
        pr = f["properties"]; g = shape(f["geometry"])
        code = pr.get("village_code_2011")
        bid = body_of_code.get(code) if code else None
        if not bid:
            # a statutory town polygon: by name within its taluka; Panaji's polygon is the corporation (with the
            # outgrowth villages the census never split out, so five Tiswadi panchayats have no shape of their own)
            d, how = places.match_body(pr["NAME"], pr.get("SUB_DIST", ""), divs, {})
            bid = d["id"] if d and d["kind"] != "village_panchayat" else None
            if not bid and pr["NAME"] == "Panaji":
                bid = "city-corporation-panaji"
        if not bid:
            unplaced.append(f"{pr['NAME']} ({pr.get('SUB_DIST')}, {code})"); continue
        geoms.setdefault(bid, []).append(g)
    features = []
    body_geom = {}
    for bid, gs in geoms.items():
        d = by_id[bid]
        u = unary_union(gs).buffer(0)
        body_geom[bid] = u
        c = u.representative_point()
        features.append({"id": bid, "level": "body", "kind": d["kind"], "name": d["name"], "taluka": d["taluka"], "district": d["district"],
                         "c": list(project(c.x, c.y)), "rings": rings_of(u.simplify(tolerance, preserve_topology=True))})
    for level, key in (("taluka", "taluka"), ("district", "district")):
        groups: dict[str, list] = {}
        for bid, u in body_geom.items():
            groups.setdefault(by_id[bid][key], []).append(u)
        for name, gs in sorted(groups.items()):
            u = unary_union(gs).buffer(0)
            d = places.get(places.slug(f"{level}-{name}")) or {}
            c = u.representative_point()
            features.append({"id": places.slug(f"{level}-{name}"), "level": level, "kind": level, "name": name,
                             "taluka": name if level == "taluka" else None, "district": d.get("district") if level == "taluka" else name,
                             "c": list(project(c.x, c.y)), "rings": rings_of(u.simplify(tolerance * 2, preserve_topology=True))})
    missing = [d["id"] for d in divs if d["id"] not in body_geom]
    return {"built": "2026-11-02", "units": "x east of 73.6°E and y south of 15.9°N, in thousandths of a degree (about 111 m)",
            "source": "DataMeet Indian Village Boundaries, Goa (ODbL 1.0), villages dissolved per local body through the LGD mapping",
            "features": features, "no_shape": missing, "unplaced_polygons": unplaced}


def save_map(data: dict) -> None:
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def available() -> bool:
    return os.path.exists(MAP_FILE)


def version() -> int:
    """The file's mtime: the page asks for `/twin/map.json?v=<this>`, so a rebuilt map is fetched afresh while an
    unchanged one may sit in the browser's cache for an hour."""
    return int(os.path.getmtime(MAP_FILE)) if available() else 0


from functools import lru_cache  # noqa: E402


@lru_cache(maxsize=2)
def _load(mtime: int) -> dict:
    with open(MAP_FILE, encoding="utf-8") as f:
        return json.load(f)


def load() -> dict:
    return _load(version())


def section(selected: str | None = None, heading: str = "Your village, and what it could draw on") -> str:
    """The HTML for the home page (and the twin index): heading, level control, map, hint, card."""
    if not available():
        return ""
    sel = f" data-selected=\"{selected}\"" if selected else ""
    return (f"<h2>{heading}</h2><p class=muted>Tap a place. The twin says what it is eligible for now, what it could plausibly become eligible for, and the step that gets it there.</p>"
            f"<div class=tmap id=tmap{sel} data-v={version()}><div class=lvl role=group aria-label='Map scale'>"
            "<button type=button data-level=district aria-pressed=true>Districts</button><button type=button data-level=taluka aria-pressed=false>Talukas</button>"
            "<button type=button data-level=body aria-pressed=false>Panchayats</button></div><p class=crumb></p>"
            "<svg viewBox='0 0 100 100' role=img aria-label='Map of Goa'><g class=shapes></g><g class=ctx></g><g class=marks></g></svg><p class=hint>Loading the map…</p><p class=legend></p></div>"
            "<div class='card tcard' id=tcard><p class=empty>Tap a district to begin, or switch to talukas or panchayats.</p></div>" + CSS + JS)
