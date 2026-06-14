# Design: Pipeline Fix + Full Timeline Dashboard

**Date:** 2026-06-14  
**Status:** Approved  
**Scope:** Fix two interlocking bugs that silently stall the pipeline, backfill 44 days of missing monsoon data, and surface full timeline on the dashboard.

---

## Problem Statement

The dashboard at `https://sayantan-aquacarta.github.io/rainfall-pipeline/` is stuck at **May 31, 2026** despite daily GitHub Actions runs showing "success". Two interlocking bugs are responsible:

1. **PIPESTATUS overwrite bug** — the workflow's "Run scrape" step always exits 0, masking Python failures.
2. **Parser/validator fails on June PDFs** — IMD changed their PDF format at monsoon onset (June PDFs are ~3 MB vs ~790 KB in May); the parser or validator throws an unhandled exception.

Because of bug 1, the workflow never detects the failure, commits the raw PDF + failure-log SQLite, and exits "success". No CSV, no DB upsert, no API rebuild — only the PDF gets saved. All 44 days of June data (June 1–13 PDFs already committed; June 14 onwards to be collected) are recoverable via the `backfill` CLI command once the parser is fixed.

---

## Architecture

No structural changes to the pipeline. All fixes are targeted at the two bugs and the dashboard's data-coverage display.

```
IMD PDF URL
    │
    ▼
scraper.fetch_pdf()           ← unchanged
    │
    ▼
scraper.save_pdf()            ← unchanged; happens before any parsing
    │
    ▼
parser.parse_pdf()            ← FIX: handle June monsoon format
    │
    ▼
validator.validate()          ← potentially adjust thresholds for monsoon
    │
    ▼
storage.upsert_rainfall()     ← unchanged
    │
    ▼
api_builder.build_all()       ← unchanged; rebuilt from full SQLite every run
    │
    ▼
docs/api/ static JSON         ← served by GitHub Pages
    │
    ▼
docs/index.html dashboard     ← ENHANCEMENT: coverage indicator
```

---

## Components

### Component 1: Workflow PIPESTATUS Fix

**File:** `.github/workflows/daily-scrape.yml`

**Bug:** `PIPESTATUS` is reset by the `echo` command before `exit` reads it.

```bash
# Current (broken)
python -m rainfall.cli scrape 2>&1 | tee scrape.log
echo "exit_code=${PIPESTATUS[0]}" >> "$GITHUB_OUTPUT"
exit ${PIPESTATUS[0]}    # ← always 0 (echo's exit code)

# Fixed
python -m rainfall.cli scrape 2>&1 | tee scrape.log
SCRAPE_EXIT=${PIPESTATUS[0]}
echo "exit_code=${SCRAPE_EXIT}" >> "$GITHUB_OUTPUT"
exit ${SCRAPE_EXIT}
```

This is a 2-line change. Once fixed, workflow failures surface correctly and the "Open issue on failure" step will fire as intended.

---

### Component 2: Parser Fix for June PDF Format

**File:** `src/rainfall/parser.py`

**Diagnosis approach:** Run `parse_pdf()` against `data/raw_pdf/imd_2026-06-01_*.pdf` (pulled from GitHub) and capture the exact exception. Then fix.

**Known signal:** June PDFs are ~3 MB vs ~790 KB for May. This means either:
- Many more pages (more regions with active monsoon rainfall)
- Embedded images/maps added to the PDF
- A different header/table structure for the monsoon reporting period

**Likely failure modes (in order of probability):**

a. `_find_dates()` fails because IMD changed the DAY/PERIOD header text for monsoon season. Fix: update or broaden the regex, or add a fallback search pattern.

b. `_TRAILING_RE` row regex fails to match new row format. Fix: update the regex to handle new column layout.

c. `validate()` fails because monsoon rainfall values exceed `day_actual_mm` upper bound of 2000 mm. Fix: raise ceiling to 5000 mm (the scientific upper bound for any real daily rainfall record).

