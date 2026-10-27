# Career Mission Control

Manual Indian job-search and review workspace built around JobSpy. No submissions, messages, credential uploads, paid AI APIs or scheduled runs.

## Use

1. Open Actions > Career Search Engine > Run workflow. Choose Naukri (default) or Naukri + LinkedIn, and 24/72/168 hours (24 is the default).
2. Download the career-results artifact within 14 days and extract it.
3. Open report.html. Filter by city, role text, screening suggestion and application status. Expand collected JDs and follow the original source links.
4. Change application status and add notes yourself after taking action. Changes save in your browser when storage is available.
5. Export tracker backup before clearing browser data or moving files. Import that JSON backup into the next report to restore your history. Retained history does not trigger applications or messages.

## What a result means

Apply candidate is a provisional suggestion based on an entry-level experience signal and at least two keyword evidence matches. It does not establish all mandatory qualifications, domain experience or employer acceptance. Stretch means a three-year minimum was parsed. Review includes incomplete data, potential gaps and uncertain role relevance. Skip is a conservative rule-based exclusion and can be wrong. All records remain available under All records.

Ranges with an upper bound above two years (for example 1-6 or 1-3) require Review, even when their minimum is low. A three-year minimum remains Stretch and a four-year or higher minimum remains Skip.

## Freshness

The source is asked for the selected posting-age window, then date_posted is checked independently. Only non-Skip records classified Within requested window appear in the default Review queue and review_queue.csv. Older and future dates are excluded. Missing, invalid or ambiguous dates stay available under All records. Date-only values are treated as day intervals; yesterday may be uncertain for a rolling 24-hour cutoff. UTC is assumed when the source omits a timezone, and that assumption is recorded. A source date does not establish the original publication time of a refreshed listing.

Experience is read from structured source fields and explicit JD statements; preferred experience is treated separately. Evidence descriptions distinguish academic/project exposure from professional experience. Rules never invent experience or selection probabilities.

## Page checks

The first 15 non-Skip records receive a normal public HTTP request. Statuses distinguish inaccessible pages, access challenges, explicit unavailability text and accessible pages. A 200 response is NOT proof that applications are still accepted. No challenges are bypassed. Records beyond the cap remain Not checked.

## Outputs

- report.html: self-contained dashboard with search, filters, JD details, notes and tracker backup/restore.
- screened_jobs.csv: all deduplicated records and reasons.
- review_queue.csv: non-Skip records within the selected date window for human review.
- raw_jobs.csv: collected source records.
- tracker_starter.csv: alternative CSV template; edited versions are not automatically merged.
- audit.json: source-level status and logs.

CSV exports use UTF-8 BOM for Excel and neutralise formula-like values. Exact job URLs retain meaningful query IDs. Duplicate descriptions can be consolidated when company, title and location also match.

## Testing

The workflow runs screening regression tests plus Chromium acceptance tests for filtering, empty state, injection handling, notes, status persistence and backup/restore, before live collection. Test fixtures are synthetic. Live source performance and eligibility remain separate from code-test success.

## Configuration and local use

Edit QUERIES, CITIES, EVIDENCE and GAPS in career_engine.py. Never put private contacts, resume files, credentials or application outcomes in this public repository. Use a D: working folder for local files.

    python career_engine.py --self-test
    python career_engine.py --ui-test
    python career_engine.py --sites naukri --hours 24 --query-count 6 --per-search 5
    python career_engine.py --input existing_jobs.csv

Live collection requires python-jobspy==1.4.1. UI tests require Playwright and its Chromium browser. An empty run exits with code 2; source failures are also recorded in the audit. Runs are bounded to 15 minutes.
