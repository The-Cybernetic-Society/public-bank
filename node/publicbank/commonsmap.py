"""
The Commons on the map: where the businesses are, and which proposal connects which of them.

The front page's map has one subject, the Commons. Each registered business that told the form where it is sits
as a dot in its village panchayat or town (several in one place fan out around its centre). Two of the five
trade-off sliders from the registration flow (earn more ↔ spend less, proven ↔ new) sit above the map; moving
them picks the draft proposal that fits that lean best, by the same rule the app uses for owners (`twin.rank_for`),
and the map joins the businesses that proposal connects: the ones that back it, the one that leads it, and the
ones whose stated needs it was written from. Tapping a business shows its need and the proposals it is part of.

Nothing here is new information: business names, needs and backers are already on the public board; the
owners' own slider answers are not sent. The talukas are drawn faintly behind as context, from the same map file.
"""
from __future__ import annotations

import json
import math
from collections import Counter

from . import core, db, places, twinmap
from .twin import tags_for

AXES = ("earn", "risk")   # the two trade-offs the front page asks; the other three stay on the owner's page
RING = 9.0                # map units (≈ 1 km) between businesses that share a place


def businesses(con, pool_id: str) -> list[dict]:
    """Every business in the pool with the place it sits in; `x`, `y` in map units; `verified` as the treasurer said."""
    if not twinmap.available():
        return []
    centre = {f["id"]: f["c"] for f in twinmap.load()["features"] if f["level"] == "body" and f.get("c")}
    apps = {a["node_id"]: a for a in db.rows(con.execute("SELECT node_id, place_id, status FROM applications WHERE pool_id=?", (pool_id,)).fetchall())}
    out = []
    for k in sorted(core.children(con, pool_id), key=lambda k: k["created_at"]):
        a = apps.get(k["id"], {})
        pid = k.get("place_id") or a.get("place_id")
        need = db.row(con.execute("SELECT text FROM needs WHERE node_id=? ORDER BY created_at DESC LIMIT 1", (k["id"],)).fetchone())
        out.append({"node_id": k["id"], "name": k["name"], "place_id": pid, "place": (places.get(pid) or {}).get("name") if pid else None,
                    "verified": a.get("status") == "verified", "need": need["text"] if need else None, "x": None, "y": None,
                    "placed": bool(pid and pid in centre)})
    # fan businesses that share a place around its centre
    by_place: Counter = Counter(b["place_id"] for b in out if b["placed"])
    seen: Counter = Counter()
    for b in out:
        if not b["placed"]:
            continue
        cx, cy = centre[b["place_id"]]
        n, i = by_place[b["place_id"]], seen[b["place_id"]]
        seen[b["place_id"]] += 1
        if n == 1:
            b["x"], b["y"] = cx, cy
        else:
            ang = 2 * math.pi * i / n - math.pi / 2
            b["x"], b["y"] = round(cx + RING * math.cos(ang), 1), round(cy + RING * math.sin(ang), 1)
    return out


def proposals(con, pool_id: str, biz: list[dict]) -> list[dict]:
    """The round's proposals with their place on the two axes and the businesses each connects."""
    rnd = core.current_round(con, pool_id)
    if not rnd or rnd["status"] not in ("proposals", "ballots", "plans", "ratifying"):
        return []
    names = {b["name"]: b["node_id"] for b in biz}
    member_node: dict[str, str] = {}
    for b in biz:
        for m in core.active_members(con, b["node_id"]):
            member_node[m["id"]] = b["node_id"]
    out = []
    for p in db.rows(con.execute("SELECT * FROM proposals WHERE round_id=? AND status IN ('eligible','draft') ORDER BY created_at", (rnd["id"],)).fetchall()):
        text = p.get("text") or ""
        t = tags_for(p["title"] + " " + text)
        backers = [x for x in db.j(p["cosign_nodes"], []) if any(b["node_id"] == x for b in biz)]
        lead = member_node.get(p.get("lead_id") or "")
        built_from = [nid for name, nid in names.items() if f"- {name}:" in text]
        out.append({"id": p["id"], "title": p["title"], "ideal": p["ideal_paise"] // 100, "source": p["source"],
                    "text": text.split("\n- ")[0][:400],   # the quoted needs become chips; the prose stays
                    "tags": {a: t.get(a, 0.0) for a in AXES}, "conf": {a: t["_conf"].get(a, 0) for a in AXES},
                    "backers": backers, "lead": lead, "built_from": built_from,
                    "scheme": db.j(p.get("scheme_json"), None) if p.get("scheme_json") else None})
    return out


