// The UI. Served as a classic script on purpose: the markup uses inline
// onclick="fn()" attributes, which resolve names through the global scope.
//
// Split out of index.html. Nothing here changed in the move.

const $ = (s)=>document.querySelector(s);
const api = async (m,u,b)=>{
  const r = await fetch(u,{method:m,headers:{'Content-Type':'application/json'},
    body:b?JSON.stringify(b):undefined});
  return r.json();
};
const esc = (s)=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

document.querySelectorAll('#nav button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('#nav button').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');
  ['dash','companies','sourcing','contacts','compose','outreach','apps','templates','profile'].forEach(t=>
    $('#tab-'+t).classList.toggle('hide', t!==b.dataset.tab));
  if(b.dataset.tab==='companies') loadCompanies();
  if(b.dataset.tab==='sourcing') loadSourcing();
  if(b.dataset.tab==='contacts') loadContacts();
  if(b.dataset.tab==='compose') loadPickers();
  if(b.dataset.tab==='outreach') loadOutreach();
  if(b.dataset.tab==='apps') loadApps();
  if(b.dataset.tab==='templates') loadTemplateList();
  if(b.dataset.tab==='profile') loadProfile();
});

/* ---------- dashboard ---------- */
async function loadDash(){
  const s = await api('GET','/api/stats');
  const card=(n,l)=>`<div class="stat"><div class="n">${n}</div><div class="l">${l}</div></div>`;
  $('#stats').innerHTML =
    card(s.companies,'companies') + card(s.contacts,'contacts') +
    card(s.with_email,'with an email') + card(s.sent,'emails sent') +
    card(s.replies,'replies') + card(s.followups_due,'follow-ups due');
  const h = [];
  h.push(`<div class="row"><b>Profile completeness</b> <span class="muted">${s.profile_completeness}%</span></div>`);
  h.push(`<div class="bar"><div style="width:${s.profile_completeness}%"></div></div>`);
  h.push('<div style="height:12px"></div>');
  const byType = (s.companies_by_type||[]).map(x=>`<span class="tag gray">${esc(x.type)} ${x.n}</span>`).join(' ');
  h.push(`<div class="row" style="margin-top:10px">${byType}</div>`);
  const cs = (s.contacts_by_status||[]).map(x=>`<span class="tag">${esc(x.status)} ${x.n}</span>`).join(' ');
  h.push(`<div class="row"><b>Contacts</b></div><div class="row">${cs||'<span class="muted">none yet</span>'}</div>`);
  const os = (s.outreach_by_status||[]).map(x=>`<span class="tag">${esc(x.status)} ${x.n}</span>`).join(' ');
  h.push(`<div class="row"><b>Outreach</b></div><div class="row">${os||'<span class="muted">none yet</span>'}</div>`);
  h.push(`<div class="row"><b>Coverage</b></div><div class="row">
    <span class="tag gray">${s.firms_with_contacts}/${s.companies} firms with contacts</span>
    <span class="tag gray">${s.firms_with_pattern} firms with an email pattern</span></div>`);
  $('#health').innerHTML = h.join('');

  const a = await api('GET','/api/analytics');
  $('#funnel').innerHTML = a.funnel.map(f=>`
    <div class="row" style="margin-bottom:4px">
      <span style="width:120px">${esc(f.stage)}</span>
      <span class="bar" style="flex:1"><div style="width:${Math.min(100,f.pct)}%"></div></span>
      <span style="width:90px;text-align:right" class="muted">${f.n} · ${f.pct}%</span>
    </div>`).join('');
  const rate=(x)=>`<span class="${x>=20?'tag good':(x>0?'tag warn':'tag gray')}">${x}%</span>`;
  $('#an-type').innerHTML = a.by_type.length ? `<table><thead><tr><th>firm type</th>
    <th>contacts</th><th>emailed</th></tr></thead><tbody>
    ${a.by_type.map(r=>`<tr><td>${esc(r.k)}</td><td>${r.contacts}</td><td>${r.emailed}</td></tr>`).join('')}</tbody></table>`
    : '<span class="muted">no contacts yet</span>';
  $('#an-tpl').innerHTML = a.by_template.length ? `<table><thead><tr><th>template</th>
    <th>sent</th><th>replied</th><th>reply rate</th></tr></thead><tbody>
    ${a.by_template.map(r=>`<tr><td>${esc(r.k)}</td><td>${r.sent}</td><td>${r.replied}</td>
      <td>${rate(r.reply_rate)}</td></tr>`).join('')}</tbody></table>`
    : '<span class="muted">nothing sent yet</span>';

  const due = await api('GET','/api/outreach?due=1');
  $('#due').innerHTML = due.length ? '<table>'+due.map(o=>`<tr>
      <td>${esc(o.first_name)} ${esc(o.last_name)}</td>
      <td class="muted">${esc(o.company_name||'')}</td>
      <td><span class="tag warn">${esc(o.next_followup_at)}</span></td>
      <td><button class="act sm" onclick="draftFollowup(${o.id})">Draft follow-up</button>
          <button class="ghost sm" onclick="viewMail(${o.id})">View</button></td></tr>`).join('')+'</table>'
    : '<span class="muted">Nothing due. Go find more contacts.</span>';
}
function openMail(id){ location.hash=''; document.querySelector('#nav button[data-tab="outreach"]').click(); }
async function demo(action){
  const r = await api('POST','/api/demo/'+action,{});
  if(action==='load') alert(`Loaded ${r.contacts} sample contacts. Remove them any time with the button next door.`);
  loadDash();
}

