# Pipeline Fix + Backfill + Dashboard Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two bugs that silently stall the pipeline since June 1, backfill 13 days of already-committed June PDFs, and add a data-coverage strip to the dashboard.

**Architecture:** Three targeted changes — (1) a 2-line workflow PIPESTATUS fix so failures surface correctly, (2) a parser regex update to handle IMD's new monsoon PDF header format (hyphen-separated dates, no `%` on departure values), and (3) a dashboard JavaScript addition that reads the existing `index.json` to render a 30-day coverage strip. After the code fixes, a one-shot backfill CLI command processes the already-committed June PDFs and rebuilds the full API.

**Tech Stack:** Python 3.12, pdfplumber, pandas, pandera, pytest — all already installed. Dashboard is vanilla JS in a single `docs/index.html`.

---

## Files

| File | Action | Responsibility |
|------|--------|----------------|
| `src/rainfall/parser.py` | Modify | Fix date-header regex + make `%` optional in departure values |
| `tests/test_parser.py` | Modify | Add tests for new header format and no-`%` departures |
| `.github/workflows/daily-scrape.yml` | Modify | Fix PIPESTATUS overwrite so failures propagate correctly |
| `docs/index.html` | Modify | Add 30-day data-coverage strip |
| `src/rainfall/api_builder.py` | Modify | Add `dates` array to `index.json` (needed by coverage strip) |
| `tests/test_api_builder.py` | Inspect | Verify existing tests still pass after api_builder change |

---

## Task 1: Fix `parser.py` — date-header regex and `%`-less departures

**Files:**
- Modify: `src/rainfall/parser.py`
- Modify: `tests/test_parser.py`

**Background on the bug:**

IMD changed their PDF header at monsoon onset (June 2, 2026):

| Field | Old format (pre-June) | New format (June+) |
|-------|----------------------|--------------------|
| Dates | Two separate lines: `DAY: 31.05.2026 TO 31.05.2026` and `PERIOD: 01.03.2026 TO 31.05.2026` | One combined line: `DAY: 01-06-2026 PERIOD: 01-06-2026 to 2026-09-30` |
| Date separator | `.` (dot) | `-` (hyphen) |
| Period-end format | `DD.MM.YYYY` | `YYYY-MM-DD` |
| Departure % | `63%`, `-69%` | `63`, `-69` (no `%` sign) |

Both formats must be supported going forward since pre-monsoon PDFs in the archive use the old format.

- [ ] **Step 1.1: Write failing tests for the new date header format**

Add to the bottom of `tests/test_parser.py`:

```python
from rainfall.parser import _find_dates, _TRAILING_RE


# ---- New monsoon header format (June+) ----

def test_find_dates_new_monsoon_format():
    """New format: combined DAY/PERIOD on one line, hyphen-separated, mixed date orders."""
    text = "DAY: 01-06-2026 PERIOD: 01-06-2026 to 2026-09-30"
    day_start, day_end, period_start, period_end = _find_dates(text)
    from datetime import date
    assert day_start == date(2026, 6, 1)
    assert day_end == date(2026, 6, 1)
    assert period_start == date(2026, 6, 1)
    assert period_end == date(2026, 9, 30)


def test_find_dates_old_format_still_works():
    """Old dot-separated format must continue to parse correctly."""
    text = "DAY : 31.05.2026 TO 31.05.2026\nPERIOD : 01.03.2026 TO 31.05.2026"
    day_start, day_end, period_start, period_end = _find_dates(text)
    from datetime import date
    assert day_end == date(2026, 5, 31)
    assert period_start == date(2026, 3, 1)
    assert period_end == date(2026, 5, 31)


def test_find_dates_raises_on_unknown_format():
    with pytest.raises(ValueError, match="Could not locate DAY/PERIOD"):
        _find_dates("No date info here")


def test_trailing_re_matches_without_percent():
    """New format: departure values are plain integers, no % sign."""
    line = "ANDAMAN & NICOBAR ISLANDS 3.9 12.4 -69 LD 3.9 1631.7 -99 LD"
    m = _TRAILING_RE.search(line)
    assert m is not None, "Regex must match departure values without %"
    assert m.group(3) == "-69"
    assert m.group(7) == "-99"


def test_trailing_re_matches_with_percent():
    """Old format: departure values have % sign — must still match."""
    line = "1 NICOBAR 11.4 7.0 63% LE 11.4 1136.0 -99% LD"
    m = _TRAILING_RE.search(line)
    assert m is not None, "Regex must match departure values with %"
    assert m.group(3) == "63%"
```

