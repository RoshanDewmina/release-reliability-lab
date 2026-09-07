const esc=(v)=>{const n=document.createElement('span');n.textContent=String(v??'—');return n.innerHTML};
const item=(title,text)=>`<div class="item"><strong>${esc(title)}</strong><p>${esc(text)}</p></div>`;
async function load(){
  const response=await fetch('/api/report');
  if(!response.ok){document.querySelector('#summary').textContent='No readable report is available. Run the lab first.';return}
  const r=await response.json();
  document.querySelector('#summary').textContent=`Run ${r.run_id.slice(0,8)} used ${r.mode} services and completed ${r.actions.length} recorded actions.`;
  const recovery=r.incidents[0]?.observed_recovery_ms;
  document.querySelector('#headline').innerHTML=[['Status',r.status],['Mode',r.mode],['Actions',r.actions.length],['Recovery',recovery?`${recovery} ms`:'—']].map(([k,v])=>`<div class="metric"><small>${esc(k)}</small><strong class="${r.status==='passed'?'pass':'fail'}">${esc(v)}</strong></div>`).join('');
  const rel=r.release||{};
  document.querySelector('#release').innerHTML=item('Candidate rejected',String(rel.candidate_rejected))+item('Restored release',rel.restored_release)+item('Rollback time',rel.rollback_ms?`${rel.rollback_ms} ms`:'—')+item('Meaning',rel.meaning);
  document.querySelector('#incidents').innerHTML=(r.incidents||[]).map(x=>item(x.id,`${x.impact} Recovery: ${x.recovery}`)).join('');
  document.querySelector('#actions').innerHTML=`<table><thead><tr><th>Action</th><th>Outcome</th><th>Duration</th></tr></thead><tbody>${r.actions.map(x=>`<tr><td>${esc(x.name)}</td><td><span class="pill ${esc(x.outcome)}">${esc(x.outcome)}</span></td><td>${esc(x.duration_ms)} ms</td></tr>`).join('')}</tbody></table>`;
  document.querySelector('#load').innerHTML=`<table><thead><tr><th>Service</th><th>Requests</th><th>Succeeded</th><th>Failed</th><th>Rate</th></tr></thead><tbody>${(r.load||[]).map(x=>`<tr><td>${esc(x.service)}</td><td>${esc(x.requests)}</td><td>${esc(x.succeeded)}</td><td>${esc(x.failed)}</td><td>${esc(x.requests_per_second)} req/s</td></tr>`).join('')}</tbody></table>`;
}
load().catch(e=>{document.querySelector('#summary').textContent=`Report error: ${e.message}`});