def data(con, pool_id: str) -> dict:
    biz = businesses(con, pool_id)
    placed = [b for b in biz if b["placed"]]
    ctx = [{"name": f["name"], "rings": f["rings"]} for f in twinmap.load()["features"] if f["level"] == "taluka"] if twinmap.available() else []
    if placed:
        xs = [b["x"] for b in placed]; ys = [b["y"] for b in placed]
        pad = max(max(xs) - min(xs), max(ys) - min(ys), 60) * 0.35
        bbox = [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad]
    else:
        m = twinmap.load() if twinmap.available() else {}
        bbox = m.get("bbox") or [0, 0, 100, 100]
    return {"pool": pool_id, "axes": [{"key": k, "left": l, "right": r, "hint": h} for k, l, r, h in core.VALUE_DIMENSIONS if k in AXES],
            "bbox": bbox, "context": ctx, "businesses": biz, "proposals": proposals(con, pool_id, biz)}


CSS = """<style>
.cmap .axes{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:0 0 10px}.cmap .axis{margin:0;padding:10px 12px}.cmap .axis .hint{display:none}
.cmap svg{width:100%;height:auto;max-height:62vh;display:block;background:var(--card);border:2px solid var(--ink);border-radius:3px;box-shadow:6px 6px 0 #20201dcc;touch-action:manipulation}
.cmap g.ctx path{fill:#f3e9d6;stroke:#20201d55;stroke-width:1px;vector-effect:non-scaling-stroke;stroke-linejoin:round}
.cmap g.links line{stroke:var(--ink);stroke-width:2px;vector-effect:non-scaling-stroke;stroke-linecap:round;stroke-dasharray:2000;stroke-dashoffset:2000;animation:cdraw .9s ease-out forwards}
.cmap g.links line.from{stroke-dasharray:6 5;stroke:#20201daa;animation:none}
.cmap g.links circle.hub{fill:var(--ink);stroke:var(--card);stroke-width:2px;vector-effect:non-scaling-stroke}.cmap g.links text{font-family:'IBM Plex Mono',monospace;font-weight:500;fill:var(--card);text-anchor:middle;dominant-baseline:central;pointer-events:none}
.cmap g.dots circle{fill:var(--gold);stroke:var(--ink);stroke-width:1.5px;vector-effect:non-scaling-stroke;cursor:pointer;transition:r .15s}.cmap g.dots circle.dim{fill:#e8dcc2;stroke:#20201d66}.cmap g.dots circle.on{fill:var(--ink)}.cmap g.dots circle.sel{stroke-width:3px}
.cmap g.dots circle.unv{stroke-dasharray:3 2}
.cmap .hint{font:400 14px 'IBM Plex Mono',monospace;color:var(--muted);margin:8px 0 0;min-height:20px}
.ccard{margin-top:16px}.ccard h3{font:500 24px/1.15 Newsreader,Georgia,serif;letter-spacing:-.015em;color:var(--ink);margin:0 0 4px}.ccard .fit{font:400 11px/1 Silkscreen,'IBM Plex Mono',monospace;text-transform:uppercase;background:var(--gold);color:var(--ink);padding:5px 8px;border-radius:8px;margin-right:6px;vertical-align:middle}
.tag.twin{background:#e4edf5;color:#2f5f8f;border:1px solid #2f5f8f55;font-weight:400}.tag.biz{background:var(--card);color:var(--muted);border:1px solid var(--line)}.ccard h3{margin-top:8px}
.ccard .m{color:var(--muted);font-size:15px;line-height:1.45;margin:6px 0}.ccard .chip{display:inline-block;border:1px solid var(--line);border-radius:8px;padding:3px 8px;margin:4px 6px 0 0;font:400 13px 'IBM Plex Mono',monospace;color:var(--ink);cursor:pointer}.ccard .chip.b{background:var(--ink);color:var(--card)}
.ccard ol.alt{list-style:none;padding:0;margin:10px 0 0;border-top:1px solid var(--line)}.ccard ol.alt li{padding:8px 0;border-bottom:1px solid var(--line);font-size:15px;cursor:pointer}.ccard ol.alt li b{font-weight:500}
.ccard a.btn{margin-top:12px}.ccard .scheme{margin:8px 0 0;padding:8px 10px;border:1px dashed var(--line);border-radius:3px;font-size:15px}
@keyframes cdraw{to{stroke-dashoffset:0}}@media(prefers-reduced-motion:reduce){.cmap g.links line{animation:none;stroke-dashoffset:0}}
</style>"""

