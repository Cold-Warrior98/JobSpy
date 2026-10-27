"""Manual career-search pipeline. Decisions are provisional, never applications."""
import argparse
import csv
import hashlib
import html
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, urlunsplit

QUERIES = ['operations analyst', 'business operations analyst', 'business analyst',
           'supply chain analyst', 'inventory analyst', 'planning analyst']
CITIES = ['Bengaluru', 'Hyderabad']
EVIDENCE = {
    'excel': 'Advanced Excel and operational reporting',
    'tableau': 'Inventory and workflow dashboards in projects',
    'power bi': 'Power Query, relationships and basic dashboard work; no DAX',
    'sql': 'Foundational SQL; no window functions or CTEs',
    'inventory': 'Academic inventory analysis across 6000+ SKUs',
    'process': 'Workflow analysis and process documentation',
    'stakeholder': 'Operational coordination and stakeholder follow-up',
    'requirements': 'Business analysis internship documentation',
}
GAPS = ['dax', 'window functions', 'sap configuration', 'd365 implementation',
        'transportation planning', 'erp implementation', 'advanced python']

def clean(value):
    return '' if value is None or str(value).lower() in ('nan', 'none', 'nat') else str(value).strip()

def norm(value):
    return re.sub(r'[^a-z0-9]+', ' ', clean(value).lower()).strip()

def fingerprint(row):
    return '|'.join(norm(row.get(k)) for k in ('company', 'title', 'location'))

def write_csv(path, rows, fields=None):
    fields = fields or list(dict.fromkeys(k for row in rows for k in row))
    fields = fields or ['title', 'company', 'job_url', 'decision', 'reason']
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            safe = {}
            for k, v in row.items():
                v = clean(v)
                safe[k] = "'" + v if v.lstrip().startswith(('=', '+', '-', '@')) else v
            writer.writerow(safe)

def self_test():
    base = dict(title='Operations Analyst', company='Example', location='Bengaluru', job_url='https://example.org/job/1',
                description='Requires 0-2 years of experience. Excel and SQL operational reporting. ' * 3)
    assert screen(base)['decision'] == 'Apply candidate'
    assert screen(dict(base, title='Senior Operations Analyst'))['decision'] == 'Skip'
    assert screen(dict(base, description=''))['decision'] == 'Review'
    assert screen(dict(base, description='Minimum 5 years experience. Excel and SQL. ' * 4))['decision'] == 'Skip'
    assert screen(dict(base, description='3 years experience. Excel reporting. ' * 6))['decision'] == 'Stretch'
    assert screen(dict(base, description='Preferred 5 years experience. Excel reporting. ' * 5))['experience_floor'] is None
    assert screen(dict(base, description=base['description']+' DAX required.'))['decision'] == 'Review'
    assert screen(dict(base, title='Middle Office Financial Operations'))['decision'] == 'Skip'
    assert len(deduplicate([dict(base, job_url='https://example.org/job/1?a=1'), dict(base, job_url='https://example.org/job/1?a=2')])) == 1
    assert len(deduplicate([dict(base, job_url='https://a.org/1'), dict(base, job_url='https://b.org/2')])) == 1
    assert len(deduplicate([dict(base, company='', job_url='https://a.org/1'), dict(base, company='', job_url='https://b.org/2')])) == 2
    assert screen(dict(base, experience_range='0-2 Yrs', description='Excel and SQL reporting. '*12))['decision'] == 'Apply candidate'
    assert screen(dict(base, experience_range='4-6', description='Excel inventory reporting. '*12))['decision'] == 'Skip'
    assert screen(dict(base, experience_range='3-5 years', description='Excel reporting. '*20))['decision'] == 'Stretch'
    assert screen(dict(base, job_url=''))['decision'] == 'Review'
    assert canonical('https://in.indeed.com/viewjob?jk=1&utm_source=x') != canonical('https://in.indeed.com/viewjob?jk=2')
    assert canonical('javascript:alert(1)') == ''
    assert canonical('https://a.org/job/1?utm_source=x') == canonical('https://a.org/job/1?utm_source=y')
    assert screen(dict(base, description='Experience with Excel. Preferred 5 years. '*10))['experience_floor'] is None
    assert screen(dict(base, description='Minimum 6 months experience. Excel and SQL. '*10))['experience_floor'] is None
    assert screen(dict(base, title='Looking For International Voice Process-CTC-5.5LPA'))['decision'] == 'Skip'
    assert screen(dict(base, title='Immediate requirement For Legal Executive position'))['decision'] == 'Skip'
    assert screen(dict(base, title='Customer Support Openings'))['decision'] == 'Skip'
    assert screen(dict(base, title='Data Analyst'))['decision'] == 'Apply candidate'
    assert screen(dict(base, title='Purchase Executive'))['decision'] == 'Apply candidate'
    for span in ['1-6 Yrs', '0-5 years', '1-3 Yrs']:
        assert screen(dict(base, experience_range=span))['decision'] == 'Review'
    now = datetime(2026,10,27,12,tzinfo=timezone.utc)
    assert freshness({'date_posted':'2026-10-27T10:00:00Z'},24,now)['freshness_status'] == 'Within requested window'
    assert freshness({'date_posted':'2026-10-25'},24,now)['freshness_status'] == 'Outside requested window'
    assert freshness({'date_posted':'2026-10-26'},24,now)['freshness_status'] == 'Uncertain date precision'
    assert freshness({},24,now)['freshness_status'] == 'Missing or invalid date'
    assert freshness({'date_posted':'2026-10-28'},24,now)['freshness_status'] == 'Future date'
    print('33 screening, deduplication and freshness checks passed')