d. `validate()` fails because `n_districts < 500` — a new PDF format with different table structure yields fewer parsed rows. Fix: investigate actual row count and adjust threshold if needed, or fix the parser to handle the new structure.

**Implementation order:** Pull → test locally → fix the specific failure path → verify with all June PDFs.

---

### Component 3: Backfill June 1–13

**Already committed PDFs:** `data/raw_pdf/imd_2026-06-{01..13}_*.pdf` (on GitHub remote).

After pulling and fixing the parser, run:
```bash
python -m rainfall.cli backfill --pdf data/raw_pdf/imd_2026-06-*.pdf
```
This re-processes each PDF through the fixed parser, writes CSVs, upserts to DB, and rebuilds the API once at the end.

**Note:** Some dates may be missing from the PDF archive (the 30-day retention prunes PDFs older than 30 days; PDFs from before May 15 are gone). For June, all PDFs (June 1–13) are within the 30-day window and should all be present.

---

### Component 4: Dashboard Coverage Indicator

**File:** `docs/index.html`

Add a visual "data freshness" bar below the stats row that shows:
- Date of the latest data point
- Days since last update (highlight in red if > 2 days)
- A compact inline calendar strip showing which of the last 30 days have data (green dot) vs gaps (grey dot)

**Implementation:** Pure JavaScript, reads from the existing `api/index.json` (`first_date`, `last_date`, `n_dates`). Cross-references with `api/by-date/YYYY-MM-DD.json` file existence check (attempted fetch, 404 = gap) for the last 30 days.

The calendar strip is CSS grid of 30 `<span>` elements colored per-day. Tooltip shows the date.

```
Last 30 days:  ■ ■ ■ · ■ ■ ■ ■ · ■ ■ ■ ■ ■ ■ ■ · · ■ ■ ■ ■ ■ ■ ■ ■ ■ ■ ■
               [green dots = data present; grey = no data; red label if stale]
```

---

## Data Flow After Fix

```
git pull                          → get June 1–13 PDFs + failure-log SQLite
parser fix                        → handle June PDF format  
rainfall backfill --pdf June PDFs → fill CSV + DB + API for June 1–13
rainfall rebuild-api              → fresh docs/api/ with all 57+ dates
git push                          → updates GitHub remote + Pages redeploy
GitHub Actions (next daily run)   → correctly scrapes June 14+ with fixed parser
```

---

## Error Handling Improvements

1. **PIPESTATUS fix** — already described; ensures all future failures surface.
2. **Scrape log tagging** — the existing `log_run("failure", ...)` already captures errors in SQLite; no additional change needed once PIPESTATUS is fixed and the issue-opener step fires.
3. **Validator ceiling** — raise `day_actual_mm` upper bound from 2000 to 5000 mm as a precaution for extreme monsoon events.

---

## Testing

1. Run parser against one May PDF (regression test — must still pass): `parse_pdf(open("data/raw_pdf/imd_2026-05-31_*.pdf", "rb").read())`
2. Run parser against one June PDF (new test — must now pass): `parse_pdf(open("data/raw_pdf/imd_2026-06-01_*.pdf", "rb").read())`
3. Run `rainfall info` after backfill — confirm `last_date = 2026-06-13`, `n_dates >= 57`
4. Open `docs/index.html` locally and verify the timeline chart and coverage indicator work

---

## Out of Scope

- Wayback Machine backfill (no missed dates — all June PDFs are already in the repo)
- Architecture changes to the storage or API layer
- Replacing pdfplumber with a different PDF library

---

## Success Criteria

- [ ] `docs/api/index.json` shows `last_date: 2026-06-13` (or today) after fix
- [ ] `by-date/` directory contains June entries
- [ ] By-district time series charts show full April–June timeline
- [ ] Dashboard coverage indicator shows data gaps visually
- [ ] Next GitHub Actions run commits new data (not silently skipped)
- [ ] Workflow correctly opens a GitHub issue on scrape failure