JS = r"""<script>
(function(){
var root=document.getElementById('cmap'),twin=document.getElementById('ctwin');if(!root)return;
var svg=root.querySelector('svg'),ctx=svg.querySelector('g.ctx'),links=svg.querySelector('g.links'),dots=svg.querySelector('g.dots'),hint=root.querySelector('.hint'),mapcard=document.getElementById('cmapcard'),card=document.getElementById('ccard'),POOL=root.dataset.pool;
var D=null,sel=null,vec={},touched=false;
function rupees(n){var s=String(n),t=s.slice(-3),h=s.slice(0,-3);return '₹'+(h?h.replace(/\B(?=(\d{2})+(?!\d))/g,',')+',':'')+t}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function pos(v){return (v-5)/5}
function fit(p){var n=0,s=0;for(var k in p.conf){if(!p.conf[k]||!(k in vec))continue;n++;s+=1-Math.abs(pos(vec[k])-p.tags[k])/2}return n?s/n:0.5}
function conn(p){var ids={};p.backers.forEach(function(x){ids[x]='b'});p.built_from.forEach(function(x){if(!ids[x])ids[x]='f'});if(p.lead)ids[p.lead]='b';return ids}
function biz(id){return D.businesses.filter(function(b){return b.node_id===id})[0]}
function ranked(){return D.proposals.map(function(p){return {p:p,f:fit(p)}}).sort(function(a,b){return b.f-a.f||a.p.title.localeCompare(b.p.title)})}
function mostBacked(){var best=null,n=-1;D.proposals.forEach(function(p){var k=Object.keys(conn(p)).length;if(k>n||(k===n&&best&&p.ideal>best.ideal)){best=p;n=k}});return best}
function path(rings){return rings.map(function(r){return 'M'+r.map(function(q){return q[0].toFixed(1)+' '+q[1].toFixed(1)}).join('L')+'Z'}).join('')}
function drawBase(){var b=D.bbox;svg.setAttribute('viewBox',[b[0],b[1],b[2]-b[0],b[3]-b[1]].join(' '));ctx.innerHTML='';D.context.forEach(function(t){var p=document.createElementNS('http://www.w3.org/2000/svg','path');p.setAttribute('d',path(t.rings));var ti=document.createElementNS('http://www.w3.org/2000/svg','title');ti.textContent=t.name;p.appendChild(ti);ctx.appendChild(p)});
  dots.innerHTML='';var r=(b[2]-b[0])/45;D.businesses.forEach(function(x){if(!x.placed)return;var c=document.createElementNS('http://www.w3.org/2000/svg','circle');c.setAttribute('cx',x.x);c.setAttribute('cy',x.y);c.setAttribute('r',r);c.dataset.id=x.node_id;c.setAttribute('tabindex','0');c.setAttribute('role','button');c.setAttribute('aria-label',x.name+(x.place?', '+x.place:''));
    if(!x.verified)c.classList.add('unv');var t=document.createElementNS('http://www.w3.org/2000/svg','title');t.textContent=x.name+(x.place?' · '+x.place:'');c.appendChild(t);
    c.addEventListener('click',function(){sel=(sel===x.node_id)?null:x.node_id;render()});c.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();sel=(sel===x.node_id)?null:x.node_id;render()}});
    c.addEventListener('mouseenter',function(){hint.textContent=x.name+(x.place?' · '+x.place:'')+(x.need?' · needs: '+x.need:'')});c.addEventListener('mouseleave',function(){hint.textContent=defaultHint()});dots.appendChild(c)})}
function defaultHint(){var n=D.businesses.filter(function(x){return x.placed}).length,u=D.businesses.length-n;return n+' business'+(n!==1?'es':'')+' on the map'+(u?' · '+u+' more have not said where they are':'')+'. Tap one.'}
function drawLinks(p,ids){links.innerHTML='';dots.querySelectorAll('circle').forEach(function(c){var k=ids?ids[c.dataset.id]:null;c.classList.toggle('on',k==='b');c.classList.toggle('dim',!!ids&&!k);c.classList.toggle('sel',c.dataset.id===sel)});if(!p)return;var pts=[];for(var id in ids){var b=biz(id);if(b&&b.placed)pts.push(b)}if(!pts.length)return;
  var hx=pts.reduce(function(s,b){return s+b.x},0)/pts.length,hy=pts.reduce(function(s,b){return s+b.y},0)/pts.length,r=(D.bbox[2]-D.bbox[0])/45;
  pts.forEach(function(b){var l=document.createElementNS('http://www.w3.org/2000/svg','line');l.setAttribute('x1',hx);l.setAttribute('y1',hy);l.setAttribute('x2',b.x);l.setAttribute('y2',b.y);if(ids[b.node_id]==='f')l.setAttribute('class','from');links.appendChild(l)});
  if(pts.length>1){var h=document.createElementNS('http://www.w3.org/2000/svg','circle');h.setAttribute('cx',hx);h.setAttribute('cy',hy);h.setAttribute('r',r*0.9);h.setAttribute('class','hub');links.appendChild(h);var t=document.createElementNS('http://www.w3.org/2000/svg','text');t.setAttribute('x',hx);t.setAttribute('y',hy);t.setAttribute('font-size',r*1.1);t.textContent=pts.length;links.appendChild(t)}}
function chips(ids,label){var names=[];for(var id in ids){var b=biz(id);if(b)names.push('<span class="chip'+(ids[id]==='b'?' b':'')+'" data-id="'+esc(id)+'">'+esc(b.name)+'</span>')}return names.length?'<p class=m>'+label+'<br>'+names.join('')+'</p>':''}
function schemeLine(p){if(!p.scheme)return '';return '<p class=scheme>A scheme could carry part of this: <b>'+esc(p.scheme.name)+'</b>'+(p.scheme.share?': '+esc(p.scheme.share):'')+(p.scheme.url?' · <a href="'+esc(p.scheme.url)+'">read it</a>':'')+'</p>'}
function wire(el){el.querySelectorAll('.chip').forEach(function(c){c.addEventListener('click',function(){sel=c.dataset.id;render();root.scrollIntoView({behavior:'smooth',block:'start'})})});
  el.querySelectorAll('li[data-p]').forEach(function(li){li.addEventListener('click',function(){var p=D.proposals.filter(function(x){return x.id===li.dataset.p})[0];if(!p)return;sel=null;drawLinks(p,conn(p));hint.textContent='Showing '+p.title+' on the map';root.scrollIntoView({behavior:'smooth',block:'start'})})});
  var bk=el.querySelector('#cback');if(bk)bk.addEventListener('click',function(e){e.preventDefault();sel=null;render()})}
function render(){var R=ranked(),top=R[0];
  if(sel){var b=biz(sel),mine=D.proposals.filter(function(p){return p.backers.indexOf(sel)>=0||p.lead===sel||p.built_from.indexOf(sel)>=0});var ids={};ids[sel]='b';mine.forEach(function(p){var c=conn(p);for(var k in c)if(!ids[k])ids[k]=c[k]});
    drawLinks(mine[0]||null,ids);
    mapcard.innerHTML='<h3>'+esc(b.name)+'</h3><p class=m>'+esc(b.place||'place not given')+(b.verified?' · verified':' · registered, not yet verified')+'</p>'+(b.need?'<p><b>Needs:</b> '+esc(b.need)+'</p>':'')+(mine.length?'<p class=m>Part of '+mine.length+' proposal'+(mine.length>1?'s':'')+':</p><ol class=alt>'+mine.map(function(p){return '<li data-p="'+esc(p.id)+'"><b>'+esc(p.title)+'</b> <span class=muted>about '+rupees(p.ideal)+'</span></li>'}).join('')+'</ol>':'<p class=m>Not part of any proposal yet.</p>')+'<a class="btn quiet" href="#" id=cback>Back to the map</a>';
    hint.textContent=b.name+(mine.length?' is connected to '+(Object.keys(ids).length-1)+' other business'+(Object.keys(ids).length!==2?'es':''):'')}
  else{var p=touched&&top?top.p:mostBacked();if(!p){drawLinks(null,null);mapcard.innerHTML='<p class=muted>No proposals on the table yet.</p>'}else{var ids=conn(p),n=Object.keys(ids).length;drawLinks(p,ids);
    mapcard.innerHTML='<p class=m style="margin:0">'+(touched?'For the lean set below, the twin would show first:':'Most backed on the table:')+'</p>'+(p.source==='twin'?'<span class="tag twin">AI twin</span>':'')+'<h3>'+esc(p.title)+'</h3><p class=m>about '+rupees(p.ideal)+(p.source==='twin'?' · suggested from what businesses said they need':' · proposed by a business')+'</p>'+chips(ids,n?'Connects '+n+' business'+(n>1?'es':'')+' (filled: backing or leading; outlined: written from their need):':'')+'<a class=btn href="/apply/'+esc(POOL)+'?for='+esc(p.id)+'">Back this with my business</a>';
    hint.textContent=n?'This proposal connects '+n+' business'+(n>1?'es':'')+' on the map':defaultHint()}}
  if(card){if(!top){card.innerHTML='<p class=muted>No proposals on the table yet.</p>'}else{var q=top.p;card.innerHTML='<span class=fit>fit '+Math.round(top.f*100)+'%</span>'+(q.source==='twin'?'<span class="tag twin">AI twin</span>':'<span class="tag biz">a business</span>')+'<span class=muted> best of '+R.length+' for this lean</span><h3>'+esc(q.title)+'</h3><p class=m>about '+rupees(q.ideal)+(q.source==='twin'?' · suggested from what businesses said they need':' · proposed by a business')+'</p>'+(q.text?'<p>'+esc(q.text)+'</p>':'')+schemeLine(q)
      +(R.length>1?'<p class=m style="margin-top:12px">Next for this lean:</p><ol class=alt>'+R.slice(1,4).map(function(x){return '<li data-p="'+esc(x.p.id)+'"><b>'+esc(x.p.title)+'</b> <span class=muted>fit '+Math.round(x.f*100)+'% · about '+rupees(x.p.ideal)+'</span></li>'}).join('')+'</ol>':'')}}
  wire(mapcard);if(card)wire(card);
  try{history.replaceState(null,'','#'+(touched?'commons='+Object.keys(vec).map(function(k){return k+'='+vec[k]}).join(','):'')+(sel?'&biz='+encodeURIComponent(sel):''))}catch(e){}}
if(twin)twin.querySelectorAll('.axis input').forEach(function(i){var o=i.closest('.axis').querySelector('output');function f(){var v=+i.value,c=v*10;i.style.setProperty('--a',Math.min(c,50)+'%');i.style.setProperty('--b',Math.max(c,50)+'%');o.textContent=v<5?'← '+o.dataset.l+(v<=1?', strongly':''):v>5?o.dataset.r+(v>=9?', strongly':'')+' →':'either';vec[i.dataset.axis]=v}i.addEventListener('input',function(){touched=true;f();if(D)render()});f()});
var m=/commons=([^&]*)/.exec(location.hash),bm=/biz=([^&]*)/.exec(location.hash);if(m&&m[1]){touched=true;m[1].split(',').forEach(function(kv){var a=kv.split('=');var i=twin&&twin.querySelector('.axis input[data-axis="'+a[0]+'"]');if(i&&a[1]!==undefined){i.value=a[1];vec[a[0]]=+a[1];i.dispatchEvent(new Event('input'))}})}if(bm)sel=decodeURIComponent(bm[1]);
fetch('/commons/'+encodeURIComponent(POOL)+'/map.json').then(function(r){return r.json()}).then(function(d){D=d;drawBase();render()}).catch(function(){hint.textContent='The map could not load.'});
})();
</script>"""