- [ ] **Step 1.2: Run the new tests to confirm they fail**

```bash
.venv\Scripts\python.exe -m pytest tests/test_parser.py::test_find_dates_new_monsoon_format tests/test_parser.py::test_trailing_re_matches_without_percent -v
```

Expected: FAIL — `ImportError` on `_find_dates` (not exported) or assertion failures.

- [ ] **Step 1.3: Update `src/rainfall/parser.py`**

Replace the existing date-regex block and `_parse_dot_date` / `_find_dates` functions with the following. Everything else in the file stays unchanged.

**Replace lines 92–108** (the `_DAY_RE`, `_PERIOD_RE`, `_NUM`, `_PCT`, `_CAT`, `_TRAILING_RE`, `_LEAD_NUM_RE` block):

```python
_DAY_RE = re.compile(
    r"DAY\s*:\s*(\d{2}\.\d{2}\.\d{4})\s*TO\s*(\d{2}\.\d{2}\.\d{4})", re.IGNORECASE
)
_PERIOD_RE = re.compile(
    r"PERIOD\s*:\s*(\d{2}\.\d{2}\.\d{4})\s*TO\s*(\d{2}\.\d{2}\.\d{4})", re.IGNORECASE
)
# New monsoon format: "DAY: 01-06-2026 PERIOD: 01-06-2026 to 2026-09-30"
_DAY_MONSOON_RE = re.compile(
    r"DAY\s*:\s*(\d{2}-\d{2}-\d{4})", re.IGNORECASE
)
_PERIOD_MONSOON_RE = re.compile(
    r"PERIOD\s*:\s*(\d{2}-\d{2}-\d{4})\s+to\s+(\d{4}-\d{2}-\d{2})", re.IGNORECASE
)

_VALID_CATS = {"LE", "E", "N", "D", "LD", "NR"}

_NUM = r"(?:-?\d+\.?\d*|\*)"
_PCT = r"(?:-?\d+\.?\d*%?|\*)"   # % is optional — new monsoon format drops it
_CAT = r"(?:LE|E|N|D|LD|NR|\*)"

_TRAILING_RE = re.compile(
    rf"\s+({_NUM})\s+({_NUM})\s+({_PCT})\s+({_CAT})"
    rf"\s+({_NUM})\s+({_NUM})\s+({_PCT})\s+({_CAT})\s*$"
)

_LEAD_NUM_RE = re.compile(r"^\s*(\d+)\s+(.*)$")
```

**Replace `_parse_dot_date` (line 128–129) with a multi-format helper:**

```python
def _parse_dot_date(s: str) -> date:
    return datetime.strptime(s, "%d.%m.%Y").date()


def _parse_date_flexible(s: str) -> date:
    """Parse DD-MM-YYYY or YYYY-MM-DD (new monsoon format)."""
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")
```

**Replace `_find_dates` (lines 201–213) with:**

