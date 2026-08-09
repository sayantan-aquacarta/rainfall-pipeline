# Architecture — India Climate Monitor (rainfall-pipeline)

> Companion to `PRD.md` (what/why) and `PROJECT_CONTEXT.md` (session-handoff operational notes,
> "what to do if things break"). This file owns *how it's built*. Update it whenever a component
> is added, replaced, or its data contract changes.
>
> **Last audited:** 2026-08-10 against commit `f6f20d3a6`.

---

## 1. System overview

```
                    ┌─────────────────────────┐
                    │   IMD (mausam.imd.gov.in)│
                    │  daily PDF, 11:30 IST     │
                    └────────────┬─────────────┘
                                 │ httpx + tenacity (5 retries)
                                 ▼
                    ┌─────────────────────────┐
                    │  scraper.py              │  → data/raw_pdf/*.pdf (30-day retention)
                    └────────────┬─────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │  parser.py                │  font-weight classifier (pdfplumber)
                    └────────────┬─────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │  validator.py             │  Pandera schema — fails fast on format drift
                    └────────────┬─────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │  storage.py                │  → data/rainfall.db (SQLite, canonical)
                    └────────────┬─────────────┘        data/raw/*.csv (one per day)
                                 │                       data/rainfall.parquet
                                 ▼
                ┌────────────────┴─────────────────┐
                ▼                                   ▼
    ┌─────────────────────┐            ┌─────────────────────────┐
    │  api_builder.py       │            │  drought.py                │
    │  → docs/api/**.json    │            │  → docs/api/drought-*.json  │
    └───────────┬───────────┘            └────────────┬─────────────┘
                │                                      │
                └──────────────────┬───────────────────┘
                                   ▼
                    ┌─────────────────────────┐
                    │  GitHub Pages (docs/)      │  index.html, drought.html,
                    │  static site, no server     │  temperature/heatwave/reservoirs (shells)
                    └─────────────────────────┘
```

Orchestration: GitHub Actions cron (`daily-scrape.yml`) runs the whole left column via
`rainfall.cli scrape`, commits the outputs, then triggers a `deploy-pages` job. See §4.

## 2. Components

| File | Responsibility | Depends on |
|---|---|---|
| `src/rainfall/config.py` | Single source of truth for paths/URLs/env overrides (`RAINFALL_*` env vars) | — |
| `src/rainfall/scraper.py` | Fetch + checksum the IMD PDF, retry logic, 30-day PDF pruning | `config` |
| `src/rainfall/parser.py` | PDF → rows. Font-weight classification (`Trebuchet MS,Bold` = aggregate, `Helvetica` = district) plus a monsoon-format branch keyed on serial-number presence | pdfplumber |
| `src/rainfall/validator.py` | Pandera schema — the tripwire that's supposed to catch format drift | pandera |
| `src/rainfall/storage.py` | SQLite upsert (idempotent, PK-based), CSV writer, Parquet snapshot, revision audit log | sqlite3, pandas |
| `src/rainfall/api_builder.py` | Regenerates all of `docs/api/**` from SQLite on every run; wipes and rewrites per-entity dirs so deletions propagate | pandas |
| `src/rainfall/drought.py` | SPI (Standardized Precipitation Index) computation from subdivision-level rows; writes `drought-latest.json` / `drought-history.json` | scipy, numpy, pandas |
| `src/rainfall/cli.py` | `rainfall` command: `scrape`, `backfill`, `rebuild-api`, `snapshot`, `info`, `drought` | click |
| `src/rainfall/logging_setup.py` | structlog JSON logging | structlog |

**Dashboard pages** (`docs/*.html`) are single-file, no-build-step HTML+CSS+JS (Chart.js via
CDN). This is an intentional design choice (see §7) — don't introduce a bundler/framework
without a concrete reason.

## 3. Data model

Canonical table, `data/rainfall.db :: rainfall`:

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

Auxiliary tables: `scrape_runs` (every run logged, success/failure), `revisions` (every value
change over time — IMD revises past days occasionally).

**Critical invariant every downstream consumer relies on:** all three `level` values
(`subdivision`, `state`, `district`) must be populated for every scrape date. `drought.py`
depends specifically on `level='subdivision'` rows existing per date — this invariant is
*currently violated* for all monsoon-format PDFs (since 2026-06-14); see `PRD.md §6.1`. There is
no schema-level or CI-level check that all three levels are present per date — the Pandera
validator checks row shape/types, not level-completeness. That's the actual root gap: the
`level` taxonomy is a load-bearing contract between `parser.py` and every consumer
(`drought.py`, potentially future modules), but nothing enforces it.

