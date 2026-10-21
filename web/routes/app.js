const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const state={authenticated:false,identity:null,context:null,session:null,candidates:[],selected:null,waypoints:{origin:[0,0],destination:[1,1]}};
const colors=['#55d6be','#f0ad4e','#6aa8ff','#d17fff','#ef7180'];

async function api(path,opts={}){
  const headers={'Content-Type':'application/json',...(opts.headers||{})};
  const r=await fetch(path,{credentials:'same-origin',...opts,headers});
  let payload=null; const text=await r.text();
  try{payload=text?JSON.parse(text):null}catch{payload=text}
  if(!r.ok){const detail=payload?.detail||payload||r.statusText;throw new Error(`${r.status}: ${detail}`)}
  return payload;
}
function setStatus(text,kind=''){ $('#status').textContent=text; $('#status').dataset.kind=kind; }
function notice(text,kind=''){const n=$('#notice');n.textContent=text||'';n.dataset.kind=kind;n.hidden=!text}
function setOptions(sel,items,value,label){const el=$(sel);el.innerHTML='';if(!items.length){const o=document.createElement('option');o.value='';o.textContent=`No ${label} available`;el.appendChild(o);return}items.forEach(item=>{const o=document.createElement('option');o.value=value(item);o.textContent=label==='plans'?(item.title||item.id):`${value(item)}${item.version?` · v${item.version}`:''}`;el.appendChild(o)})}
function updateGenerateState(){const ok=state.authenticated&&$('#planId').value&&$('#profileId').value&&$('#datasetId').value;$('#create').disabled=!ok}

async function login(){
  notice('Signing in…');
  try{
    const x=await api('/routes/auth/login',{method:'POST',body:JSON.stringify({email:$('#loginEmail').value,password:$('#loginPassword').value})});
    $('#loginPassword').value=''; state.authenticated=!!x.authenticated;state.identity=x.identity||null;await hydrate();
  }catch(e){notice(e.message,'error');setStatus('AUTH REQUIRED','error')}
}
async function logout(){try{await api('/routes/auth/logout',{method:'POST'})}catch{}state.authenticated=false;state.identity=null;state.context=null;showAuth();clearResources();setStatus('AUTH REQUIRED');notice('Signed out.')}
function showAuth(){
  $('#signedOut').hidden=state.authenticated;$('#signedIn').hidden=!state.authenticated;
  $('#identityName').textContent=state.identity?.display_name||state.identity?.email||state.identity?.id||'';
}
function clearResources(){setOptions('#planId',[],x=>x.id,'plans');setOptions('#profileId',[],x=>x.profile_id,'profiles');setOptions('#datasetId',[],x=>x.dataset_id,'datasets');updateGenerateState()}

async function hydrate(){
  showAuth(); if(!state.authenticated){clearResources();return}
  setStatus('SYNCING'); notice('Loading APOLLO resources…');
  try{
    const [context,plans,profiles,datasets,ready]=await Promise.all([
      api('/v1/routes/context'),api('/v1/plans'),api('/v1/routes/profiles'),api('/v1/routes/datasets'),api('/ready')
    ]);
    state.context=context;
    setOptions('#planId',plans||[],x=>x.id,'plans');
    setOptions('#profileId',profiles||[],x=>x.profile_id,'profiles');
    setOptions('#datasetId',datasets||[],x=>x.dataset_id,'datasets');
    const missing=[];if(!(plans||[]).length)missing.push('APOLLO plan');if(!(profiles||[]).length)missing.push('vehicle profile');if(!(datasets||[]).length)missing.push('route dataset');
    if(missing.length){notice(`Planning is connected, but ${missing.join(', ')} data is missing. Route generation stays disabled until NEXUS/APOLLO provides it.`,'warn');setStatus('DATA NEEDED','warn')}
    else{notice('JANUS authenticated. Planning resources loaded.','ok');setStatus(ready?.status==='ready'?'ONLINE / READY':'ONLINE / DEGRADED',ready?.status==='ready'?'ok':'warn')}
    updateGenerateState();
  }catch(e){notice(e.message,'error');setStatus('NOT READY','error');updateGenerateState()}
}
async function restoreSession(){try{const x=await api('/routes/auth/session');state.authenticated=!!x.authenticated;state.identity=x.identity||null;showAuth();if(state.authenticated)await hydrate();else{clearResources();setStatus('AUTH REQUIRED');notice('Sign in with JANUS to use route planning.')}}catch(e){setStatus('AUTH ERROR','error');notice(e.message,'error')}}

