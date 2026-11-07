// UI logic tests:  node tests/ui_logic_test.js   (run from the project root)
const fs=require('fs');
const src=fs.readFileSync('web/app.js','utf8');
const a=src.indexOf("const ICONS"), b=src.indexOf("/* ───────────────────────── state");
const c=src.indexOf("function parseSummary"), d=src.indexOf("function openConfirm");
const e=src.indexOf("function summarize"), f=src.indexOf("const kvChips");
global.matchMedia=()=>({matches:false});
const code = "const esc = s => String(s ?? '').replace(/[&<>\"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', \"'\": '&#39;' }[c]));\n"
  + src.slice(a,b) + "\n" + src.slice(c,d) + "\n" + src.slice(e,f)
  + "\nmodule.exports={md,fmtDate,parseSummary,summarize,esc};";
fs.writeFileSync('/tmp/extracted.js', code);
const m=require('/tmp/extracted.js');
const assert=require('assert');
// markdown
const html=m.md("Here are **two** options:\n\n1. Wed 18 Nov 2026, 09:00\n2. Fri 20 Nov 2026, 10:00\n\nLate cancellation fee is $15 (Source: 02_cancellation_and_rescheduling_policy.md, Late cancellations).");
assert(html.includes('<ol><li>Wed 18 Nov 2026, 09:00</li><li>Fri 20 Nov 2026, 10:00</li></ol>'), html);
assert(html.includes('<strong>two</strong>') && html.includes('class="src"'));
// XSS safety
const evil=m.md('<img src=x onerror=alert(1)> **b**');
assert(!evil.includes('<img') && evil.includes('&lt;img'), evil);
// dates
assert.deepStrictEqual(m.fmtDate('2026-11-18'), {wd:'Wed',day:18,mon:'Nov',year:2026,long:'Wed 18 Nov 2026'});
// confirmation parsing: real summaries produced by the agent
let p=m.parseSummary('BOOK Dr. Leila Haddad (Dermatology), Fri 20 Nov 2026, 09:00 [slot D05-20261120-0900] - reason: Skin problem');
assert.strictEqual(p.action,'BOOK'); assert.deepStrictEqual(p.rows,[['Doctor','Dr. Leila Haddad · Dermatology'],['When','Fri 20 Nov 2026, 09:00'],['Reason','Skin problem']]);
p=m.parseSummary('CANCEL Dr. Amina Rahman (General Medicine), Thu 12 Nov 2026, 09:30 [A-1001]');
assert.strictEqual(p.action,'CANCEL'); assert.strictEqual(p.rows[1][1],'Thu 12 Nov 2026, 09:30');
p=m.parseSummary('RESCHEDULE Dr. Leila Haddad (Dermatology), Wed 18 Nov 2026, 09:00 [A-1003] -> Dr. Leila Haddad (Dermatology), Wed 11 Nov 2026, 09:00 [slot D05-20261111-0900]');
assert.strictEqual(p.action,'RESCHEDULE'); assert(p.rows[0][1].includes('18 Nov') && p.rows[1][1].includes('11 Nov'), JSON.stringify(p));
p=m.parseSummary('BOOK_APPOINTMENT {"slot_id": "X"}'); assert.strictEqual(p.action,'CONFIRM');
// summaries
assert.strictEqual(m.summarize('find_available_slots',{ok:true,count:2}),'2 open slots found');
assert(m.summarize('book_appointment',{ok:true,appointment:{appointment_id:'A-1003',doctor_name:'Dr. X',date:'2026-11-18',start_time:'09:00'}}).startsWith('Booked A-1003'));
assert(m.summarize('book_appointment',{ok:false,error_code:'SLOT_UNAVAILABLE',message:'taken'}).startsWith('SLOT_UNAVAILABLE'));
assert(m.summarize('cancel_appointment',{ok:true,appointment_id:'A-1',late_cancellation:true}).includes('late'));
console.log('JS logic tests passed');
