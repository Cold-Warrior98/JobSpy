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
from datetime import datetime, timezone
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

def canonical(url):
    p = urlsplit(clean(url))
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/'), '', ''))

def fingerprint(row):
    return '|'.join(norm(row.get(k)) for k in ('company', 'title', 'location'))

def deduplicate(rows):
    unique, urls, signatures = [], set(), set()
    for row in sorted(rows, key=lambda r: len(clean(r.get('description'))), reverse=True):
        url, sig = canonical(row.get('job_url')), fingerprint(row)
        usable_sig = all(clean(row.get(k)) for k in ('company', 'title', 'location'))
        if (url and url in urls) or (usable_sig and sig in signatures):
            continue
        if url:
            urls.add(url)
        if usable_sig:
            signatures.add(sig)
        unique.append(row)
    return unique

def screen(row):
    title = clean(row.get('title')).lower()
    desc = clean(row.get('description')).lower()
    text = title + ' ' + desc
    matched = [key for key in EVIDENCE if re.search(r'\b' + re.escape(key) + r'\b', text)]
    gaps = [key for key in GAPS if key in desc]
    years, snippets = [], []
    for sentence in re.split(r'[\n.;]', desc):
        if 'experience' not in sentence or re.search(r'preferred|desirable|advantage|nice.to.have', sentence):
            continue
        for match in re.finditer(r'\b(\d{1,2})\s*(?:[-–]|to)\s*(\d{1,2})\s*years?\b|\b(\d{1,2})\s*\+?\s*years?\b', sentence):
            years.append(int(match.group(1) or match.group(3)))
            snippets.append(sentence.strip()[:250])
    minimum = max(years) if years else None
    relevant = bool(re.search(r'operations|business analyst|supply chain|inventory|planning|procurement|process|program', title))
    decision, reason = 'Review', 'Eligibility not established; read full JD'
    if re.search(r'\b(senior|sr|lead|manager|director|head|principal)\b', title):
        decision, reason = 'Skip', 'Title indicates seniority outside primary search'
    elif re.search(r'financial operations|middle office|investment|accounting|tax analyst|credit analyst', title):
        decision, reason = 'Skip', 'Finance-focused role outside primary target'
    elif minimum is not None and minimum >= 4:
        decision, reason = 'Skip', 'Stated experience exceeds entry-level search'
    elif not relevant:
        decision, reason = 'Review', 'Role relevance unclear from title'
    elif len(desc) < 150:
        decision, reason = 'Review', 'Missing or short JD; cannot assess eligibility'
    elif minimum == 3:
        decision, reason = 'Stretch', 'Three years stated; check flexibility and relevant experience'
    elif gaps:
        decision, reason = 'Review', 'Potential skill gaps; distinguish mandatory from preferred'
    elif minimum is not None and minimum <= 2 and len(matched) >= 2:
        decision, reason = 'Apply candidate', 'Entry-level experience signal and supporting evidence; manual verification required'
    return dict(decision=decision, reason=reason, experience_floor=minimum,
                experience_evidence=' | '.join(snippets), evidence_matches='; '.join(matched),
                supporting_evidence='; '.join(EVIDENCE[k] for k in matched),
                gaps_to_check='; '.join(gaps), application_status='Not applied',
                live_status='Not verified', manual_review_required='Yes')

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
    base = dict(title='Operations Analyst', company='Example', location='Bengaluru',
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
    print('11 screening and deduplication checks passed')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--per-search', type=int, default=5, choices=range(1,21))
    parser.add_argument('--hours', type=int, default=168, choices=[24,72,168])
    parser.add_argument('--query-count', type=int, default=3, choices=range(1,7))
    parser.add_argument('--sites', default='naukri')
    parser.add_argument('--input', type=Path, help='Screen a local CSV without scraping')
    args = parser.parse_args()
    if args.self_test:
        self_test(); return 0
    out = Path('career-results'); out.mkdir(exist_ok=True)
    rows, audit = [], []
    if args.input:
        with args.input.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
    else:
        from jobspy import scrape_jobs
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
                    record = dict(site=site,city=city,query=query,rows=0)
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
                        record['status'] = 'Partial' if blocked or record['errors'] else ('Retrieved' if items else 'Empty')
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
        row['record_id'] = hashlib.sha256((canonical(row.get('job_url')) or fingerprint(row)).encode()).hexdigest()[:16]
        row['collected_at_utc'] = datetime.now(timezone.utc).isoformat()
    order = {'Apply candidate':0,'Stretch':1,'Review':2,'Skip':3}
    unique.sort(key=lambda r: (order[r['decision']], -len(r['evidence_matches'])))
    write_csv(out/'screened_jobs.csv',unique)
    shortlist = [r for r in unique if r['decision'] != 'Skip']
    write_csv(out/'review_queue.csv',shortlist)
    tracker_fields = ['record_id','company','title','location','job_url','decision','application_status','applied_date','follow_up_date','outcome','notes']
    write_csv(out/'tracker_starter.csv',shortlist,tracker_fields)
    counts = {d:sum(r['decision']==d for r in unique) for d in order}
    summary = dict(raw_records=len(rows),unique_records=len(unique),decisions=counts,
                   collection_status='Imported' if args.input else ('No data' if not rows else 'Partial' if any(a['status'] in ['Failed','Partial','Skipped after source block'] for a in audit) else 'Completed'),
                   eligibility_status='Provisional rule-based screening; no live application verification',
                   tracker_status='Starter file only; preserve your edited tracker separately between runs',audit=audit)
    (out/'audit.json').write_text(json.dumps(summary,indent=2,default=str),encoding='utf-8')
    columns = ['decision','company','title','location','experience_floor','reason','evidence_matches','gaps_to_check','job_url']
    table = '<table><tr>'+''.join('<th>'+html.escape(c)+'</th>' for c in columns)+'</tr>'
    for row in unique:
        table += '<tr>'+''.join('<td>'+html.escape(clean(row.get(c)))+'</td>' for c in columns)+'</tr>'
    page = '<!doctype html><meta charset="utf-8"><title>Career review queue</title><style>body{font:15px Arial;margin:32px;color:#16243a}table{border-collapse:collapse}td,th{border:1px solid #ccd;padding:10px;text-align:left;vertical-align:top}th{background:#eaf1fa}h1{color:#145c68}</style><h1>Career Search Review</h1><p>Provisional screening. Verify the complete JD and live application page before applying.</p><pre>'+html.escape(json.dumps({k:v for k,v in summary.items() if k!='audit'},indent=2))+'</pre>'+table+'</table>'
    (out/'report.html').write_text(page,encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='audit'},indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write('## Career engine results\n\n'+json.dumps(counts)+'\n\nCollection: '+summary['collection_status']+'\n\nDownload career-results. Decisions require manual JD and application-page verification.\n')
    return 0 if rows else 2

if __name__ == '__main__':
    sys.exit(main())
