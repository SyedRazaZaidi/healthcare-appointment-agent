/* CareBridge UI — talks to the real agent over a WebSocket.
   Everything shown in the "Agent mind" panel is a live event from the Python agent
   (plans, MCP tool calls, RAG retrieval, memory writes, confirmations, guardrails). */
(() => {
'use strict';

/* ───────────────────────── helpers ───────────────────────── */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; };
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

const ICONS = {
  send: '<path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  menu: '<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  activity: '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
  layers: '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
  book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
  zap: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
  alert: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  refresh: '<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>',
  server: '<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>',
  heart: '<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>',
  chat: '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/>',
  list: '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
  tool: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  arrow: '<line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
  sun: '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
  moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  user: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
};
const icon = (n, cls = '') => `<svg class="ic ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[n] || ''}</svg>`;
const hydrate = (root = document) => $$('[data-icon]', root).forEach(n => { if (!n.firstElementChild) n.innerHTML = icon(n.dataset.icon); });

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
function fmtDate(iso) {
  const [y, m, d] = String(iso).split('-').map(Number);
  const wd = WEEKDAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  return { wd, day: d, mon: MONTHS[m - 1], year: y, long: `${wd} ${d} ${MONTHS[m - 1]} ${y}` };
}

/* Minimal, safe markdown: escape first, then bold / code / lists / (Source: …) chips. */
function inline(t) {
  return t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\(Source:\s*([^)]+)\)/g, (_, s) => `<span class="src">${icon('book')}${s}</span>`);
}
function md(text) {
  const out = []; let list = null;
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const raw of esc(text).split('\n')) {
    const line = raw.trimEnd();
    const ol = line.match(/^\s*(\d+)[.)]\s+(.*)$/), ul = line.match(/^\s*[-*•]\s+(.*)$/);
    if (ol) { if (list !== 'ol') { close(); out.push('<ol>'); list = 'ol'; } out.push(`<li>${inline(ol[2])}</li>`); }
    else if (ul) { if (list !== 'ul') { close(); out.push('<ul>'); list = 'ul'; } out.push(`<li>${inline(ul[1])}</li>`); }
    else if (!line.trim()) close();
    else { close(); out.push(`<p>${inline(line)}</p>`); }
  }
  close();
  return out.join('');
}

/* ───────────────────────── state ───────────────────────── */
const AVATARS = [['#2dd4bf', '#3b82f6'], ['#a78bfa', '#ec4899'], ['#fbbf24', '#f97316']];
const S = {
  ws: null, retry: null, patient: new URLSearchParams(location.search).get('patient') || 'P1001',
  info: null, patients: [], busy: false, connected: false, thinking: null, sessionId: null,
  confirmId: null, running: [], stats: { plans: 0, tools: 0, replans: 0, guards: 0 }, apptSig: {}, kbLoaded: false,
};
const R = {
  messages: $('#messages'), hero: $('#hero'), input: $('#input'), send: $('#send'), composer: $('#composer'),
  timeline: $('#timeline'), live: $('#live-pill'), liveText: $('#live-text'), toasts: $('#toasts'),
};

/* ───────────────────────── boot ───────────────────────── */
function boot() {
  hydrate();
  $('#send').innerHTML = icon('send');
  $('#open-sidebar').innerHTML = icon('menu');
  $('#close-sidebar').innerHTML = icon('x');
  setTheme(localStorageGet('cb-theme') || 'dark');
  buildHero(); buildQuick(); bindUI();
  loadInfo().then(connect).catch(err => { toast('err', 'Cannot reach the server', String(err)); setConn(false); });
}
function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }

