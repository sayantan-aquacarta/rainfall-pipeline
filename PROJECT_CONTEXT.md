# PROJECT_CONTEXT.md — Briefing for Future Claude Sessions

> **Who this is for:** Any future Claude session (Chat, Cowork, Code, or a new conversation) that needs to pick up work on this project without re-reading months of chat history. Read this first, then `README.md`, then the file you're being asked to change.

---

## 1. What this project is, in one paragraph

A **daily rainfall data pipeline for India**. It scrapes the India Meteorological Department (IMD) "District Rainfall Distribution" PDF every day at 11:30 AM IST, parses it into structured data (~770 rows covering ~36 subdivisions and ~700 districts across ~28 states), and publishes it as:
- A SQLite database (`data/rainfall.db`) — primary storage
- Per-day CSVs (`data/raw/rainfall_YYYY-MM-DD.csv`) — versioned in git
- A Parquet snapshot (`data/rainfall.parquet`) — for analytics
- A static JSON API (`docs/api/**`) — served by GitHub Pages
- An interactive HTML dashboard (`docs/index.html`) — also GitHub Pages

Everything runs on **free GitHub infrastructure** (Actions + Pages + repo). Monthly cost: ₹0.

---

## 2. Live URLs (check these to verify things are working)

- **Repo:** https://github.com/sayantan-aquacarta/rainfall-pipeline
- **Dashboard:** https://sayantan-aquacarta.github.io/rainfall-pipeline/
- **JSON API index:** https://sayantan-aquacarta.github.io/rainfall-pipeline/api/index.json
- **Latest day JSON:** https://sayantan-aquacarta.github.io/rainfall-pipeline/api/latest.json
- **Source PDF (upstream):** https://mausam.imd.gov.in/Rainfall/DISTRICT_RAINFALL_DISTRIBUTION_COUNTRY_INDIA_cd.pdf

---

## 3. Local setup

- **OS:** Windows 11
- **Project path:** `D:\1_1_Aquacarta_Work\Work_6_IMD_Data_Extraction\rainfall-pipeline\rainfall-pipeline`
- **Python:** 3.14 (virtualenv at `.venv/`)
- **Shell:** PowerShell
- **Venv activation:** `.\.venv\Scripts\Activate.ps1` (prompt shows `(.venv)` when active)
- **Git user:** sayantan-aquacarta / sayantan@aquacarta.in

**To run anything locally, always activate the venv first.** Every command below assumes `(.venv)` is active.

---

## 4. Architecture & key decisions (read this before suggesting changes)

### Why PDF instead of HTML scraping
The IMD page (`rainfall_statistics_3.php`) does NOT contain tabular data in HTML — it embeds 12 PNG images plus a downloadable PDF. The PDF is the only machine-readable source. Don't suggest reverting to HTML scraping or using Playwright — we tried, it doesn't work.

### Why SQLite + Git instead of Postgres
Intentional. Dataset is tiny (~770 rows/day → ~3M rows after 10 years). SQLite + Git gives free time-travel backups (every commit is a snapshot). Postgres would add ops burden for zero analytical benefit. See README "Scaling notes" for when to upgrade (short answer: not anytime soon).

### Why static JSON API instead of FastAPI
Dataset is read-heavy, write-once-per-day. Static files on GitHub Pages CDN scale infinitely for free. A FastAPI server would need a host, have cold-start latency, and cost money/effort. Don't re-introduce it unless there's a concrete use case that can't be served by pre-generated JSON.

### Why font-weight classification in the parser
**Critical and non-obvious.** The IMD PDF puts subdivisions, states, and districts in visually distinct rows — but they share the same X-position in the layout, the same serial number patterns, and the same text structure. Pure text-based classification FAILS (we tried two approaches, both had false positives).

The reliable signal: **subdivisions and state-header rows are rendered in `Trebuchet MS,Bold`; districts are rendered in `Helvetica` (regular)**. `src/rainfall/parser.py` uses pdfplumber's char-level `fontname` metadata to detect this. If IMD ever changes fonts, this classifier breaks — the Pandera validator will catch the failure (row counts will drop below thresholds) and open a GitHub issue.

### Why daily CSV files AND a SQLite DB AND a Parquet snapshot
Different consumers want different things. Researchers want CSVs for Excel. Data scientists want Parquet for pandas/Polars. The dashboard reads JSON. SQLite is the canonical source of truth (the only one with upsert logic and indices). The others are derived outputs, always regenerated from SQLite.

---

## 5. The automation loop

