# Product Requirements Document — India Climate Monitor (rainfall-pipeline)

> Companion to `ARCHITECTURE.md` (system design) and `PROJECT_CONTEXT.md` (session-handoff
> operational notes). This file owns *what the product should do and for whom*; architecture
> owns *how it's built*. Keep both in sync when scope changes.
>
> **Last audited:** 2026-08-10. Status column below reflects a live code/data audit on that
> date, not aspirational state — verify against `git log` and the live site before trusting it
> if this file is more than a few weeks old.

---

## 1. Vision

A free, self-updating, open-source climate data service for India. One person maintains it
part-time; the infrastructure cost is ₹0/month (GitHub Actions + Pages + repo storage). The
long-term goal is a small family of daily-refreshed climate indicators — rainfall, drought,
temperature extremes, heat/cold wave alerts, reservoir storage — cross-linked as one "India
Climate Monitor" product, each backed by a public static JSON API researchers and developers
can build on without asking permission or paying for a key.

## 2. Users

- **Primary:** the maintainer (Sayantan), using the dashboard and JSON API for personal/EcoCarta
  analysis work.
- **Secondary:** researchers, journalists, and developers who discover the dashboard or API and
  want India-wide rainfall/drought/climate data without scraping IMD themselves.
- **Non-user:** nobody is depending on this for operational/safety-critical decisions (no SLA,
  no paging, best-effort freshness).

## 3. Module status (ground truth as of 2026-08-10)

| Module | Dashboard page | Backing pipeline | Status | Notes |
|---|---|---|---|---|
| Rainfall | `docs/index.html` | `parser.py` → `storage.py` → `api_builder.py` | 🟢 **Live, healthy** | Daily data current to within 1 day (IMD's own publication lag). 41,800+ observations, 760 districts. |
| Drought (SPI) | `docs/drought.html` | `drought.py` | 🟢 **Live, fixed 2026-08-10** | Was frozen since 2026-05-31 (see §6.1, now resolved). Parser restored + full historical backfill (June 14 – Aug 8) run; `reference_date` now tracks current data across 86 dates. Nav label corrected to "Live" on all 5 dashboard pages. |
| Temperature | `docs/temperature.html` | none | 🟡 **UI shell only** | No scraper/parser exists. Page is honest about this ("Pipeline in Development"). |
| Heat/Cold Wave Alerts | `docs/heatwave.html` | none | 🟡 **UI shell only** | Same — no backing pipeline. |
| Reservoir Storage | `docs/reservoirs.html` | none | 🟡 **UI shell only** | Same — no backing pipeline. Different upstream source (CWC, not IMD). |

## 4. Functional requirements — Rainfall module (the only complete module; baseline for the rest)

1. **FR-1** Scrape the IMD district rainfall PDF daily at 11:30 IST, with retry (httpx + tenacity,
   5 attempts) and a checksum-named archival copy in `data/raw_pdf/`.
2. **FR-2** Parse into district/state/subdivision rows using the font-weight classifier
   (Bold = aggregate row, Regular = district row — see `ARCHITECTURE.md §5`).
3. **FR-3** Validate against a Pandera schema before writing; fail the run rather than write
   malformed data.
4. **FR-4** Upsert into SQLite keyed on `(date, level, subdivision, state, district)`; log any
   value change to a `revisions` audit table (IMD sometimes revises past days).
5. **FR-5** Publish every day's data as a static JSON API (`/api/latest.json`, `/api/by-date/*`,
   `/api/by-state/*`, `/api/by-district/*`, `/api/index.json`, `/api/states.json`).
6. **FR-6** Render an interactive dashboard (Chart.js, no build step) with state/district/date
   filtering and CSV/JSON export.
7. **FR-7** On scrape failure, auto-open a labeled GitHub issue with the log tail (dedup by day).
8. **FR-8** Retain only the most recent 30 days of raw PDFs (configurable); never prune parsed
   data.

These eight requirements are met today and should be treated as the reference contract every
future module (temperature, heatwave, reservoirs) is expected to satisfy before being marked
🟢 Live.

## 5. Roadmap

### P0 — done (fixed 2026-08-10)
- ~~Restore subdivision-level rows in monsoon-format PDFs~~ — fixed. `parser.py`'s monsoon
  branch now classifies bold/no-serial rows by name against a canonical 36-subdivision master
  list instead of collapsing everything to `level="state"`. See `ARCHITECTURE.md §5` for the
  mechanism (name-based subdivision recognition + spelling-canonicalization map) and why the
  earlier "7 hardcoded composites" idea was replaced with a general rule after real-PDF
  inspection revealed state-wrapper lines (e.g. "RAJASTHAN" wrapping "EAST/WEST RAJASTHAN") and
  double-nested UT enclaves (Daman & Diu inside both Gujarat Region and Saurashtra & Kutch) that
  a fixed whitelist wouldn't have handled correctly.
- ~~Backfill historical gap~~ — done. Recovered June 14 – July 10 PDFs from git blob history
  (pruned from the working tree by the 30-day retention policy, but never lost — `data/` is
  git-tracked) and re-ran backfill across the full June 14 – Aug 8 monsoon-format range.
  `drought-history.json` now spans 86 dates (was 40, frozen at 2026-05-31).