async function loadInfo() {
  const r = await fetch('/api/info');
  if (!r.ok) throw new Error(`/api/info → ${r.status}`);
  S.info = await r.json();
  S.patients = S.info.patients;
  if (!S.patients.some(p => p.patient_id === S.patient)) S.patient = S.patients[0].patient_id;
  const i = S.info;
  $('#st-llm').textContent = i.llm.split(':').pop();
  $('#st-llm').title = i.llm;
  $('#st-mcp').textContent = `${i.mcp.tools.length} tools · stdio`;
  $('#st-mcp').title = i.mcp.tools.join(', ');
  $('#st-rag').textContent = `${i.rag.documents} docs · ${i.rag.chunks} chunks`;
  $('#st-rag').title = `${i.rag.embedder} · threshold ${i.rag.threshold}`;
  const t = new Date(i.clinic_now);
  $('#st-time').textContent = `${WEEKDAYS[t.getDay()]} ${t.getDate()} ${MONTHS[t.getMonth()]} · ${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;
  buildPatientSwitch();
}

const avStyle = i => `--av:linear-gradient(135deg,${AVATARS[i % 3][0]},${AVATARS[i % 3][1]});--av-glow:${AVATARS[i % 3][0]}99`;
const initials = n => n.split(' ').map(x => x[0]).join('').slice(0, 2).toUpperCase();

function buildPatientSwitch() {
  const box = $('#patient-switch'); box.innerHTML = '';
  S.patients.forEach((p, i) => {
    const b = el('button', 'pswitch' + (p.patient_id === S.patient ? ' active' : ''));
    b.setAttribute('role', 'tab');
    b.innerHTML = `<span class="avatar" style="${avStyle(i)}">${initials(p.name)}</span>${esc(p.name.split(' ')[0])}${p.is_new ? ' · new' : ''}`;
    b.onclick = () => switchPatient(p.patient_id);
    box.appendChild(b);
  });
  const idx = S.patients.findIndex(p => p.patient_id === S.patient), p = S.patients[idx];
  const av = $('#patient-avatar'); av.textContent = initials(p.name); av.setAttribute('style', avStyle(idx));
  $('#patient-name').textContent = p.name;
  $('#patient-id').textContent = `${p.patient_id}${p.is_new ? ' · new patient' : ''}`;
}

function switchPatient(id) {
  if (id === S.patient) return;
  S.patient = id;
  history.replaceState(null, '', `?patient=${id}`);
  buildPatientSwitch();
  const old = S.ws; S.ws = null; if (old) old.close();
  clearTimeout(S.retry);
  connect();
}

/* ───────────────────────── websocket ───────────────────────── */
function connect() {
  clearTimeout(S.retry);
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws?patient=${encodeURIComponent(S.patient)}`);
  S.ws = ws;
  ws.onopen = () => setConn(true);
  ws.onmessage = ev => { try { onMessage(JSON.parse(ev.data)); } catch (e) { console.error(e); } };
  ws.onclose = () => { if (S.ws !== ws) return; setConn(false); setBusy(false); S.retry = setTimeout(connect, 2000); };
}
const wsSend = obj => { if (S.ws && S.ws.readyState === 1) { S.ws.send(JSON.stringify(obj)); return true; } return false; };

function setConn(on) {
  S.connected = on;
  $('#dot-conn').classList.toggle('on', on);
  $('#st-conn').textContent = on ? 'live' : 'reconnecting…';
  if (!S.busy) setStatus(on ? 'Idle' : 'Offline');
  R.send.disabled = !on || S.busy;
}

function onMessage(m) {
  switch (m.type) {
    case 'session': return onSession(m);
    case 'trace': return onTrace(m.event);
    case 'turn_start': return setBusy(true);
    case 'assistant': return onAssistant(m);
    case 'confirm_request': return openConfirm(m);
    case 'confirm_timeout': closeConfirm(); return toast('warn', 'Confirmation timed out', 'Nothing was changed.');
    case 'state': return renderState(m);
    case 'session_saved': return toast('ok', 'Session saved to memory', m.summary);
    case 'turn_end': return setBusy(false);
    case 'error': setBusy(false); return toast('err', 'Something went wrong', m.message);
  }
}

/* ───────────────────────── session & chat ───────────────────────── */
function onSession(m) {
  S.sessionId = m.session_id;
  $('#session-chip').textContent = `session ${m.session_id}`;
  resetChat();
  const { facts, summaries } = m.memory_loaded;
  addTimeline('memory', 'layers', `<div class="tl-title"><span class="tag">Session started</span></div>
    <div class="tl-sub">Long-term memory loaded: <b>${facts}</b> saved preference${facts === 1 ? '' : 's'}, <b>${summaries}</b> earlier session${summaries === 1 ? '' : 's'}.</div>`);
}

function resetChat() {
  $$('.msg, .slots, .thinking', R.messages).forEach(n => n.remove());
  R.hero.hidden = false; S.thinking = null;
  $$('.tl', R.timeline).forEach(n => n.remove());
  $('#mind-empty').hidden = false; S.running = [];
  S.stats = { plans: 0, tools: 0, replans: 0, guards: 0 }; renderStats(false);
  setBusy(false);
}

