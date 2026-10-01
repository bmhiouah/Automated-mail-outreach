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
  ['dash','queue','companies','sourcing','contacts','cvs','outreach','profile']
    .forEach(t=>$('#tab-'+t).classList.toggle('hide', t!==b.dataset.tab));
  if(b.dataset.tab==='queue') loadQueueTab();
  if(b.dataset.tab==='companies') loadCompanies();
  if(b.dataset.tab==='sourcing') loadSourcing();
  if(b.dataset.tab==='contacts') loadContacts();
  if(b.dataset.tab==='cvs') loadCvsTab();
  if(b.dataset.tab==='outreach') loadOutreach();
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
      <td><button class="act sm" onclick="queueFollowup(${o.id})">Draft follow-up</button>
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
      <td><button class="ghost sm" onclick="writeToContact(${r.id})">Write</button>
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

/* ---------- the review queue ---------- */
let qCurrent = null;

async function loadQueueTab(){
  await Promise.all([loadQueueStatus(), loadQueue(), loadTodo(), loadCvPickers(),
                      loadModels()]);
  loadHistory();
  await showNextDraft();          // land straight on something to decide
}

/* ---------- connection + counters ---------- */
async function loadQueueStatus(){
  const s = await api('GET','/api/queue/status');
  const m = s.llm || {}, mail = s.mail || {};
  const bits = [];
  bits.push(m.configured
    ? `<span class="tag good">model: ${esc(m.model)}</span>
       <span class="tag gray">${m.calls} calls · ${m.cached_hits} cached</span>`
    : `<span class="tag warn">no model</span><span class="muted"> ${esc(m.reason||'')}</span>`);
  if(mail.send_mode==='live'){
    bits.push(mail.configured
      ? `<span class="tag good">sending: ${esc(mail.from||mail.smtp_user)}</span>
         <span class="tag gray">${mail.sent_today}/${mail.daily_cap} today</span>`
      : `<span class="tag bad">live, but no SMTP credentials</span>`);
  } else {
    bits.push(`<span class="tag warn">dry mode</span>
      <span class="muted"> nothing is transmitted — set <code>mail.send_mode = "live"</code>
      in config.json when you are ready</span>`);
  }
  $('#q-status').innerHTML = '<div class="row">'+bits.join('')+'</div>';
}

function qBadgeClass(s){
  return s==='sent' ? 'good' : s==='cancelled' ? 'gray'
       : s==='failed' ? 'bad' : s==='pending' ? 'warn' : 'good';
}

function addrKindTag(kind){
  if(kind==='verified') return '<span class="tag good">verified</span>';
  if(kind==='pattern')  return `<span class="tag warn" title="reconstructed from the firm's convention, not checked">guessed</span>`;
  return '<span class="tag bad">none</span>';
}

function flag(text, kind){
  const bg = {bad:'var(--bad-soft)', warn:'var(--warn-soft)', good:'var(--good-soft)',
              gray:'#f1efe8'}[kind] || 'var(--warn-soft)';
  return `<div class="flag" style="background:${bg}">${esc(text)}</div>`;
}

// For messages that need real markup. Interpolated values inside still go
// through esc() by hand.
function flagHtml(html, kind){
  const bg = {bad:'var(--bad-soft)', warn:'var(--warn-soft)', good:'var(--good-soft)',
              gray:'#f1efe8'}[kind] || 'var(--warn-soft)';
  return `<div class="flag" style="background:${bg}">${html}</div>`;
}