```python
def _find_dates(text: str) -> tuple[date, date, date, date]:
    # Old format: separate DAY and PERIOD lines with dot-separated dates
    day_match = _DAY_RE.search(text)
    period_match = _PERIOD_RE.search(text)
    if day_match and period_match:
        return (
            _parse_dot_date(day_match.group(1)),
            _parse_dot_date(day_match.group(2)),
            _parse_dot_date(period_match.group(1)),
            _parse_dot_date(period_match.group(2)),
        )
    # New monsoon format: "DAY: DD-MM-YYYY PERIOD: DD-MM-YYYY to YYYY-MM-DD"
    day_m = _DAY_MONSOON_RE.search(text)
    period_m = _PERIOD_MONSOON_RE.search(text)
    if day_m and period_m:
        day_date = _parse_date_flexible(day_m.group(1))
        return (
            day_date,
            day_date,
            _parse_date_flexible(period_m.group(1)),
            _parse_date_flexible(period_m.group(2)),
        )
    raise ValueError(
        "Could not locate DAY/PERIOD headers in PDF — IMD format may have changed"
    )
```

- [ ] **Step 1.4: Run all failing tests — they should now pass**

```bash
.venv\Scripts\python.exe -m pytest tests/test_parser.py -v
```

Expected output (all green):
```
tests/test_parser.py::test_parses_dates PASSED
tests/test_parser.py::test_minimum_districts PASSED
...
tests/test_parser.py::test_find_dates_new_monsoon_format PASSED
tests/test_parser.py::test_find_dates_old_format_still_works PASSED
tests/test_parser.py::test_find_dates_raises_on_unknown_format PASSED
tests/test_parser.py::test_trailing_re_matches_without_percent PASSED
tests/test_parser.py::test_trailing_re_matches_with_percent PASSED
```

- [ ] **Step 1.5: Quick smoke test on a real June PDF**

```bash
.venv\Scripts\python.exe -c "
from src.rainfall.parser import parse_pdf, to_dataframe
from src.rainfall.validator import validate
data = open('data/raw_pdf/imd_2026-06-02_aa595a62.pdf', 'rb').read()
parsed = parse_pdf(data)
df = validate(to_dataframe(parsed))
print('day_end:', parsed.day_end, '  rows:', len(df))
"
```

Expected: `day_end: 2026-06-01  rows: ~775`

- [ ] **Step 1.6: Commit**

```bash
git add src/rainfall/parser.py tests/test_parser.py
git commit -m "fix(parser): handle IMD monsoon PDF format — hyphen dates, no-% departures"
```

---

## Task 2: Fix workflow — PIPESTATUS overwrite bug

**Files:**
- Modify: `.github/workflows/daily-scrape.yml`

**Background on the bug:**

```bash
# Current (broken): the echo command resets PIPESTATUS, so exit always gets 0
python -m rainfall.cli scrape 2>&1 | tee scrape.log
echo "exit_code=${PIPESTATUS[0]}" >> "$GITHUB_OUTPUT"
exit ${PIPESTATUS[0]}    # ← PIPESTATUS[0] here is echo's exit code, always 0

# Fix: capture PIPESTATUS immediately before anything resets it
python -m rainfall.cli scrape 2>&1 | tee scrape.log
SCRAPE_EXIT=${PIPESTATUS[0]}
echo "exit_code=${SCRAPE_EXIT}" >> "$GITHUB_OUTPUT"
exit ${SCRAPE_EXIT}
```

- [ ] **Step 2.1: Apply the fix to the workflow file**

In `.github/workflows/daily-scrape.yml`, find the "Run scrape" step (around line 44–49) and replace the `run:` block:

```yaml
      - name: Run scrape
        id: scrape
        run: |
          python -m rainfall.cli scrape 2>&1 | tee scrape.log
          SCRAPE_EXIT=${PIPESTATUS[0]}
          echo "exit_code=${SCRAPE_EXIT}" >> "$GITHUB_OUTPUT"
          exit ${SCRAPE_EXIT}
```

- [ ] **Step 2.2: Verify the file looks correct**

```bash
grep -A 6 "name: Run scrape" .github/workflows/daily-scrape.yml
```

Expected:
```yaml
      - name: Run scrape
        id: scrape
        run: |
          python -m rainfall.cli scrape 2>&1 | tee scrape.log
          SCRAPE_EXIT=${PIPESTATUS[0]}
          echo "exit_code=${SCRAPE_EXIT}" >> "$GITHUB_OUTPUT"
          exit ${SCRAPE_EXIT}
```