```
Cron trigger (06:00 UTC = 11:30 IST, daily)
  → GitHub Actions checks out repo
  → pip install -e .
  → `rainfall scrape` CLI command runs:
      → fetch_pdf()        (httpx + tenacity, 5 retries)
      → save_pdf()         (audit trail in data/raw_pdf/)
      → parse_pdf()        (pdfplumber + font classifier)
      → validate()         (Pandera schema; fails fast on drift)
      → write_csv()        (data/raw/rainfall_YYYY-MM-DD.csv)
      → upsert_rainfall()  (SQLite, logs revisions to `revisions` table)
      → write_parquet_snapshot()
      → build_all()        (regenerates docs/api/**)
  → git commit + push     ("data: rainfall snapshot YYYY-MM-DD")
  → deploy-pages job      (rebuilds GitHub Pages site)
  → On failure: opens an auto-labeled GitHub issue
```

See `.github/workflows/daily-scrape.yml` for exact steps.

---

## 6. File map (what lives where)

```
src/rainfall/
  config.py         — all paths/URLs/settings (env-driven)
  logging_setup.py  — structlog JSON logger
  scraper.py        — httpx + tenacity; downloads & checksums PDF
  parser.py         — pdfplumber + font classifier; the fragile magic
  validator.py      — Pandera schema; tripwire for format drift
  storage.py        — SQLite upserts, CSV writer, Parquet snapshot
  api_builder.py    — regenerates docs/api/** JSON from SQLite
  cli.py            — `rainfall` command (scrape, backfill, info, etc.)

tests/
  test_parser.py     — runs against a saved PDF fixture
  test_storage.py    — idempotency & revision tracking
  test_api_builder.py — JSON output shape
  fixtures/sample_imd.pdf — real IMD PDF from project start

docs/
  index.html        — single-file dashboard (Chart.js, no build step)
  api/              — generated; don't edit by hand
  .nojekyll         — disables Jekyll on GitHub Pages

data/                 # OWNED BY THE BOT — don't edit by hand
  raw/*.csv         — one per day
  raw_pdf/*.pdf     — forensic audit trail
  rainfall.db       — SQLite (canonical)
  rainfall.parquet  — analytics snapshot

.github/workflows/
  daily-scrape.yml  — cron + commit + deploy
  tests.yml         — runs pytest on PRs
```

---

## 7. Common CLI commands

```powershell
# Activate venv first (always)
.\.venv\Scripts\Activate.ps1

# Scrape today's data
rainfall scrape

# See DB stats (dates, districts, last runs)
rainfall info

# Regenerate static JSON API from current DB
rainfall rebuild-api

# Write a fresh Parquet snapshot
rainfall snapshot

# Re-process a saved PDF (useful for backfill)
rainfall backfill --pdf data/raw_pdf/imd_YYYY-MM-DD_XXXXXXXX.pdf

# Run tests
pytest

# Preview dashboard locally
cd docs
python -m http.server 8000
# → open http://localhost:8000, Ctrl+C when done
cd ..
```

---

## 8. Git workflow for making changes

```powershell
# 1. Make sure you're in sync
git pull

# 2. Make your change (edit files)
# 3. Test locally
pytest
# Or for dashboard changes: preview at localhost:8000

# 4. Commit and push
git status                            # what changed
git add .
git commit -m "describe change"
git push
```

GitHub Actions redeploys Pages automatically. Daily cron uses new code from the next run.

### Rules
- **NEVER edit files in `data/`** — the bot owns that directory. Changes there will conflict with the next bot commit.
- **NEVER commit secrets/tokens to the repo.** The repo has no secrets currently; keep it that way.
- If a change is risky, use a branch: `git checkout -b experiment-name`, then merge to main when happy.
- If you push something that breaks the workflow: `git revert HEAD && git push` to roll back immediately.

---

## 9. Data schema (rainfall table)

```sql
CREATE TABLE rainfall (
    date                  TEXT NOT NULL,   -- ISO YYYY-MM-DD
    period_start          TEXT NOT NULL,
    period_end            TEXT NOT NULL,
    level                 TEXT NOT NULL,   -- 'subdivision' | 'state' | 'district'
    subdivision           TEXT,
    state                 TEXT,
    district              TEXT,
    day_actual_mm         REAL,
    day_normal_mm         REAL,
    day_departure_pct     REAL,
    day_category          TEXT,            -- LE | E | N | D | LD | NR
    period_actual_mm      REAL,
    period_normal_mm      REAL,
    period_departure_pct  REAL,
    period_category       TEXT,
    scraped_at            TEXT NOT NULL,
    PRIMARY KEY (date, level, subdivision, state, district)
);
```