/* ---------- the list below: a way back to something you skipped ---------- */
async function loadQueue(){
  const f = $('#q-filter').value;
  const rows = await api('GET','/api/queue'+(f?('?status='+f):''));
  const counts = await api('GET','/api/queue/counts');
  $('#q-count').textContent = rows.length+' shown';
  $('#q-badge').innerHTML = ['pending','validated','sent','cancelled','failed']
    .filter(s=>counts[s]).map(s=>`<span class="tag ${qBadgeClass(s)}">${s} ${counts[s]}</span>`).join(' ');
  $('#q-table').innerHTML =
    `<thead><tr><th style="width:32px"></th><th>to</th><th>subject</th>
      <th style="width:110px">status</th><th style="width:92px">address</th>
      <th style="width:64px">grade</th></tr></thead><tbody>`+
    rows.map(r=>{
      let q={}; try{ q=JSON.parse(r.quality||'{}'); }catch(e){}
      const who = [r.first_name,r.last_name].filter(Boolean).join(' ');
      const isCurrent = qCurrent && r.id===qCurrent.id;
      return `<tr onclick="openQueueRow(${r.id})"
        style="${isCurrent?'background:var(--accent-soft)':''}">
        <td>${r.edited?'<span class="muted" title="you edited this">&#9998;</span>':''}</td>
        <td><b>${esc(who)}</b><br><span class="muted">${esc(r.company_name||r.firm||'')}</span></td>
        <td>${esc(r.subject||'')}</td>
        <td><span class="tag ${qBadgeClass(r.status)}">${esc(r.status)}</span></td>
        <td>${addrKindTag(r.addr_kind)}</td>
        <td>${q.grade?`<span class="tag ${q.grade==='A'?'good':(q.grade==='B'?'warn':'bad')}">${esc(q.grade)}</span>`:''}</td>
      </tr>`;
    }).join('')+'</tbody>';
}

/* ---------- the review cursor ---------- */
async function showNextDraft(){
  const pending = await api('GET','/api/queue?status=pending');
  $('#q-progress').textContent = pending.length ? `${pending.length} left` : 'queue empty';
  if(!pending.length){
    qCurrent = null;
    $('#q-id').value = '';
    $('#q-who').textContent = 'Nothing waiting. Tick someone on the left to draft.';
    ['#q-to','#q-subject','#q-body','#q-kind'].forEach(s=>$(s).value='');
    $('#q-flags').innerHTML = '';
    $('#q-result').innerHTML = flag('You are through the queue.', 'good');
    loadQueue();
    return;
  }
  await openQueueRow(pending[0].id);
}

async function openQueueRow(id){
  const q = await api('POST','/api/queue/get',{id});
  if(q.error){ $('#q-result').innerHTML = flag(q.error,'bad'); return; }
  qCurrent = q;
  $('#q-id').value = q.id;
  $('#q-who').innerHTML = `<b>${esc((q.contact.first_name||'')+' '+(q.contact.last_name||''))}</b>
    &mdash; ${esc(q.company.name||q.contact.company_name||'')}
    ${q.contact.job_title?`<span class="muted">(${esc(q.contact.job_title)})</span>`:''}`;
  $('#q-to').value = q.to_addr||'';
  $('#q-kind').value = q.addr_kind||'none';
  $('#q-subject').value = q.subject||'';
  $('#q-body').value = q.body||'';
  $('#q-cvrow').style.display = 'flex';
  $('#q-cv').value = q.cv_id||'';

  // The two things standing between this draft and a real person, both shown
  // before the button rather than after a refusal.
  let h = '';
  if(q.specifics && q.specifics.length){
    h += `<div class="flag" style="background:var(--good-soft);color:var(--good)">
      <b>What this draft is built on</b><br>`
      + q.specifics.map(s=>'&bull; '+esc(s)).join('<br>')
      + `<br><span style="font-size:11.5px">Check these against the database before
         you send &mdash; a claim that is not in the data is one you cannot defend.</span></div>`;
  } else if((q.generation||'').startsWith('llm:')){
    h += flag('This draft names nothing specific to the person or the firm. A mail '
      +'that could go to anyone is a mail that gets deleted.','warn');
  }
  if(q.cv) h += `<div class="flag" style="background:var(--accent-soft);color:var(--accent)">
    CV attached: <b>${esc(q.cv.name)}</b>${q.cv.status!=='validated'?' (not validated)':''}</div>`;
  (q.flags||[]).forEach(f=>h+=flag(f,'warn'));
  if(q.quality && q.quality.issues) q.quality.issues.forEach(i=>h+=flag(i,'warn'));
  if(q.blocking && q.blocking.length) q.blocking.forEach(b=>h+=flag(b,'bad'));
  if(q.send_error) h += flag(q.send_error,'bad');
  $('#q-flags').innerHTML = h;
  $('#q-result').innerHTML = '';
  const readOnly = q.status!=='pending';
  ['#q-to','#q-subject','#q-body','#q-kind'].forEach(s=>$(s).disabled = readOnly);
  if(readOnly){
    $('#q-result').innerHTML = flag('This draft is '+q.status+' and can no longer be edited.','gray');
  }
  loadQueue();
}