- [ ] **Step 2.3: Commit**

```bash
git add .github/workflows/daily-scrape.yml
git commit -m "fix(ci): capture PIPESTATUS before echo resets it so scrape failures propagate"
```

---

## Task 3: Add `dates` array to `api/index.json`

**Files:**
- Modify: `src/rainfall/api_builder.py`
- Inspect: `tests/test_api_builder.py` (no change expected)

The dashboard coverage strip needs the list of all available dates. The index.json is already fetched at startup, so adding `dates` there costs zero extra requests.

- [ ] **Step 3.1: Read the current test file to understand what's expected**

```bash
.venv\Scripts\python.exe -m pytest tests/test_api_builder.py -v
```

Note which assertions exist about `index.json` structure.

- [ ] **Step 3.2: Update `src/rainfall/api_builder.py` — add `dates` to `index.json`**

In `build_all()`, find the `_write_json(api_dir / "index.json", {...})` call (around line 124) and add `"dates": dates` to the dict:

```python
    _write_json(api_dir / "index.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "first_date": dates[0],
        "last_date": dates[-1],
        "n_dates": len(dates),
        "n_states": len(state_index),
        "n_districts": districts_df["district"].nunique(),
        "row_count": len(df),
        "dates": dates,                       # ← add this line
        "endpoints": {
            "latest":      "/api/latest.json",
            "by_date":     "/api/by-date/{YYYY-MM-DD}.json",
            "by_state":    "/api/by-state/{slug}.json",
            "by_district": "/api/by-district/{slug}.json",
            "states_list": "/api/states.json",
        },
    })
```

- [ ] **Step 3.3: Run all tests to confirm nothing breaks**

```bash
.venv\Scripts\python.exe -m pytest -v
```

Expected: all green.

- [ ] **Step 3.4: Commit**

```bash
git add src/rainfall/api_builder.py
git commit -m "feat(api): add dates array to index.json for dashboard coverage strip"
```

---

## Task 4: Add 30-day coverage strip to the dashboard

**Files:**
- Modify: `docs/index.html`

The coverage strip is a row of 30 colored dots (last 30 calendar days). Green = data present, grey = gap. A staleness label appears in red if the most recent data is more than 2 days old. All data comes from `idx.dates` which is already fetched by `init()`.

- [ ] **Step 4.1: Add CSS for the coverage strip**

In `docs/index.html`, find the `</style>` closing tag and insert before it:

```css
  .coverage-bar {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.85rem 1.25rem;
    margin-bottom: 1.5rem;
    display: flex;
    align-items: center;
    gap: 1rem;
    flex-wrap: wrap;
  }

  .coverage-label {
    font-size: 0.85rem;
    font-weight: 600;
    min-width: 160px;
  }

  .coverage-label.stale { color: var(--deficit); }
  .coverage-label.fresh { color: var(--excess); }

  .day-strip {
    display: flex;
    gap: 3px;
    align-items: center;
  }

  .day-dot {
    width: 10px;
    height: 10px;
    border-radius: 2px;
    cursor: default;
  }

  .day-dot.has-data { background: var(--excess); }
  .day-dot.no-data  { background: var(--border); }

  .coverage-legend {
    font-size: 0.78rem;
    color: var(--muted);
  }
```

- [ ] **Step 4.2: Add the HTML element for the coverage strip**

In `docs/index.html`, find the `<div class="row">` that contains the four stat cards (around line 275) and insert the coverage bar HTML **after** the closing `</div>` of that row and **before** the `<div class="controls">`:

```html
  <div class="coverage-bar">
    <div class="coverage-label" id="coverage-label">Loading…</div>
    <div class="day-strip" id="day-strip"></div>
    <div class="coverage-legend">← 30 days · each dot = 1 day</div>
  </div>
```

- [ ] **Step 4.3: Add the `renderCoverageStrip` function**

In `docs/index.html`, find the `function renderStats(idx)` function and add this new function immediately after it:

```javascript
function renderCoverageStrip(idx) {
  const available = new Set(idx.dates || []);
  const today = new Date();
  const strip = document.getElementById("day-strip");
  const label = document.getElementById("coverage-label");

  // Build last-30-days date strings (oldest first)
  const dots = [];
  for (let i = 29; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const ds = d.toISOString().split("T")[0];
    const hasData = available.has(ds);
    dots.push(`<span class="day-dot ${hasData ? "has-data" : "no-data"}" title="${ds}"></span>`);
  }
  strip.innerHTML = dots.join("");

  // Staleness label
  const lastDate = idx.last_date;
  const msPerDay = 86400000;
  const ageDays = Math.round((today - new Date(lastDate)) / msPerDay);
  if (ageDays <= 1) {
    label.textContent = "Data current";
    label.className = "coverage-label fresh";
  } else {
    label.textContent = `Last update: ${lastDate} (${ageDays}d ago)`;
    label.className = "coverage-label stale";
  }
}
```

- [ ] **Step 4.4: Call `renderCoverageStrip` from `init()`**

In the `init()` function, find where `renderStats(index)` is called and add the coverage strip call directly after it:

```javascript
    renderStats(index);
    renderCoverageStrip(index);   // ← add this line
    renderTable(latest.rows);
```

- [ ] **Step 4.5: Visual check — open the dashboard locally**

```bash
# On Windows: open in default browser
start docs\index.html
```

Verify:
- The coverage strip appears below the stat cards
- Dots are present (grey for past dates without data, green for dates that have data in the current API)
- Staleness label shows correctly (should be "stale" since local API is still May 31 at this point)

- [ ] **Step 4.6: Commit**

```bash
git add docs/index.html
git commit -m "feat(dashboard): add 30-day data coverage strip to surfacing data gaps"
```

---

## Task 5: Backfill June 1–13 and rebuild full API

**Background:** All 13 June PDFs are already in `data/raw_pdf/`. The `backfill` CLI command re-processes each PDF through the (now fixed) parser, writes CSVs, upserts to SQLite, and rebuilds the API.

- [ ] **Step 5.1: Identify all June PDFs to backfill**

```bash
.venv\Scripts\python.exe -c "
import os
pdfs = sorted(f for f in os.listdir('data/raw_pdf') if 'imd_2026-06' in f)
for p in pdfs:
    print(p)
print(f'Total: {len(pdfs)} PDFs')
"
```

Expected: 13 files from `imd_2026-06-01_*.pdf` to `imd_2026-06-13_*.pdf`.

- [ ] **Step 5.2: Run the backfill (processes all June PDFs in one command)**

```bash
.venv\Scripts\python.exe -m rainfall.cli backfill --pdf data/raw_pdf/imd_2026-06-02_aa595a62.pdf data/raw_pdf/imd_2026-06-03_056ac44a.pdf data/raw_pdf/imd_2026-06-04_056ac44a.pdf data/raw_pdf/imd_2026-06-05_da0ac6bc.pdf data/raw_pdf/imd_2026-06-06_b095921c.pdf data/raw_pdf/imd_2026-06-07_849f0628.pdf data/raw_pdf/imd_2026-06-08_1174b4fc.pdf data/raw_pdf/imd_2026-06-09_1174b4fc.pdf data/raw_pdf/imd_2026-06-10_b04debf6.pdf data/raw_pdf/imd_2026-06-11_d6e6e13f.pdf data/raw_pdf/imd_2026-06-12_2403b613.pdf data/raw_pdf/imd_2026-06-13_c1863d45.pdf
```

Note: `imd_2026-06-01_bffec3ae.pdf` is intentionally excluded — it contains May 31 data (already in DB).
Note: Duplicate PDFs (June 04 == June 03, June 09 == June 08) will upsert the same data — idempotent, no harm done.

Expected output per PDF:
```
Processing data/raw_pdf/imd_2026-06-02_aa595a62.pdf...
  date=2026-06-01  rows=775  {'inserted': 775, 'updated': 0, 'unchanged': 0}
...
```