const scrollDown = () => requestAnimationFrame(() => R.messages.scrollTo({ top: R.messages.scrollHeight, behavior: reduceMotion ? 'auto' : 'smooth' }));
function appendMsg(node) {
  R.hero.hidden = true;
  if (S.thinking && S.thinking.isConnected) R.messages.insertBefore(node, S.thinking); else R.messages.appendChild(node);
  scrollDown();
}
function addUser(text) {
  const idx = Math.max(0, S.patients.findIndex(p => p.patient_id === S.patient));
  const n = el('div', 'msg user');
  n.innerHTML = `<div class="msg-av" style="${avStyle(idx)}">${esc(initials(S.patients[idx]?.name || 'You'))}</div><div class="bubble"><p>${esc(text)}</p></div>`;
  appendMsg(n);
}
function onAssistant(m) {
  removeThinking();
  const emergency = m.guardrail === 'emergency';
  const n = el('div', 'msg assistant' + (emergency ? ' alert' : ''));
  const tag = emergency ? `<div class="guard-tag">${icon('shield')} Safety guardrail · answered by code, not the model</div>`
    : (m.guardrail ? `<div class="guard-tag">${icon('alert')} ${esc(m.guardrail.replace(/_/g, ' '))}</div>` : '');
  n.innerHTML = `<div class="msg-av">${icon(emergency ? 'alert' : 'heart')}</div><div class="bubble">${tag}${md(m.text)}</div>`;
  appendMsg(n);
}
function showThinking(text) {
  if (!S.thinking || !S.thinking.isConnected) {
    S.thinking = el('div', 'thinking', `<div class="msg-av">${icon('heart')}</div><div class="bubble"><span class="dots"><i></i><i></i><i></i></span><span class="think-text">${esc(text)}</span></div>`);
    R.hero.hidden = true; R.messages.appendChild(S.thinking); scrollDown();
  } else $('.think-text', S.thinking).textContent = text;
}
const removeThinking = () => { if (S.thinking) S.thinking.remove(); S.thinking = null; };

function addSlots(slots) {
  const wrap = el('div', 'slots', `<div class="slots-head">${icon('calendar')} Available slots <span class="mono">${slots.length}</span></div><div class="slots-grid"></div>`);
  const grid = $('.slots-grid', wrap);
  slots.forEach((s, i) => {
    const d = fmtDate(s.date), b = el('button', 'slot');
    b.style.animationDelay = `${i * 70}ms`;
    b.innerHTML = `<div class="s-day">${d.wd} ${d.day} ${d.mon}</div><div class="s-time">${esc(s.start_time)}</div>
      <div class="s-doc">${esc(s.doctor_name)} · ${esc(s.department)}</div><div class="s-cta">Book this ${icon('arrow')}</div>`;
    b.onclick = () => sendUser(`Please book ${s.doctor_name} on ${d.long} at ${s.start_time}.`);
    grid.appendChild(b);
  });
  appendMsg(wrap);
}

function sendUser(text) {
  text = (text || '').trim();
  if (!text) return;
  if (!S.connected) return toast('warn', 'Not connected', 'Waiting for the server…');
  if (S.busy) return toast('warn', 'One moment', 'The assistant is still working on your last message.');
  addUser(text);
  if (wsSend({ type: 'user', text })) { setBusy(true); showThinking('Reading your message…'); }
  R.input.value = ''; autosize();
}

function setBusy(b) {
  S.busy = b;
  R.send.disabled = b || !S.connected;
  if (!b) { removeThinking(); setStatus(S.connected ? 'Idle' : 'Offline'); } else setStatus('Thinking…', 'busy');
}
function setStatus(text, mode) {
  R.liveText.textContent = text;
  R.live.classList.toggle('busy', mode === 'busy');
  R.live.classList.toggle('wait', mode === 'wait');
  if (S.busy && mode !== 'wait') showThinking(text);
}

/* ───────────────────────── agent mind ───────────────────────── */
function addTimeline(cls, ic, html, extra) {
  $('#mind-empty').hidden = true;
  const n = el('div', `tl ${cls}`, `<div class="tl-ic">${icon(ic)}</div><div class="tl-body">${html}</div>`);
  if (extra) extra(n);
  R.timeline.appendChild(n);
  const panel = $('#panel-mind');
  requestAnimationFrame(() => panel.scrollTo({ top: panel.scrollHeight, behavior: reduceMotion ? 'auto' : 'smooth' }));
  return n;
}
function renderStats(bumpKey) {
  for (const [k, id] of [['plans', 'stat-plans'], ['tools', 'stat-tools'], ['replans', 'stat-replans'], ['guards', 'stat-guards']]) {
    const b = $('#' + id); b.textContent = S.stats[k];
    if (bumpKey === k) { const st = b.parentElement; st.classList.remove('bump'); void st.offsetWidth; st.classList.add('bump'); }
  }
}
const bumpStat = k => { S.stats[k]++; renderStats(k); };