/* ---------- companies ---------- */
async function loadCompanies(){
  const p = new URLSearchParams();
  if($('#cq').value) p.set('q',$('#cq').value);
  if($('#ctype').value) p.set('type',$('#ctype').value);
  if($('#ctier').value) p.set('tier',$('#ctier').value);
  if($('#cstatus').value) p.set('status',$('#cstatus').value);
  if($('#cno').checked) p.set('without_contacts','1');
  const rows = await api('GET','/api/companies?'+p.toString());
  $('#ccount').textContent = rows.length+' firms';
  const types='bank,hedge_fund,prop_hft,asset_manager,commodity,insurance_am,broker,crypto,other'
    .split(',').map(t=>`<option value="${t}">${t}</option>`).join('');
  const statuses='to_research,researched,has_contacts,approached,dead'
    .split(',').map(t=>`<option value="${t}">${t}</option>`).join('');
  const patterns=['','first.last','firstlast','f.last','flast','first','last.first','firstl','first_last']
    .map(t=>`<option value="${t}">${t||'—'}</option>`).join('');
  $('#ctable').innerHTML = `<thead><tr><th style="width:230px">Name</th><th style="width:170px">Domain</th>
    <th style="width:130px">Type</th><th style="width:110px">City</th><th style="width:150px">Email pattern</th>
    <th style="width:60px">Tier</th><th style="width:130px">Status</th><th></th></tr></thead><tbody>`+
    rows.map(r=>`<tr>
      <td><input class="cell" value="${esc(r.name)}" data-id="${r.id}" data-f="name"></td>
      <td><input class="cell" value="${esc(r.domain||'')}" data-id="${r.id}" data-f="domain"></td>
      <td><select class="cell" data-id="${r.id}" data-f="type">${types.replace(`value="${r.type}"`,`value="${r.type}" selected`)}}</select></td>
      <td><input class="cell" value="${esc(r.hq_city||'')}" data-id="${r.id}" data-f="hq_city"></td>
      <td><select class="cell" data-id="${r.id}" data-f="email_pattern">${patterns.replace(`value="${r.email_pattern||''}"`,`value="${r.email_pattern||''}" selected`)}}</select></td>
      <td><input class="cell" value="${esc(r.tier)}" data-id="${r.id}" data-f="tier" style="width:50px"></td>
      <td><select class="cell" data-id="${r.id}" data-f="status">${statuses.replace(`value="${r.status||''}"`,`value="${r.status||''}" selected`)}}</select></td>
      <td><button class="ghost sm" onclick="showBrief(${r.id})">Brief</button>
          <button class="ghost sm" onclick="findPeople(${r.id})">Find people</button>
          ${r.careers_url?`<a class="ghost sm" href="${esc(r.careers_url)}" target="_blank" rel="noopener" style="text-decoration:none" title="Careers page">Careers ↗</a>`:''}
          <button class="ghost sm" onclick="del('companies',${r.id},loadCompanies)">×</button></td></tr>`).join('')+'</tbody>';
  bindCells('#ctable','companies', loadCompanies);
  $('#company-list').innerHTML = rows.map(r=>`<option value="${esc(r.name)}">`).join('');
  const sel = $('#v-company');
  const keep = sel.value;
  sel.innerHTML = rows.map(r=>`<option value="${r.id}">${esc(r.name)}${r.email_pattern?' — '+esc(r.email_pattern):''}</option>`).join('');
  if(keep) sel.value = keep;
}
async function verifyPattern(){
  const r = await api('POST','/api/infer-pattern',
    {company_id:$('#v-company').value, sample_email:$('#v-email').value});
  if(r.error){ $('#v-result').innerHTML=`<div class="flag">${esc(r.error)}</div>`; return; }
  const cls = r.confidence>=0.8 ? 'good' : 'warn';
  $('#v-result').innerHTML = `<div class="flag" style="background:var(--${cls}-soft);color:var(--${cls})">
    Pattern <b>${esc(r.pattern)}</b> @ ${esc(r.domain)} · confidence ${Math.round(r.confidence*100)}%
    · unlocks ${r.unlocked_contacts} contact(s). Source: ${esc(r.source)}. ${esc(r.note)}</div>`;
  $('#v-email').value=''; loadCompanies();
}
async function showBrief(id){
  const r = await api('GET','/api/companies?ids='+id);
  const c = r[0];
  if(!c){ alert('Firm not found.'); return; }
  if(!c.research && !c.careers_url){ alert('Nothing on file for this firm yet.'); return; }
  const body = c.research
    ? esc(c.research).replace(/Best hook:/i,'<br><b>Best hook:</b>')
    : '<span class="muted">No brief written for this firm yet. The careers page is below.</span>';
  const careers = c.careers_url
    ? `<div style="margin-top:12px"><a href="${esc(c.careers_url)}" target="_blank" rel="noopener">Careers page ↗</a></div>`
    : '';
  const div = document.createElement('div');
  div.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;z-index:100';
  div.innerHTML = `<div style="background:var(--panel);border:1px solid var(--line);border-radius:12px;max-width:680px;max-height:80vh;overflow:auto;padding:20px 22px;box-shadow:0 8px 30px rgba(0,0,0,.15)">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <b style="font-size:15px">${esc(c.name)}</b>
      <button class="ghost sm" onclick="this.closest('div').parentElement.remove()">Close</button>
    </div>
    <div style="font-size:13px;line-height:1.6;color:var(--text)">${body}</div>
    ${careers}
    <div style="margin-top:10px;font-size:12px;color:var(--muted)">${c.research_updated?'Brief updated '+esc(c.research_updated):''}</div>
  </div>`;
  document.body.appendChild(div);
  div.addEventListener('click',e=>{ if(e.target===div) div.remove(); });
}
async function findPeople(id){
  const r = await api('GET','/api/sourcing/'+id);
  if(r.error){ alert(r.error); return; }
  window.open(r.xray_url,'_blank');
  window.open(r.careers_url,'_blank');
}
async function loadApps(){
  const rows = await api('GET','/api/applications');
  const statuses='to_apply,applied,online_test,interview,offer,rejected,withdrawn'
    .split(',').map(t=>`<option value="${t}">${t.replace('_',' ')}</option>`).join('');
  $('#atable').innerHTML = `<thead><tr><th style="width:200px">Company</th><th>Role</th>
    <th style="width:130px">Status</th><th style="width:140px">Applied</th><th></th></tr></thead><tbody>`+
    rows.map(r=>`<tr>
      <td><input class="cell" value="${esc(r.company_name||'')}" data-id="${r.id}" data-f="company_name"></td>
      <td><input class="cell" value="${esc(r.role||'')}" data-id="${r.id}" data-f="role"></td>
      <td><select class="cell" data-id="${r.id}" data-f="status">${statuses.replace(`value="${r.status||''}"`,`value="${r.status||''}" selected`)}}</select></td>
      <td><input class="cell" type="date" value="${esc((r.applied_at||'').slice(0,10))}" data-id="${r.id}" data-f="applied_at"></td>
      <td><button class="ghost sm" onclick="del('applications',${r.id},loadApps)">×</button></td></tr>`).join('')+'</tbody>';
  bindCells('#atable','applications', loadApps);
}
async function addApp(){
  const b={company_name:$('#a-company').value, role:$('#a-role').value, url:$('#a-url').value,
    status:$('#a-status').value, applied_at:$('#a-date').value};
  if(!b.company_name && !b.role) return;
  const co = await api('GET','/api/companies?q='+encodeURIComponent(b.company_name||''));
  const hit = co.find(c=>c.name.toLowerCase()===(b.company_name||'').toLowerCase());
  if(hit) b.company_id = hit.id;
  await api('POST','/api/applications',b);
  ['#a-company','#a-role','#a-url'].forEach(x=>$(x).value='');
  loadApps();
}
async function retag(){
  const r = await api('POST','/api/retag',{overwrite:false});
  alert(r.error ? r.error : `Tagged ${r.updated} contact(s) with a desk and seniority level. Existing values were kept.`);
  loadContacts();
}
async function applyGuesses(){
  const r = await api('POST','/api/apply-guesses',{});
  alert(r.error ? r.error : `${r.updated} email(s) filled from firm patterns (marked "guessed").`);
  loadContacts(); loadDash();
}
async function addCompany(){
  const b = {name:$('#cnew-name').value, domain:$('#cnew-domain').value, type:$('#cnew-type').value,
    hq_city:$('#cnew-city').value, tier:$('#cnew-tier').value};
  if(!b.name) return;
  await api('POST','/api/companies',b);
  ['#cnew-name','#cnew-domain','#cnew-city'].forEach(x=>$(x).value='');
  loadCompanies();
}