- [ ] **Step 5.3: Verify the DB now has June data**

```bash
.venv\Scripts\python.exe -m rainfall.cli info
```

Expected:
```
first_date  last_date  n_dates  n_rows  n_states  n_districts
2026-04-13 2026-06-13      ~50  ~43000        ~24          719
```

The `last_date` must be `2026-06-13` (or the highest date in the backfilled PDFs). `n_dates` should increase from 40 to ~50.

- [ ] **Step 5.4: Rebuild the API explicitly to get the full `dates` array**

The `backfill` command rebuilds the API at the end, but run explicitly to confirm:

```bash
.venv\Scripts\python.exe -m rainfall.cli rebuild-api
```

Expected: `API rebuilt: {'dates': ~50, 'states': ..., 'districts': 719}`

- [ ] **Step 5.5: Spot-check the rebuilt API**

```bash
.venv\Scripts\python.exe -c "
import json
idx = json.load(open('docs/api/index.json'))
print('first_date:', idx['first_date'])
print('last_date:', idx['last_date'])
print('n_dates:', idx['n_dates'])
print('dates[-5:]:', idx.get('dates', [])[-5:])
print('by-date files:', len(list(__import__('pathlib').Path('docs/api/by-date').glob('*.json'))))
"
```

Expected:
```
first_date: 2026-04-13
last_date: 2026-06-13
n_dates: ~50
dates[-5:]: ['2026-06-09', '2026-06-10', '2026-06-11', '2026-06-12', '2026-06-13']
by-date files: ~50
```

- [ ] **Step 5.6: Verify the dashboard coverage strip locally**

Open `docs/index.html` in a browser and confirm:
- The coverage strip now shows green dots for June dates
- Stats card shows `last_date: 2026-06-13`
- Staleness label shows 1 or 2 days (since today is June 14)

- [ ] **Step 5.7: Commit the backfilled data**

```bash
git add data/ docs/api/ docs/.nojekyll
git commit -m "data: backfill June 2026 monsoon data (June 1-13) after parser fix"
```

---

## Task 6: Push and verify end-to-end

- [ ] **Step 6.1: Run the full test suite one last time**

```bash
.venv\Scripts\python.exe -m pytest -v
```

Expected: all tests pass.

- [ ] **Step 6.2: Push to GitHub**

```bash
git push origin main
```

- [ ] **Step 6.3: Monitor the next scheduled GitHub Actions run**

The next run triggers at 06:00 UTC on June 15. To verify the fix worked:
1. Go to `https://github.com/sayantan-aquacarta/rainfall-pipeline/actions`
2. Watch the "Run scrape" step — it must exit with the correct code now
3. A new commit "data: rainfall snapshot 2026-06-14 [skip ci]" should appear

- [ ] **Step 6.4: Verify the live dashboard**

After GitHub Pages redeploys (5–10 min after push), open `https://sayantan-aquacarta.github.io/rainfall-pipeline/` and confirm:
- `last_date` stat shows `2026-06-13`
- Coverage strip has green dots through June 13
- District time series charts show April–June data (not just April–May)

---

## Self-Review Checklist

**Spec coverage:**
- [x] Bug 1 (PIPESTATUS) → Task 2
- [x] Bug 2 (parser date regex) → Task 1
- [x] Bug 2 (parser `%` requirement) → Task 1
- [x] Backfill June 1–13 → Task 5
- [x] Dashboard gap indicator → Task 4
- [x] `dates` array in index.json → Task 3
- [x] Full API rebuild → Task 5
- [x] Tests for new format → Task 1
- [x] Push + verify → Task 6

**Placeholder scan:** No TBDs or incomplete code blocks present.

**Type consistency:**
- `renderCoverageStrip(idx)` called from `init()` where `idx` is the response from `getJSON("index.json")` — matches.
- `_find_dates(text)` now exported and tested directly — matches test imports.
- `_TRAILING_RE` tested directly — matches parser module import.