async function saveQueueRow(){
  const id = $('#q-id').value;
  if(!id) return;
  const r = await api('POST','/api/queue/edit',{id,
    to_addr:$('#q-to').value, subject:$('#q-subject').value, body:$('#q-body').value,
    addr_kind:$('#q-kind').value, cv_id:$('#q-cv').value||null});
  $('#q-result').innerHTML = r.error ? flag(r.error,'bad')
                                    : flag('Edits saved. Nothing has been sent.','good');
  loadQueue();
}

async function validateQueueRow(){
  const id = $('#q-id').value;
  if(!id){ $('#q-result').innerHTML = flag('Nothing to send.','warn'); return; }
  const to = $('#q-to').value.trim();
  const kind = $('#q-kind').value;
  if(kind==='pattern'){
    if(!confirm("This address was RECONSTRUCTED from the firm's email convention, not "
      +'verified against a real person.\n\n'+to+'\n\nSending to a wrong address is a bounce '
      +'at best. Send anyway?')) return;
  } else if(!confirm('Send this mail to '+to+' now?')) return;

  const r = await api('POST','/api/queue/validate',{id,
    to_addr:$('#q-to').value, subject:$('#q-subject').value, body:$('#q-body').value,
    addr_kind:kind, cv_id:$('#q-cv').value||null, confirm_pattern:kind==='pattern'});
  if(r.error){
    $('#q-result').innerHTML = flag(r.error,'bad')+(r.note?flag(r.note,'warn'):'');
    return;                                   // stay put: nothing was decided
  }
  loadHistory(); loadTodo();
  await showNextDraft();                      // the whole point: move on
  const said = r.sent ? 'Sent. '+r.note : (r.note||r.error);
  $('#q-result').innerHTML = flag(said, r.sent?'good':'warn');
  loadQueueStatus();
}

async function cancelQueueRow(){
  const id = $('#q-id').value;
  if(!id) return;
  if(!confirm('Cancel this draft? It is kept for the record, nothing is sent, and they '
    +'stay in your "not contacted" list.')) return;
  const r = await api('POST','/api/queue/cancel',{id});
  if(r.error){ $('#q-result').innerHTML = flag(r.error,'bad'); return; }
  loadTodo();
  await showNextDraft();
  $('#q-result').innerHTML = flag('Cancelled. They are still in your "not contacted" list.','gray');
}

// Skip without deciding anything: moves the cursor, leaves the draft pending.
// This is what makes the list below a safety net rather than a dead end.
async function skipQueueRow(){
  const pending = await api('GET','/api/queue?status=pending');
  if(pending.length < 2){ $('#q-result').innerHTML = flag('That was the last one.','gray'); return; }
  const i = pending.findIndex(r=>qCurrent && r.id===qCurrent.id);
  const next = pending[(i + 1) % pending.length];
  await openQueueRow(next.id);
  $('#q-result').innerHTML = flag('Skipped — still waiting on you.','gray');
}

function copyQueue(btn){
  const txt = `To: ${$('#q-to').value}\nSubject: ${$('#q-subject').value}\n\n${$('#q-body').value}`;
  navigator.clipboard.writeText(txt).then(()=>{
    const old = btn.textContent; btn.textContent='copied';
    setTimeout(()=>{ btn.textContent=old; }, 1200);
  });
}

function openQueueInMail(){
  const url = 'mailto:'+encodeURIComponent(($('#q-to').value||'').trim())
    + '?subject='+encodeURIComponent($('#q-subject').value||'')
    + '&body='+encodeURIComponent($('#q-body').value||'');
  if(url.length > 1900){ alert('Too long for a mailto link — use Copy instead.'); return; }
  window.location.href = url;
}

/* ---------- who is left to contact ---------- */
function todoQuery(){
  const p = new URLSearchParams();
  if($('#q-f-city').value.trim()) p.set('city', $('#q-f-city').value.trim());
  if($('#q-f-type').value) p.set('type', $('#q-f-type').value);
  if($('#q-f-desk').value) p.set('desk', $('#q-f-desk').value);
  if($('#q-f-tier').value) p.set('tier', $('#q-f-tier').value);
  if($('#q-f-search').value.trim()) p.set('search', $('#q-f-search').value.trim());
  if($('#q-f-pending').checked) p.set('no_pending', '1');
  p.set('limit','300');
  return p.toString();
}