function project(coords){if(!coords.length)return[];const xs=coords.map(c=>c[0]),ys=coords.map(c=>c[1]);let minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);if(minX===maxX){minX-=1;maxX+=1}if(minY===maxY){minY-=1;maxY+=1}return coords.map(([x,y])=>[80+(x-minX)/(maxX-minX)*840,620-(y-minY)/(maxY-minY)*540])}
function draw(){const g=$('#routes');g.innerHTML='';state.candidates.forEach((c,i)=>{if(c.geometry?.type!=='LineString')return;const pts=project(c.geometry.coordinates);const p=document.createElementNS('http://www.w3.org/2000/svg','polyline');p.setAttribute('points',pts.map(x=>x.join(',')).join(' '));p.setAttribute('class','route'+(state.selected===c.candidate_id?' selected':''));p.setAttribute('stroke',colors[i%colors.length]);p.dataset.id=c.candidate_id;p.addEventListener('click',()=>select(c.candidate_id));g.appendChild(p)});drawWaypoints()}
function drawWaypoints(){const g=$('#waypoints');g.innerHTML='';const vals=state.candidates.length?[]:[state.waypoints.origin,state.waypoints.destination];project(vals).forEach((pt,i)=>{const c=document.createElementNS('http://www.w3.org/2000/svg','circle');c.setAttribute('cx',pt[0]);c.setAttribute('cy',pt[1]);c.setAttribute('r',10);c.setAttribute('class','waypoint');c.dataset.kind=i?'destination':'origin';c.addEventListener('pointerdown',e=>{c.setPointerCapture(e.pointerId);c.onpointermove=ev=>{const rect=$('#map').getBoundingClientRect();c.setAttribute('cx',Math.max(20,Math.min(980,(ev.clientX-rect.left)/rect.width*1000)));c.setAttribute('cy',Math.max(20,Math.min(680,(ev.clientY-rect.top)/rect.height*700)))};c.onpointerup=()=>{c.onpointermove=null}});g.appendChild(c)})}
function select(id){state.selected=id;const c=state.candidates.find(x=>x.candidate_id===id);if(!c)return;$('#whyText').innerHTML=`<b>${c.explanation||'No explanation'}</b><br><br>Hard constraints: ${(c.rejection_reasons||[]).join(', ')||'satisfied'}<br>Score factors: <code>${escapeHtml(JSON.stringify(c.score_breakdown||{}))}</code><br>Data quality: <code>${escapeHtml(JSON.stringify(c.data_quality||{}))}</code><br>Provenance: <code>${escapeHtml(JSON.stringify(c.provenance||{}))}</code>`;renderCards();renderCompare();draw()}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]))}
function renderCards(){const root=$('#cards');root.innerHTML='';if(!state.candidates.length){root.innerHTML='<p class="muted">No generated routes yet.</p>';return}state.candidates.forEach((c,i)=>{const d=document.createElement('button');d.type='button';d.className='card'+(c.candidate_id===state.selected?' selected':'');d.innerHTML=`<div><b>ROUTE ${i+1}</b> · ${c.feasibility}</div><div class="score">${c.score==null?'—':Number(c.score).toFixed(4)}</div><div class="metrics"><span>${c.metrics?.distance_km??'—'} km</span><span>${c.metrics?.eta_minutes??'—'} min</span><span>Energy ${c.metrics?.energy_estimate??'—'}</span><span>Comms ${c.metrics?.comms_quality??'—'}</span></div>`;d.onclick=()=>select(c.candidate_id);root.appendChild(d)});if(state.candidates.length&&!state.selected){state.selected=state.candidates[0].candidate_id;select(state.selected)}}
function renderCompare(){const root=$('#compareGrid');if(state.candidates.length<2){root.innerHTML='<p class="muted">Generate at least two routes to compare them.</p>';return}root.innerHTML=state.candidates.map((c,i)=>`<article class="compareCard"><b>ROUTE ${i+1}</b><span>${c.feasibility}</span><strong>${c.score==null?'—':Number(c.score).toFixed(4)}</strong><small>${c.metrics?.distance_km??'—'} km · ${c.metrics?.eta_minutes??'—'} min</small><small>Energy ${c.metrics?.energy_estimate??'—'} · Comms ${c.metrics?.comms_quality??'—'}</small></article>`).join('')}

