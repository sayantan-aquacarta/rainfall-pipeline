# India Rainfall Data Pipeline

A **fully open-source, zero-cost, fully automated** daily rainfall data pipeline for India. Scrapes the [IMD District Rainfall Distribution](https://mausam.imd.gov.in/imd_latest/contents/rainfall_statistics_3.php) PDF every day at **11:30 IST**, parses it into structured data, and serves it via a static JSON API and interactive dashboard on GitHub Pages.

- **$0/month** — runs entirely on free GitHub infrastructure (Actions + Pages + repo)
- **Fully autonomous** — daily cron, auto-commits data, auto-opens issues on failure
- **Research-ready** — every datapoint tied to a Git commit; Parquet + SQLite + per-day CSVs
- **Reproducible** — raw PDFs archived alongside parsed data
- **Idempotent** — re-running a day overwrites cleanly; data revisions are logged

---

## Live Dashboard

**[https://sayantan-aquacarta.github.io/rainfall-pipeline/](https://sayantan-aquacarta.github.io/rainfall-pipeline/)**

The dashboard updates automatically every day. It shows:
- Daily rainfall by category (Large Excess / Excess / Normal / Deficient / Large Deficient / No Rain) across all ~760 districts
- 60-day data coverage timeline
- Top 10 wettest districts (cumulative seasonal rainfall)
- District-level time series (select any state + district)
- Download district data as JSON or CSV

**JSON API (no auth, no rate limits):**
```
https://sayantan-aquacarta.github.io/rainfall-pipeline/api/index.json
https://sayantan-aquacarta.github.io/rainfall-pipeline/api/latest.json
https://sayantan-aquacarta.github.io/rainfall-pipeline/api/by-date/2026-06-13.json
https://sayantan-aquacarta.github.io/rainfall-pipeline/api/by-state/maharashtra.json
https://sayantan-aquacarta.github.io/rainfall-pipeline/api/by-district/maharashtra-pune.json
```

---

## What's New (June 2026)

### Monsoon 2026 PDF Format Support
IMD changed the PDF format at monsoon onset (June 2026). The parser now handles both formats automatically:

| Field | Pre-monsoon format | Monsoon format (June+) |
|---|---|---|
| Date header | `DAY : 31.05.2026 TO 31.05.2026` | `DAY: 01-06-2026 PERIOD: 01-06-2026 to 03-06-2026` |
| Row classification | Bold = aggregate, regular = district | Serial number present = district |
| Departure values | `63%` | `63` (no `%` sign) |
| PDF size | ~790 KB | ~3 MB (more districts active in monsoon) |

### Smart URL Fallback
When IMD changes or moves the PDF URL, the scraper now automatically recovers without manual intervention. `fetch_pdf()` cascades through three strategies:

1. **Primary URL** — tries the known URL with full retry logic (5 retries, exponential backoff)
2. **Page discovery** — scrapes the IMD statistics page (`rainfall_statistics_3.php`) to find the current PDF link automatically
3. **URL variant list** — tries a list of historically-used alternate paths on the IMD server

If the URL has permanently changed, set `RAINFALL_PDF_URL` in the repo secrets to override.

### Dashboard Redesign
The dashboard at `https://sayantan-aquacarta.github.io/rainfall-pipeline/` has been fully redesigned:
- Deep blue gradient header with live data freshness indicator
- Stat cards with colour-coded top borders (date / districts / archive depth / total observations)
- 60-day coverage timeline with month labels
- Area-gradient time series chart for any district
- Top-10 districts horizontal bar chart with opacity gradient
- Category colour legend (LE / E / N / D / LD / NR) with departure thresholds
- Striped table with hover highlight, rounded cards, Inter typography
- Responsive layout for mobile and tablet

### Bug Fixes
- **PIPESTATUS overwrite** — the GitHub Actions workflow was silently swallowing parse failures (exit code was always 0). Fixed by capturing `SCRAPE_EXIT=${PIPESTATUS[0]}` before the `echo` step.
- **State name normalisation** — pre-monsoon data had subdivision names leaking into the state column (e.g. `N. I. KARNATAKA` instead of `KARNATAKA`), breaking district time series across the April–June boundary. Fixed with a SQL `UPDATE OR REPLACE` migration and an expanded `_STATE_NORMALISE` mapping in the parser.
- **Validator thresholds** — raised the daily rainfall upper bound from 2000 mm to 5000 mm to accommodate extreme monsoon events; relaxed the subdivision row count check to accept monsoon-format PDFs (which emit `level=state` rows instead of `level=subdivision`).
- **June data backfilled** — all 13 June 2026 PDFs (June 1–13) were reprocessed and are now in the archive. The database now covers 2026-04-13 → 2026-06-13 (50 dates, 760 districts, 41,800+ observations).

---

## Architecture

```
GitHub Actions (cron: 06:00 UTC = 11:30 IST)
   │
   ├─ scraper.py   →  download IMD PDF (3-strategy fallback + 5 retries)
   ├─ parser.py    →  pdfplumber, handles pre-monsoon and monsoon formats
   ├─ validator.py →  Pandera schema check
   └─ storage.py   →  SQLite upsert  +  per-day CSV  +  Parquet snapshot
                          │
                          ↓
                   api_builder.py
                   generates static JSON
                          │
                          ↓
   git commit → GitHub Pages → /api/*.json + /index.html dashboard
```

**No servers. No databases to host. No credit card required.**

---

## Data model

Each row in `data/rainfall.db` (table `rainfall`):

| Column | Type | Description |
|---|---|---|
| `date` | DATE | Data date (the "day" in the IMD report) |
| `period_start`, `period_end` | DATE | Cumulative-rainfall period bounds |
| `level` | TEXT | `subdivision` / `state` / `district` |
| `subdivision`, `state`, `district` | TEXT | Hierarchy (NULL for higher-level rows) |
| `day_actual_mm`, `day_normal_mm`, `day_departure_pct`, `day_category` | — | Daily rainfall |
| `period_actual_mm`, `period_normal_mm`, `period_departure_pct`, `period_category` | — | Cumulative rainfall |
| `scraped_at` | TIMESTAMP | When this row was written |

**Category codes:** `LE` Large Excess (>+60%) · `E` Excess (+20 to +60%) · `N` Normal (±19%) · `D` Deficient (−20 to −59%) · `LD` Large Deficient (<−60%) · `NR` No Rain.

Auxiliary tables: `scrape_runs` (audit log of every run) and `revisions` (every IMD value change over time).

---

## Quick start (local)

```bash
git clone https://github.com/sayantan-aquacarta/rainfall-pipeline
cd rainfall-pipeline
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Scrape today's data
rainfall scrape

# Inspect the database
rainfall info

# Re-process a saved PDF
rainfall backfill --pdf data/raw_pdf/imd_2026-06-13_<sha>.pdf

# Regenerate the static API from the DB
rainfall rebuild-api

# Run tests
pytest
```

The scrape command runs end-to-end in ~10 seconds and produces:
- `data/raw_pdf/imd_YYYY-MM-DD_<sha>.pdf` — raw PDF (audit trail, last 30 days)
- `data/raw/rainfall_YYYY-MM-DD.csv` — per-day versioned CSV
- `data/rainfall.db` — SQLite (full history, indexed)
- `data/rainfall.parquet` — compressed snapshot for analytics
- `docs/api/**` — static JSON files for the API
- `docs/index.html` — dashboard (already in repo)

> **PDF retention:** only the most recent 30 days of raw PDFs are kept in `data/raw_pdf/` (~24 MB cap). Older PDFs are auto-deleted after each successful scrape. The parsed data in SQLite, CSVs, and Parquet is never affected. Override with `RAINFALL_PDF_RETENTION_DAYS` env var (`0` = keep all indefinitely).

---

## Using the data

### Download the entire archive

```bash
git clone https://github.com/sayantan-aquacarta/rainfall-pipeline
# → all daily CSVs, full SQLite, full Parquet
```

### Use the JSON API (no auth, no rate limits)

GitHub Pages serves static files via CDN — effectively unlimited reads.

```
https://sayantan-aquacarta.github.io/rainfall-pipeline/api/index.json
https://sayantan-aquacarta.github.io/rainfall-pipeline/api/latest.json
https://sayantan-aquacarta.github.io/rainfall-pipeline/api/by-date/2026-06-13.json
https://sayantan-aquacarta.github.io/rainfall-pipeline/api/by-state/maharashtra.json
https://sayantan-aquacarta.github.io/rainfall-pipeline/api/by-district/maharashtra-pune.json
https://sayantan-aquacarta.github.io/rainfall-pipeline/api/states.json
```

### Query in Python / pandas

```python
import pandas as pd
df = pd.read_parquet("https://github.com/sayantan-aquacarta/rainfall-pipeline/raw/main/data/rainfall.parquet")
df.query("state == 'KERALA' and level == 'district'").groupby("date")["day_actual_mm"].mean().plot()
```

```python
import sqlite3, pandas as pd
conn = sqlite3.connect("data/rainfall.db")
pune = pd.read_sql(
    "SELECT date, day_actual_mm FROM rainfall WHERE district='PUNE' AND level='district' ORDER BY date",
    conn
)
```

### Use the interactive dashboard

Visit **[https://sayantan-aquacarta.github.io/rainfall-pipeline/](https://sayantan-aquacarta.github.io/rainfall-pipeline/)** to filter by state/district, view time series, and download data.

---

## Deployment

### One-time setup (5 minutes)

1. **Fork or create the repo** on GitHub.
2. **Settings → Actions → General → Workflow permissions:** select *Read and write permissions*.
3. **Settings → Pages → Build and deployment → Source:** select *GitHub Actions*.
4. **Push the code.** The first scheduled run will happen at the next 06:00 UTC.

To trigger immediately: **Actions tab → Daily rainfall scrape → Run workflow**.

### What the workflow does on each run

1. Downloads the IMD PDF (3-strategy URL fallback, 5 retries with exponential backoff)
2. Parses (handles both pre-monsoon and monsoon formats), validates (Pandera schema), and stores
3. Regenerates the static JSON API
4. Commits `data/` and `docs/api/` to `main` with message `data: rainfall snapshot YYYY-MM-DD`
5. Deploys `docs/` to GitHub Pages
6. On failure: uploads PDF + log as artifact, opens a GitHub Issue tagged `scrape-failure`

### Environment variables / secrets

| Variable | Default | Purpose |
|---|---|---|
| `RAINFALL_PDF_URL` | IMD primary URL | Override the PDF URL if IMD permanently changes it |
| `RAINFALL_PDF_RETENTION_DAYS` | `30` | Days of raw PDFs to keep (`0` = keep all) |

### Cost

GitHub Free includes **2,000 Actions minutes/month** for public repos (unlimited for public repos, in practice). One run takes ~3 minutes. Daily runs ≈ 90 min/month. Pages = free.

**Total monthly cost: $0.**

---

## Robustness

| Failure mode | Mitigation |
|---|---|
| IMD site temporarily down | 5 retries with exponential backoff (2 s → 60 s) |
| IMD changes PDF URL | 3-strategy fallback: primary → page discovery → URL variants; set `RAINFALL_PDF_URL` to override permanently |
| IMD changes PDF format | `is_monsoon_format` flag auto-detects format; Pandera schema validation flags unexpected drift; raw PDF archived for re-parse; failure issue auto-opened |
| Partial data (districts missing) | Schema validation catches < 500 districts as anomaly |
| Duplicate runs | Primary-key upsert is a no-op |
| GitHub Actions outage | Re-running a missed day fetches the "latest" PDF (same URL); for historical gaps use `rainfall backfill --pdf` |
| Concurrent runs | `concurrency:` block in workflow serialises them |
| Push race with manual commit | Workflow retries `git pull --rebase` + push 3 times |

---

## Honest caveats

- **The IMD PDF URL serves "latest" only** — there's no public archive of past dates at predictable URLs. Once this pipeline runs daily, *your repo* becomes the archive. To bootstrap historical data, you need to source PDFs from elsewhere (or contact IMD).
- **IMD changes PDF formats at season boundaries.** This pipeline handles the pre-monsoon (dot-separated dates, bold = aggregate) and monsoon (hyphen-separated dates, serial number = district) formats. If IMD switches PDF generators entirely, the parser will need updating — the Pandera validator is the tripwire.
- **Some districts appear in multiple subdivisions** (e.g., DIU). The PK upsert handles this — last write wins. This is correct behaviour given IMD's source data.
- **Data quality matches IMD's.** This pipeline is a faithful mirror, not a corrector. Missing values (`*` in the PDF) become NULL.

---

## Project structure

```
rainfall-pipeline/
├── .github/workflows/
│   ├── daily-scrape.yml       # cron 06:00 UTC, commits + deploys
│   └── tests.yml              # CI on PRs
├── src/rainfall/
│   ├── config.py              # env-driven settings
│   ├── logging_setup.py       # structlog JSON logging
│   ├── scraper.py             # httpx + tenacity; 3-strategy URL fallback
│   ├── parser.py              # pdfplumber; handles pre-monsoon & monsoon formats
│   ├── validator.py           # Pandera schema
│   ├── storage.py             # SQLite + CSV + Parquet writers
│   ├── api_builder.py         # static JSON API generator
│   └── cli.py                 # `rainfall` command
├── tests/
│   ├── test_parser.py         # format regression tests (both PDF formats)
│   ├── test_scraper.py        # URL discovery + PDF pruning tests
│   ├── test_storage.py
│   ├── test_api_builder.py
│   └── fixtures/sample_imd.pdf
├── data/                      # ← committed by the bot
│   ├── raw/rainfall_*.csv
│   ├── raw_pdf/imd_*.pdf
│   ├── rainfall.db
│   └── rainfall.parquet
├── docs/                      # ← GitHub Pages root
│   ├── index.html             # redesigned Chart.js dashboard (June 2026)
│   └── api/                   # static JSON
├── pyproject.toml
├── Makefile
├── .env.example
├── .gitignore
└── README.md
```

---

## Scaling notes

The current setup scales to:
- **~5 years of daily data** before the repo gets uncomfortably large (~500 MB)
- **Unlimited reads** on the JSON API (GitHub Pages CDN)

To go further:
- **Years 2+:** Use [`git lfs`](https://git-lfs.com/) for the Parquet snapshot, archive old per-day CSVs to GitHub Releases.
- **Multi-source aggregation** (e.g., IMD + state-level boards): keep the same schema, add a `source` column, parallelise scrapers in a matrix job.
- **Real-time API queries** (e.g., complex SQL): add an optional Supabase mirror step that pushes the SQLite contents to a Postgres free tier nightly. The static API stays as the canonical free read path.
- **ML pipelines:** the Parquet snapshot is column-oriented and compressed — load directly into Polars/DuckDB/pandas. Add an `anomaly_score` column populated by a separate workflow that runs after the scrape.

---

## Created by

[EcoCarta](https://www.ecocarta.ai/)

---

## License

MIT — see [LICENSE](LICENSE).

Source data © India Meteorological Department, Ministry of Earth Sciences, Government of India. This pipeline reproduces it for research and educational purposes under fair use; cite IMD as the upstream source in any publication.
