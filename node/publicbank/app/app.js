/* The Commons from the inside: sign in with a WhatsApp code, then one picture of your business in the round, the twin's
   questions, the possibility space, the table, the ballot, your own idea (typed or spoken), and a line to the twin.
   Everything comes from one call, /me/home.json, and every action answers with the same picture refreshed. */
(function(){
  'use strict';
  var BASE='';   // the page is served by the Public Bank app itself, so the API is this origin
  try{var ov=new URLSearchParams(location.search).get('api');if(ov!=null)localStorage.setItem('pb_api',ov);var sv=localStorage.getItem('pb_api');if(sv!=null)BASE=sv;}catch(e){}   // ?api=<origin> (or ?api= for this origin) points the page at another Public Bank app, for previews
  var RM=matchMedia('(prefers-reduced-motion: reduce)').matches;
  var S={token:null,home:null,phone:'',cc:'91',axX:'earn',axY:'scope',filter:'all',hot:null,history:[],backs:0,node:null,busy:false};
  var ERR={sign_in:'Sign in again.',bad_phone:'That does not look like a phone number.',too_many_codes:'Three codes an hour is the limit for one number. Try again later.',
    code_expired:'That code has expired. Send a fresh one.',bad_code:'Not that code.',too_many_attempts:'Five wrong guesses: that code is dead. Send a fresh one.',
    points_must_sum_100:'The points must add up to exactly 100.',unknown_proposal:'The list changed. Reload and try again.',proposals_closed:'Proposals are closed for this round.',
    ballots_closed:'Ballots are closed.',ranking_closed:'Ranking is closed.',ratification_closed:'Approval is closed.',not_coordinator:'Only the business’s coordinator can do that.',
    node_votes_by_coordinator:'In this Commons each business votes once, through its coordinator.',not_a_member:'This number is not in a business here yet.',
    bad_amounts:'That amount does not work.',bad_text:'A few more words are needed.',no_open_round:'No round is open right now.',wrong_stage:'The round is not at that stage.'};

  // ---------------------------------------------------------------- small helpers
  function $(id){return document.getElementById(id);}
  function h(tag,attrs,kids){var e=document.createElement(tag);attrs=attrs||{};Object.keys(attrs).forEach(function(k){
      if(k==='class')e.className=attrs[k];else if(k==='text')e.textContent=attrs[k];else if(k==='html')e.innerHTML=attrs[k];else if(k.slice(0,2)==='on')e.addEventListener(k.slice(2),attrs[k]);else if(attrs[k]!=null)e.setAttribute(k,attrs[k]);});
    (kids||[]).forEach(function(c){if(c==null)return;e.appendChild(typeof c==='string'?document.createTextNode(c):c);});return e;}
  function svg(tag,attrs,kids){var e=document.createElementNS('http://www.w3.org/2000/svg',tag);attrs=attrs||{};Object.keys(attrs).forEach(function(k){if(k==='text')e.textContent=attrs[k];else if(k.slice(0,2)==='on')e.addEventListener(k.slice(2),attrs[k]);else e.setAttribute(k,attrs[k]);});(kids||[]).forEach(function(c){if(c)e.appendChild(c);});return e;}
  function indian(n){var x=String(Math.round(n));if(x.length<=3)return x;var last=x.slice(-3),rest=x.slice(0,-3);return rest.replace(/\B(?=(\d{2})+(?!\d))/g,',')+','+last;}
  function rs(n){return '₹'+indian(n||0);}
  function friendly(code,detail){var t=ERR[code]||('That did not work ('+String(code||'error').replace(/_/g,' ')+').');if(code==='bad_code'&&detail)t+=' '+detail+(detail==='1'?' guess':' guesses')+' left.';return t;}
  function toast(text){var t=$('toast');t.textContent=text;t.classList.add('on');clearTimeout(t._t);t._t=setTimeout(function(){t.classList.remove('on');},2600);}
  function showErr(id,text){var e=$(id);e.textContent=text;e.classList.remove('on');void e.offsetWidth;e.classList.add('on');}
  function hideErr(id){$(id).classList.remove('on');}
  function show(view){['v-phone','v-code','v-home'].forEach(function(v){$(v).hidden=v!==view;});$('nav').hidden=view!=='v-home';$('who').hidden=view!=='v-home';$('who-out').hidden=view==='v-home';window.scrollTo(0,0);}
  function api(path,body){
    var hd={'Content-Type':'application/json'};if(S.token)hd.Authorization='Bearer '+S.token;
    return fetch(BASE+path,{method:body?'POST':'GET',headers:hd,body:body?JSON.stringify(body):undefined}).then(function(r){
      return r.json().catch(function(){return {};}).then(function(j){
        if(r.status===401&&S.token&&path.indexOf('/me/')===0){signout(true);throw new Error('Your sign-in has lapsed. Sign in again.');}
        if(!r.ok)throw new Error(friendly(j.error,j.detail));return j;});
    });
  }
  function countUp(el,to,prefix,ind){var from=+(el.getAttribute('data-v')||0);el.setAttribute('data-v',to);if(RM||from===to){el.textContent=(prefix||'')+(ind?indian(to):to);return;}
    var t0=null,dur=1100;function f(t){if(!t0)t0=t;var k=Math.min(1,(t-t0)/dur);k=1-Math.pow(1-k,3);el.textContent=(prefix||'')+(ind?indian(Math.round(from+(to-from)*k)):Math.round(from+(to-from)*k));if(k<1)requestAnimationFrame(f);}requestAnimationFrame(f);}
  function pop(id){var w=$(id);if(!w)return;w.classList.remove('did');void w.offsetWidth;w.classList.add('did');}
  function stamp(parent,text){var s=h('span',{class:'stamp',text:text});parent.appendChild(s);requestAnimationFrame(function(){s.classList.add('on');});return s;}

  // confetti: a short burst of paper, gold and teal, then gone
  function confetti(n){if(RM)return;var c=$('confetti'),ctx=c.getContext('2d'),W=c.width=innerWidth,H=c.height=innerHeight,ps=[],cols=['#d9a33d','#197c83','#20201d','#bd4c3c','#fbf6e9','#47724c'];
    for(var i=0;i<(n||120);i++)ps.push({x:W/2+(Math.random()-.5)*W*.3,y:H*.35,vx:(Math.random()-.5)*14,vy:-Math.random()*14-4,r:Math.random()*6+3,c:cols[i%cols.length],a:Math.random()*6,s:(Math.random()-.5)*.3,life:1});
    var t0=null;function f(t){if(!t0)t0=t;var k=(t-t0)/1800;ctx.clearRect(0,0,W,H);ps.forEach(function(p){p.x+=p.vx;p.y+=p.vy;p.vy+=.35;p.vx*=.99;p.a+=p.s;ctx.save();ctx.translate(p.x,p.y);ctx.rotate(p.a);ctx.globalAlpha=Math.max(0,1-k);ctx.fillStyle=p.c;ctx.fillRect(-p.r/2,-p.r/2,p.r,p.r*1.6);ctx.restore();});if(k<1)requestAnimationFrame(f);else ctx.clearRect(0,0,W,H);}requestAnimationFrame(f);}

  // ---------------------------------------------------------------- sign in
  try{S.token=localStorage.getItem('pb_token');S.phone=localStorage.getItem('pb_phone')||'';S.cc=localStorage.getItem('pb_cc')||'91';}catch(e){}
  $('phone').value=S.phone;$('cc').value=S.cc;
  $('f-phone').addEventListener('submit',function(e){
    e.preventDefault();hideErr('phone-err');var phone=$('phone').value.replace(/\D/g,''),cc=$('cc').value;
    if(phone.length<6){showErr('phone-err','The number your business registered with, please.');return;}
    var b=e.target.querySelector('.send');b.disabled=true;b.textContent='Sending…';
    api('/auth/wa/start',{phone:phone,cc:cc}).then(function(j){
      S.phone=phone;S.cc=cc;try{localStorage.setItem('pb_phone',phone);localStorage.setItem('pb_cc',cc);}catch(x){}
      $('code-to').textContent='+'+cc+' '+(j.to||phone).replace(/•/g,'·');$('code-dev').hidden=j.via!=='log';$('code-dev').textContent=j.code?'Local development: no WhatsApp number is connected, so here is the code: '+j.code:'This server has no WhatsApp number connected yet, so the code is in its log. (Local development.)';
      $('code-wa').textContent='';if(j.wa_me){$('code-wa').appendChild(document.createTextNode(' · '));$('code-wa').appendChild(h('a',{href:j.wa_me,target:'_blank',rel:'noreferrer',text:'Message LOGIN to us on WhatsApp'}));}
      $('boxes').className='boxes';[].forEach.call($('boxes').children,function(i){i.value='';});show('v-code');setTimeout(function(){$('boxes').children[0].focus();},80);
    }).catch(function(er){showErr('phone-err',er.message);}).then(function(){b.disabled=false;b.textContent='Send the code on WhatsApp';});
  });
  var boxes=[].slice.call($('boxes').children);
  boxes.forEach(function(inp,i){
    inp.addEventListener('input',function(){var v=inp.value.replace(/\D/g,'');if(v.length>1){fill(v,i);return;}inp.value=v;inp.classList.toggle('hit',!!v);if(v&&i<5)boxes[i+1].focus();if(code().length===6)$('f-code').requestSubmit();});
    inp.addEventListener('keydown',function(e){if(e.key==='Backspace'&&!inp.value&&i>0){boxes[i-1].value='';boxes[i-1].classList.remove('hit');boxes[i-1].focus();}});
    inp.addEventListener('paste',function(e){var t=(e.clipboardData||window.clipboardData).getData('text');if(t){e.preventDefault();fill(t.replace(/\D/g,''),0);}});
  });
  function fill(v,from){for(var k=0;k<v.length&&from+k<6;k++){boxes[from+k].value=v[k];boxes[from+k].classList.add('hit');}boxes[Math.min(5,from+v.length)].focus();if(code().length===6)$('f-code').requestSubmit();}
  function code(){return boxes.map(function(b){return b.value;}).join('');}
  $('f-code').addEventListener('submit',function(e){
    e.preventDefault();hideErr('code-err');if(code().length<6){showErr('code-err','All six digits, please.');return;}
    var b=e.target.querySelector('.send');b.disabled=true;b.textContent='Checking…';$('boxes').className='boxes';
    api('/auth/wa/verify',{phone:S.phone,cc:S.cc,code:code()}).then(function(j){
      $('boxes').classList.add('good');S.token=j.token;try{localStorage.setItem('pb_token',j.token);}catch(x){}
      setTimeout(function(){enter(true);},RM?0:450);
    }).catch(function(er){$('boxes').classList.add('bad');showErr('code-err',er.message);boxes.forEach(function(x){x.value='';x.classList.remove('hit');});boxes[0].focus();}).then(function(){b.disabled=false;b.textContent='Sign in';});
  });
  $('code-again').addEventListener('click',function(e){e.preventDefault();$('f-phone').requestSubmit();});
  $('code-back').addEventListener('click',function(e){e.preventDefault();show('v-phone');$('phone').focus();});
  $('signout').addEventListener('click',function(e){e.preventDefault();signout();toast('Signed out');});
  function signout(quiet){S.token=null;S.home=null;S.history=[];try{localStorage.removeItem('pb_token');}catch(x){}if(!quiet)fetch(BASE+'/me/signout',{method:'POST'}).catch(function(){});document.body.classList.remove('live');$('who').querySelector('span').textContent='YOUR COMMONS';show('v-phone');}

  function enter(fresh){
    show('v-home');$('hello-h').textContent='One moment…';
    api('/me/home.json'+(S.node?'?node='+encodeURIComponent(S.node):'')).then(function(hm){render(hm);if(fresh){confetti(140);toast('You’re in');}}).catch(function(er){toast(er.message);});
  }
  if(S.token)enter(false);else{api('/me/home.json').then(function(hm){show('v-home');render(hm);}).catch(function(){show('v-phone');});}

  // ---------------------------------------------------------------- the picture
  function act(path,body,after){
    if(S.busy)return Promise.resolve();S.busy=true;if(S.node)body.node=S.node;
    return api(path,body).then(function(hm){render(hm);if(after)after(hm);}).catch(function(er){toast(er.message);}).then(function(){S.busy=false;});
  }
  function render(hm){
    S.home=hm;document.body.classList.toggle('live',!!(hm.round&&['proposals','ballots','plans','ratifying'].indexOf(hm.round.stage)>-1));
    $('who').querySelector('span').textContent=((hm.member.display||'')+(hm.business?' · '+hm.business.name:'')).toUpperCase()||'YOUR COMMONS';$('initials').textContent=initials(hm);
    $('asof').textContent=hm.as_of?'as of '+hm.as_of+' IST':'';
    renderHello(hm);renderAsk(hm);renderSpace(hm);renderTable(hm);renderBiz(hm);renderMap(hm);renderChips(hm);renderLean(hm);renderGoa(hm);if($('d-profile').open)renderProfile(hm);
    if(!S.history.length)greet(hm);
  }

  function renderHello(hm){
    var b=hm.business,r=hm.round,f=hm.fund;
    $('hello-k').textContent=hm.pool?hm.pool.name:'Signed in';
    var first=(hm.member.display||'').split(' ')[0];
    $('hello-h').textContent=b?('Hello'+(first?', '+first:'')+'. '+b.name+' is in.'):'Hello'+(first?', '+first:'')+'.';
    $('hello-p').textContent=!b?hm.note||'':!r?'No round is open right now. The twin still listens: say what your business needs below.':
      (r.stage==='proposals'?'Ideas are on the table until the vote. Back the ones your business would use, lead one if you’ll organise it, or add your own.':
       r.stage==='ballots'?'It’s time to vote: split 100 points across the list. You can change your ballot until ballots close.':
       r.stage==='plans'?'The votes are in and four plans are on the table. Rank them, best first.':
       r.stage==='ratifying'?'One plan won. Approve it or reject it; it stands only when enough businesses say yes.':
       r.stage==='ratified'?'The plan is approved. The treasurer is paying the vendors directly.':
       r.stage==='failed'?'The plan did not reach the threshold, so no money moves this round.':'The round has paid out. Every rupee is on the public board.');
    var st=$('stage');st.textContent='';if(r){st.appendChild(h('span',{class:'now'},[h('i'),r.stage_word]));st.appendChild(h('span',{class:'next',text:'Next: '+r.next+'.'}));}
    var fund=$('fund'),pot=Math.max(1,f.pot||0),pct=function(v){return Math.min(100,100*v/pot).toFixed(1)+'%';};
    fund.style.setProperty('--paid',pct(f.paid||0));fund.style.setProperty('--approved',pct(f.approved||0));fund.style.setProperty('--asked',pct(Math.min(f.asked||0,f.remaining||0)));
    var lg=$('fund-lg');lg.textContent='';
    lg.appendChild(h('span',{html:'<i style="background:repeating-linear-gradient(135deg,#d9a33d 0 4px,transparent 4px 9px)"></i>'},['asked for '+rs(f.asked)]));
    lg.appendChild(h('span',{html:'<i></i>'},['left '+rs(f.remaining)]));lg.appendChild(h('span',{text:'paid '+rs(f.paid)+' · approved '+rs(f.approved)}));
    var facts=$('facts');
    if(!facts.children.length){[['pot','in the fund'],['shops','businesses in'],['ideas','ideas on the table'],['backed','backed by you']].forEach(function(x){facts.appendChild(h('div',{},[h('b',{id:'fact-'+x[0],text:'0'}),x[1]]));});}
    countUp($('fact-pot'),f.pot||0,'₹',true);countUp($('fact-shops'),hm.pool?hm.pool.shops:0);countUp($('fact-ideas'),hm.proposals.length);countUp($('fact-backed'),hm.proposals.filter(function(p){return p.backed_by_me||p.lead_is_me;}).length);
    var tl=$('tl');tl.textContent='';hm.timeline.forEach(function(t){var d=new Date(t.date+'T00:00:00');
      tl.appendChild(h('li',{class:t.state==='done'?'done':t.state==='next'?'now':''},[h('time',{text:d.toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'})}),h('div',{},[h('b',{text:t.label}),t.state==='next'?h('em',{text:'next'}):null,h('span',{text:t.note})])]));});
  }

  // ---------------------------------------------------------------- the twin asks
  function renderAsk(hm){
    var box=$('ask-in'),q=hm.questions[0],old=box.firstElementChild;
    function draw(){box.textContent='';var card=h('div',{class:'q'+(q?'':' none')});
      card.appendChild(h('div',{class:'who'},[h('i'),'The twin asks']));
      if(!q){card.appendChild(h('p',{class:'text',text:hm.business?'Nothing to ask right now. I’ll have more as the round moves; until then, the table below is yours.':'Register your business on the Commons first, then I’ll have questions.'}));box.appendChild(card);return;}
      if(hm.questions.length>1)card.appendChild(h('span',{class:'count',text:'1 of '+hm.questions.length}));
      card.appendChild(h('p',{class:'text',text:q.text}));
      if(q.kind==='charter'){card.appendChild(h('ol',{class:'rules'},q.lines.map(function(l){return h('li',{text:l});})));
        card.appendChild(h('div',{class:'acts'},[h('button',{class:'btn primary small',type:'button',text:'Yes, agree for '+hm.business.name,onclick:function(){done(card,'AGREED',function(){return act('/me/charter',{});});}}),
          h('button',{class:'btn small ghost',type:'button',text:'Not now',onclick:function(){skip(card);}})]));}
      else if(q.kind==='sliders'){var vec={};var sl=h('div',{class:'sliders'});q.axes.forEach(function(a){vec[a.key]=5;var L=h('span',{text:a.left}),R=h('span',{text:a.right}),inp=h('input',{type:'range',min:0,max:10,value:5,'aria-label':a.left+' or '+a.right});
          inp.addEventListener('input',function(){vec[a.key]=+inp.value;L.classList.toggle('on',+inp.value<5);R.classList.toggle('on',+inp.value>5);});
          sl.appendChild(h('div',{class:'sl'},[h('div',{class:'poles'},[L,R]),inp,h('div',{class:'h',text:a.hint})]));});
        card.appendChild(sl);card.appendChild(h('div',{class:'acts'},[h('button',{class:'btn primary small',type:'button',text:'That’s us',onclick:function(){done(card,'NOTED',function(){return act('/me/values',{vector:vec,phase:q.phase||'before'});});}})]));}
      else if(q.kind==='text'){var ta=h('textarea',{rows:2,maxlength:300,placeholder:q.placeholder||'',name:'ans'}),mic=h('button',{type:'button',class:'mic','aria-label':'Dictate'});wireMic(mic,ta);
        card.appendChild(h('span',{class:'mic-wrap'},[ta,mic]));
        card.appendChild(h('div',{class:'acts'},[h('button',{class:'btn primary small',type:'button',text:'Tell the twin',onclick:function(){var v=ta.value.trim();if(v.length<3){ta.focus();return;}
            done(card,'NOTED',function(){return q.key==='need'?act('/me/need',{text:v}):act('/me/twin/answer',{key:q.key,answer:v});});}}),h('button',{class:'btn small ghost',type:'button',text:'Skip',onclick:function(){skip(card);}})]));}
      else if(q.kind==='yesno'){card.appendChild(h('div',{class:'acts'},[h('button',{class:'btn primary small',type:'button',text:'Yes, we’d use it',onclick:function(){done(card,'BACKED',function(){return act('/me/twin/answer',{key:q.key,answer:'yes'},function(){S.backs++;if(S.backs===1)confetti(80);toast('Backed: '+q.title);});});}}),
          h('button',{class:'btn small',type:'button',text:'Not for us',onclick:function(){done(card,'NOTED',function(){return act('/me/twin/answer',{key:q.key,answer:'no'});});}})]));}
      box.appendChild(card);}
    if(old&&!RM&&old.getAttribute('data-key')!==(q?q.key:'none')){old.classList.add('out');setTimeout(draw,330);}else draw();
    if(box.firstElementChild)box.firstElementChild.setAttribute('data-key',q?q.key:'none');
  }
  var skipped={};
  function skip(card){var hm=S.home;skipped[hm.questions[0].key]=1;hm.questions=hm.questions.filter(function(x){return !skipped[x.key];});card.classList.add('out');setTimeout(function(){renderAsk(hm);},RM?0:330);}
  function done(card,word,go){card.querySelectorAll('button').forEach(function(b){b.disabled=true;});stamp(card,word);setTimeout(function(){go();},RM?0:520);}

  // ---------------------------------------------------------------- the possibility space
  var axSel={x:$('ax-x'),y:$('ax-y')};
  function renderSpace(hm){
    ['x','y'].forEach(function(k){var sel=axSel[k];if(!sel.children.length){hm.axes.forEach(function(a){sel.appendChild(h('option',{value:a.key,text:a.left+' ↔ '+a.right}));});sel.value=k==='x'?S.axX:S.axY;sel.addEventListener('change',function(){S[k==='x'?'axX':'axY']=sel.value;renderSpace(S.home);});}});
    var plot=$('plot');plot.textContent='';var W=400,H=328,m=34,ax=hm.axes.filter(function(a){return a.key===S.axX;})[0],ay=hm.axes.filter(function(a){return a.key===S.axY;})[0];
    var s=svg('svg',{viewBox:'0 0 '+W+' '+H,role:'img','aria-label':'The ideas on the table placed on two trade-offs'});
    var g=svg('g',{class:'grid'});for(var i=1;i<8;i++){g.appendChild(svg('line',{x1:m+(W-2*m)*i/8,y1:m,x2:m+(W-2*m)*i/8,y2:H-m}));g.appendChild(svg('line',{x1:m,y1:m+(H-2*m)*i/8,x2:W-m,y2:m+(H-2*m)*i/8}));}s.appendChild(g);
    s.appendChild(svg('line',{class:'axis',x1:W/2,y1:m,x2:W/2,y2:H-m}));s.appendChild(svg('line',{class:'axis',x1:m,y1:H/2,x2:W-m,y2:H/2}));
    s.appendChild(svg('text',{x:m,y:H-10,text:ax.left}));s.appendChild(svg('text',{x:W-m,y:H-10,'text-anchor':'end',text:ax.right}));
    s.appendChild(svg('text',{x:8,y:H-m,transform:'rotate(-90 8 '+(H-m)+')',text:ay.left}));s.appendChild(svg('text',{x:8,y:m,transform:'rotate(-90 8 '+m+')','text-anchor':'end',text:ay.right}));
    function X(v){return m+(W-2*m)*(v+1)/2;}function Y(v){return H-m-(H-2*m)*(v+1)/2;}
    function jit(id,k){var hsh=0;for(var i=0;i<id.length;i++)hsh=(hsh*31+id.charCodeAt(i))%9973;return ((hsh%17)/17-.5)*k;}
    var v=hm.business&&hm.business.values;
    if(v){var mx=X((v[S.axX]-5)/5),my=Y((v[S.axY]-5)/5);s.appendChild(svg('g',{class:'me-cross'},[svg('line',{x1:mx-14,y1:my,x2:mx+14,y2:my}),svg('line',{x1:mx,y1:my-14,x2:mx,y2:my+14}),svg('circle',{cx:mx,cy:my,r:9}),svg('text',{x:mx+12,y:my-10,text:'you lean here'})]));}
    hm.proposals.forEach(function(p,i){var tx=p.spoke.indexOf(S.axX)>-1?p.tags[S.axX]:0,ty=p.spoke.indexOf(S.axY)>-1?p.tags[S.axY]:0;
      var cx=X(tx)+jit(p.id,46),cy=Y(ty)+jit(p.id+'y',40),r=Math.max(6,Math.min(22,Math.sqrt(p.ideal)/14));
      var cls='p'+(p.lead_is_me?' lead':p.backed_by_me?' me':p.source==='twin'?' ai':'')+(S.hot&&S.hot!==p.id?' dim':'')+(S.hot===p.id?' hot':'')+(S.born&&S.born[p.id]?' born':'');
      var gp=svg('g',{class:cls,tabindex:0,role:'button','aria-label':p.title,onclick:function(){focusProp(p.id);},onkeydown:function(e){if(e.key==='Enter')focusProp(p.id);}},[svg('circle',{class:'halo',cx:cx,cy:cy,r:r+4}),svg('circle',{cx:cx,cy:cy,r:r}),svg('text',{x:cx,y:cy-r-5,'text-anchor':'middle',text:p.title.length>34?p.title.slice(0,32)+'…':p.title})]);
      gp.appendChild(svg('title',{text:p.title+' · '+rs(p.ideal)+' · '+p.backers.length+' backing'}));s.appendChild(gp);});
    plot.appendChild(s);S.born=null;
  }
  function focusProp(id){S.hot=S.hot===id?null:id;renderSpace(S.home);renderMap(S.home);
    document.querySelectorAll('.prop').forEach(function(el){el.classList.toggle('hot',el.getAttribute('data-id')===S.hot);});
    var el=document.querySelector('.prop[data-id="'+id+'"]');if(el&&S.hot){el.classList.add('expanded');el.scrollIntoView({behavior:RM?'auto':'smooth',block:'center'});}}

  // ---------------------------------------------------------------- the table, the ballot, the plans, the approval
  function renderTable(hm){
    var box=$('table-in'),r=hm.round;box.textContent='';
    $('table-title').textContent=!r?'ON.THE.TABLE':r.stage==='ballots'?'YOUR.BALLOT':r.stage==='plans'?'FOUR.PLANS':r.stage==='ratifying'?'THE.PLAN':r.stage==='proposals'?'ON.THE.TABLE':'THE.RESULT';
    if(!r){box.appendChild(h('p',{class:'empty',text:'No round is open. When one opens, the ideas will be here.'}));return;}
    if(r.stage==='proposals')return tableProposals(hm,box);
    if(r.stage==='ballots')return tableBallot(hm,box);
    return tablePlans(hm,box);
  }
  function propCard(p,i,hm,extra){
    var card=h('div',{class:'prop'+(p.backed_by_me||p.lead_is_me?' me':'')+(S.hot===p.id?' hot':''),'data-id':p.id,style:'animation-delay:'+Math.min(i,10)*0.05+'s'});
    var t=h('b',{class:'t',text:p.title});if(p.source==='twin')t.appendChild(h('span',{class:'pill',text:'AI draft'}));if(p.suggested)t.appendChild(h('span',{class:'pill you',text:'for you'}));if(p.status==='eligible'&&hm.round.stage==='proposals')t.appendChild(h('span',{class:'pill vote',text:'to the vote'}));
    card.appendChild(h('div',{class:'top'},[h('span',{class:'n',text:(i<9?'0':'')+(i+1)}),h('div',{},[t]),h('span',{class:'amt',text:rs(p.ideal)})]));
    var meta=h('p',{class:'meta'});
    if(p.backers.length){meta.appendChild(document.createTextNode('backed by '));meta.appendChild(h('b',{text:p.backers.join(', ')}));}else meta.appendChild(document.createTextNode('nobody backs it yet'));
    if(p.lead){meta.appendChild(document.createTextNode(' · led by '));meta.appendChild(h('b',{text:p.lead_is_me?'you':p.lead}));}else if(hm.round.stage==='proposals')meta.appendChild(document.createTextNode(' · no lead yet'));
    if(p.built_from.length)meta.appendChild(document.createTextNode(' · drafted from what '+p.built_from.join(' and ')+' said'));
    card.appendChild(meta);
    if(p.scheme&&p.scheme.name)card.appendChild(h('p',{class:'scheme',text:'A scheme could carry part of this: '+p.scheme.name+(p.scheme.pays?' ('+p.scheme.pays+')':'')}));
    if(p.text&&p.text!==p.title)card.appendChild(h('p',{class:'more',text:p.text}));
    var acts=h('div',{class:'acts'});if(extra)extra(acts,card);
    if(p.text&&p.text!==p.title)acts.appendChild(h('button',{type:'button',class:'lnk',text:'more',onclick:function(){card.classList.toggle('expanded');this.textContent=card.classList.contains('expanded')?'less':'more';}}));
    card.appendChild(acts);card.addEventListener('click',function(e){if(e.target.closest('button'))return;focusProp(p.id);});
    return card;
  }
  function tableProposals(hm,box){
    var fl=h('div',{class:'filters'});[['all','All'],['you','For you'],['mine','Backed by you'],['ai','AI drafts'],['people','From businesses']].forEach(function(f){fl.appendChild(h('button',{type:'button',class:S.filter===f[0]?'on':'',text:f[1],onclick:function(){S.filter=f[0];tableProposals(hm,box);}}));});
    box.textContent='';box.appendChild(fl);
    var list=hm.proposals.filter(function(p){return S.filter==='all'||(S.filter==='you'&&p.suggested)||(S.filter==='mine'&&(p.backed_by_me||p.lead_is_me))||(S.filter==='ai'&&p.source==='twin')||(S.filter==='people'&&p.source!=='twin');});
    if(!list.length){box.appendChild(h('p',{class:'empty',text:hm.proposals.length?'Nothing under this filter.':'Nothing on the table yet. Say what your business needs and the twin drafts the first idea.'}));return;}
    var ps=h('div',{class:'props'});
    list.forEach(function(p,i){ps.appendChild(propCard(p,i,hm,function(acts,card){
      if(p.lead_is_me)acts.appendChild(h('span',{class:'ok',text:'you lead this ✓'}));
      else{
        if(!p.lead&&hm.business.coordinator)acts.appendChild(h('button',{type:'button',class:'btn small',text:'I’ll lead this',onclick:function(){var b=this;b.disabled=true;stamp(card,'LEADING');setTimeout(function(){act('/me/lead',{pid:p.id},function(){toast('You lead: '+p.title);});},RM?0:500);}}));
        if(p.backed_by_me)acts.appendChild(h('span',{class:'ok',text:'backed ✓'}));
        else if(hm.business.coordinator)acts.appendChild(h('button',{type:'button',class:'btn primary small',text:'Back this',onclick:function(){var b=this;b.disabled=true;stamp(card,'BACKED');setTimeout(function(){act('/me/cosign',{pid:p.id},function(){S.backs++;if(S.backs===1)confetti(80);toast('Backed: '+p.title);});},RM?0:500);}}));
        else acts.appendChild(h('span',{class:'ok',style:'color:var(--muted)',text:'your coordinator backs for the business'}));
      }
    }));});
    box.appendChild(ps);
  }
  function tableBallot(hm,box){
    var r=hm.round,elig=hm.proposals.filter(function(p){return p.status==='eligible';}),cur=(hm.ballot&&hm.ballot.points)||{},pts={};
    if(!r.can_ballot){box.appendChild(h('p',{class:'empty',text:r.eligible_for_vote?'In this Commons each business votes once, through its coordinator. Yours will see the ballot here.':'Your business is not yet verified by the treasurer, so it cannot vote in this round yet. Everything else here still works.'}));
      box.appendChild(h('div',{class:'props',style:'margin-top:14px'},elig.map(function(p,i){return propCard(p,i,hm);})));return;}
    var meter=h('div',{class:'meter'},[h('span',{class:'num',id:'meter-num',text:'0 of 100'}),h('div',{class:'bar'},[h('i',{id:'meter-bar'})]),h('button',{type:'button',class:'btn primary small',id:'meter-go',text:hm.ballot?'Change my ballot':'Cast my ballot'})]);
    box.appendChild(h('p',{class:'hint',text:'Split 100 points across the list. More points, more of the fund it gets under Equal Shares. Leave some unspent if nothing else deserves them.'}));
    box.appendChild(meter);
    var ps=h('div',{class:'props'});
    function total(){return Object.keys(pts).reduce(function(s,k){return s+(pts[k]||0);},0);}
    function refresh(){var t=total();$('meter-num').textContent=t+' of 100';$('meter-bar').style.width=Math.min(100,t)+'%';meter.classList.toggle('full',t===100);meter.classList.toggle('over',t>100);}
    elig.forEach(function(p,i){pts[p.id]=cur[p.id]||0;ps.appendChild(propCard(p,i,hm,function(acts,card){acts.remove();
      var rg=h('input',{type:'range',min:0,max:100,value:pts[p.id],'aria-label':'Points for '+p.title}),nm=h('input',{type:'number',min:0,max:100,value:pts[p.id],'aria-label':'Points for '+p.title});
      rg.addEventListener('input',function(){pts[p.id]=+rg.value;nm.value=rg.value;refresh();});nm.addEventListener('input',function(){pts[p.id]=Math.max(0,Math.min(100,+nm.value||0));rg.value=pts[p.id];refresh();});
      card.appendChild(h('div',{class:'bal'},[rg,nm]));}));});
    box.appendChild(ps);
    var un=h('p',{class:'unspent',text:''});box.appendChild(un);
    var refresh0=refresh;refresh=function(){refresh0();un.textContent=total()<100?(100-total())+' points left unspent':total()>100?'That is '+(total()-100)+' too many.':'All 100 points placed.';};refresh();
    $('meter-go').addEventListener('click',function(){var t=total();if(t>100){toast('That is more than 100.');return;}var send={};Object.keys(pts).forEach(function(k){if(pts[k])send[k]=pts[k];});if(t<100)send._unspent=100-t;
      this.disabled=true;act('/me/ballot',{points:send},function(){confetti(160);toast('Ballot cast');});});
  }
  function tablePlans(hm,box){
    var r=hm.round,pl=hm.plans,elig=hm.proposals.filter(function(p){return p.status==='eligible';});
    if(hm.decision)box.appendChild(h('div',{class:'decided'+(hm.decision.verdict==='ratified'?'':' bad'),text:hm.decision.verdict==='ratified'?'Approved: '+hm.decision.approvals+' of '+hm.decision.eligible+' said yes.':'Not approved: '+hm.decision.approvals+' yes, '+hm.decision.rejections+' no, of '+hm.decision.eligible+'.'}));
    if(!pl||!pl.plans.length){box.appendChild(h('p',{class:'empty',text:'The plans are being computed.'}));return;}
    if(pl.receipt.length){var rc=h('div',{class:'receipt'},[h('b',{text:'Your receipt under Equal Shares: where your equal share of the fund went.'}),h('div',{class:'bars'},pl.receipt.map(function(x){return h('i',{style:'width:'+x.pct+'%',title:x.title+' '+x.pct+'%'});})),h('span',{class:'fine',style:'margin:0',text:pl.receipt.map(function(x){return x.title+' '+x.pct+'%';}).join(' · ')})]);box.appendChild(rc);}
    var tb=h('table'),tr=h('tr',{},[h('th',{text:'Item'})]);pl.plans.forEach(function(p){tr.appendChild(h('th',{class:p.winner?'win':'',text:p.name+(p.valid?'':' (not valid)')}));});tb.appendChild(tr);
    elig.forEach(function(p){var row=h('tr',{},[h('td',{text:p.title})]);pl.plans.forEach(function(q){row.appendChild(h('td',{class:'num',text:rs(q.amounts[p.id]||0)}));});tb.appendChild(row);});
    box.appendChild(h('div',{class:'plans'},[h('p',{class:'hint',text:'Four ways to spend the same votes. Equal Shares gives every business an equal say; Most points follows the totals; Nash balances; Help the least served lifts the smallest.'}),tb]));
    if(r.stage==='plans'){var order=(pl.my_ranking.length?pl.my_ranking:pl.plans.map(function(p){return p.id;})).slice(),names={};pl.plans.forEach(function(p){names[p.id]=p.name;});
      var rk=h('div',{class:'rank'});function draw(){rk.textContent='';order.forEach(function(id,i){rk.appendChild(h('div',{},[h('b',{text:(i+1)+'.'}),names[id],h('button',{type:'button',class:'up','aria-label':'Move up',text:'↑',disabled:i===0?'':null,onclick:function(){order.splice(i-1,0,order.splice(i,1)[0]);draw();}}),h('button',{type:'button',class:'dn','aria-label':'Move down',text:'↓',disabled:i===order.length-1?'':null,onclick:function(){order.splice(i+1,0,order.splice(i,1)[0]);draw();}})]));});}draw();
      box.appendChild(h('p',{class:'hint',style:'margin-top:16px',text:'Rank the plans, best first. The winner is picked by Schulze, so a plan most businesses rank high beats one a few rank first.'}));box.appendChild(rk);
      box.appendChild(h('button',{type:'button',class:'btn primary send',text:pl.my_ranking.length?'Change my ranking':'Submit my ranking',onclick:function(){this.disabled=true;act('/me/rank',{order:order},function(){toast('Ranking saved');});}}));}
    if(r.stage==='ratifying'){var w=pl.plans.filter(function(p){return p.winner;})[0];
      box.appendChild(h('p',{class:'hint',style:'margin-top:16px',text:'The winning plan is '+(w?w.name:'?')+'. '+(hm.my_verdict?'You said '+hm.my_verdict+'. You can change it until approval closes.':'Approve it or reject it. Not answering counts as not approving.')}));
      box.appendChild(h('div',{class:'verdict'},[h('button',{type:'button',class:'btn ok',text:'Approve',onclick:function(){act('/me/approve',{verdict:'approve'},function(){confetti(120);toast('Approved');});}}),h('button',{type:'button',class:'btn no',text:'Reject',onclick:function(){act('/me/approve',{verdict:'reject'},function(){toast('Rejected');});}})]));}
    if(hm.ledger.length){var lt=h('table',{},[h('tr',{},[h('th',{text:'Paid to'}),h('th',{text:'Amount'}),h('th',{text:'Status'}),h('th',{text:'Ref'})])]);hm.ledger.forEach(function(x){lt.appendChild(h('tr',{},[h('td',{text:x.title}),h('td',{class:'num',text:rs(x.amount)}),h('td',{text:x.status}),h('td',{text:x.reference||'—'})]));});box.appendChild(h('div',{class:'plans ledger'},[h('p',{class:'hint',style:'margin-top:16px',text:'The ledger. The fund pays vendors directly; the treasurer records each reference.'}),lt]));}
  }

  // ---------------------------------------------------------------- your own idea
  $('f-propose').addEventListener('submit',function(e){
    e.preventDefault();hideErr('propose-err');var f=e.target,title=f.title.value.trim(),ideal=+f.ideal.value;
    if(title.length<3){showErr('propose-err','Give it a name.');f.title.focus();return;}if(!(ideal>0)){showErr('propose-err','About how much would it cost, in rupees?');f.ideal.focus();return;}
    var b=f.querySelector('.send');b.disabled=true;b.textContent='Putting it on the table…';
    act('/me/propose',{title:title,text:f.text.value.trim(),ideal:ideal,lead:f.lead.checked,upi:f.upi.value.trim()},function(hm){f.reset();f.lead.checked=true;confetti(140);toast('On the table: '+title);
      var p=hm.proposals.filter(function(x){return x.title===title;})[0];if(p){S.born={};S.born[p.id]=1;renderSpace(hm);focusProp(p.id);}})
    .then(function(){b.disabled=false;b.textContent='Put it on the table';});
  });

  // ---------------------------------------------------------------- ask the twin
  function greet(hm){var msgs=$('msgs');if(msgs.children.length)return;
    say('twin',hm.business?'I’m the twin of the Commons: the place, its schemes, and what every business here said it needs. Ask me which idea fits '+hm.business.name+', which scheme could carry part of one, or what happens next.':'I’m the twin of the Commons. Ask me anything about the round.',null);}
  function say(who,text,src){var msgs=$('msgs'),m=h('div',{class:'msg '+who});if(who==='twin'&&!RM){m.textContent='';msgs.appendChild(m);var i=0;(function tick(){i+=3;m.textContent=text.slice(0,i);if(i<text.length)setTimeout(tick,14);else if(src)m.appendChild(h('span',{class:'src',text:src==='model'?'from the twin’s model':'from the twin’s records'}));msgs.scrollTop=msgs.scrollHeight;})();}
    else{m.textContent=text;if(who==='twin'&&src)m.appendChild(h('span',{class:'src',text:src==='model'?'from the twin’s model':'from the twin’s records'}));msgs.appendChild(m);}msgs.scrollTop=msgs.scrollHeight;return m;}
  function renderChips(hm){var c=$('chat-chips');c.textContent='';var qs=['Which idea fits my business best?','Which scheme could carry part of one?','What happens next, and when?'];
    if(hm.round&&hm.round.stage==='ballots')qs.unshift('How should I split my 100 points?');qs.forEach(function(q){c.appendChild(h('button',{type:'button',text:q,onclick:function(){ask(q);}}));});}
  $('f-chat').addEventListener('submit',function(e){e.preventDefault();var q=e.target.q.value.trim();if(q.length<2)return;e.target.q.value='';ask(q);});
  function ask(q){if(S.asking)return;S.asking=true;say('me',q);var w=h('div',{class:'msg twin wait'},[h('i'),h('i'),h('i')]);$('msgs').appendChild(w);$('msgs').scrollTop=$('msgs').scrollHeight;
    api('/me/twin/ask',{question:q,history:S.history.slice(-6),node:S.node}).then(function(j){w.remove();say('twin',j.answer,j.source);S.history.push({role:'user',text:q});S.history.push({role:'assistant',text:j.answer});})
    .catch(function(er){w.remove();say('twin','I could not answer just now ('+er.message+')',null);}).then(function(){S.asking=false;});}

  // ---------------------------------------------------------------- your business and the map
  function renderBiz(hm){
    var box=$('biz-in'),b=hm.business;box.textContent='';
    if(!b){box.appendChild(h('p',{class:'empty',text:hm.note||'No business yet.'}));return;}
    if(hm.businesses.length>1){var sel=h('select',{'aria-label':'Which business'});hm.businesses.forEach(function(x){sel.appendChild(h('option',{value:x.id,text:x.name+' ('+x.role+')'}));});sel.value=b.id;sel.addEventListener('change',function(){S.node=sel.value;enter(false);});box.appendChild(sel);}
    var hd=h('h2',{text:b.name});hd.appendChild(h('span',{class:'pill '+(b.verified?'ok':'grey'),text:b.verified?'verified':'awaiting verification'}));if(b.charter)hd.appendChild(h('span',{class:'pill grey',text:'charter signed'}));box.appendChild(hd);
    box.appendChild(h('p',{class:'place',text:(b.place?b.place+' · ':'')+b.team+(b.team===1?' person':' people')+' on the team'+(b.coordinator?' · you coordinate':'')}));
    box.appendChild(h('h3',{class:'sub',style:'margin:14px 0 2px',text:'What you said you need'}));
    var ul=h('ul',{class:'needs'});b.needs.forEach(function(n){ul.appendChild(h('li',{text:n.text},[h('small',{text:n.status==='drafted'?'the twin drafted from this':'open: a draft is on its way'})]));});if(!b.needs.length)ul.appendChild(h('li',{text:'Nothing yet.'}));box.appendChild(ul);
    var ta=h('input',{name:'need',maxlength:300,placeholder:'One more thing the business needs'}),mic=h('button',{type:'button',class:'mic','aria-label':'Dictate'});wireMic(mic,ta);
    var nf=h('form',{class:'need'},[h('span',{class:'mic-wrap'},[ta,mic]),h('button',{type:'submit',class:'btn small',text:'Add'})]);
    nf.addEventListener('submit',function(e){e.preventDefault();var v=ta.value.trim();if(v.length<3)return;act('/me/need',{text:v},function(){toast('Noted. The twin is drafting.');pop('table');});});box.appendChild(nf);
    if(hm.schemes&&hm.schemes.slots&&hm.schemes.slots.length){var sc=h('div',{class:'schemes'},[h('h3',{text:'Schemes your business could draw · '+hm.schemes.summary.eligible+' eligible, '+hm.schemes.summary.plausible+' plausible of '+hm.schemes.summary.schemes})]);
      hm.schemes.slots.forEach(function(s){var sl=h('div',{class:'slot'});var bb=h('b',{text:s.name});bb.appendChild(h('span',{class:'tag '+s.status,text:s.status}));sl.appendChild(bb);sl.appendChild(h('span',{class:'d',text:s.slot_label+(s.department?' · '+s.department:'')+(s.max_rupees?' · up to '+rs(s.max_rupees):'')}));if(s.steps&&s.steps.length)sl.appendChild(h('div',{style:'font-size:14px;color:var(--muted)',text:'Step: '+s.steps[0]}));sc.appendChild(sl);});
      if(hm.schemes.unlocks.length)sc.appendChild(h('p',{class:'fine',text:'One step that opens the most: '+hm.schemes.unlocks[0].step+' ('+hm.schemes.unlocks[0].schemes+' schemes).'}));box.appendChild(sc);}
    box.appendChild(h('p',{class:'fine',text:'Inside here, businesses see each other by name: who backs what, who leads. Outside, the public board shows counts only.'}));
  }
  function renderMap(hm){
    var box=$('map'),d=hm.map;box.textContent='';if(!d||!d.bbox||!d.businesses.length){box.appendChild(h('p',{class:'empty',style:'padding:20px',text:'The map fills in as businesses say where they are.'}));return;}
    var x0=d.bbox[0],y0=d.bbox[1],x1=d.bbox[2],y1=d.bbox[3],r=(x1-x0)/45,s=svg('svg',{viewBox:x0+' '+y0+' '+(x1-x0)+' '+(y1-y0),role:'img','aria-label':'The businesses of the Commons on the map'});
    (d.context||[]).forEach(function(t){s.appendChild(svg('path',{d:t.rings.map(function(ring){return 'M'+ring.map(function(q){return q[0].toFixed(1)+' '+q[1].toFixed(1);}).join('L')+'Z';}).join('')},[svg('title',{text:t.name})]));});
    var placed=d.businesses.filter(function(b){return b.x!=null;}),byName={};placed.forEach(function(b){byName[b.name]=b;});
    var hot=S.hot&&hm.proposals.filter(function(p){return p.id===S.hot;})[0],lit={};
    if(hot){var joined=[];hot.backers.forEach(function(n){if(byName[n])joined.push([byName[n],'']);});if(hot.lead&&byName[hot.lead])joined.push([byName[hot.lead],'']);hot.built_from.forEach(function(n){if(byName[n])joined.push([byName[n],'built']);});
      if(joined.length){var hx=joined.reduce(function(a,j){return a+j[0].x;},0)/joined.length,hy=joined.reduce(function(a,j){return a+j[0].y;},0)/joined.length;joined.forEach(function(j){lit[j[0].id]=1;s.appendChild(svg('line',{class:'ln '+j[1],x1:hx,y1:hy,x2:j[0].x,y2:j[0].y}));});
        $('map-hint').textContent='“'+hot.title+'” joins '+joined.length+(joined.length===1?' business':' businesses')+': '+joined.map(function(j){return j[0].name;}).filter(function(v,i,a){return a.indexOf(v)===i;}).join(', ')+'.';}
      else $('map-hint').textContent='“'+hot.title+'” joins nobody on the map yet.';}
    else $('map-hint').textContent='Every business in the Commons, in its place. Tap an idea on the table to see whom it joins.';
    placed.forEach(function(b){if(b.me)s.appendChild(svg('circle',{class:'me-ring',cx:b.x,cy:b.y,r:r*1.6}));
      s.appendChild(svg('circle',{class:'b'+(b.verified?'':' unv')+(b.me?' me':'')+(lit[b.id]?' lit':''),cx:b.x,cy:b.y,r:r,onclick:function(){toast(b.name+(b.place?' · '+b.place:'')+(b.need?' · needs: '+b.need:''));}},[svg('title',{text:b.name+(b.place?' · '+b.place:'')+(b.need?'\n'+b.need:'')})]));
      if(b.me||lit[b.id])s.appendChild(svg('text',{x:b.x,y:b.y-r*1.5,text:b.name}));});
    box.appendChild(s);
  }

  // ---------------------------------------------------------------- the microphone (the browser's own speech recognition)
  var SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(SR)document.body.classList.add('has-mic');
  function wireMic(btn,target){if(!SR){btn.remove();return;}var rec=null;
    btn.addEventListener('click',function(){if(rec){rec.stop();return;}rec=new SR();rec.lang=$('lang').value;rec.interimResults=true;rec.continuous=false;var base=target.value?target.value.replace(/\s+$/,'')+' ':'';
      rec.onresult=function(e){var t='';for(var i=0;i<e.results.length;i++)t+=e.results[i][0].transcript;target.value=base+t;target.dispatchEvent(new Event('input'));};
      rec.onend=function(){rec=null;btn.classList.remove('on');target.focus();};rec.onerror=function(e){toast(e.error==='not-allowed'?'The microphone is blocked for this site.':'Could not hear that. Try again.');};
      btn.classList.add('on');try{rec.start();}catch(x){rec=null;btn.classList.remove('on');}});}
  document.querySelectorAll('.mic[data-for]').forEach(function(b){var f=b.closest('form'),t=f&&f.elements[b.getAttribute('data-for')];if(t)wireMic(b,t);});

  // ---------------------------------------------------------------- the radar: five trade-offs as one shape
  function initials(hm){var n=(hm.member.display||hm.business&&hm.business.name||'?').split(/\s+/);return ((n[0]||'')[0]+((n[1]||'')[0]||'')).toUpperCase();}
  function radar(el,axes,vec,o){
    o=o||{};el.textContent='';var n=axes.length,W=300,H=276,cx=W/2,cy=H/2+4,R=92,sv=svg('svg',{viewBox:'0 0 '+W+' '+H,role:'img','aria-label':'Your five trade-offs as one shape'});
    if(!vec){el.appendChild(sv);el.appendChild(h('div',{class:'none',text:o.empty||'Not answered yet. The twin will ask.'}));return;}
    var v={};axes.forEach(function(a){v[a.key]=+(vec[a.key]==null?5:vec[a.key]);});
    function ang(i){return -Math.PI/2+i*2*Math.PI/n;}
    function rad(x){return R*(0.12+0.88*x/10);}
    function pt(i,x){return [cx+rad(x)*Math.cos(ang(i)),cy+rad(x)*Math.sin(ang(i))];}
    [2.5,5,7.5,10].forEach(function(k){sv.appendChild(svg('polygon',{class:'ring'+(k===10?' outer':''),points:axes.map(function(a,i){return pt(i,k).join(',');}).join(' ')}));});
    axes.forEach(function(a,i){var p=pt(i,10);sv.appendChild(svg('line',{class:'spoke',x1:cx,y1:cy,x2:p[0],y2:p[1]}));
      var lx=cx+(R+22)*Math.cos(ang(i)),ly=cy+(R+22)*Math.sin(ang(i)),anchor=Math.abs(Math.cos(ang(i)))<.2?'middle':Math.cos(ang(i))>0?'start':'end';
      sv.appendChild(svg('text',{x:lx,y:ly+3,'text-anchor':anchor,text:a.right}));
      sv.appendChild(svg('text',{class:'in',x:lx,y:ly+13,'text-anchor':anchor,text:'← '+a.left}));});
    if(o.after){sv.appendChild(svg('polygon',{class:'shape after',points:axes.map(function(a,i){return pt(i,+(o.after[a.key]==null?5:o.after[a.key])).join(',');}).join(' ')}));}
    var shape=svg('polygon',{class:'shape'});sv.appendChild(shape);
    var dots=axes.map(function(a,i){var p=pt(i,v[a.key]);var c=svg('circle',{class:'v',cx:p[0],cy:p[1],r:6,tabindex:o.editable?0:null,'aria-label':a.left+' or '+a.right+': '+v[a.key]+' of 10'});c.appendChild(svg('title',{text:a.left+' ↔ '+a.right+': '+v[a.key]+' of 10'}));sv.appendChild(c);return c;});
    var val=svg('text',{class:'val',x:cx,y:H-4,'text-anchor':'middle',text:''});sv.appendChild(val);
    function draw(){shape.setAttribute('points',axes.map(function(a,i){return pt(i,v[a.key]).join(',');}).join(' '));dots.forEach(function(c,i){var p=pt(i,v[axes[i].key]);c.setAttribute('cx',p[0]);c.setAttribute('cy',p[1]);});}
    draw();
    if(o.editable){var drag=null;
      function loc(e){var r=sv.getBoundingClientRect();return [(e.clientX-r.left)*W/r.width,(e.clientY-r.top)*H/r.height];}
      function nearest(p){var best=0,bd=1e9;axes.forEach(function(a,i){var d=Math.hypot(p[0]-cx-R*Math.cos(ang(i))*.6,p[1]-cy-R*Math.sin(ang(i))*.6);var dv=Math.hypot(p[0]-+dots[i].getAttribute('cx'),p[1]-+dots[i].getAttribute('cy'));d=Math.min(d,dv);if(d<bd){bd=d;best=i;}});return best;}
      sv.addEventListener('pointerdown',function(e){drag=nearest(loc(e));dots[drag].classList.add('drag');sv.setPointerCapture(e.pointerId);move(e);e.preventDefault();});
      function move(e){if(drag==null)return;var p=loc(e),d=((p[0]-cx)*Math.cos(ang(drag))+(p[1]-cy)*Math.sin(ang(drag)))/R;var x=Math.max(0,Math.min(10,Math.round((d-0.12)/0.88*10)));v[axes[drag].key]=x;val.textContent=axes[drag].left+' ↔ '+axes[drag].right+': '+x+' of 10';draw();}
      sv.addEventListener('pointermove',move);
      sv.addEventListener('pointerup',function(){if(drag==null)return;dots[drag].classList.remove('drag');drag=null;if(o.onchange)o.onchange(Object.assign({},v));});
      sv.addEventListener('keydown',function(e){var i=dots.indexOf(e.target);if(i<0)return;var k=axes[i].key;if(e.key==='ArrowRight'||e.key==='ArrowUp')v[k]=Math.min(10,v[k]+1);else if(e.key==='ArrowLeft'||e.key==='ArrowDown')v[k]=Math.max(0,v[k]-1);else return;e.preventDefault();draw();if(o.onchange)o.onchange(Object.assign({},v));});}
    el.appendChild(sv);
  }
  function renderLean(hm){var b=hm.business;radar($('lean-radar'),hm.axes,b&&b.values,{after:b&&b.values_after,empty:'Not answered yet. The twin asks above; or open your profile and draw it.'});
    $('lean-hint').textContent=b&&b.values_after?'Gold is what you said before the round; the dashed teal shape is after. Tap to change.':'The five trade-offs your business answered, as one shape. The twin reads it when it drafts for you. Tap to change it.';}
  $('lean-radar').addEventListener('click',function(){openProfile();});

  // ---------------------------------------------------------------- the profile
  var pd=$('d-profile'),pending=null;
  function openProfile(){if(!S.home||!pd.showModal)return;renderProfile(S.home);pd.showModal();}
  $('who').addEventListener('click',openProfile);
  pd.querySelector('[data-close]').addEventListener('click',function(){pd.close();});pd.addEventListener('click',function(e){if(e.target===pd)pd.close();});
  function renderProfile(hm){
    var b=hm.business,f=$('f-profile');$('p-initials').textContent=initials(hm);$('p-name').textContent=hm.member.display||'';$('p-sub').textContent=b?(b.name+(b.place?' · '+b.place:'')+' · '+(b.coordinator?'coordinator':'team member')):'';
    f.display.value=hm.member.display||'';f.business.value=b?b.name:'';f.place.value=b&&b.place||'';
    $('p-biz-l').hidden=$('p-place-l').hidden=!(b&&b.coordinator);
    var acts=$('p-radar-acts');acts.textContent='';
    radar($('p-radar'),hm.axes,b&&b.values||{earn:5,horizon:5,scope:5,means:5,risk:5},{editable:!!b,after:b&&b.values_after,onchange:function(v){pending=v;acts.textContent='';
      acts.appendChild(h('button',{type:'button',class:'btn primary small',text:'Save my lean',onclick:function(){this.disabled=true;act('/me/values',{vector:pending,phase:'before'},function(){pending=null;toast('Lean saved');});}}));
      acts.appendChild(h('button',{type:'button',class:'btn small ghost',text:'Undo',onclick:function(){pending=null;renderProfile(S.home);}}));}});
    if(!(b&&b.values))acts.appendChild(h('span',{class:'fine',style:'margin:0',text:'Drag the points to draw it, then save.'}));
    var c=hm.choices||{},box=$('p-choices');box.textContent='';
    function grp(title,items,fmt){var g=h('div',{class:'grp'},[h('h4',{text:title})]);if(!items||!items.length)g.appendChild(h('p',{class:'none',text:'Nothing yet.'}));else{var ul=h('ul');items.forEach(function(x){ul.appendChild(fmt(x));});g.appendChild(ul);}box.appendChild(g);}
    grp('Backed',c.backed,function(x){return h('li',{},[x.title,h('span',{class:'amt',text:rs(x.ideal)})]);});
    grp('Leading',c.led,function(x){return h('li',{},[x.title,h('span',{class:'amt',text:rs(x.ideal)})]);});
    grp('Put forward',c.proposed,function(x){return h('li',{},[x.title,h('span',{class:'ans',text:x.status}),h('span',{class:'amt',text:rs(x.ideal)})]);});
    grp('Your ballot',c.ballot,function(x){return h('li',{},[x.title,h('span',{class:'amt',text:x.points+' pts'})]);});
    if(c.ranking&&c.ranking.length)grp('Your ranking',c.ranking.map(function(n,i){return {n:n,i:i};}),function(x){return h('li',{},[(x.i+1)+'. '+x.n]);});
    if(c.verdict)grp('The plan',[c.verdict],function(x){return h('li',{},[h('span',{class:'ans '+(x==='approve'?'yes':''),text:x==='approve'?'approved':'rejected'})]);});
    grp('What you told the twin',(b&&b.needs||[]).map(function(n){return {kind:'need',answer:n.text};}).concat(c.answers||[]),function(x){
      return h('li',{},[x.kind==='need'?'Need: '+x.answer:x.kind==='use'?'Would you use “'+(x.title||x.key)+'”?':x.kind==='leftover'?'A leftover should go to: '+x.answer:x.kind+': '+x.answer,
        x.kind==='use'?h('span',{class:'ans '+(x.answer==='yes'?'yes':''),text:x.answer}):null]);});
  }
  $('f-profile').addEventListener('submit',function(e){e.preventDefault();hideErr('profile-err');var f=e.target,b=S.home.business,body={display:f.display.value.trim()};
    if(b&&b.coordinator){if(f.business.value.trim()&&f.business.value.trim()!==b.name)body.business=f.business.value.trim();if(f.place.value.trim()&&f.place.value.trim()!==(b.place||''))body.place=f.place.value.trim();}
    var btn=f.querySelector('button');btn.disabled=true;
    api('/me/profile',Object.assign(body,S.node?{node:S.node}:{})).then(function(hm){render(hm);toast('Saved');}).catch(function(er){showErr('profile-err',er.message);}).then(function(){btn.disabled=false;});});

  // ---------------------------------------------------------------- the twin of Goa: every place, every scheme
  var G={data:null,level:'district',focus:null,sel:null,cards:{}};
  function renderGoa(hm){G.hm=hm;if(G.data){drawGoa();return;}if(G.loading)return;G.loading=true;
    fetch(BASE+'/twin/map.json').then(function(r){return r.json();}).then(function(d){G.data=d;var dl=$('gt-places');dl.textContent='';d.features.filter(function(f){return f.level==='body';}).forEach(function(f){dl.appendChild(h('option',{value:f.name+(f.taluka?' · '+f.taluka:'')}));});
      var me=hm.business&&hm.business.place_id;if(me){var f=byId(me);if(f){G.level='body';G.focus=byId('taluka-'+slug(f.taluka));G.sel=f.id;}}drawGoa();if(me)goaCard(me);}).catch(function(){$('gt-hint').textContent='The map of Goa did not load. Try again later.';});}
  function slug(s){return String(s||'').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'');}
  function byId(id){return G.data.features.filter(function(f){return f.id===id;})[0];}
  function inFocus(f){if(!G.focus)return true;if(G.focus.level==='district')return f.district===G.focus.name;if(G.focus.level==='taluka')return f.taluka===G.focus.name;return true;}
  function pathD(rings){return rings.map(function(ring){return 'M'+ring.map(function(q){return q[0].toFixed(1)+' '+q[1].toFixed(1);}).join('L')+'Z';}).join('');}
  function drawGoa(){var box=$('gt-map');box.textContent='';var fs=G.data.features.filter(function(f){return f.level===G.level&&inFocus(f);});
    var xs=[],ys=[];fs.forEach(function(f){f.rings.forEach(function(r){r.forEach(function(q){xs.push(q[0]);ys.push(q[1]);});});});
    var bb=xs.length?[Math.min.apply(null,xs),Math.min.apply(null,ys),Math.max.apply(null,xs),Math.max.apply(null,ys)]:G.data.bbox,pad=Math.max(bb[2]-bb[0],bb[3]-bb[1])*.06;
    var sv=svg('svg',{viewBox:[bb[0]-pad,bb[1]-pad,bb[2]-bb[0]+2*pad,bb[3]-bb[1]+2*pad].join(' '),role:'img','aria-label':'Goa, tappable'});
    if(G.focus)sv.appendChild(svg('path',{class:'ctx',d:pathD(G.focus.rings)}));
    var mine=G.hm.business&&G.hm.business.place_id,counts={};(G.hm.map.businesses||[]).forEach(function(b){if(b.verified)counts[b.place_id]=(counts[b.place_id]||0)+1;});
    fs.forEach(function(f){var p=svg('path',{d:pathD(f.rings),class:(G.sel===f.id?'sel ':'')+(f.level==='body'&&f.id===mine?'mine':''),tabindex:0,role:'button','aria-label':f.name,onclick:function(){G.sel=f.id;drawGoa();goaCard(f.id);},onkeydown:function(e){if(e.key==='Enter'){G.sel=f.id;drawGoa();goaCard(f.id);}}});p.appendChild(svg('title',{text:f.name}));sv.appendChild(p);});
    var r=(bb[2]-bb[0]+2*pad)/60;fs.forEach(function(f){var n=f.level==='body'?counts[f.id]:Object.keys(counts).reduce(function(s,id){var b=byId(id);return s+((b&&(f.level==='taluka'?b.taluka===f.name:b.district===f.name))?counts[id]:0);},0);
      if(n&&f.c){sv.appendChild(svg('circle',{class:'mark',cx:f.c[0],cy:f.c[1],r:r}));sv.appendChild(svg('text',{class:'mark',x:f.c[0],y:f.c[1],'font-size':r*1.2,text:n}));}});
    box.appendChild(sv);
    [].forEach.call($('gt-lvl').children,function(b){b.setAttribute('aria-pressed',b.getAttribute('data-l')===G.level?'true':'false');});
    var cr=$('gt-crumb');cr.textContent='';if(G.focus){cr.appendChild(h('a',{text:'Goa',onclick:function(){G.focus=null;G.level='district';drawGoa();}}));if(G.focus.level==='taluka'){var d=G.data.features.filter(function(f){return f.level==='district'&&f.name===G.focus.district;})[0];if(d)cr.appendChild(h('a',{text:d.name,onclick:function(){G.focus=d;G.level='taluka';drawGoa();}}));}cr.appendChild(document.createTextNode(G.focus.name));}
    $('gt-hint').textContent=G.level==='district'?'Two districts. Tap one.':G.level==='taluka'?'Twelve talukas. Tap one for its card, then open its villages.':'Every village panchayat and town'+(G.focus?' in '+G.focus.name:'')+'. Black dots count the Commons’ verified businesses.';}
  $('gt-lvl').addEventListener('click',function(e){var b=e.target.closest('button');if(!b||!G.data)return;G.level=b.getAttribute('data-l');if(G.level==='district')G.focus=null;if(G.level==='taluka'&&G.focus&&G.focus.level==='taluka'){var d=G.data.features.filter(function(f){return f.level==='district'&&f.name===G.focus.district;})[0];G.focus=d||null;}drawGoa();});
  function goaCard(id){var box=$('gt-card');box.textContent='';box.appendChild(h('p',{class:'empty',text:'Reading the twin…'}));
    var f=byId(id),p=G.cards[id]?Promise.resolve(G.cards[id]):fetch(BASE+'/twin/'+encodeURIComponent(id)+'/card.json').then(function(r){return r.json();}).then(function(c){G.cards[id]=c;return c;});
    p.then(function(c){if(G.sel!==id)return;box.textContent='';box.appendChild(h('h3',{text:c.name}));box.appendChild(h('p',{class:'kind',text:c.kind_word+(c.taluka&&c.kind!=='taluka'?' · '+c.taluka:'')+(c.members_words?' · '+c.members_words:'')}));
      var fa=c.facts||{},facts=h('div',{class:'facts'});[['households','households'],['population','people'],['farmers','farmers'],['fisher_households','fisher households']].forEach(function(k){if(fa[k[0]]!=null)facts.appendChild(h('div',{},[h('b',{text:indian(fa[k[0]])}),k[1]]));});box.appendChild(facts);
      var sm=c.summary||{};box.appendChild(h('div',{class:'counts'},[h('span',{class:'tag eligible',text:sm.eligible+' eligible'}),h('span',{class:'tag plausible',text:sm.plausible+' plausible'}),h('span',{class:'tag unlikely',text:sm.unlikely+' unlikely'}),' of '+sm.schemes+' schemes']));
      var ol=h('ol',{class:'top'});(c.top||[]).forEach(function(s,i){ol.appendChild(h('li',{},[h('span',{class:'n',text:String(i+1)}),h('div',{},[h('span',{class:'slot',text:s.slot_label||''}),h('b',{text:s.name}),h('span',{class:'m',text:(s.department||'')+(s.max_rupees?' · up to '+rs(s.max_rupees):'')+(s.why?' · '+s.why:'')}),s.steps&&s.steps.length?h('span',{class:'s',text:s.steps[0]}):null])]));});box.appendChild(ol);
      if(c.businesses_draw)box.appendChild(h('p',{class:'biz',text:c.businesses_draw.businesses+' Commons business'+(c.businesses_draw.businesses===1?'':'es')+' here, eligible for '+c.businesses_draw.eligible_any+' schemes between them'+(c.businesses_draw.step?'; the step that opens the most: '+c.businesses_draw.step:'')+'.'}));
      var acts=h('div',{class:'acts',style:'display:flex;gap:8px;flex-wrap:wrap'});
      if(f&&f.level==='district')acts.appendChild(h('button',{type:'button',class:'btn primary small',text:'See its talukas',onclick:function(){G.focus=f;G.level='taluka';drawGoa();}}));
      if(f&&f.level==='taluka')acts.appendChild(h('button',{type:'button',class:'btn primary small',text:'See its villages',onclick:function(){G.focus=f;G.level='body';drawGoa();}}));
      acts.appendChild(h('a',{class:'btn small',href:BASE+c.url,target:'_blank',rel:'noreferrer',text:'Every scheme for '+c.name}));
      acts.appendChild(h('button',{type:'button',class:'btn small ghost',text:'Ask the twin about it',onclick:function(){ask('What could '+c.name+' draw on, and what fits a business like mine there?');$('chat').scrollIntoView({behavior:RM?'auto':'smooth'});}}));
      box.appendChild(acts);}).catch(function(){box.textContent='';box.appendChild(h('p',{class:'empty',text:'Could not read that place just now.'}));});}

  // nav: smooth scroll inside the app
  $('nav').addEventListener('click',function(e){var a=e.target.closest('a');if(!a)return;e.preventDefault();var t=document.querySelector(a.getAttribute('href'));if(t)t.scrollIntoView({behavior:RM?'auto':'smooth',block:'start'});});
})();