const TYPE_LABELS = {bank:'Banks', hedge_fund:'Hedge funds', prop_hft:'Prop & HFT',
  asset_manager:'Asset managers', commodity:'Commodities', insurance_am:'Insurance AM',
  broker:'Brokers', crypto:'Crypto', other:'Other'};

async function loadTodo(){
  const t = await api('GET','/api/todo?'+todoQuery());
  // Facets only rebuilt when the filter is empty, so choosing "Paris" does not
  // throw away the city list you are choosing from.
  if(!$('#q-f-city').value.trim() && !$('#q-f-type').value){
    $('#q-cities').innerHTML = (t.cities||[]).map(c=>`<option value="${esc(c)}">`).join('');
    if(!$('#q-f-type').dataset.filled){
      $('#q-f-type').innerHTML = '<option value="">Any firm type</option>'
        + (t.types||[]).map(x=>`<option value="${esc(x)}">${esc(TYPE_LABELS[x]||x)}</option>`).join('');
      $('#q-f-type').dataset.filled = '1';
    }
    if(!$('#q-f-desk').dataset.filled){
      $('#q-f-desk').innerHTML = '<option value="">Any desk</option>'
        + (t.desks||[]).map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');
      $('#q-f-desk').dataset.filled = '1';
    }
  }
  $('#q-todo-count').textContent = t.n_remaining+' to go · '
    + t.n_contacted+' contacted · '+t.n_pending+' in the queue';
  $('#q-todo').innerHTML =
    `<thead><tr><th style="width:32px"></th><th>person</th><th>firm</th>
      <th style="width:88px">city</th><th style="width:92px">address</th></tr></thead><tbody>`+
    t.remaining.map(r=>`<tr>
      <td><input type="checkbox" class="q-pick" value="${r.id}" ${r.email?'':'disabled'}></td>
      <td>${esc(r.first_name+' '+r.last_name)}</td>
      <td>${esc(r.firm||r.company_name||'')}</td>
      <td class="muted">${esc(r.city||'')}</td>
      <td>${r.email?emailBadge(r):'<span class="tag gray">none</span>'}</td>
    </tr>`).join('')+'</tbody>';
}

/* ---------- which model writes the draft ---------- */
// Populated once from the endpoint's own /models list, minus the ones measured
// to reject /chat/completions on this gateway. Cached because it is a network
// call the user should not pay for on every tab switch.
let modelCache = null;
async function loadModels(force){
  if(modelCache && !force) return modelCache;
  const m = await api('GET','/api/llm/models');
  modelCache = m;
  const opts = (m.models||[]).map(x =>
    `<option value="${esc(x)}"${x===m.current?' selected':''}>${esc(x)}</option>`).join('');
  $('#q-model').innerHTML = opts || '<option value="">no models available</option>';
  $('#q-model').title = (m.models||[]).length + ' models this endpoint serves'
    + ((m.untested||[]).length ? '; ' + m.untested.length + ' not offered (unverified here)' : '');
  return m;
}

async function draftSelected(btn){
  const ids = [...document.querySelectorAll('.q-pick:checked')].map(i=>i.value);
  const out = $('#q-gen-result');
  if(!ids.length){ out.innerHTML = flag('Tick someone in the list above first.','warn'); return; }
  // Checked up front rather than after 20 failed calls: without a key every
  // single request comes back an error, and the first one tells the whole story.
  const st = await api('GET','/api/queue/status');
  if(!st.llm || !st.llm.configured){
    out.innerHTML = flagHtml('<b>No model configured, so nothing was drafted.</b><br>'
      + esc(st.llm.reason||'')
      + '<br><br>Add this to <code>config.json</code> and restart:<br>'
      + '<code>"llm": {"api_key": "sk-...", "base_url": "https://api.openai.com/v1", '
      + '"model": "gpt-4o-mini"}</code>','bad');
    return;
  }
  const cv = $('#q-draft-cv').value||null;
  const note = $('#q-gen-note').value||'';
  const model = $('#q-model').value||'';
  btn.disabled = true;
  let made = 0, failed = 0, last = '';
  for(const id of ids){
    const r = await api('POST','/api/queue/draft',
      {contact_id:+id, cv_id:cv, note, model});
    if(r.error){ failed++; last = r.error; } else made++;
    out.innerHTML = flag('Drafting… '+made+' done, '+failed+' failed'
      + (failed?' — '+last:''),'gray');
  }
  btn.disabled = false;
  out.innerHTML = flagHtml(failed
    ? (made+' drafted, '+failed+' failed<br>'+esc(last))
    : (made+' drafted — the first one is on the right'), failed?'warn':'good');
  loadQueue(); loadQueueStatus(); loadTodo();
  await showNextDraft();
}