def experience_signals(row):
    """Read structured source fields and explicit experience statements separately."""
    patterns = [r'(?<!\d)(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*(?:years?|yrs?)\b',
                r'(?<!\d)(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b']
    signals = []
    for key in ['experience_range', 'experience', 'experience_required']:
        raw = clean(row.get(key))
        if raw and re.fullmatch(r'\d{1,2}\s*(?:-|–|to)\s*\d{1,2}', raw):
            raw += ' years'
        for pattern in patterns:
            found = list(re.finditer(pattern, raw.lower()))
            if found:
                signals.append((int(found[0].group(1)), key+': '+raw)); break
    for sentence in re.split(r'[\n.;]', clean(row.get('description')).lower()):
        if not re.search(r'\bexperience\b', sentence):
            continue
        if re.search(r'preferred|desirable|advantage|nice.to.have|not required|no prior', sentence):
            continue
        for pattern in patterns:
            found = list(re.finditer(pattern, sentence))
            if found:
                signals.extend((int(m.group(1)), sentence.strip()[:250]) for m in found)
                break
    return signals

def freshness(row, hours, now=None):
    """Date-only source values are intervals in UTC, never invented timestamps."""
    now = now or datetime.now(timezone.utc)
    raw = clean(row.get('date_posted'))
    status = 'Missing or invalid date'
    try:
        posted = datetime.fromisoformat(raw.replace('Z','+00:00'))
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        cutoff = now - timedelta(hours=hours)
        date_only = bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[ T]00:00:00)?', raw))
        if posted > now:
            status = 'Future date'
        elif date_only and posted < cutoff < posted + timedelta(days=1):
            status = 'Uncertain date precision'
        elif posted < cutoff:
            status = 'Outside requested window'
        else:
            status = 'Within requested window'
    except (ValueError, TypeError):
        pass
    return dict(freshness_status=status, requested_hours=hours,
                freshness_basis='Source date_posted; UTC assumed when timezone absent')