/* ---------- sourcing ---------- */
async function loadSourcing(){
  const p = new URLSearchParams();
  p.set('tier', $('#sq-tier').value);
  if($('#sq-type').value) p.set('type',$('#sq-type').value);
  p.set('without_contacts', $('#sq-only-firms').checked ? '1' : '0');
  $('#sq-export').href = '/api/export/sourcing?'+p.toString();
  const rows = await api('GET','/api/sourcing-queue?'+p.toString());
  $('#sq-count').textContent = rows.length+' firms';
  if(!rows.length){
    $('#sq-list').innerHTML = `<div class="card"><div class="flag" style="background:var(--good-soft);color:var(--good)">
      Every firm in this filter already has a contact. Widen the filter, or move on to writing mail.</div></div>`;
    return;
  }
  const readiness = r => {
    const bits = [r.careers_is_verified, !!r.hook, !!r.domain];
    const n = bits.filter(Boolean).length;
    const cls = n===3 ? 'good' : n===2 ? 'warn' : 'gray';
    return `<span class="tag ${cls}">${n}/3 ready</span>`;
  };
  $('#sq-list').innerHTML = rows.map(r=>`
    <div class="card">
      <div class="row" style="justify-content:space-between;margin-bottom:6px">
        <div><b>${esc(r.name)}</b>
          <span class="muted"> · tier ${r.tier} · ${esc(r.type)}${r.hq_city?' · '+esc(r.hq_city):''}${r.subtype?' · '+esc(r.subtype):''}</span>
          ${readiness(r)}
        </div>
        <div class="row" style="margin:0">
          ${r.careers_url?`<a class="ghost sm" href="${esc(r.careers_url)}" target="_blank" rel="noopener" style="text-decoration:none">Careers ↗</a>`:''}
          <a class="ghost sm" href="${esc(r.xray_url)}" target="_blank" rel="noopener" style="text-decoration:none">Find people ↗</a>
          <button class="ghost sm" onclick="addContactFor(${r.id},'${esc(r.name).replace(/'/g,"\\'")}')">+ Add contact</button>
        </div>
      </div>
      ${r.hook?`<div style="font-size:13px;margin-bottom:6px"><span class="muted">Hook:</span> ${esc(r.hook)}</div>`
              :`<div class="muted" style="font-size:12.5px;margin-bottom:6px">No brief yet — write one in the Companies tab.</div>`}
      <div style="font-size:12px;color:var(--muted);margin-bottom:4px">
        Target titles: ${esc(r.titles.join(', '))}</div>
      <div style="font-size:11.5px;background:#fbfaf7;border:1px solid var(--line);border-radius:7px;padding:7px 9px;
                  font-family:ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all">
        <span id="q-${r.id}">${esc(r.xray_query)}</span>
        <a href="#" onclick="copyQuery(this,${r.id});return false" style="margin-left:6px">copy</a>
      </div>
    </div>`).join('');
}
function copyQuery(el,id){
  const src = document.getElementById('q-'+id);
  navigator.clipboard.writeText(src ? src.textContent : '');
  el.textContent = 'copied';
}
function addContactFor(companyId, companyName){
  document.querySelector('#nav button[data-tab="contacts"]').click();
  $('#k-company').value = companyName;
  $('#k-first').focus();
}

