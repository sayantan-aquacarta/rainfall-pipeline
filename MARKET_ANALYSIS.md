# Market Analysis & Differentiation — India Climate Monitor

> Companion to `PRD.md` (requirements/roadmap) and `ARCHITECTURE.md` (system design). This file
> answers two questions: (1) what does the competitive landscape actually look like, verified
> against real sites/PDFs rather than assumed, and (2) what would make this project distinctly
> more useful than what already exists, not just "more modules."
>
> **Research date:** 2026-08-10. Findings below are grounded in live fetches of the cited URLs on
> that date — verify freshness before treating any specific endpoint/URL claim as still true.

---

## 1. Competitive landscape (verified, not assumed)

| Product | Who runs it | What it covers | Granularity | Cadence | Access model | Gap vs. this project |
|---|---|---|---|---|---|---|
| [India-WRIS](https://indiawris.gov.in) | National Water Informatics Centre (Ministry of Jal Shakti) | Rainfall, river levels/discharge, reservoirs, groundwater — the official "everything" portal | Station/basin, GIS-first | Daily/weekly, varies by layer | Public web GIS; API existence mentioned but undocumented publicly | Comprehensive but heavyweight and GIS-tool-oriented, not built for a developer to `fetch()` a small JSON payload in one line. No visible open static API, no git-auditable revision history. |
| [India Drought Monitor](https://indiadroughtmonitor.in) | Water & Climate Lab, IIT Gandhinagar | Combined Drought Index (CDI, not SPI) — 5 severity classes | State + district, map-based | Weekly | Free web maps, PNG downloads; API not evident | Academic/research tool: map-first, no confirmed JSON API, no per-subdivision daily SPI time series a developer can pull programmatically. Different index (CDI vs. our SPI) — genuinely complementary, not a straight substitute. |
| [IMDLIB](https://www.sciencedirect.com/science/article/abs/pii/S1364815223002554) | Academic (open-source) | Gridded (0.25°×0.25°) historical rainfall/temperature retrieval | Grid cell, not district/administrative | Historical archive, not live | Python library, no dashboard | For researchers comfortable with Python + gridded NetCDF-style data. No live daily update, no dashboard, no district-level administrative framing. |
| [data.gov.in](https://www.data.gov.in/catalog/rainfall) | Govt Open Data Platform | Raw dataset catalog (subdivision rainfall, IMD grids, station data from multiple agencies) | Varies wildly by dataset | Irregular, often monthly/seasonal batch uploads | CSV/API downloads, dataset-by-dataset | A catalog, not a product — inconsistent schemas across datasets, no unified daily-updated API, no dashboard, no cross-indicator correlation. |
| IMD's own portal (`mausam.imd.gov.in`) | IMD | Everything, spread across dozens of `.php` pages, regional-center PDFs, and (per §3) an undocumented-but-real public API at `api.imd.gov.in` | Mixed — some national, much of it per-RMC (region) | Mixed | Free, fragmented across many URLs | This *is* the primary data source we already build on. The gap isn't IMD's data, it's the absence of one unified, git-versioned, easy-to-consume product layered on top of it. |

**Bottom line:** nobody currently offers what this project already partially is — a free, no-signup,
no-API-key, static-JSON, daily-updated, district-level rainfall + drought product with a full
git-committed audit trail of every value IMD has ever revised. That combination (not any single
feature) is the actual differentiator, and it should be protected/emphasized rather than diluted
by chasing feature parity with India-WRIS's much larger but heavier scope.

## 2. What's already genuinely differentiated (don't lose this while expanding)

1. **Static JSON, zero auth, zero cost, zero infrastructure** — `fetch('.../api/latest.json')`
   works from a browser console with no signup. None of the competitors above offer this for
   district-level daily data.
2. **Git-committed revision history** — the `revisions` table + daily commits mean every value
   IMD has ever revised is auditable with `git log`/`git blame`, and every historical PDF is
   preserved as forensic evidence. No competitor surfaces this.
3. **Cross-indicator correlation potential** — rainfall + drought (SPI) already share one
   database; reservoirs.html's own roadmap already envisions rainfall→storage correlation. India-
   WRIS has the raw layers but doesn't correlate them into a single narrative; this project could.
4. **Format-change resilience now proven twice** — the state-name normalization fix and this
   session's subdivision-classification fix mean the pipeline has now survived two IMD format
   changes without losing historical continuity. That's an unglamorous but real reliability edge.

## 3. Feasibility findings for the three planned modules (this session's research)

### Temperature — harder than expected via PDF, promising via API
No national "district-wise max/min temperature" PDF exists analogous to the rainfall PDF.
`realized_temperature.php` shows only a static map image. Temperature data on IMD's public site
is fragmented across ~15-20 separate per-RMC "daily weather report" PDFs (Ahmedabad, Chennai,
Delhi, Shillong, etc.), each with its own layout — replicating the rainfall PDF's parsing
approach here would mean building and maintaining many regional parsers instead of one national
one. **Better path found:** IMD's public REST API (`api.imd.gov.in`, see below) exposes
`/api/v1/current_wx` (station-level current conditions) and `/api/v1/cityforecast` (7-day
forecast incl. temperature) as JSON — narrower geographic coverage than "all 760 districts" but
structurally far more robust than PDF scraping. Access requirements (registration, possible IP
allow-listing) are **not yet confirmed** — see §4 caveat before committing engineering time.

### Heat/Cold Wave Alerts — the PDF/GIS path is a dead end; the API path looks strong
`districtWiseHeatwaveWarning.php` is a GIS map with per-district icon overlays (Red/Orange/
Yellow/Green as image assets), not a table or PDF — scraping it would mean image/icon
classification, which is fragile and was explicitly the kind of approach this project already
rejected once for rainfall ("why PDF over HTML/Playwright scraping" in `PROJECT_CONTEXT.md`).
**Strong alternative found:** `api.imd.gov.in`'s `/api/v1/districtwarning` endpoint returns JSON
with 5-day warnings per district, including explicit warning codes for Heat Wave (9), Cold Wave
(12), and Cold Day (13) with severity color codes — i.e., exactly the structured data this module
needs, natively in JSON, no PDF or image parsing required at all. If access is confirmed workable
(§4), this module is arguably **easier** to build than rainfall was.

### Reservoirs — the PDF source is stale; CWC has moved to a live JSON dashboard (RSMS)
**Update 2026-08-10 (second research pass):** the PDF bulletin listing described below is
confirmed **stale** — `cwc.gov.in/reservoirs-storage-bulletin` has not published a new bulletin
since 08.05.2025 (verified via the page's own pagination: page 1 of 21, sorted newest-first,
still shows May 2025 as the latest entry, 15 months behind the actual current date). Building
against it now would mean shipping a module that's frozen from day one — exactly the "looks live,
isn't" failure mode this session already fixed twice for the drought module. **Do not build
against the PDF bulletin path described in the paragraph below without re-verifying it first.**

CWC has migrated reservoir monitoring to a new system: **RSMS** (`rsms.cwc.gov.in`), an Angular
SPA at `/frameWork/web/public-dashboard`. This is confirmed genuinely live and more current than
the old PDF ever was — the page shows "Latest Storage Status... as on 06-08-2026" with
"Last updated: 10-08-2026 03:22 AM" (within a day of the actual current date), covering the same
166 reservoirs, all-India aggregate totals (live capacity at FRL, current storage, last year,
normal), and per-state/per-basin filtering. Network inspection (Chrome DevTools via
`read_network_requests`, not guessing) found a real backend API at `rsms.cwc.gov.in/admin/*`:
- `POST /admin/dashboard-reservoir-list` — **confirmed working**, no auth, empty JSON body
  (`{}`) returns all 166 reservoirs as `{id, name, reservoir_state_id}`. This is a filter-dropdown
  feed, not the storage data itself.
- `POST /admin/dashboard-state-list`, `POST /admin/dashboard-basin-list` — return `{message,
  total, result, count_data, total_reservoirs, status}` on the real page load (200 OK observed in
  the network log) but returned 500 on every parameter guess tried (`state_id`, `reservoir_state_id`,
  `type`, empty body). **The actual request body Angular sends was not captured** — the app uses
  a UI library (not a plain `<select>` with native change events; a dispatched `change` event did
  not trigger a new fetch) that a monkey-patched `window.fetch` running across a full page
  `navigate()` couldn't observe (the patch is lost on hard navigation; the SPA's own router
  wasn't exercised through an actual UI click in this session).
- The endpoint carrying the actual per-reservoir storage table (FRL, current level, % of FRL,
  etc. — visible in the rendered page) was **not identified**. Eight plausible names were tried
  (`dashboard-reservoir-storage`, `dashboard-storage-list`, `reservoir-storage-detail`, etc.) —
  all 404 "Route not found," confirming the API framework is real but the correct route is not a
  simple name-pattern guess.

**Recommended next step, not yet done:** revisit with either (a) a live interactive browser
session where a human or agent actually clicks the state/reservoir dropdown and reads the
resulting request from DevTools' Network tab directly (more reliable than scripted event
dispatch against an unfamiliar UI framework), or (b) contact CWC/NWIC for API documentation —
government portals moving to Angular SPAs sometimes publish Swagger/OpenAPI docs even without
linking them prominently. **Do not extrapolate a working scraper from the one confirmed endpoint
alone** — `dashboard-reservoir-list` provides names only, not storage values, so a module built
on it alone would ship with an empty data table.

Once the correct endpoint is found, this becomes an *easier* build than rainfall: JSON in, no PDF
parsing, no font-weight tricks, and — if RSMS updates as frequently as the "10-08-2026 03:22 AM"
timestamp suggests — potentially higher-cadence data than the old weekly PDF ever offered.

<details>
<summary>Original (now superseded) PDF-based finding, kept for reference</summary>

Verified directly: CWC's weekly bulletin (`cwc.gov.in/sites/default/files/bulletin-DD-MM-YYYY.pdf`,
stable "View" URL pattern — the separate token-gated "Download" link should be avoided) is a
26-page, cleanly-structured, text-extractable PDF (confirmed via this project's own `pdfplumber`).
The core data table ("REGION/STATE WISE WEEKLY REPORT OF 161 IMPORTANT RESERVOIRS") has real
columns — reservoir name, FRL, current level, live capacity, current storage, % of FRL (current
year / last year / normal), irrigation CCA, hydel MW — grouped by Region → State section headers,
extractable with `pdfplumber.extract_tables()` without needing a font-weight trick (CWC's PDF has
actual grid lines, unlike IMD's rainfall PDF). This remains a viable **fallback** if RSMS's API
can't be pinned down, but it means building against a source that's 15 months stale as of this
writing and hoping CWC resumes publishing to it — the live RSMS dashboard should be exhausted as
an option first.

</details>

## 4. Critical caveat before building against `api.imd.gov.in`

The API's own portal (`api.imd.gov.in/public/index.php`) requires account creation, and the
sibling page (`apis.php`) references an "IP Whitelisting Portal" — language that strongly
suggests production access may require registering a **fixed IP address**. This would be
incompatible with the current zero-cost architecture: GitHub Actions runners use rotating,
non-whitelistable IPs. **Before writing any code against this API, verify concretely**:
1. Whether unauthenticated/no-key access actually works for the endpoints needed
   (`districtwarning`, `current_wx`, `cityforecast`) — test with a real HTTP call, not a docs read.
2. If a key is required, whether it's tied to a fixed IP (blocking) or just a bearer token
   (workable — GitHub Actions can hold a repo secret).
3. Rate limits and terms of use for automated/scheduled polling (once or twice daily, matching
   this project's existing cadence, should be well within reason for any public government API,
   but confirm rather than assume).

If IP-whitelisting turns out to be mandatory, the fallback is a self-hosted always-on relay (adds
real infrastructure cost, breaking the ₹0/month design goal) or reverting to the harder per-RMC
PDF-scraping path for temperature specifically, while re-evaluating whether Heat/Cold Wave Alerts
should instead be redesigned around whatever the reachable subset of the API allows.

## 5. Ideas that would make this uniquely useful (beyond "add three more dashboards")

Ranked by differentiation value relative to effort, given what §1-§2 show nobody else is doing:

1. **Rainfall ↔ Reservoir correlation view.** Once Reservoirs ships, show upstream-subdivision
   rainfall against downstream reservoir fill rate on one chart. This is explicitly *not*
   available from India-WRIS (raw layers, no correlation) or CWC (reservoir-only) — it's the
   single highest-leverage idea here because it needs no new data source, just a join across data
   this project will already own.
2. **A genuinely open `/api/changelog.json`** surfacing the `revisions` table as a public feed —
   "which districts had their rainfall numbers revised by IMD in the last 30 days, and by how
   much." No competitor exposes IMD's own data-quality/revision behavior; this is unique, cheap
   (the data already exists in SQLite), and valuable to researchers studying data reliability.
3. **District drought "streak" tracking** — consecutive weeks/months a subdivision has spent in
   D1+ (Moderate Drought or worse), not just current-snapshot severity. India Drought Monitor
   shows a current map; a streak/duration view is a different, complementary lens nobody
   surfaces cleanly.
4. **A stability/trust indicator per module** — given this project has now survived two silent
   format-change bugs, publish `last_verified_fresh` and a simple automated freshness check (the
   tripwire already proposed in `PRD.md §5`) *on the dashboard itself*, not just in JSON metadata.
   Government portals rarely admit when their own data might be stale; visibly proving freshness
   would be a trust differentiator, not just an internal safeguard.
5. **Simple embeddable widgets** (a single `<script src="…widget.js" data-district="…">` that
   renders a tiny sparkline) — turns the static JSON API into something journalists/bloggers can
   drop into an article with zero backend, extending reach without adding server cost.

Items 1-3 use data this project will already have once Reservoirs ships and require no new
upstream research. Items 4-5 are pure product work on top of the existing rainfall+drought data
and could ship before any new government-data integration at all.

## 6. Recommended sequencing

1. ~~Ship the P0 freshness tripwire~~ — **done 2026-08-10.** `drought.py` now flags
   `is_stale`/`staleness_days` in `drought-latest.json`, `drought.html` shows a red "Stale" pill
   in place of the green "Live" one when triggered. Also fixed in the same session: the drought
   freeze itself (parser bug, backfilled June 14 – Aug 8) and a Delhi/Delhi(UT) state-name split
   discovered via live-site verification after deploy — same bug class, different symptom.
2. **Reservoirs — blocked on one more research step, not a build step.** The PDF path is
   confirmed stale (15 months, see §3). The replacement RSMS live dashboard is confirmed to exist
   and work, but the specific API call that returns per-reservoir storage values (not just the
   name list) wasn't identified in this session's research pass — see §3 for exactly what's
   confirmed vs. still needed. Resolve that one endpoint before writing any scraper code; don't
   build against a guess.
3. Once #2's endpoint is confirmed: ship the **rainfall↔reservoir correlation view** (§5.1)
   immediately after the reservoir module itself — this is where the real differentiation shows
   up, not in the reservoir dashboard alone.
4. Verify `api.imd.gov.in` access concretely (§4) before committing to Temperature/Heat-Wave
   Alerts. If it's genuinely open (no IP allow-listing), both modules become significantly easier
   than originally scoped in `PRD.md §5 P2` and should be re-estimated downward. If it's blocked,
   revisit scope rather than falling back to fragile per-RMC PDF/GIS scraping by default.
5. Layer in §5.2-§5.5 (changelog feed, drought streaks, freshness UI, embeddable widgets)
   opportunistically — each is small, independent, and doesn't block on any new data source.