- ~~Fix the Drought nav-label inconsistency~~ — done. All 5 dashboard pages now show
  Drought as "Live".
- **Still open:** a freshness tripwire. `drought.py` (and ideally `api_builder.py`) should
  compare `reference_date`/`last_date` against "today" and fail loud (or at minimum set a
  `stale: true` flag in the JSON payload) if the gap exceeds ~2 days, instead of silently
  republishing old data with a new timestamp. This exact bug class — pipeline "succeeds" while
  serving stale data — is what let the drought freeze go unnoticed for 10 weeks, and nothing
  currently prevents a recurrence if a future format change breaks classification again.

### P1 — Close small integrity/hygiene gaps
- Pin/install `scipy` in the local dev venv so `pytest` collects locally (currently fails on
  `ModuleNotFoundError: scipy` — CI reinstalls fresh each run so this is a local-only gap, but
  it silently defeats "run tests before pushing").
- Replace placeholder `user_agent` in `config.py` (`your-org`, `your-email@example.com`) with
  real contact info — good scraping citizenship, and IMD is more likely to not block a
  self-identifying client.
- Gitignore or remove stray untracked files at repo root (`rtk.exe`,
  `rtk-x86_64-pc-windows-msvc.zip`, `graphify-out/`, `.github/copilot-instructions.md`) —
  unrelated tool artifacts currently sitting in the working tree.

### P2 — New modules (build in this order; each must satisfy FR-1..FR-8 pattern before going Live)
1. **Temperature Extremes** — same IMD source family as rainfall (per `temperature.html`'s own
   roadmap panel, parser work is already flagged "in progress" there — verify against actual
   code before trusting that label; as of this audit no `temperature.py` module exists).
2. **Heat/Cold Wave Alerts** — depends on Temperature module for correlation per its own hero
   copy; separate IMD warning PDF.
3. **Reservoir Storage** — different publisher (CWC, not IMD), weekly not daily cadence, HTML
   table primary format with PDF fallback per its own "Data Source" panel.

Each new module should get its own `src/rainfall/<module>.py`, its own Pandera-style validation,
its own `docs/api/<module>-*.json` outputs, and a dashboard page that only claims "Live" once
FR-1..FR-8 are actually true — the drought module is the cautionary example of what happens when
a "Live" label outruns the pipeline behind it.

## 6. Known issues (defect log)

### 6.1 [FIXED 2026-08-10] Drought SPI data frozen since 2026-05-31
**Symptom:** `docs/api/drought-latest.json` → `reference_date` never advanced past `2026-05-31`,
while `generated_at` updated every day. **Root cause:** `parser.py`'s `is_monsoon_format` branch
(introduced 2026-06-14 to handle IMD's June PDF format change) classified every aggregate/header
row as `level="state"` and never as `level="subdivision"`. `drought.py::compute_drought_status()`
selects `WHERE level='subdivision'`, so it had zero new input rows for ~10 weeks. No crash, no
failed validation — silent semantic drift. **Impact:** the public Drought dashboard displayed
incorrect (stale) SPI values to any visitor from mid-June through 2026-08-10.
**Fix:** rewrote the monsoon-format branch to classify bold/no-serial rows by name against a
canonical 36-subdivision list (see `ARCHITECTURE.md §5`); backfilled June 14 – Aug 8 by
recovering pruned PDFs from git blob history. Verified: 36 subdivisions/date, `reference_date`
now tracks current data, all 57 tests pass.

### 6.2 [FIXED 2026-08-10] Cross-page nav inconsistency
`index.html` nav bar marked Drought "Soon"; `drought.html`'s own nav bar marked itself "Live".
Fixed by correcting the label (now "Live") on all 5 dashboard pages, now that §6.1 is actually
fixed. The underlying structural issue — nav bars are hand-copied per page, not a shared partial
— is unfixed; see `ARCHITECTURE.md §8.3` for the recommended approach if a 6th page is added.

### 6.3 [FIXED 2026-08-10] Local dev environment drift
`.venv` was missing `scipy`; `pytest` failed at collection for `test_drought.py`. Fixed via
`pip install -e ".[dev]"`. CI (`tests.yml`) does this fresh on every run so it likely never
affected CI, but "run tests locally before pushing" wasn't actually possible until this ran.

## 7. Non-goals

- No user accounts, no server-side compute, no paid infrastructure — the zero-cost static
  architecture is a deliberate constraint, not a limitation to "fix" (see `ARCHITECTURE.md §7`
  for the reasoning already on record).
- No SLA/uptime guarantee — this is a best-effort personal/research project.
- No historical backfill beyond what's in `data/raw_pdf/` — IMD doesn't publish past PDFs at
  predictable URLs.

## 8. Success signals

- Every module marked 🟢 Live has data whose `reference_date`/`last_date` is within 2 days of
  "today" — verified by the P0 freshness tripwire, not by eyeballing a timestamp.
- `pytest` passes locally and in CI.
- Dashboard nav is identical (and accurate) across all pages.