/* ---------- contacts ---------- */
// A masked address looks like a real one in a table. The badge is the only
// thing telling you which is which before you press send.
function emailBadge(r){
  if(r.email) return '';
  if(r.email_masked) return '<span class="tag warn" title="masked Prospeo address - becomes a candidate once the firm pattern is known">masked</span>';
  return '<span class="tag gray" title="no address">none</span>';
}
async function loadContacts(){
  const p = new URLSearchParams();
  if($('#kq').value) p.set('q',$('#kq').value);
  const rows = await api('GET','/api/contacts?'+p.toString());
  $('#kcount').textContent = rows.length+' contacts';
  // Location and level come from the harvesters; showing them is the whole
  // point of collecting them, so they are columns and not hidden in a modal.
  $('#ktable').innerHTML = `<thead><tr><th style="width:170px">Name</th><th style="width:180px">Company</th>
    <th style="width:180px">Title</th><th style="width:100px">Desk</th><th style="width:110px">Level</th>
    <th style="width:150px">Location</th>
    <th style="width:215px">Email</th><th style="width:80px">Source</th><th></th></tr></thead><tbody>`+
    rows.map(r=>`<tr>
      <td><input class="cell" value="${esc(r.first_name)}" data-id="${r.id}" data-f="first_name">
          <input class="cell" value="${esc(r.last_name)}" data-id="${r.id}" data-f="last_name"></td>
      <td><input class="cell" value="${esc(r.company_name||'')}" data-id="${r.id}" data-f="company_name"></td>
      <td><input class="cell" value="${esc(r.job_title||'')}" data-id="${r.id}" data-f="job_title"
          title="${esc(r.position_raw||'')}"></td>
      <td><input class="cell" value="${esc(r.desk||'')}" data-id="${r.id}" data-f="desk"></td>
      <td><input class="cell" value="${esc(r.seniority_level||r.seniority||'')}" data-id="${r.id}" data-f="seniority_level"></td>
      <td><input class="cell" value="${esc(r.city||'')}" data-id="${r.id}" data-f="city"
          title="${esc(r.location_raw||'')}"></td>
      <td><input class="cell" value="${esc(r.email||'')}" placeholder="${esc(r.email_masked||r.email_guess||'')}" data-id="${r.id}" data-f="email">
          ${emailBadge(r)}</td>
      <td><span class="tag gray">${esc(r.source||'')}</span></td>
      <td><button class="ghost sm" onclick="toCompose(${r.id})">Mail</button>
          <button class="ghost sm" onclick="del('contacts',${r.id},loadContacts)">×</button></td></tr>`).join('')+'</tbody>';
  bindCells('#ktable','contacts', loadContacts);
}
async function addContact(){
  const b = {first_name:$('#k-first').value, last_name:$('#k-last').value, job_title:$('#k-title').value,
    company_name:$('#k-company').value, email:$('#k-email').value, linkedin_url:$('#k-linkedin').value};
  if(!b.first_name && !b.last_name) return;
  await api('POST','/api/contacts',b);
  ['#k-first','#k-last','#k-title','#k-email','#k-linkedin'].forEach(x=>$(x).value='');
  loadContacts(); loadDash();
}
async function importContacts(){
  const b = {csv_text:$('#import-text').value, company_name:$('#import-company').value,
    delimiter:$('#import-delim').value==='\\t'?'\t':$('#import-delim').value};
  const r = await api('POST','/api/import/contacts',b);
  $('#import-result').textContent = r.error ? r.error : `${r.added} added, ${r.updated} updated, ${r.skipped} skipped`;
  if(!r.error){ $('#import-text').value=''; loadContacts(); loadDash(); }
}