def screen(row):
    title = clean(row.get('title')).lower()
    desc = clean(row.get('description')).lower()
    text = title + ' ' + desc
    matched = [key for key in EVIDENCE if re.search(r'\b'+re.escape(key)+r'\b', text)]
    gaps = [key for key in GAPS if key in desc]
    signals = experience_signals(row)
    minimum = max((n for n, _ in signals), default=None)
    ceilings = []
    for field in ['experience_range','experience','experience_required','description']:
        value = clean(row.get(field)).lower()
        if field != 'description' and re.fullmatch(r'\d{1,2}\s*(?:-|–|to)\s*\d{1,2}',value):
            value += ' years'
        for part in re.split(r'[\n.;]',value):
            if field == 'description' and ('experience' not in part or re.search(r'preferred|desirable|advantage|nice.to.have',part)):
                continue
            ceilings.extend(int(m.group(2)) for m in re.finditer(r'(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*(?:years?|yrs?)\b',part))
    maximum = max(ceilings,default=None)
    relevant = bool(re.search(r'operations|business analyst|supply chain|inventory|planning|procurement|purchase|process analyst|process improvement|excellence|program operations|data analyst', title))
    decision, reason = 'Review', 'Read JD: experience or requirements not established'
    if re.search(r'voice|\bbpo\b|telecall|call cent|customer (support|service)|legal executive|training department|business development|sales executive', title):
        decision, reason = 'Skip', 'Role family outside operations, analytics and supply-chain transition'
    elif re.search(r'\b(senior|sr\.?|lead|manager|director|head|principal)\b', title):
        decision, reason = 'Skip', 'Seniority signal outside primary entry-level search; manual override available'
    elif re.search(r'financial operations|middle office|investment|accounting|tax analyst|credit analyst', title):
        decision, reason = 'Skip', 'Finance-focused title outside primary target'
    elif minimum is not None and minimum >= 4:
        decision, reason = 'Skip', 'Source or JD states four or more years minimum experience'
    elif not relevant:
        decision, reason = 'Skip', 'No target-role signal in title; available under All records for manual override'
    elif len(desc) < 150:
        reason = 'Missing or short JD; recover description before deciding'
    elif minimum == 3:
        decision, reason = 'Stretch', 'Three years stated; confirm flexibility before preparing application'
    elif maximum is not None and maximum > 2:
        reason = 'Broad experience range; inspect actual JD before recommending'
    elif gaps:
        reason = 'Potential skill gaps; check which are mandatory'
    elif minimum is not None and minimum <= 2 and len(matched) >= 2:
        decision, reason = 'Apply candidate', 'Entry-level experience signal and evidence overlap; qualification and domain checks still required'
    if not clean(row.get('job_url')):
        decision, reason = 'Review', 'No source URL; cannot act on this record'
    return dict(decision=decision, reason=reason, experience_floor=minimum, experience_ceiling=maximum,
                experience_evidence=' | '.join(s for _,s in signals), evidence_matches='; '.join(matched),
                supporting_evidence='; '.join(EVIDENCE[k] for k in matched), gaps_to_check='; '.join(gaps),
                application_status='Not applied', live_status='Not checked', manual_review_required='Yes')

def canonical(url):
    from urllib.parse import parse_qsl, urlencode
    p = urlsplit(clean(url))
    if p.scheme.lower() not in ['http','https'] or not p.netloc:
        return ''
    query = [(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ['trackingid','refid','trk','source']]
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path.rstrip('/'),urlencode(sorted(query)),''))

def deduplicate(rows):
    unique, urls, content_keys = [], set(), set()
    for row in sorted(rows,key=lambda r:len(clean(r.get('description'))),reverse=True):
        url = canonical(row.get('job_url'))
        desc = norm(row.get('description'))
        key = fingerprint(row)+'|'+desc
        usable = all(clean(row.get(k)) for k in ['company','title','location']) and len(desc)>100
        if (url and url in urls) or (usable and key in content_keys):
            continue
        if url: urls.add(url)
        if usable: content_keys.add(key)
        unique.append(row)
    return unique

def check_link(row):
    """Accessibility is not proof that the employer is still accepting applications."""
    import requests
    url = canonical(row.get('job_url_direct')) or canonical(row.get('job_url'))
    if not url: return 'Missing or invalid URL'
    try:
        response = requests.get(url,timeout=12,headers={'User-Agent':'Mozilla/5.0'},allow_redirects=True)
        if response.status_code != 200: return 'Unverified HTTP '+str(response.status_code)
        body = response.text.lower()
        if any(x in body for x in ['captcha','verify you are human','access denied','security check']):
            return 'Unverified: access challenge'
        if any(x in body for x in ['no longer accepting applications','job is no longer available','position has been filled','job has expired']):
            return 'Possibly closed: page says unavailable; confirm manually'
        return 'Page accessible; accepting applications not verified'
    except requests.RequestException as exc:
        return 'Unverified: '+type(exc).__name__

def render_dashboard(rows, summary):
    data = json.dumps([{k:clean(v) for k,v in row.items()} for row in rows],default=str).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    heading = html.escape(json.dumps({k:v for k,v in summary.items() if k!='audit'},indent=2))
    return DASHBOARD.replace('__DATA__',data).replace('__SUMMARY__',heading)