**Category codes:** LE = Large Excess, E = Excess, N = Normal, D = Deficient, LD = Large Deficient, NR = No Rain.

Aux tables: `scrape_runs` (every run logged) and `revisions` (every value change over time).

---

## 10. Features built so far

- [x] Scraper with retries + checksum
- [x] PDF parser with font-based classification
- [x] Pandera schema validation
- [x] SQLite with idempotent upserts + revision tracking
- [x] Per-day CSV + Parquet outputs
- [x] Static JSON API generator
- [x] Chart.js dashboard with state/district/date filtering
- [x] **In-dashboard CSV/JSON download** with date-range picker (browser-side filter, no backend). CSV is primary, JSON is "advanced" link.
- [x] GitHub Actions daily cron
- [x] Auto-open issue on failure
- [x] GitHub Pages deploy job
- [x] **30-day PDF retention** — `prune_old_pdfs()` in `scraper.py` auto-deletes PDFs older than 30 days after each successful scrape, capping `data/raw_pdf/` at ~24 MB (~800 KB × 30). Configurable via `RAINFALL_PDF_RETENTION_DAYS` env var (set to `0` to keep forever).
- [x] 25 passing tests

---

## 11. Known quirks / gotchas

- **DIU appears twice** in the source PDF (once under DAMAN AND DIU, once under SAURASHTRA & KUTCH). PK upsert handles it — last write wins. This is correct behavior, not a bug.
- **First time pushing**: user was on Python 3.14 (newer than spec'd 3.11+). Everything works, but future compatibility issues may reference this.
- **Subdivision count**: ~36 met-subdivisions is expected. Sometimes the parser extracts 42 — this is because single-district subdivisions like "BIHAR" or "DELHI" act as both subdivision AND state.
- **State count**: dashboard shows ~44 "states" which includes composite subdivisions treated as state contexts. Real Indian states count is ~28 — this is a presentation quirk, not a data error.
- **Cold boot**: if `data/rainfall.db` doesn't exist, `rainfall info` will fail. Run `rainfall scrape` once first to initialize.
- **PDF retention**: only the most recent 30 days of raw PDFs are kept in `data/raw_pdf/`. Older files are deleted automatically after each successful scrape. The parsed data (SQLite, CSVs, Parquet) is never affected — only the forensic PDF archive is trimmed. Override the window with `RAINFALL_PDF_RETENTION_DAYS=N` (set to `0` for unlimited). A pruning failure logs a warning but never aborts the scrape.

---

## 12. What to do if things break

### Daily scrape failed (red X in GitHub Actions)
1. Check the Actions tab — an auto-opened issue has the error log
2. Most common cause: IMD site temporarily down → next day's run usually recovers
3. If parser broke (format changed): download the PDF from `data/raw_pdf/` (uploaded as workflow artifact), test the parser locally, fix, push
4. Never manually edit `data/` to "fix" a bad day — use `rainfall backfill --pdf <path>` instead

### Dashboard not updating
- Check Settings → Pages shows "Your site is live at..."
- Check the deploy-pages job in Actions succeeded
- Browser cache: hard-refresh (Ctrl+Shift+R)

### Tests fail after a change
- Parser tests use `tests/fixtures/sample_imd.pdf` — this is a snapshot from project start. Don't replace it unless the new fixture has been manually verified.
- Storage/API tests use tmp dirs — they should never touch real data.

---

## 13. Honest acknowledgments

- This project was built in one collaborative session between the user (Sayantan) and Claude. The user is an experienced domain person but not a daily software developer — instructions should stay concrete and terminal-friendly.
- The font-weight parser trick wasn't obvious — we debugged two wrong heuristics before finding it. If you're tempted to "clean up" that code, preserve the font-based classification.
- The dashboard is a single 500-line HTML file. It could be split into separate .js/.css, but the current single-file design is a feature (easy to edit, no build step). Don't "modernize" it into React/Vue without a concrete reason.

---

## 14. What this doc deliberately doesn't cover

- Python basics, Git basics, GitHub Actions basics — check the README or ask
- How IMD publishes data upstream (we don't know; we just consume the PDF)
- ML / anomaly detection — placeholder hooks exist, no models built yet
- Historical backfill beyond what's in `data/raw_pdf/` — IMD doesn't publish past PDFs at predictable URLs

---

**Last updated:** 2026-04-14 by Claude (Sonnet 4.6) in a Cowork session — added 30-day PDF retention feature.
**If this file is stale:** check `README.md` for anything newer, and `git log` for recent commits.