/* ---------- paste people ---------- */
let ppData = null;

function confBadge(c){
  const cls = c>=0.85 ? 'good' : (c>=0.6 ? 'warn' : 'gray');
  return `<span class="tag ${cls}">${c.toFixed(2)}</span>`;
}

async function readPeople(){
  const text = $('#pp-text').value;
  if(!text.trim()) return;
  $('#pp-result').textContent = 'reading…';
  const r = await api('POST','/api/parse-people',
    {text, company_name:$('#pp-company').value});
  if(r.error){ $('#pp-result').textContent = r.error; return; }
  ppData = r;
  $('#pp-result').textContent = '';
  renderPeople();
}

function renderPeople(){
  const rows = ppData.candidates || [];
  const s = ppData.stats || {};
  $('#pp-summary').textContent =
    `${rows.length} people read from ${s.lines} lines · ${s.with_email||0} with a real address · `
    + `${s.known_firms||0} matched a firm in your list`
    + (s.merged ? ` · ${s.merged} two-line profiles stitched` : '');
  const COLS = [['first_name','First',95],['last_name','Last',95],['job_title','Title',200],
                ['company_name','Firm',190],['email','Email',210]];
  $('#pp-table').innerHTML =
    '<tr><th></th><th>Conf.</th>' + COLS.map(col=>`<th>${col[1]}</th>`).join('') + '<th>Why</th></tr>'
    + rows.map((p,i)=>`<tr>
        <td><input type="checkbox" class="pp-keep" data-i="${i}" ${p.confidence>=0.6?'checked':''}></td>
        <td>${confBadge(p.confidence)}</td>
        ${COLS.map(col=>`<td><input class="pp-f" data-i="${i}" data-k="${col[0]}"
            value="${esc(p[col[0]])}" style="width:${col[2]}px"${
              col[0]==='company_name'?' list="company-list"':''}></td>`).join('')}
        <td class="muted">${esc(p.evidence)}</td>
      </tr>`).join('');
  $('#pp-table').querySelectorAll('.pp-f').forEach(el=>el.oninput=()=>{
    ppData.candidates[+el.dataset.i][el.dataset.k] = el.value;
  });
  $('#pp-save').disabled = !rows.length;
  $('#pp-review').classList.toggle('hide', !rows.length && !(ppData.unparsed||[]).length);
  const un = ppData.unparsed || [];
  $('#pp-unparsed').innerHTML = un.length
    ? `<div class="hint"><strong>${un.length} line(s) I would not guess at:</strong><br>`
      + un.map(u=>`&nbsp;• <code>${esc(u.line)}</code> — ${esc(u.reason)}`).join('<br>') + '</div>'
    : '';
}

async function savePeople(){
  if(!ppData) return;
  const keep = [...$('#pp-table').querySelectorAll('.pp-keep')]
    .filter(c=>c.checked).map(c=>ppData.candidates[+c.dataset.i]);
  if(!keep.length){ $('#pp-result').textContent = 'nothing ticked'; return; }
  $('#pp-result').textContent = 'saving…';
  const r = await api('POST','/api/import-people', {
    candidates: keep,
    pattern_samples: ppData.pattern_samples || [],
    company_name: $('#pp-company').value,
    source: $('#pp-source').value
  });
  if(r.error){ $('#pp-result').textContent = r.error; return; }
  let msg = `${r.added} added, ${r.updated} updated, ${r.skipped} skipped`;
  const pl = r.pattern_learning || {};
  if((pl.learned||[]).length){
    msg += ' · pattern learned: ' + pl.learned.map(l=>
      `${l.company} → ${l.pattern} (${l.confidence})`).join(', ');
  }
  if((pl.skipped||[]).length){
    msg += ` · ${pl.skipped.length} address(es) told us the domain but not the convention`;
  }
  $('#pp-result').textContent = msg;
  $('#pp-text').value = ''; ppData = null; $('#pp-save').disabled = true;
  $('#pp-review').classList.add('hide');
  loadContacts(); loadDash();
}