async function loadHistory(){
  const rows = await api('GET','/api/history?limit=200');
  $('#h-count').textContent = rows.length+' recorded';
  $('#q-history').innerHTML =
    `<thead><tr><th style="width:140px">when</th><th>person</th><th>firm</th>
      <th>address</th><th style="width:88px">trust</th><th>subject</th></tr></thead><tbody>`+
    rows.map(t=>`<tr>
      <td class="muted">${esc((t.sent_at||'').replace('T',' ').slice(0,16))}</td>
      <td>${esc(((t.first_name||'')+' '+(t.last_name||'')).trim()||'—')}</td>
      <td>${esc(t.firm||t.company_name||'')}</td>
      <td>${esc(t.to_addr||'')}</td>
      <td>${addrKindTag(t.addr_kind)}</td>
      <td>${esc((t.subject||'').slice(0,60))}</td>
    </tr>`).join('')+'</tbody>';
}

/* ---------- CV variants ---------- */
let currentCvId = null;

async function loadCvPickers(){
  const cvs = await api('GET','/api/cvs');
  // Only validated variants appear here: this is the picker that decides what
  // gets attached to a real mail, so the status filter is the gate, not a nicety.
  const ok = cvs.variants.filter(v=>v.status==='validated');
  const opt = '<option value="">Base CV (no attachment)</option>'
    + ok.map(v=>`<option value="${v.id}">${esc(v.name)}</option>`).join('');
  $('#q-cv').innerHTML = opt;
  $('#q-draft-cv').innerHTML = opt;
}

async function loadCvsTab(){
  const firms = await api('GET','/api/companies');
  const keep = $('#cv-firm').value;
  $('#cv-firm').innerHTML = '<option value="">Pick a firm…</option>'
    + firms.map(f=>`<option value="${f.id}">${esc(f.name)}</option>`).join('');
  $('#cv-firm').value = keep;
  loadCvs();
}

async function loadCvs(){
  const r = await api('GET','/api/cvs');
  $('#cv-base-info').textContent = r.base_present
    ? r.base_bytes+' characters, stored in My profile'
    : 'none yet — paste one in My profile first';
  $('#cv-table').innerHTML =
    `<thead><tr><th>name</th><th>written for</th><th style="width:96px">status</th>
      <th style="width:64px">size</th><th style="width:120px">made by</th></tr></thead><tbody>`+
    r.variants.map(v=>`<tr onclick="openCv(${v.id})"
      style="${v.id===currentCvId?'background:var(--accent-soft)':''}">
      <td><b>${esc(v.name)}</b>${v.used_by?`<span class="muted"> · used ${v.used_by}x</span>`:''}</td>
      <td>${esc(v.firm||'')} ${v.role_target?`<span class="muted">${esc(v.role_target)}</span>`:''}</td>
      <td><span class="tag ${v.status==='validated'?'good':(v.status==='rejected'?'gray':'warn')}">${esc(v.status)}</span></td>
      <td class="muted">${v.bytes||0}</td>
      <td class="muted">${esc(v.generation||'manual')}</td>
    </tr>`).join('')+'</tbody>';
}

async function openCv(id){
  currentCvId = id;
  const v = await api('POST','/api/cvs/get',{id});
  if(v.error) return;
  $('#cv-name').value = v.name||'';
  $('#cv-body').value = v.body||'';
  $('#cv-edit-title').innerHTML = 'Editing: <span class="muted">'+esc(v.name)+'</span>';
  $('#cv-saved').textContent = '';
  loadCvs();
}