DASHBOARD = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Career Mission Control</title>
<style>:root{color-scheme:light}*{box-sizing:border-box}body{font:16px system-ui;margin:0;background:#eef2f7;color:#17253b}header{background:#122c42;color:white;padding:30px max(5vw,20px)}header p{max-width:850px;color:#cfdfeb}h1{margin:4px 0;font-size:32px}main{max-width:1250px;margin:auto;padding:24px}button,input,select{font:inherit;padding:11px;border:1px solid #b6c5d5;border-radius:7px}button{background:#087f81;color:white;cursor:pointer}button:hover{background:#075d62}.controls{display:flex;flex-wrap:wrap;gap:12px;align-items:end;background:white;padding:20px;border-radius:12px}label{display:grid;gap:6px;font-size:14px;font-weight:600}#search{min-width:220px}.count{padding:18px 0;font-weight:700}.card{background:white;padding:22px;margin:14px 0;border-radius:12px;border-left:5px solid #17898c;box-shadow:0 2px 8px #122c420a}.card h2{font-size:21px;margin:7px 0}.meta{color:#526277}.badge{font-size:13px;font-weight:700;padding:5px 9px;background:#e1f2f1;border-radius:6px;display:inline-block}.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin:18px 0}.facts div{background:#f3f6fa;padding:12px;border-radius:8px;white-space:pre-wrap}.facts b{display:block;margin-bottom:6px}.actions{display:flex;gap:12px;flex-wrap:wrap;align-items:center}a{color:#06676e;font-weight:700}textarea{display:block;width:100%;font:inherit;margin:10px 0;padding:12px;border:1px solid #b6c5d5;border-radius:8px}details{margin:14px 0}summary{cursor:pointer;font-weight:600}.jd{white-space:pre-wrap;max-height:420px;overflow:auto;padding:12px;background:#f5f7fa}#message{padding:12px 0;color:#075d62}pre{overflow:auto;font-size:13px}#empty{padding:40px;background:white;border-radius:12px}footer{font-size:13px;color:#556;padding:25px 0}</style>
<header><div>JOB SEARCH / OPERATIONS AND ANALYTICS</div><h1>Career Mission Control</h1><p>Decide which opportunities deserve your time. Screening suggestions are provisional. Read the full JD and confirm eligibility and application availability before applying.</p></header><main><section class="controls" aria-label="Search controls"><label>Search roles or companies<input id="search" type="search" aria-label="Search jobs" placeholder="Inventory, analyst, company..."></label><label>City<select id="city" aria-label="Filter city"><option value="">All cities</option><option>Bengaluru</option><option>Hyderabad</option></select></label><label>Screening<select id="decision" aria-label="Filter decision"><option value="active">Review queue</option><option value="">All records</option><option>Apply candidate</option><option>Stretch</option><option>Review</option><option>Skip</option></select></label><label>Application status<select id="status" aria-label="Filter status"><option value="">All statuses</option><option>Not applied</option><option>Applied</option><option>Interview</option><option>Offer</option><option>Rejected</option><option>Archived</option></select></label><button id="export">Export tracker backup</button><label>Restore tracker<input id="import" type="file" accept="application/json,.json" aria-label="Import tracker"></label></section><div id="message" role="status"></div><div id="count" class="count" aria-live="polite"></div><section id="jobs" aria-label="Job results"></section><div id="empty" hidden>No jobs match these filters. Try All records or another city.</div><details><summary>Run audit and limits</summary><pre>__SUMMARY__</pre></details><footer>Tracker changes stay in this browser when storage is available. Export a backup before moving files or clearing browser data. Import that backup into a future report to restore your history. No applications or messages are sent.</footer></main>
<script id="job-data" type="application/json">__DATA__</script><script>
const rows=JSON.parse(document.getElementById('job-data').textContent), allowed=['Not applied','Applied','Interview','Offer','Rejected','Archived'];
const el=id=>document.getElementById(id);let tracker={};try{tracker=JSON.parse(localStorage.getItem('career-tracker-v2')||'{}')}catch(e){}
function message(s){el('message').textContent=s}function save(){try{localStorage.setItem('career-tracker-v2',JSON.stringify(tracker))}catch(e){message('Browser storage unavailable. Export your tracker backup to preserve changes.')}}
function text(tag,value,parent,cls){const n=document.createElement(tag);n.textContent=value||'Not provided';if(cls)n.className=cls;parent.append(n);return n}
function safeURL(s){try{const u=new URL(s);return /^https?:$/.test(u.protocol)?u.href:null}catch(e){return null}}
function render(){const q=el('search').value.toLowerCase(),city=el('city').value,d=el('decision').value,s=el('status').value;el('jobs').replaceChildren();let count=0;
for(const r of rows){const t=tracker[r.record_id]||{},st=allowed.includes(t.status)?t.status:'Not applied';if(q&&!([r.title,r.company,r.description].join(' ').toLowerCase().includes(q)))continue;if(city&&!(String(r.location||'')+' '+String(r.search_city||'')).toLowerCase().includes(city.toLowerCase()))continue;if(d==='active'&&(r.decision==='Skip'||(r.freshness_status&&r.freshness_status!=='Within requested window')))continue;if(d&&d!=='active'&&r.decision!==d)continue;if(s&&s!==st)continue;count++;
const card=document.createElement('article');card.className='card';text('span',r.decision,card,'badge');text('h2',r.title,card);text('p',[r.company,r.location||r.search_city,r.date_posted].filter(Boolean).join(' | '),card,'meta');text('p',r.reason,card);const facts=document.createElement('div');facts.className='facts';for(const [label,value] of [['Freshness',r.freshness_status],['Experience signal',r.experience_evidence],['Your supporting evidence',r.supporting_evidence],['Gaps to inspect',r.gaps_to_check||'No listed gap detected; other requirements still need review'],['Application-page check',r.live_status]]){const box=document.createElement('div');text('b',label,box);text('span',value,box);facts.append(box)}card.append(facts);
const actions=document.createElement('div');actions.className='actions';const url=safeURL(r.job_url_direct)||safeURL(r.job_url);if(url){const a=text('a','Open job and verify',actions);a.href=url;a.target='_blank';a.rel='noopener noreferrer'}const label=document.createElement('label');label.textContent='Application status';const select=document.createElement('select');select.setAttribute('aria-label','Application status for '+r.title);for(const v of allowed){const op=document.createElement('option');op.value=v;op.textContent=v;select.append(op)}select.value=st;select.onchange=()=>{tracker[r.record_id]={...t,status:select.value,updated_at:new Date().toISOString()};save();render()};label.append(select);actions.append(label);card.append(actions);
const notes=document.createElement('textarea');notes.placeholder='Your notes, application date and next follow-up';notes.setAttribute('aria-label','Notes for '+r.title);notes.value=t.notes||'';notes.onchange=()=>{tracker[r.record_id]={...(tracker[r.record_id]||{}),notes:notes.value,updated_at:new Date().toISOString()};save()};card.append(notes);const details=document.createElement('details');text('summary','Read collected job description',details);text('div',r.description||'Description missing. Open the source job.',details,'jd');card.append(details);el('jobs').append(card)}el('count').textContent=count+' shown / '+rows.length+' unique records';el('empty').hidden=count!==0}
for(const id of ['search','city','decision','status'])el(id).addEventListener(id==='search'?'input':'change',render);
el('export').onclick=()=>{const blob=new Blob([JSON.stringify({version:2,exported_at:new Date().toISOString(),tracker},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='career-tracker-backup.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message('Tracker backup exported. Keep it for your next report.')};
el('import').onchange=async()=>{try{const payload=JSON.parse(await el('import').files[0].text());if(payload.version!==2||!payload.tracker||Array.isArray(payload.tracker)||typeof payload.tracker!=='object')throw Error('Invalid tracker file');let added=0;for(const [id,t] of Object.entries(payload.tracker)){if(!/^[a-zA-Z0-9_-]{1,80}$/.test(id)||!t||typeof t!=='object'||!allowed.includes(t.status||'Not applied'))continue;tracker[id]={status:t.status||'Not applied',notes:String(t.notes||'').slice(0,10000),updated_at:String(t.updated_at||'')};added++}save();render();message('Imported '+added+' tracker records.')}catch(e){message('Import failed: '+e.message)}};render();
</script></html>'''

def ui_test():
    import tempfile
    from playwright.sync_api import sync_playwright
    a = dict(title='Inventory Analyst',company='Example',location='Bengaluru',description='Excel inventory reporting. '*15,job_url='https://example.org/job/1',record_id='fixture1')
    a.update(screen(a));a['decision']='Review'
    b = dict(a,title='Senior Analyst',location='Hyderabad',record_id='fixture2',decision='Skip')
    evil = dict(a,title='</script><script>window.PWNED=1</script>',record_id='fixture3')
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'report.html';path.write_text(render_dashboard([a,b,evil],dict(audit=[])),encoding='utf-8')
        with sync_playwright() as p:
            browser=p.chromium.launch()
            page=browser.new_page(accept_downloads=True)
            page.goto(path.as_uri())
            assert page.locator('article').count()==2
            assert page.evaluate('window.PWNED') is None
            page.get_by_label('Search jobs',exact=True).fill('Inventory Analyst')
            assert page.locator('article').count()==1
            page.get_by_label('Application status for Inventory Analyst',exact=True).select_option('Applied')
            page.get_by_label('Notes for Inventory Analyst',exact=True).fill('Follow up Friday')
            page.get_by_label('Notes for Inventory Analyst',exact=True).press('Tab')
            page.reload()
            page.get_by_label('Search jobs',exact=True).fill('Inventory Analyst')
            assert page.get_by_label('Application status for Inventory Analyst',exact=True).input_value()=='Applied'
            assert page.get_by_label('Notes for Inventory Analyst',exact=True).input_value()=='Follow up Friday'
            with page.expect_download() as downloaded:
                page.get_by_role('button',name='Export tracker backup').click()
            saved=Path(folder)/'backup.json';downloaded.value.save_as(str(saved))
            assert json.loads(saved.read_text())['tracker']['fixture1']['status']=='Applied'
            page.evaluate('localStorage.clear()');page.reload()
            page.get_by_label('Import tracker',exact=True).set_input_files(str(saved))
            page.get_by_role('status').filter(has_text='Imported').wait_for()
            page.get_by_label('Search jobs',exact=True).fill('Inventory Analyst')
            assert page.get_by_label('Application status for Inventory Analyst',exact=True).input_value()=='Applied'
            page.get_by_label('Search jobs',exact=True).fill('No matching vacancy')
            assert page.locator('#empty').is_visible()
            page.get_by_label('Search jobs',exact=True).fill('')
            page.get_by_label('Filter decision',exact=True).select_option('')
            page.get_by_label('Filter city',exact=True).select_option('Hyderabad')
            assert page.locator('article').count()==1
            browser.close()
    print('Dashboard browser tests passed: filtering, empty state, XSS, tracker persistence, backup and restore')



def configure_sources():
    import jobspy
    from jobspy.naukri import Naukri
    from jobspy.model import Site
    class EntryNaukri(Naukri):
        def _search_params(self):
            return super()._search_params() | {'experience': 1}
    jobspy.SCRAPER_MAPPING[Site.NAUKRI] = EntryNaukri


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--ui-test', action='store_true')
    parser.add_argument('--per-search', type=int, default=5, choices=range(1,21))
    parser.add_argument('--hours', type=int, default=24, choices=[24,72,168])
    parser.add_argument('--query-count', type=int, default=3, choices=range(1,7))
    parser.add_argument('--sites', default='naukri')
    parser.add_argument('--input', type=Path, help='Screen a local CSV without scraping')
    args = parser.parse_args()
    if args.self_test:
        self_test(); return 0
    if args.ui_test:
        ui_test(); return 0
    out = Path('career-results'); out.mkdir(exist_ok=True)
    rows, audit = [], []
    if args.input:
        with args.input.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
    else:
        from jobspy import scrape_jobs
        configure_sources()
        class Capture(logging.Handler):
            def emit(self, record):
                messages.append(record.getMessage())
        for site in args.sites.split(','):
            if site not in ['naukri','linkedin']:
                raise ValueError('Only naukri and linkedin supported by this pipeline')
            blocked = False
            for city in CITIES:
                for query in QUERIES[:args.query_count]:
                    if blocked:
                        audit.append(dict(site=site,city=city,query=query,status='Skipped after source block',rows=0)); continue
                    messages = []
                    handler = Capture(); logger = logging.getLogger('jobspy.'+site); logger.addHandler(handler)
                    record = dict(site=site,city=city,query=query,rows=0,requested_experience=1 if site=='naukri' else None)
                    started = time.monotonic()
                    try:
                        jobs = scrape_jobs(site_name=[site],search_term=query,location=city,
                                           results_wanted=args.per_search,hours_old=args.hours,
                                           country_indeed='India',fetch_description=True)
                        items = jobs.to_dict('records')
                        for row in items:
                            row.update(search_query=query,search_city=city,source_site=site)
                        rows.extend(items)
                        record.update(rows=len(items),errors=jobs.attrs.get('errors',{}),warnings=jobs.attrs.get('warnings',{}))
                        blocked = '429' in json.dumps(record) or any('429' in m for m in messages)
                        record['status'] = 'Partial' if blocked or record['errors'] or record['warnings'] else ('Retrieved' if items else 'Empty')
                    except Exception as exc:
                        record.update(status='Failed',error=str(exc))
                    finally:
                        logger.removeHandler(handler)
                    record.update(logs=messages,seconds=round(time.monotonic()-started,1))
                    audit.append(record); print(json.dumps(record,default=str),flush=True)
                    time.sleep(5)
    write_csv(out/'raw_jobs.csv',rows)
    unique = deduplicate(rows)
    for row in unique:
        row.update(screen(row))
        row.update(freshness(row,args.hours))
        if row['freshness_status'] != 'Within requested window':
            row['decision'] = 'Skip' if row['freshness_status'] in ['Outside requested window','Future date'] else 'Review'
            row['reason'] = 'Freshness gate: '+row['freshness_status']
        row['record_id'] = hashlib.sha256((canonical(row.get('job_url')) or fingerprint(row)).encode()).hexdigest()[:16]
        row['collected_at_utc'] = datetime.now(timezone.utc).isoformat()
    for row in [r for r in unique if r['decision'] != 'Skip'][:15]:
        row['live_status'] = check_link(row)
    order = {'Apply candidate':0,'Stretch':1,'Review':2,'Skip':3}
    unique.sort(key=lambda r: (order[r['decision']], -len(r['evidence_matches'])))
    write_csv(out/'screened_jobs.csv',unique)
    shortlist = [r for r in unique if r['decision'] != 'Skip' and r['freshness_status'] == 'Within requested window']
    write_csv(out/'review_queue.csv',shortlist)
    tracker_fields = ['record_id','company','title','location','job_url','decision','application_status','applied_date','follow_up_date','outcome','notes']
    write_csv(out/'tracker_starter.csv',shortlist,tracker_fields)
    counts = {d:sum(r['decision']==d for r in unique) for d in order}
    summary = dict(requested_hours=args.hours, review_queue_records=len(shortlist), freshness_counts={s:sum(r['freshness_status']==s for r in unique) for s in sorted({r['freshness_status'] for r in unique})}, raw_records=len(rows),unique_records=len(unique),decisions=counts,
                   collection_status='Imported' if args.input else ('No data' if not rows else 'Partial' if any(a['status'] in ['Failed','Partial','Skipped after source block'] for a in audit) else 'Completed'),
                   eligibility_status='Provisional rule-based screening; no live application verification',
                   tracker_status='Dashboard browser storage plus export/import backup; CSV starter is separate',audit=audit)
    (out/'audit.json').write_text(json.dumps(summary,indent=2,default=str),encoding='utf-8')
    (out/'report.html').write_text(render_dashboard(unique, summary),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='audit'},indent=2))
    for row in shortlist[:20]:
        print('REVIEW_RECORD '+json.dumps({k:clean(row.get(k)) for k in ['title','company','location','experience_range','experience_evidence','job_url','decision','reason','live_status']},default=str))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write('## Career engine results\n\n'+json.dumps(counts)+'\n\nCollection: '+summary['collection_status']+'\n\nDownload career-results. Decisions require manual JD and application-page verification.\n')
            f.write('\n## Review queue\n\n')
            for r in shortlist[:20]:
                title = clean(r.get('title')).replace('|',' ').replace('[','').replace(']','')
                company = clean(r.get('company')).replace('|',' ')
                url = canonical(r.get('job_url'))
                f.write('- ['+title+']('+url+') - '+company+' - '+r['decision']+'; experience: '+clean(r.get('experience_range'))+'; '+r['live_status']+'\n')
    return 0 if rows else 2

if __name__ == '__main__':
    sys.exit(main())
