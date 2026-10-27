# Career Search Engine

A manual, rule-based review assistant around JobSpy. No auto-apply, messages, AI APIs, credentials, or scheduled runs.

## Run on GitHub

Open Actions > Career Search Engine > Run workflow. Choose Naukri or Naukri + LinkedIn and a 24/72/168-hour search window. The workflow searches six role families across Bengaluru and Hyderabad, requesting five results per search. Source filters and dates are not independently verified.

Download the career-results artifact within 14 days. Open report.html for the overview and review_queue.csv in Excel. CSVs use UTF-8 BOM and neutralise formula-like cell values.

## Outputs

- raw_jobs.csv: original collected records.
- screened_jobs.csv: deduplicated records, provisional decisions, reasons, experience excerpts, evidence and gaps.
- review_queue.csv: records not marked Skip.
- tracker_starter.csv: blank application-tracking fields. Preserve your edited tracker separately; repeat runs do not merge application history.
- audit.json: per-search status, logs and errors.
- report.html: readable static report.

## Meaning of decisions

Apply candidate means a parsed entry-level experience signal plus at least two keyword evidence matches. It is NOT a verified eligibility decision. Stretch means three years stated. Review includes unknown requirements, short JDs, unclear relevance and possible skill gaps. Skip covers selected senior titles, finance titles and parsed experience of four years or more. These conservative heuristics can be wrong: review the original JD, including every mandatory requirement.

No source URL or application page is checked for continued availability. Qualification restrictions, language, domain-specific experience, salary, work authorisation and relocation requirements require manual review. Evidence keywords do not establish professional experience. No selection probabilities are generated.

## Configure

Edit QUERIES, CITIES, EVIDENCE and GAPS in career_engine.py. Keep private resume details, contacts, credentials and application outcomes out of this public repository.

## Local use

From a Python environment with python-jobspy==1.4.1 installed:

    python career_engine.py --self-test
    python career_engine.py --sites naukri --hours 24 --query-count 6 --per-search 5
    python career_engine.py --input existing_jobs.csv

Outputs go to career-results under the current working directory. On Windows, use a D: working folder if local files must stay on D:. Import mode and self-tests use only the Python standard library.

## Failure behaviour

An empty run exits with code 2. Source blocks or errors appear in the audit as Partial/Failed; subsequent queries for a rate-limited source are skipped. A successful GitHub job proves execution completed, not that every source worked or any job is eligible.