## 4. Automation loop

```
cron: 06:00 UTC daily (= 11:30 IST)
  → GitHub Actions checkout
  → pip install -e .
  → rainfall.cli scrape:
      fetch_pdf() → save_pdf() → parse_pdf() → validate()
      → write_csv() → upsert_rainfall() → write_parquet_snapshot()
      → build_all()            (api_builder.py → docs/api/**)
      → compute_and_build()    (drought.py → docs/api/drought-*.json)   [--with-drought flag, on by default in CLI]
  → git commit ("data: rainfall snapshot YYYY-MM-DD [skip ci]") + push (rebase-retry ×3)
  → on success → deploy-pages job → GitHub Pages rebuild
  → on failure → auto-open labeled GitHub issue with log tail (deduped per day)
```

Concurrency group `rainfall-scrape` prevents overlapping runs. `deploy-pages` only runs
`if: success()` on the scrape job — a scrape failure correctly blocks a stale-looking deploy from
masking itself as a fresh one, though it does *not* protect against the "succeeded but produced
semantically stale data" failure mode described in §3.

Separate `tests.yml` runs `ruff check` + `pytest -v` on push/PR to `main` when
`src/**`, `tests/**`, or `pyproject.toml` change.

## 5. The parser: why it's fragile and how it's kept honest

The IMD PDF renders subdivisions, states, and districts with the same X-position and similar
text structure — pure text-based row classification produced false positives in two earlier
attempts. The reliable signal is **font**: aggregate rows (subdivision/state) render in
`Trebuchet MS,Bold`; district rows render in `Helvetica` (regular). `parser.py` reads
pdfplumber's char-level `fontname` metadata to classify rows on this basis.

**This broke in June 2026** when IMD changed the monsoon-season PDF to render *all* rows bold.
The fix (`is_monsoon_format` flag, keyed on a new date-header regex) switched classification to
serial-number presence instead of boldness — correct for the district/non-district split, but it
collapsed the subdivision/state distinction in the process (every aggregate row became
`level="state"`), which was the direct cause of the drought-data staleness documented in
`PRD.md §6.1`. **Fixed 2026-08-10.**

**How the fix works:** serial-number presence still separates districts from aggregate rows, but
aggregate rows are no longer uniformly `level="state"`. Instead, a bold/no-serial row is a genuine
*subdivision* boundary iff its name (after canonicalizing known monsoon-format spelling changes)
matches a hardcoded 36-entry master subdivision list (`_SUBDIVISIONS` in `parser.py`, sourced from
the pre-monsoon data that was already correctly classified). Any other bold/no-serial row is
treated as a nested state-context update under whichever subdivision is currently open — this
covers both genuine nested states inside composite subdivisions (e.g. "ASSAM" inside
"ASSAM & MEGHALAYA") *and* cosmetic state-wrapper lines IMD's monsoon layout now prints above
groups of subdivisions (e.g. "RAJASTHAN" printed above "EAST RAJASTHAN"/"WEST RAJASTHAN", which
carries no new information since standalone subdivisions already self-map to their state via
`_STATE_NORMALISE`).

This approach was chosen over hardcoding a fixed list of "composite" subdivisions after directly
inspecting the real monsoon PDF's line sequence (`_extract_lines_with_font` on a saved PDF) — that
inspection showed the structure is genuinely more varied than the original 7-composite mental
model: some states (Gujarat, Rajasthan, Karnataka, Madhya Pradesh, Uttar Pradesh) get an explicit
wrapper line over multiple subdivisions; some subdivisions nest multiple states directly (Assam &
Meghalaya, Konkan & Goa, NMMT, etc.); and at least one UT (Dadra & Nagar Haveli and Daman & Diu)
nests inside *two different* subdivisions (Gujarat Region and Saurashtra & Kutch) for different
districts — matching the pre-existing documented quirk "DIU appears twice in the source PDF." A
name-based master-list test handles all of these uniformly without needing to special-case each
pattern. `level="state"` rows are not consumed by any current downstream code (verified by grep)
so the occasional harmless junk row from a wrapper line has no functional impact.

**Lesson for whoever touches this next:** if IMD renames a subdivision again, add the mapping to
`_SUBDIVISION_NORMALISE`, not to `_STATE_NORMALISE` — they're different concerns (the state map
governs the `state` column; the subdivision map governs the `subdivision` column and, critically,
which master-list membership test decides `level="subdivision"` vs `level="state"`).