/* ---------- compose ---------- */
let currentOutreachId = null;   // the row being edited, once saved
let currentFollows = null;      // set when this draft is a follow-up to another mail
async function loadPickers(){
  const [cs, ts] = await Promise.all([api('GET','/api/contacts'), api('GET','/api/templates')]);
  $('#pick-contact').innerHTML = cs.map(c=>
    `<option value="${c.id}">${esc(c.first_name)} ${esc(c.last_name)} — ${esc(c.company_name||'')}</option>`).join('');
  $('#pick-template').innerHTML = ts.map(t=>`<option value="${t.id}">${esc(t.name)}</option>`).join('');
  if(!cs.length) $('#pick-contact').innerHTML='<option value="">no contacts yet — add some first</option>';
}
async function generate(){
  const r = await api('POST','/api/generate',
    {contact_id:$('#pick-contact').value, template_id:$('#pick-template').value});
  if(r.error){ $('#flags').innerHTML = `<div class="flag">${esc(r.error)}</div>`; return; }
  $('#m-to').value = r.to; $('#m-subject').value = r.subject; $('#m-body').value = r.body;
  currentOutreachId = null; currentFollows = null;   // a fresh mail is never a follow-up
  $('#flags').innerHTML = (r.flags||[]).length
    ? r.flags.map(f=>`<div class="flag">${esc(f)}</div>`).join('') : '';
  renderBrief(r);
  renderScore(r.quality);
}
function renderBrief(r){
  const el = $('#brief');
  if(!r.brief){ el.innerHTML = r.company
    ? `<div class="flag" style="background:#f1efe8;color:#5f5e5a">No brief for <b>${esc(r.company)}</b> yet — write the hook yourself, or ask me to research the firm.</div>`
    : ''; return; }
  const hook = esc(r.brief_hook);
  el.innerHTML = `<div class="flag" style="background:var(--accent-soft);color:var(--accent);line-height:1.55">
    <b>Firm intel — ${esc(r.company)}</b><br>
    <span style="color:var(--text)">${esc(r.brief).replace(/\s*Best hook:.*$/i,'')}</span>
    ${hook?`<br><b>Best hook:</b> <span style="color:var(--text)">${hook}</span>`:''}
    <div style="margin-top:6px;font-size:11.5px">
      <a href="#" id="copy-hook">copy hook</a>
      &nbsp;·&nbsp; or use <code>{{company_hook}}</code> / <code>{{company_research}}</code> in a template
    </div></div>`;
  const link = $('#copy-hook');
  if(link) link.onclick = e => {
    e.preventDefault();
    navigator.clipboard.writeText(r.brief_hook||'');
    link.textContent = 'copied';
  };
}
function renderScore(q){
  if(!q){ $('#score').innerHTML=''; return; }
  const cls = q.grade==='A' ? 'good' : (q.grade==='B' ? 'warn' : 'bad');
  $('#score').innerHTML = `<div class="flag" style="background:var(--${cls}-soft);color:var(--${cls})">
    Grade ${q.grade} · ${q.score}/100 · ${q.words} words
    ${q.issues.length ? '<br>' + q.issues.map(i=>'· '+esc(i)).join('<br>')
                      : '<br>' + esc(q.notes.join(' '))}</div>`;
}
async function checkQuality(){
  const r = await api('POST','/api/score',{contact_id:$('#pick-contact').value,
    subject:$('#m-subject').value, body:$('#m-body').value});
  renderScore(r);
}
async function saveDraft(){
  const r = await api('POST','/api/outreach',{contact_id:$('#pick-contact').value,
    template_id:$('#pick-template').value, subject:$('#m-subject').value, body:$('#m-body').value,
    status:'draft'});
  currentOutreachId = r.id;
  $('#flags').innerHTML = r.error?`<div class="flag">${esc(r.error)}</div>`:
    '<div class="flag" style="background:var(--good-soft);color:var(--good)">Draft saved.</div>';
}
async function markSent(){
  if(!currentOutreachId){ await saveDraft(); }
  await api('POST','/api/mark-sent',
    {id:currentOutreachId, followup_days:7, follows:currentFollows});
  $('#flags').innerHTML = '<div class="flag" style="background:var(--good-soft);color:var(--good)">'
    + (currentFollows ? 'Follow-up sent. The original thread is no longer due.'
                      : 'Marked sent. Follow-up set for +7 days.') + '</div>';
  currentFollows = null;
  loadDash();
}

/* ---------- getting the mail out ---------- */
function copyField(sel, btn){
  const el = $(sel);
  if(!el) return;
  navigator.clipboard.writeText(el.value||'').then(()=>{
    const old = btn.textContent; btn.textContent = 'copied';
    setTimeout(()=>{ btn.textContent = old; }, 1200);
  });
}
function copyAll(btn){
  const txt = `Subject: ${$('#m-subject').value}\n\n${$('#m-body').value}`;
  navigator.clipboard.writeText(txt).then(()=>{
    const old = btn.textContent; btn.textContent = 'copied';
    setTimeout(()=>{ btn.textContent = old; }, 1200);
  });
}
function openInMail(){
  const to = ($('#m-to').value||'').trim();
  const subject = $('#m-subject').value||'';
  const body = $('#m-body').value||'';
  if(!body.trim()){ alert('Generate or write something first.'); return; }
  // mailto has practical length limits; past ~1800 chars clients silently drop
  // the body, so warn rather than let you send a half-empty email.
  const url = 'mailto:'+encodeURIComponent(to)+'?subject='+encodeURIComponent(subject)
            + '&body='+encodeURIComponent(body);
  if(url.length > 1900){
    $('#mailto-hint').innerHTML = '<b>This mail is too long for a mailto link</b> — your client would '
      + 'truncate it. Use "Copy both" and paste instead.';
    return;
  }
  window.location.href = url;
}
async function draftFollowup(outreachId){
  const r = await api('POST','/api/draft-followup',{outreach_id:outreachId});
  if(r.error){ alert(r.error); return; }
  document.querySelector('#nav button[data-tab="compose"]').click();
  await loadPickers();
  $('#pick-contact').value = r.contact_id;
  $('#pick-template').value = r.template_id;
  currentOutreachId = null; currentFollows = r.follows;
  $('#m-to').value = r.to; $('#m-subject').value = r.subject; $('#m-body').value = r.body;
  renderBrief(r); renderScore(r.quality);
  $('#flags').innerHTML = `<div class="flag" style="background:var(--warn-soft);color:var(--warn)">
    This is a follow-up to "${esc((r.original_subject||r.subject).replace(/^Re: /,''))}" — keeping the same
    subject so it threads. Sending it will clear the original from your due list.</div>`;
}
function toCompose(id){
  document.querySelector('#nav button[data-tab="compose"]').click();
  loadPickers().then(()=>{ $('#pick-contact').value=id; generate(); });
}