function summarize(name, r) {
  if (!r || typeof r !== 'object') return String(r ?? '').slice(0, 140);
  if (r.ok === false) return `${r.error_code || 'error'} — ${r.message || ''}`;
  const a = r.appointment || {};
  switch (name) {
    case 'find_available_slots': return `${r.count} open slot${r.count === 1 ? '' : 's'} found`;
    case 'list_doctors': return `${(r.doctors || []).length} doctors listed`;
    case 'list_my_appointments': return `${r.count} appointment${r.count === 1 ? '' : 's'} on file`;
    case 'get_slot': return r.slot ? `${r.slot.doctor_name} · ${r.slot.date} ${r.slot.start_time} · ${r.slot.status}` : 'slot';
    case 'book_appointment': return `Booked ${a.appointment_id} — ${a.doctor_name}, ${a.date} ${a.start_time}`;
    case 'reschedule_appointment': return `Rescheduled ${r.old_appointment_id} → ${a.appointment_id} — ${a.doctor_name}, ${a.date} ${a.start_time}`;
    case 'cancel_appointment': return `Cancelled ${r.appointment_id}${r.late_cancellation ? ' · late cancellation, fee may apply' : ''}`;
  }
  return 'ok';
}
const kvChips = obj => `<div class="kv">${Object.entries(obj || {}).map(([k, v]) => `<span>${esc(k)}=<b>${esc(Array.isArray(v) ? v.join(',') : v)}</b></span>`).join('')}</div>`;

function onTrace(ev) {
  switch (ev.kind) {
    case 'plan': case 'replan': {
      const re = ev.kind === 'replan';
      bumpStat(re ? 'replans' : 'plans'); setStatus(re ? 'Replanning…' : 'Planning…', 'busy');
      const steps = (ev.steps || []).map(s => `<li><div class="step-main">${esc(s.step)}${s.tool && s.tool !== '-' ? `<span class="tbadge">${esc(s.tool)}</span>` : ''}${s.why ? `<div class="step-why">${esc(s.why)}</div>` : ''}</div></li>`).join('');
      addTimeline(re ? 'replan' : 'plan', re ? 'refresh' : 'list', `<div class="tl-title"><span class="tag">${re ? 'Replan' : 'Plan'} v${ev.version}</span></div>
        <div class="tl-sub">${esc(ev.goal)}</div>${re && ev.reason ? `<div class="tl-sub" style="color:var(--ink-amber)">↻ ${esc(ev.reason)}</div>` : ''}<ol class="steps">${steps}</ol>`);
      break;
    }
    case 'tool_call': {
      bumpStat('tools'); setStatus(`Calling ${ev.name}…`, 'busy');
      const n = addTimeline('tool', 'server', `<div class="tl-title"><span class="tag">MCP tool call</span><span class="mono">${esc(ev.name)}</span><span class="spin"></span></div>
        <div class="tl-sub">JSON-RPC over stdio → clinic scheduling server</div>${kvChips(ev.args)}`);
      n.dataset.name = ev.name; S.running.push(n);
      break;
    }
    case 'tool_result': return onToolResult(ev);
    case 'rag': return onRag(ev);
    case 'memory': {
      const d = ev.detail;
      let line = '', cls = 'memory', tag = 'Memory';
      if (ev.op === 'remember') { line = `Saved <b>${esc(d.saved.key)}</b> = ${esc(d.saved.value)}`; tag = 'Memory write'; }
      else if (ev.op === 'remember_rejected') { line = esc(d.error || 'rejected'); cls = 'guard'; tag = 'Memory write refused'; }
      else if (ev.op === 'forget') { line = `Forgot <b>${esc(d.key)}</b> (${d.deleted} removed)`; tag = 'Memory forget'; }
      else if (ev.op === 'session_summary_saved') { line = esc(String(d)); tag = 'Session summary'; }
      addTimeline(cls, cls === 'guard' ? 'shield' : 'layers', `<div class="tl-title"><span class="tag">${tag}</span></div><div class="tl-sub">${line}</div>`);
      setStatus('Updating memory…', 'busy');
      break;
    }
    case 'confirm':
      addTimeline(`confirm ${ev.approved ? 'yes' : 'no'}`, ev.approved ? 'check' : 'x',
        `<div class="tl-title"><span class="tag">Human confirmation · ${ev.approved ? 'approved' : 'declined'}</span></div><div class="tl-sub">${esc(ev.summary)}</div>`);
      setStatus('Working…', 'busy');
      break;
    case 'guardrail':
      bumpStat('guards');
      addTimeline('guard', 'shield', `<div class="tl-title"><span class="tag">Guardrail · ${esc(String(ev.rule).replace(/_/g, ' '))}</span></div><div class="tl-sub">${esc(ev.detail)}</div>`);
      break;
    case 'tool_error':
      addTimeline('error', 'alert', `<div class="tl-title"><span class="tag">Error · ${esc(ev.name)}</span></div><div class="tl-sub">${esc(ev.error)}</div>`);
      break;
    case 'info':
      if (/failed|rate limited/i.test(ev.text || '')) addTimeline('info', 'alert', `<div class="tl-sub">${esc(String(ev.text).slice(0, 220))}</div>`);
      break;
  }
}