async function proposeCv(){
  const firm = $('#cv-firm').value;
  $('#cv-propose-result').innerHTML = flag('Asking the model...','gray');
  const r = await api('POST','/api/cvs/propose',
    {company_id:firm?+firm:null, role_target:$('#cv-role').value||''});
  if(r.error){ $('#cv-propose-result').innerHTML = flag(r.error,'bad'); return; }
  $('#cv-propose-result').innerHTML = flag('Proposed: '+r.variant.name
    +'. It is NOT approved - read it, edit it, then Validate.', 'warn');
  currentCvId = r.variant.id;
  $('#cv-name').value = r.variant.name;
  $('#cv-body').value = r.variant.body;
  $('#cv-edit-title').innerHTML = 'Reviewing: <span class="muted">'
    +esc(r.variant.name)+'</span>';
  loadCvs();
}

async function saveCv(){
  if(!currentCvId){ newCv(); return; }
  const r = await api('POST','/api/cvs/save',
    {id:currentCvId, name:$('#cv-name').value, body:$('#cv-body').value});
  $('#cv-saved').textContent = r.error ? r.error : 'saved';
  loadCvs(); loadCvPickers();
}

async function newCv(){
  const r = await api('POST','/api/cvs/create', {name:$('#cv-name').value||'untitled',
    body:$('#cv-body').value, company_id:$('#cv-firm').value||null});
  if(r.error){ $('#cv-saved').textContent = r.error; return; }
  currentCvId = r.id;
  $('#cv-saved').textContent = 'created';
  loadCvs(); loadCvPickers();
}

async function reviewCv(decision){
  if(!currentCvId){ $('#cv-saved').textContent = 'pick a variant first'; return; }
  if(decision==='validated' && !confirm('Validate this CV? A validated variant can be attached '
    +'to a queued mail. The base CV is never changed either way.')) return;
  const r = await api('POST','/api/cvs/review',{id:currentCvId, decision});
  $('#cv-saved').textContent = r.error ? r.error : decision;
  loadCvs(); loadCvPickers();
}

function openBaseCv(){
  document.querySelector('#nav button[data-tab="profile"]').click();
  setTimeout(()=>{ const t=$('#p-cv'); if(t) t.scrollIntoView({behavior:'smooth'}); }, 60);
}
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
          ${(r.status==='sent')?`<button class="ghost sm" onclick="queueFollowup(${r.id})">Follow up</button>`:''}
          <button class="ghost sm" onclick="del('outreach',${r.id},loadOutreach)">×</button></td></tr>`).join('')+'</tbody>';
  bindCells('#otable','outreach', loadOutreach);
}
async function viewMail(id){
  const rows = await api('GET','/api/outreach');
  const r = rows.find(x=>x.id===id);
  if(!r) return;
  // Sent mail is read-only by design: the text is the record of what the person
  // actually received, and an editor that could change it would quietly rewrite
  // history. The buttons below are the only actions offered on it.
  alert((r.first_name||'')+' '+(r.last_name||'')+'  —  '+(r.company_name||'')+'\n\n'
    +'To: '+(r.contact_email||'')+'\nSubject: '+(r.subject||'')+'\n\n'+(r.body||'')
    +'\n\nStatus: '+r.status+(r.next_followup_at?('\nFollow-up due: '+r.next_followup_at):''));
}

// Follow-ups go through the queue like everything else, so a bump can never be
// the one mail sent without being read. Repointed here rather than deleted when
// the Compose tab went.
async function queueFollowup(outreachId){
  const r = await api('POST','/api/queue/followup',{outreach_id:outreachId});
  if(r.error){ alert(r.error); return; }
  document.querySelector('#nav button[data-tab="queue"]').click();
  await loadQueueTab();
  $('#q-result').innerHTML = flag('Follow-up drafted from your template. Read it, then send.','good');
}

// "Write" on a contact: draft for that one person and take you to the result.
// The Write button used to open the Compose tab, which no longer exists, so
// without this the Contacts tab would have had a button pointing at nothing.
async function writeToContact(contactId){
  const r = await api('POST','/api/queue/draft',{contact_id:contactId});
  if(r.error){ alert(r.error); return; }
  document.querySelector('#nav button[data-tab="queue"]').click();
  await loadQueueTab();
  await openQueueRow(r.queue.id);
}
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