/* ---------- outreach ---------- */
async function loadOutreach(){
  const p = new URLSearchParams();
  if($('#ostatus').value) p.set('status',$('#ostatus').value);
  if($('#odue').checked) p.set('due','1');
  const rows = await api('GET','/api/outreach?'+p.toString());
  $('#ocount').textContent = rows.length+' items';
  const statuses='draft,approved,sent,replied,positive,negative,closed'
    .split(',').map(t=>`<option value="${t}">${t}</option>`).join('');
  $('#otable').innerHTML = `<thead><tr><th style="width:170px">Contact</th><th style="width:160px">Company</th>
    <th>Subject</th><th style="width:130px">Status</th><th style="width:120px">Sent</th>
    <th style="width:130px">Next follow-up</th><th style="width:70px">Stage</th><th></th></tr></thead><tbody>`+
    rows.map(r=>`<tr>
      <td>${esc(r.first_name||'')} ${esc(r.last_name||'')}</td>
      <td class="muted">${esc(r.company_name||'')}</td>
      <td>${esc((r.subject||'').slice(0,70))}</td>
      <td><select class="cell" data-id="${r.id}" data-f="status">${statuses.replace(`value="${r.status||''}"`,`value="${r.status||''}" selected`)}}</select></td>
      <td class="muted">${esc((r.sent_at||'').slice(0,10))}</td>
      <td><input class="cell" type="date" value="${esc((r.next_followup_at||'').slice(0,10))}" data-id="${r.id}" data-f="next_followup_at"></td>
      <td><input class="cell" value="${esc(r.followup_stage)}" data-id="${r.id}" data-f="followup_stage" style="width:50px"></td>
      <td><button class="ghost sm" onclick="viewMail(${r.id})">View</button>
          ${(r.status==='sent')?`<button class="ghost sm" onclick="draftFollowup(${r.id})">Follow up</button>`:''}
          <button class="ghost sm" onclick="del('outreach',${r.id},loadOutreach)">×</button></td></tr>`).join('')+'</tbody>';
  bindCells('#otable','outreach', loadOutreach);
}
async function viewMail(id){
  const rows = await api('GET','/api/outreach');
  const r = rows.find(x=>x.id===id);
  document.querySelector('#nav button[data-tab="compose"]').click();
  setTimeout(()=>{ currentOutreachId=id; $('#m-subject').value=r.subject||''; $('#m-body').value=r.body||'';
    $('#m-to').value=r.contact_email||''; $('#flags').innerHTML=''; },50);
}

/* ---------- templates ---------- */
let templatesCache=[];
async function loadTemplateList(){
  templatesCache = await api('GET','/api/templates');
  $('#t-select').innerHTML = templatesCache.map(t=>`<option value="${t.id}">${esc(t.name)}</option>`).join('');
  if(templatesCache.length) loadTemplate();
}
function loadTemplate(){
  const t = templatesCache.find(x=>x.id==$('#t-select').value);
  if(!t) return;
  $('#t-name').value=t.name||''; $('#t-family').value=t.role_family||'generic';
  $('#t-subject').value=t.subject_tpl||''; $('#t-body').value=t.body_tpl||'';
}
function newTemplate(){
  $('#t-select').value=''; $('#t-name').value=''; $('#t-subject').value=''; $('#t-body').value='';
}
async function saveTemplate(){
  const b={name:$('#t-name').value, role_family:$('#t-family').value,
    subject_tpl:$('#t-subject').value, body_tpl:$('#t-body').value};
  if(!b.name) return;
  if($('#t-select').value) await api('PUT','/api/templates/'+$('#t-select').value,b);
  else { const r=await api('POST','/api/templates',b); await loadTemplateList(); $('#t-select').value=r.id; }
  loadTemplateList();
}

/* ---------- profile ---------- */
const P_FIELDS=[['full_name','Full name'],['headline','Headline (one line)'],['email','Email'],
  ['phone','Phone'],['linkedin','LinkedIn URL'],['github','GitHub / site'],['website','Website'],
  ['city','City'],['target_roles','Target roles'],['years_exp','Years of experience'],
  ['education','Education'],['key_skills','Key skills'],['projects','Projects'],
  ['achievements','Achievements'],['languages','Languages'],['availability','Availability']];