async function createPlan(){
  if($('#create').disabled)return;notice('Creating route session…');setStatus('PLANNING');
  try{
    const dep=new Date($('#depart').value||Date.now()),latest=new Date($('#latest').value||Date.now()+3600000);
    const context=state.context||await api('/v1/routes/context');
    const body={plan_id:$('#planId').value,vehicle_profile_id:$('#profileId').value,dataset_id:$('#datasetId').value,origin:{lat:+$('#olat').value,lon:+$('#olon').value},destination:{lat:+$('#dlat').value,lon:+$('#dlon').value},origin_node_id:$('#originNode').value.trim(),destination_node_id:$('#destinationNode').value.trim(),planning_window:{earliest_departure:dep.toISOString(),latest_departure:latest.toISOString()},enabled_layers:$$('[data-layer]:checked').map(x=>x.dataset.layer),security_context:context,max_alternatives:4};
    const s=await api('/v1/routes/sessions',{method:'POST',body:JSON.stringify(body)});state.session=s.id;
    const gen=await api(`/v1/routes/sessions/${s.id}/generate`,{method:'POST'});state.candidates=gen.candidates||[];state.selected=null;renderCards();renderCompare();draw();await loadWindows();notice(`Generated ${state.candidates.length} route candidate${state.candidates.length===1?'':'s'}.`,'ok');setStatus('PLAN READY','ok')
  }catch(e){notice(e.message,'error');setStatus('PLAN FAILED','error')}
}
async function loadWindows(){if(!state.session)return;try{const x=await api(`/v1/routes/sessions/${state.session}/windows`,{method:'POST',body:JSON.stringify({interval_minutes:30})});const root=$('#windowBars');root.innerHTML='';(x.windows||[]).forEach(w=>{const b=document.createElement('span');b.className='bar'+(w.recommended?' best':'');b.style.width=`${Math.max(8,100/(x.windows.length||1)-4)}%`;b.title=`${w.departure_time} · ${w.score??'unavailable'}`;root.appendChild(b)});$('#windowStatus').textContent=`${x.windows?.length||0} intervals`}catch(e){$('#windowStatus').textContent='unavailable';notice(`Routes generated, but departure windows failed: ${e.message}`,'warn')}}
async function replay(){try{const id=$('#replayId').value.trim()||state.session;if(!id)throw new Error('Enter a session ID or generate a route first.');const x=await api(`/v1/routes/sessions/${id}/replay`);$('#replayOut').textContent=JSON.stringify(x,null,2);state.candidates=x.candidates||[];state.session=id;state.selected=null;renderCards();renderCompare();draw();notice(`Replay loaded in ${x.replay_mode||'stored'} mode.`,'ok')}catch(e){$('#replayOut').textContent=e.message;notice(e.message,'error')}}

$$('.mode').forEach(b=>b.onclick=()=>{$$('.mode').forEach(x=>x.classList.remove('active'));b.classList.add('active');const mode=b.dataset.mode;$('#planner').hidden=mode!=='plan';$('#comparePane').hidden=mode!=='compare';$('#replayPane').hidden=mode!=='replay';$('#candidates').hidden=mode==='replay';renderCompare()});
$('#loginButton').onclick=login;$('#logoutButton').onclick=logout;$('#loginPassword').addEventListener('keydown',e=>{if(e.key==='Enter')login()});$('#create').onclick=createPlan;$('#loadReplay').onclick=replay;
['#planId','#profileId','#datasetId'].forEach(s=>$(s).onchange=updateGenerateState);$$('[data-layer]').forEach(x=>x.onchange=()=>notice(`${x.dataset.layer.toUpperCase()} ${x.checked?'ON':'OFF'}`));
drawWaypoints();restoreSession();