function onToolResult(ev) {
  const r = ev.result, ok = r && r.ok !== false;
  const idx = S.running.findIndex(n => n.dataset.name === ev.name);
  const card = idx >= 0 ? S.running.splice(idx, 1)[0] : null;
  if (card) {
    card.classList.add(ok ? 'ok' : 'fail');
    $('.spin', card)?.replaceWith(Object.assign(el('span', 'tag', ok ? '✓ done' : '⚠ failed')));
    $('.tl-sub', card).innerHTML = esc(summarize(ev.name, r));
    $('.tl-ic', card).innerHTML = icon(ok ? 'check' : 'alert');
    if (!ok && r.agent_hint) card.querySelector('.tl-body').insertAdjacentHTML('beforeend', `<div class="tl-sub" style="color:var(--ink-amber)">↻ Agent told to replan instead of retrying</div>`);
    const pre = el('pre', 'json'); pre.hidden = true; pre.textContent = JSON.stringify(r, null, 2);
    const tg = el('button', 'code-toggle', 'Show raw result'); tg.onclick = () => { pre.hidden = !pre.hidden; tg.textContent = pre.hidden ? 'Show raw result' : 'Hide raw result'; };
    card.querySelector('.tl-body').append(tg, pre);
  }
  if (ok && ev.name === 'find_available_slots' && r.slots?.length) addSlots(r.slots);
  if (ok && (ev.name === 'book_appointment' || ev.name === 'reschedule_appointment')) {
    confetti(); toast('ok', 'Appointment confirmed', `${r.appointment.appointment_id} · ${r.appointment.doctor_name} · ${fmtDate(r.appointment.date).long}, ${r.appointment.start_time}`);
  }
  if (!ok && r.error_code === 'SLOT_UNAVAILABLE') toast('warn', 'That slot was just taken', 'The agent is replanning with alternatives…');
  setStatus('Thinking…', 'busy');
}

function onRag(ev) {
  bumpStat('tools'); setStatus('Reading clinic documents…', 'busy');
  const thr = S.info?.rag?.threshold ?? 0.25;
  const ps = ev.passages || [];
  const body = ps.length ? ps.map(p => `<div class="passage"><div class="p-head">${icon('file')}<span class="p-src">${esc(p.source.replace(/\.md$/, ''))} · ${esc(p.section)}</span><span class="p-score">${p.score.toFixed(2)}</span></div>
      <div class="bar"><i data-w="${Math.min(100, p.score * 100)}"></i><u style="left:${thr * 100}%"></u></div></div>`).join('')
    : `<div class="passage below"><div class="p-head">${icon('alert')}<span>No passage cleared the evidence threshold — the agent must say it has no evidence.</span></div></div>`;
  const n = addTimeline('rag', 'search', `<div class="tl-title"><span class="tag">RAG retrieval</span><span class="tbadge">${ps.length} hit${ps.length === 1 ? '' : 's'}</span></div>
    <div class="tl-sub">“${esc(ev.query)}”</div>${body}<div class="cutoff">evidence threshold ${thr}</div>`);
  requestAnimationFrame(() => requestAnimationFrame(() => $$('.bar i', n).forEach(i => { i.style.width = i.dataset.w + '%'; })));
}

/* ───────────────────────── state panels ───────────────────────── */
function renderState(m) {
  renderAppts(m.appointments || []);
  renderMemory(m.memory || { facts: [], summaries: [] }, m.audit || []);
}

const STATUS_LABEL = { confirmed: 'Confirmed', cancelled_by_clinic: 'Cancelled by clinic', cancelled_by_patient: 'Cancelled', rescheduled: 'Rescheduled' };
function renderAppts(list) {
  const box = $('#appts-list'); box.innerHTML = '';
  $('#badge-appts').textContent = list.filter(a => a.status === 'confirmed').length;
  if (!list.length) { box.innerHTML = `<div class="empty"><div class="empty-art" data-icon="calendar"></div><b>No upcoming appointments</b><span>Ask the assistant to find you a slot.</span></div>`; hydrate(box); return; }
  const next = {};
  list.forEach(a => {
    const d = fmtDate(a.date), changed = S.apptSig[a.appointment_id] !== undefined && S.apptSig[a.appointment_id] !== a.status;
    const isNew = S.apptSig[a.appointment_id] === undefined && Object.keys(S.apptSig).length > 0;
    next[a.appointment_id] = a.status;
    box.appendChild(el('div', `appt ${a.status}${changed || isNew ? ' flash' : ''}`, `
      <div class="date-badge"><small>${d.mon}</small><b>${d.day}</b><span>${d.wd}</span></div>
      <div class="a-main"><div class="a-doc">${esc(a.doctor_name)}</div>
        <div class="a-sub">${esc(a.department)} · ${esc(a.start_time)}${a.reason ? ' · ' + esc(a.reason) : ''}</div>
        <span class="status ${a.status}">${STATUS_LABEL[a.status] || esc(a.status)}</span>
        ${a.action_required ? `<div class="a-note">${esc(a.action_required)}</div>` : ''}</div>
      <span class="a-id">${esc(a.appointment_id)}</span>`));
  });
  S.apptSig = next;
}