def map_section(pool_id: str, con=None) -> str:
    """Where the Commons is: the businesses as dots, the most-backed proposal's connections drawn, a tap for any
    business. No controls: the sliders live in the twin section below, and drive the same map. The SVG gets its
    final proportions here, server-side, so the page below it does not move when the data lands."""
    vb = "0 0 100 140"
    if con is not None:
        try:
            b = data(con, pool_id)["bbox"]
            vb = f"{b[0]:.1f} {b[1]:.1f} {b[2] - b[0]:.1f} {b[3] - b[1]:.1f}"
        except Exception:
            pass
    return ("<h2>Where the Commons is</h2><p class=muted>Every registered business sits in its village or town. The lines join the businesses the most-backed "
            "proposal brings together. Tap a business to see what it needs and which proposals it is part of.</p>"
            f"<div class=cmap id=cmap data-pool='{pool_id}'>"
            f"<svg viewBox='{vb}' role=img aria-label='Map of the Commons'><g class=ctx></g><g class=links></g><g class=dots></g></svg><p class=hint>Loading…</p></div>"
            "<div class='card ccard' id=cmapcard><p class=muted>…</p></div>" + CSS + JS)


def twin_section() -> str:
    """The digital twin and the AI that drafts from it, in two short paragraphs."""
    return ("<h2>What is an AI digital twin?</h2>"
            "<p>Behind the page is a <b>digital twin</b> of Goan businesses: a record of each business, what it said it needs, where it sits, and government schemes that could help support it, drawn from 770 Goa schemes read against every village and town.</p>"
            "<p>An <b>AI drafts proposals</b> from the needs: when two shops say they need a fridge for the season, it writes one proposal for a shared cold store, "
            "prices it for Goa, and notes the scheme that could carry part of it. It never decides anything. A draft reaches the ballot only when a business leads "
            "it and others back it, and every proposal on this page says whether a person or the twin wrote it.</p>")