async function loadProfile(){
  const p = await api('GET','/api/profile')||{};
  $('#pform').innerHTML = P_FIELDS.map(([k,l])=>
    `<div><label class="muted" style="font-size:12px">${l}</label>
     <input id="p-${k}" value="${esc(p[k]||'')}" style="width:100%"></div>`).join('') +
    `<div style="grid-column:1/-1"><label class="muted" style="font-size:12px">Pitch (2 sentences used across templates)</label>
     <textarea id="p-pitch" rows="3">${esc(p.pitch||'')}</textarea></div>`;
  $('#p-cv').value = p.cv_text||'';
  $('#cv-preview').innerHTML = ''; $('#cv-result').textContent = '';
  renderGaps(p);
}
async function saveProfile(){
  const b={};
  P_FIELDS.forEach(([k])=>{ if($('#p-'+k)) b[k]=$('#p-'+k).value; });
  b.pitch = $('#p-pitch').value; b.cv_text = $('#p-cv').value;
  await api('PUT','/api/profile',b);
  const d=new Date(); $('#psaved').textContent='saved at '+d.toLocaleTimeString();
  renderGaps(b); loadDash();
}

/* ---------- CV reader ---------- */
const CV_LABELS = {
  full_name:'Full name', headline:'Headline', email:'Email', phone:'Phone',
  linkedin:'LinkedIn', github:'GitHub / site', website:'Website', city:'City',
  education:'Education', years_exp:'Years of experience', key_skills:'Key skills',
  languages:'Languages', target_roles:'Target roles', projects:'Projects',
  achievements:'Achievements', pitch:'Pitch'
};
async function extractCv(){
  const text = $('#p-cv').value.trim();
  if(text.length < 40){ $('#cv-result').textContent = 'Paste more of your CV first.'; return; }
  $('#cv-result').textContent = 'reading…';
  const r = await api('POST','/api/parse-cv',{cv_text:text});
  if(r.error){ $('#cv-result').textContent = r.error; $('#cv-preview').innerHTML=''; return; }
  $('#cv-result').textContent = `found ${Object.keys(CV_LABELS).length - r.missing.length} of ${Object.keys(CV_LABELS).length} fields · ${r.confidence}%`;
  const rows = Object.keys(CV_LABELS).map(k=>{
    const v = r.fields[k]||'';
    const ok = String(v).trim();
    return `<tr>
      <td class="muted" style="width:150px">${CV_LABELS[k]}</td>
      <td>${ok?esc(v):'<span class="muted">— not found —</span>'}</td>
      <td class="muted" style="width:210px;font-size:11.5px">${ok?esc(r.evidence[k]||''):''}</td>
    </tr>`;
  }).join('');
  $('#cv-preview').innerHTML = `
    <div class="scroll" style="margin-top:10px"><table><thead><tr>
      <th>Field</th><th>Proposed value</th><th>Where it came from</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <div class="row" style="margin-top:10px">
      <button class="act" onclick='applyCv(${JSON.stringify(r.fields).replace(/'/g,"&#39;")})'>Fill the form with these</button>
      <span class="muted">Then correct anything wrong and hit Save. Nothing is saved yet.</span>
    </div>
    ${r.critical_missing.length?`<div class="flag">Could not find: <b>${r.critical_missing.map(k=>CV_LABELS[k]||k).join(', ')}</b> — these matter most for tailoring, so type them in yourself.</div>`:''}`;
}
function applyCv(fields){
  Object.keys(fields).forEach(k=>{
    const el = document.getElementById('p-'+k);
    if(el && String(fields[k]||'').trim()) el.value = fields[k];
  });
  $('#cv-result').textContent = 'form filled — review, then Save profile';
  document.querySelector('#pform').scrollIntoView({behavior:'smooth', block:'start'});
}
function renderGaps(p){
  const critical = ['full_name','key_skills','projects','pitch','education','target_roles'];
  const empty = critical.filter(k=>!String(p[k]||'').trim());
  $('#p-gaps').innerHTML = empty.length
    ? `<div class="flag">Still empty: <b>${empty.map(k=>(CV_LABELS[k]||k)).join(', ')}</b>.
       Emails built with these missing will contain invisible gaps — the generator will flag them.</div>`
    : `<div class="flag" style="background:var(--good-soft);color:var(--good)">Profile is complete enough to write tailored mail.</div>`;
}
document.addEventListener('change', e=>{
  if(e.target && e.target.id === 'p-cv-file'){
    const f = e.target.files[0]; if(!f) return;
    const rd = new FileReader();
    rd.onload = ()=>{ $('#p-cv').value = rd.result; extractCv(); };
    rd.readAsText(f);
  }
});

/* ---------- shared ---------- */
function bindCells(selector, table, reload){
  document.querySelectorAll(selector+' .cell').forEach(el=>{
    const send=async()=>{
      const body={}; body[el.dataset.f]=el.value;
      await api('PUT',`/api/${table}/${el.dataset.id}`,body);
      if(reload) reload(); loadDash();
    };
    el.onchange=send;
  });
}
async function del(table,id,reload){
  if(!confirm('Delete this row?')) return;
  await api('DELETE',`/api/${table}/${id}`);
  reload(); loadDash();
}

loadDash();