function renderMemory(mem, audit) {
  $('#badge-memory').textContent = mem.facts.length;
  const facts = $('#facts'); facts.innerHTML = '';
  if (!mem.facts.length) facts.innerHTML = `<div class="note">Nothing saved yet. Tell the assistant a preference — “I prefer mornings” — and watch it appear here.</div>`;
  mem.facts.forEach((f, i) => {
    const n = el('div', 'fact', `<div><div class="f-cat">${esc(f.category.replace(/_/g, ' '))}</div><b>${esc(f.key.replace(/_/g, ' '))}</b>: ${esc(f.value)}</div>`);
    n.style.animationDelay = `${i * 60}ms`;
    const b = el('button', '', icon('trash')); b.title = 'Forget this'; b.setAttribute('aria-label', `Forget ${f.key}`);
    b.onclick = () => { wsSend({ type: 'forget', key: f.key }); toast('ok', 'Forgotten', f.key.replace(/_/g, ' ')); };
    n.appendChild(b); facts.appendChild(n);
  });
  const sums = $('#summaries'); sums.innerHTML = '';
  if (!mem.summaries.length) sums.innerHTML = `<div class="note">No earlier sessions.</div>`;
  mem.summaries.slice().reverse().forEach(s => sums.appendChild(el('div', 'note', `<time>${esc(String(s.created_at).slice(0, 10))}</time>${esc(s.summary)}`)));
  const au = $('#audit'); au.innerHTML = '';
  if (!audit.length) au.innerHTML = `<div class="note">No booking changes yet. Every approved or declined action is logged here.</div>`;
  audit.slice().reverse().forEach(r => {
    const state = r.human_confirmed === 0 ? 'declined' : (r.ok ? 'ok' : 'failed');
    const label = state === 'declined' ? 'Declined by patient' : state === 'ok' ? 'Approved · succeeded' : 'Approved · failed';
    au.appendChild(el('div', `audit ${state}`, `<div class="a-ic">${icon(state === 'ok' ? 'check' : state === 'declined' ? 'x' : 'alert')}</div><div><b>${esc(r.tool.replace(/_/g, ' '))}</b> · ${label}<small>${esc(r.summary)}</small></div>`));
  });
}

/* knowledge base */
let kbTimer = null;
async function kbSearch(q) {
  const box = $('#kb-results');
  if (!q.trim()) { box.innerHTML = ''; $('#kb-hint').hidden = false; return; }
  $('#kb-hint').hidden = true;
  try {
    const r = await (await fetch('/api/kb/search?q=' + encodeURIComponent(q))).json();
    box.innerHTML = `<div class="cutoff">evidence threshold ${r.threshold}</div>`;
    r.results.forEach(p => {
      const n = el('div', 'passage' + (p.above ? '' : ' below'), `<div class="p-head">${icon('file')}<span class="p-src">${esc(p.source.replace(/\.md$/, ''))} · ${esc(p.section)}</span><span class="p-score">${p.score.toFixed(2)}</span></div>
        <div class="bar"><i data-w="${Math.min(100, p.score * 100)}"></i><u style="left:${r.threshold * 100}%"></u></div><div class="p-text">${esc(p.text)}</div>`);
      box.appendChild(n);
    });
    requestAnimationFrame(() => requestAnimationFrame(() => $$('.bar i', box).forEach(i => { i.style.width = i.dataset.w + '%'; })));
  } catch (e) { box.innerHTML = `<div class="note">Search failed: ${esc(e)}</div>`; }
}
async function loadDocs() {
  if (S.kbLoaded) return; S.kbLoaded = true;
  try {
    const docs = await (await fetch('/api/kb/docs')).json();
    $('#kb-count').textContent = `${docs.length} files · ${docs.reduce((a, d) => a + d.chunks, 0)} chunks`;
    const box = $('#kb-docs'); box.innerHTML = '';
    docs.forEach(d => box.appendChild(el('div', 'doc', `<div class="d-ic">${icon('file')}</div><div><b>${esc(d.title)}</b><small>${esc(d.source)}</small></div><span class="d-n">${d.chunks} chunks</span>`)));
  } catch { S.kbLoaded = false; }
}