If IMD changes the PDF format again, `validator.py`'s Pandera schema is the designed tripwire —
but note it validates *shape*, not *level-completeness*, so a repeat of the June-2026-style bug
(right shape, wrong level tagging) would slip through again without the freshness tripwire
proposed in `PRD.md §5`.

## 6. Infrastructure & deployment

- **Compute:** GitHub Actions (free tier, public repo).
- **Storage:** SQLite + CSV + Parquet, all committed to git (`data/` is bot-owned — see rule in
  `PROJECT_CONTEXT.md §8`). No external database.
- **Serving:** GitHub Pages, static files only, served from `docs/`.
- **Secrets:** none currently required (public PDF, public repo). Keep it that way per
  `PROJECT_CONTEXT.md`.
- **Cost:** ₹0/month by design.

## 7. Key architectural decisions (don't relitigate without a concrete new constraint)

- **PDF over HTML scraping** — the IMD page embeds PNGs + a PDF, no tabular HTML exists. Tried
  and rejected: HTML/Playwright scraping.
- **SQLite + Git over Postgres** — dataset is small (~770 rows/day), and git commits double as
  free time-travel backups. Revisit only if row volume or write concurrency changes materially.
- **Static JSON over FastAPI** — read-heavy, write-once-daily workload; GitHub Pages CDN scales
  free. Revisit only for a concrete need static files can't serve (e.g., server-side filtering
  at query time).
- **Single-file dashboards, no build step** — intentionally easy to hand-edit. Don't introduce
  React/Vue/a bundler without a concrete reason.

## 8. Known technical debt (see `PRD.md §6` for the prioritized defect log)

1. **`level` taxonomy is unenforced** (§3) — the specific instance (drought staleness) was fixed
   2026-08-10, but the underlying gap remains: nothing checks that all three `level` values are
   present per date, so the same class of bug could recur silently if the PDF format changes
   again. Still the biggest single risk to any future module that depends on a specific `level`
   value being populated.
2. **No freshness/staleness check anywhere in the pipeline** — a run can "succeed" (exit 0, data
   committed, Pages deployed) while silently republishing old data with a new timestamp, as it did
   for the drought module for ~10 weeks before the 2026-08-10 fix. Nothing compares
   `reference_date` to "today" — this is why the bug went unnoticed for so long, and it's still
   true today even though the specific instance is fixed. Proposed in `PRD.md §5`, not yet built.
3. **Dashboard nav bars are hand-duplicated per page**, not a shared partial/include (no build
   step means no templating engine either) — this is *why* `index.html` and `drought.html`
   disagree about whether Drought is "Live." If a fifth dashboard page is added, expect the same
   drift unless nav is extracted into one JS-injected partial or a light templating step is
   introduced at build time (still no server needed — could be a pre-commit or CI step that
   stamps the same nav HTML into all `docs/*.html` files).
4. **Local dev/CI drift** — fixed 2026-08-10 (`.venv` was missing `scipy`). If this recurs, verify
   `pip install -e ".[dev]"` was re-run after `pyproject.toml` dependency changes.
5. **Repo grows unbounded** (`.git` ~145MB and climbing ~daily from committed binary snapshots)
   — accepted tradeoff today (see §7), but worth a periodic check that it's still true as data
   volume grows over years, per the "Scaling notes" already flagged in `PROJECT_CONTEXT.md`.

## 9. Extension pattern for new modules (temperature, heatwave, reservoirs)

Each new module should mirror the rainfall module's shape exactly, so the P0 lesson (§3, §8.1-2)
isn't repeated:

1. `src/rainfall/<module>.py` — scrape/parse/validate/store, own SQLite table (or extend the
   existing schema if the source PDF is genuinely the same shape — verify, don't assume, since
   assuming caused the current bug).
2. A Pandera schema for the new table, **plus** an explicit completeness check if the module has
   any multi-level taxonomy analogous to `level` — don't repeat the unenforced-invariant mistake.
3. `docs/api/<module>-*.json` outputs, generated idempotently like `api_builder.py`.
4. A dashboard page cloned from an existing shell (`temperature.html` et al. already have the nav
   and layout scaffolding) — flip its nav badge from "Soon" to "Live" only once the module
   satisfies `PRD.md §4`'s FR-1..FR-8 pattern, and update that badge identically on **every**
   other dashboard page's nav in the same commit (see debt item §8.3).
5. Wire into `daily-scrape.yml` (or a separate workflow if cadence differs — reservoirs is weekly
   per CWC's bulletin schedule, not daily).
