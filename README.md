# 🌧️ India Rainfall Data Pipeline

A **fully open-source, zero-cost, fully automated** daily rainfall data pipeline for India. Scrapes the [IMD District Rainfall Distribution](https://mausam.imd.gov.in/imd_latest/contents/rainfall_statistics_3.php) PDF every day at **11:30 IST**, parses it into structured data, and serves it via a static JSON API and dashboard on GitHub Pages.

- ✅ **$0/month** — runs entirely on free GitHub infrastructure (Actions + Pages + repo)
- ✅ **Fully autonomous** — daily cron, auto-commits data, auto-opens issues on failure
- ✅ **Research-ready** — every datapoint tied to a Git commit; Parquet + SQLite + per-day CSVs
- ✅ **Reproducible** — raw PDFs archived alongside parsed data
- ✅ **Idempotent** — re-running a day overwrites cleanly; data revisions are logged

---

## Architecture

```
GitHub Actions (cron: 06:00 UTC = 11:30 IST)
   │
   ├─ scraper.py   →  download IMD PDF (5 retries, exp backoff)
   ├─ parser.py    →  pdfplumber + font-weight classification
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

**Category codes:** `LE` Large Excess · `E` Excess · `N` Normal · `D` Deficient · `LD` Large Deficient · `NR` No Rain.

Auxiliary tables: `scrape_runs` (audit log of every run) and `revisions` (every IMD value change over time).

---

## Quick start (local)

```bash
git clone https://github.com/your-org/rainfall-pipeline
cd rainfall-pipeline
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Scrape today's data
rainfall scrape

# Inspect the database
rainfall info

# Re-process a saved PDF
rainfall backfill --pdf data/raw_pdf/imd_2026-04-13_318be450.pdf

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

> **PDF retention:** only the most recent 30 days of raw PDFs are kept in `data/raw_pdf/` (~24 MB cap). Older PDFs are auto-deleted after each successful scrape. The parsed data in SQLite, CSVs, and Parquet is never affected. Override the window with the `RAINFALL_PDF_RETENTION_DAYS` environment variable (set to `0` to keep all PDFs indefinitely).

---

## Using the data

### Download the entire archive

Just `git clone` the repo. The `data/` directory contains everything:

```bash
git clone https://github.com/your-org/rainfall-pipeline
# → all daily CSVs, full SQLite, full Parquet
```

### Use the JSON API (no auth, no rate limits)

GitHub Pages serves static files via Cloudflare's CDN — effectively unlimited reads.

```
https://your-org.github.io/rainfall-pipeline/api/index.json
https://your-org.github.io/rainfall-pipeline/api/latest.json
https://your-org.github.io/rainfall-pipeline/api/by-date/2026-04-13.json
https://your-org.github.io/rainfall-pipeline/api/by-state/maharashtra.json
https://your-org.github.io/rainfall-pipeline/api/by-district/maharashtra-pune.json
https://your-org.github.io/rainfall-pipeline/api/states.json
```

### Query in Python / R / pandas

```python
import pandas as pd
df = pd.read_parquet("https://github.com/your-org/rainfall-pipeline/raw/main/data/rainfall.parquet")
df.query("state == 'KERALA' and level == 'district'").groupby("date")["day_actual_mm"].mean().plot()
```

```python
import sqlite3, pandas as pd
conn = sqlite3.connect("data/rainfall.db")
pune = pd.read_sql("SELECT date, day_actual_mm FROM rainfall WHERE district='PUNE' AND level='district' ORDER BY date", conn)
```

### Use the dashboard

Open `https://your-org.github.io/rainfall-pipeline/` — filter by state/district, view time series, download.

---

## Deployment

### One-time setup (5 minutes)

1. **Fork or create the repo** on GitHub.
2. **Settings → Actions → General → Workflow permissions:** select *Read and write permissions*.
3. **Settings → Pages → Build and deployment → Source:** select *GitHub Actions*.
4. **Push the code.** The first scheduled run will happen at the next 06:00 UTC.

To trigger immediately: **Actions tab → Daily rainfall scrape → Run workflow**.

### What the workflow does on each run

1. Downloads the IMD PDF (5 retries with exponential backoff)
2. Parses, validates (Pandera schema), and stores
3. Regenerates the static JSON API
4. Commits `data/` and `docs/api/` to `main` with message `data: rainfall snapshot YYYY-MM-DD`
5. Deploys `docs/` to GitHub Pages
6. On failure: uploads PDF + log as artifact, opens a GitHub Issue tagged `scrape-failure`

### Cost

GitHub Free includes **2,000 Actions minutes/month** for public repos (unlimited, actually — Actions on public repos are free). One run takes ~3 minutes including setup. Daily runs = ~90 min/month. Pages = free, no bandwidth limit on the free tier for reasonable use.

**Total monthly cost: $0.**

---

## Robustness

| Failure mode | Mitigation |
|---|---|
| IMD site temporarily down | 5 retries with exponential backoff (2s → 60s) |
| IMD changes PDF format | Pandera schema validation flags it; raw PDF archived for re-parse; failure issue auto-opened |
| Partial data (some districts missing) | Schema validation catches < 500 districts as anomaly |
| Duplicate runs | Primary-key upsert is a no-op |
| GitHub Actions outage | Catch-up: re-running covers any missed day (PDF URL is a stable "latest" URL — but for true backfill of *past* dates, you need archived PDFs) |
| Concurrent runs | `concurrency:` block in workflow serializes them |
| Push race with manual commit | Workflow retries `git pull --rebase` + push 3 times |
| Repo grows too large (years out) | Archive raw CSVs > 1 year old to a Releases artifact; keep DB + Parquet in main |

---

## Honest caveats

- **The IMD PDF URL serves "latest" only** — there's no public archive of past dates at predictable URLs. Once this pipeline runs daily, *your repo* becomes the archive. To bootstrap historical data, you'd need to source PDFs from elsewhere (or contact IMD).
- **Schema may evolve.** IMD has changed PDF formats before. The font-weight classifier is robust to minor layout changes, but if IMD switches PDF generators entirely, the parser will need updating. The schema validator is your tripwire.
- **Some districts appear in multiple subdivisions** (e.g., DIU). The PK upsert handles this — last write wins. This is correct behavior given IMD's source data.
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
│   ├── scraper.py             # httpx + tenacity retries
│   ├── parser.py              # pdfplumber + font-weight classification
│   ├── validator.py           # Pandera schema
│   ├── storage.py             # SQLite + CSV + Parquet writers
│   ├── api_builder.py         # static JSON API generator
│   └── cli.py                 # `rainfall` command
├── tests/
│   ├── test_parser.py
│   ├── test_storage.py
│   ├── test_api_builder.py
│   └── fixtures/sample_imd.pdf
├── data/                      # ← committed by the bot
│   ├── raw/rainfall_*.csv
│   ├── raw_pdf/imd_*.pdf
│   ├── rainfall.db
│   └── rainfall.parquet
├── docs/                      # ← GitHub Pages root
│   ├── index.html             # Chart.js dashboard
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
- **~5 years of daily data** before the repo gets uncomfortably large (~500MB)
- **Unlimited reads** on the JSON API (GitHub Pages CDN)

To go further:
- **Years 2+:** Use [`git lfs`](https://git-lfs.com/) for the Parquet snapshot, archive old per-day CSVs to GitHub Releases.
- **Multi-source aggregation** (e.g., IMD + state-level boards): keep the same schema, add a `source` column, parallelize scrapers in a matrix job.
- **Real-time API queries** (e.g., complex SQL): add an optional Supabase mirror step that pushes the SQLite contents to a Postgres free tier nightly. The static API stays as the canonical free read path.
- **ML pipelines:** the Parquet snapshot is column-oriented and compressed — load directly into Polars/DuckDB/pandas. Add an `anomaly_score` column populated by a separate workflow that runs after the scrape.

---

## License

MIT — see [LICENSE](LICENSE).

Source data © India Meteorological Department, Ministry of Earth Sciences, Government of India. This pipeline reproduces it for research and educational purposes under fair use; cite IMD as the upstream source in any publication.