/* ───────────────────────── confirmation dialog ───────────────────────── */
function parseSummary(s) {
  const m = s.match(/^(BOOK|CANCEL|RESCHEDULE)\s+(.*)$/s);
  if (!m) return { action: 'CONFIRM', rows: [['Action', s]] };
  const [, action, rest] = m;
  const one = t => {
    const x = t.match(/^(.*?)\s*\(([^)]+)\),\s*([^[]+?)\s*\[(?:slot )?([^\]]+)\](?:\s*-\s*reason:\s*(.*))?$/);
    if (!x) return [['Details', t]];
    const rows = [['Doctor', `${x[1]} · ${x[2]}`], ['When', x[3]]];
    if (x[5]) rows.push(['Reason', x[5]]);
    return rows;
  };
  if (action === 'RESCHEDULE') {
    const [from, to] = rest.split(' -> ');
    const f = one(from || ''), t = one(to || '');
    return { action, rows: [['From', f.map(r => r[1]).slice(0, 2).join(' — ')], ['To', t.map(r => r[1]).slice(0, 2).join(' — '), 'move']] };
  }
  return { action, rows: one(rest) };
}
function openConfirm(m) {
  S.confirmId = m.id;
  const p = parseSummary(m.summary);
  const a = $('#modal-action'); a.textContent = p.action; a.classList.toggle('danger', p.action === 'CANCEL');
  $('#modal-detail').innerHTML = p.rows.map(r => `<div class="md-row ${r[2] || ''}"><small>${esc(r[0])}</small><b>${esc(r[1])}</b></div>`).join('');
  const bar = $('#countdown-bar'); bar.style.animation = 'none'; void bar.offsetWidth;
  if (!reduceMotion) bar.style.animation = `drain ${m.timeout}s linear forwards`;
  $('#modal').hidden = false; $('#approve').focus();
  setStatus('Waiting for your confirmation', 'wait');
}
function answerConfirm(approved) {
  if (!S.confirmId) return;
  wsSend({ type: 'confirm', id: S.confirmId, approved });
  closeConfirm();
  setStatus(approved ? 'Applying your change…' : 'Okay, leaving things as they are…', 'busy');
}
function closeConfirm() { S.confirmId = null; $('#modal').hidden = true; }

/* ───────────────────────── toasts, confetti ───────────────────────── */
function toast(kind, title, text) {
  const n = el('div', `toast ${kind}`, `<div class="t-ic">${icon(kind === 'ok' ? 'check' : kind === 'err' ? 'x' : 'alert')}</div><div><b>${esc(title)}</b><span>${esc(text || '')}</span></div>`);
  R.toasts.appendChild(n);
  setTimeout(() => { n.classList.add('out'); setTimeout(() => n.remove(), 450); }, 4600);
}
function confetti() {
  if (reduceMotion) return;
  const cv = $('#confetti'), cx = cv.getContext('2d'), W = cv.width = innerWidth, H = cv.height = innerHeight;
  const colors = ['#2dd4bf', '#60a5fa', '#a78bfa', '#f472b6', '#fbbf24'];
  const ps = Array.from({ length: 140 }, () => ({
    x: W / 2 + (Math.random() - .5) * W * .25, y: H * .62, vx: (Math.random() - .5) * 15, vy: -Math.random() * 17 - 6, g: .4,
    s: Math.random() * 7 + 4, r: Math.random() * 6.28, vr: (Math.random() - .5) * .4, c: colors[(Math.random() * colors.length) | 0], life: 1,
  }));
  let t = 0;
  (function frame() {
    cx.clearRect(0, 0, W, H);
    for (const p of ps) {
      p.vy += p.g; p.x += p.vx; p.y += p.vy; p.vx *= .99; p.r += p.vr; p.life -= .008;
      cx.save(); cx.globalAlpha = Math.max(p.life, 0); cx.translate(p.x, p.y); cx.rotate(p.r); cx.fillStyle = p.c; cx.fillRect(-p.s / 2, -p.s / 2, p.s, p.s * .6); cx.restore();
    }
    if (++t < 170) requestAnimationFrame(frame); else cx.clearRect(0, 0, W, H);
  })();
}

/* ───────────────────────── UI wiring ───────────────────────── */
const HERO = [
  { ic: 'calendar', c: 'teal', t: 'Find a dermatologist', d: 'Morning slots, skipping Mondays', p: "I'd like to see a dermatologist next week. I prefer mornings and I can never do Mondays." },
  { ic: 'book', c: 'blue', t: 'Ask about policies', d: 'Grounded answers with sources', p: 'What happens if I have to cancel at the last minute?' },
  { ic: 'server', c: 'violet', t: 'Check my bookings', d: 'Live data through MCP', p: 'Which appointments do I have booked?' },
  { ic: 'layers', c: 'pink', t: 'Remember my preferences', d: 'Saved to long-term memory', p: 'Please remember that I prefer morning appointments.' },
];
const QUICK = ['Do you offer telehealth?', 'What are the clinic hours?', 'Reschedule my appointment', 'Cancel my check-up', 'What medicine should I take for a headache?', 'I have chest pain'];

function buildHero() {
  const g = $('#hero-grid');
  HERO.forEach((h, i) => {
    const n = el('button', 'hero-card', `<div class="hc-ic">${icon(h.ic)}</div><div><b>${h.t}</b><span>${h.d}</span></div>`);
    n.style.cssText = `--c:var(--${h.c});--c-ink:var(--ink-${h.c});animation-delay:${200 + i * 90}ms`;
    n.onclick = () => sendUser(h.p); g.appendChild(n);
  });
}
function buildQuick() {
  const q = $('#quick');
  QUICK.forEach(t => { const b = el('button', 'chip-btn', esc(t)); b.type = 'button'; b.onclick = () => sendUser(t); q.appendChild(b); });
}
function autosize() { R.input.style.height = 'auto'; R.input.style.height = Math.min(R.input.scrollHeight, 140) + 'px'; }

function setTheme(t) {
  document.documentElement.dataset.theme = t; localStorageSet('cb-theme', t);
  $('#theme-toggle').innerHTML = icon(t === 'dark' ? 'sun' : 'moon');
  document.querySelector('meta[name="theme-color"]').content = t === 'dark' ? '#060a16' : '#eef2fb';
}

async function simulate(event, btn) {
  btn.classList.add('loading');
  try {
    const r = await fetch('/api/simulate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ event, patient: S.patient }) });
    const j = await r.json();
    toast(j.ok ? (event === 'reset' ? 'ok' : 'warn') : 'err', j.ok ? 'Change event injected' : 'Could not inject', j.message);
    if (j.ok) {
      addTimeline('replan', 'zap', `<div class="tl-title"><span class="tag">Simulation</span></div><div class="tl-sub">${esc(j.message)}</div>`);
      if (event === 'reset') { S.apptSig = {}; wsSend({ type: 'new_session' }); } else wsSend({ type: 'refresh' });
    }
  } catch (e) { toast('err', 'Request failed', String(e)); }
  btn.classList.remove('loading');
}

function bindUI() {
  R.composer.addEventListener('submit', e => { e.preventDefault(); sendUser(R.input.value); });
  R.input.addEventListener('input', autosize);
  R.input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendUser(R.input.value); } });
  $('#approve').onclick = () => answerConfirm(true);
  $('#decline').onclick = () => answerConfirm(false);
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && S.confirmId) answerConfirm(false); });
  $('#new-session').onclick = () => { if (S.busy) return toast('warn', 'One moment', 'Wait for the assistant to finish.'); wsSend({ type: 'new_session' }); closeSidebar(); };
  $('#theme-toggle').onclick = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
  $$('.sim-btn').forEach(b => b.onclick = () => simulate(b.dataset.sim, b));
  $$('.tab').forEach(t => t.onclick = () => {
    $$('.tab').forEach(x => x.classList.toggle('active', x === t));
    $$('.panel').forEach(p => p.classList.toggle('active', p.id === 'panel-' + t.dataset.tab));
    if (t.dataset.tab === 'kb') loadDocs();
  });
  $('#kb-q').addEventListener('input', e => { clearTimeout(kbTimer); kbTimer = setTimeout(() => kbSearch(e.target.value), 260); });
  $$('#mobile-tabs button').forEach(b => b.onclick = () => {
    $$('#mobile-tabs button').forEach(x => x.classList.toggle('active', x === b));
    $('#app').className = 'app view-' + b.dataset.view;
  });
  $('#open-sidebar').onclick = () => $('#sidebar').classList.add('open');
  $('#close-sidebar').onclick = closeSidebar;
  $('#app').addEventListener('click', e => { if ($('#sidebar').classList.contains('open') && !e.target.closest('#sidebar') && !e.target.closest('#open-sidebar')) closeSidebar(); });
}
const closeSidebar = () => $('#sidebar').classList.remove('open');

boot();
})();
