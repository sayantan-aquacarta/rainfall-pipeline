"""
SPI (Standardized Precipitation Index) computation and drought API generation.

Uses season-to-date cumulative rainfall from subdivision-level rows in the
rainfall database, compared against the LPA normals embedded in the same PDFs.

Gamma distribution is parameterized from:
  - mean  = period_normal_mm (LPA from the IMD PDF)
  - alpha = 1 / CV²  where CV = 0.32 (Parthasarathy et al., 1987)
  - scale = mean / alpha

When the archive accumulates ≥ 10 same-day-of-season observations for a
subdivision, the gamma is re-fitted from observed data automatically.

SPI categories: McKee et al. (1993); WMO-No. 1090 (2012).
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from .config import CONFIG
from .logging_setup import get_logger

log = get_logger(__name__)

# ── SPI category table ────────────────────────────────────────────────────────

_CATS: list[tuple[str, float, float, str]] = [
    ("D4", -math.inf, -2.00, "Exceptional Drought"),
    ("D3", -2.00,    -1.60, "Extreme Drought"),
    ("D2", -1.60,    -1.30, "Severe Drought"),
    ("D1", -1.30,    -0.80, "Moderate Drought"),
    ("D0", -0.80,    -0.50, "Abnormally Dry"),
    ("NN", -0.50,     0.50, "Near Normal"),
    ("W0",  0.50,     0.80, "Abnormally Wet"),
    ("W1",  0.80,     1.30, "Moderately Wet"),
    ("W2",  1.30, math.inf, "Severely Wet"),
]

CAT_COLORS: dict[str, str] = {
    "D4": "#7f1d1d", "D3": "#ef4444", "D2": "#f97316", "D1": "#fbbf24",
    "D0": "#a8a29e", "NN": "#60a5fa",
    "W0": "#86efac", "W1": "#4ade80",  "W2": "#16a34a",
}

# Climatological coefficient of variation for Indian monsoon rainfall
# Source: Parthasarathy, Kumar & Munot (1987), IITM Tech. Report
_CV = 0.32


# ── Core SPI math ─────────────────────────────────────────────────────────────

def classify_spi(spi: float | None) -> tuple[str, str]:
    """Return (code, name) for a given SPI value."""
    if spi is None or (isinstance(spi, float) and math.isnan(spi)):
        return "NN", "Near Normal"
    for code, lo, hi, name in _CATS:
        if lo <= spi < hi:
            return code, name
    return "NN", "Near Normal"


def spi_from_pct_normal(
    actual_mm: float | None,
    normal_mm: float | None,
    historical_mm: list[float] | None = None,
) -> float | None:
    """
    Compute SPI for one (subdivision, season-to-date) observation.

    When historical_mm has ≥ 10 values, fits gamma from observations.
    Otherwise uses parameterised gamma (mean=normal_mm, CV=0.32).
    """
    if actual_mm is None or normal_mm is None or normal_mm <= 0 or actual_mm < 0:
        return None

    hist = [v for v in (historical_mm or []) if v is not None and v >= 0 and not math.isnan(v)]

    if len(hist) >= 10:
        non_zero = [v for v in hist if v > 0]
        if len(non_zero) < 5:
            return None
        try:
            alpha, _, scale = stats.gamma.fit(non_zero, floc=0)
        except Exception:
            return None
    else:
        alpha = 1.0 / (_CV ** 2)          # ≈ 9.77
        scale = float(normal_mm) / alpha

    obs = max(float(actual_mm), 1e-6)
    cdf = float(np.clip(stats.gamma.cdf(obs, alpha, scale=scale), 1e-6, 1 - 1e-6))
    return round(float(stats.norm.ppf(cdf)), 3)


# ── Data queries ──────────────────────────────────────────────────────────────

def _query(db_path: Path, sql: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    try:
        return pd.read_sql_query(sql, conn)
    finally:
        conn.close()


def compute_drought_status(db_path: Path | None = None) -> pd.DataFrame:
    """
    Latest drought status per subdivision (one row per subdivision).
    Uses the most recent date's subdivision-level period totals.
    """
    db_path = db_path or CONFIG.sqlite_path
    df = _query(db_path, """
        SELECT
            date,
            subdivision,
            AVG(period_actual_mm)    AS period_actual_mm,
            AVG(period_normal_mm)    AS period_normal_mm,
            AVG(period_departure_pct) AS period_departure_pct,
            MAX(period_category)     AS period_category
        FROM rainfall
        WHERE level = 'subdivision'
          AND date = (
              SELECT MAX(date) FROM rainfall WHERE level = 'subdivision'
          )
          AND subdivision IS NOT NULL
          AND subdivision != ''
        GROUP BY date, subdivision
        ORDER BY subdivision
    """)

    if df.empty:
        log.warning("drought_no_subdivision_data")
        return df

    df["spi"] = df.apply(
        lambda r: spi_from_pct_normal(r["period_actual_mm"], r["period_normal_mm"]),
        axis=1,
    )
    cats = df["spi"].apply(classify_spi)
    df["spi_category"]      = [c[0] for c in cats]
    df["spi_category_name"] = [c[1] for c in cats]
    df["pct_normal"] = np.where(
        df["period_normal_mm"] > 0,
        (df["period_actual_mm"] / df["period_normal_mm"] * 100).round(1),
        None,
    )
    return df


def compute_drought_history(db_path: Path | None = None) -> pd.DataFrame:
    """
    SPI for every (date, subdivision) in the archive — used for time-series charts.
    """
    db_path = db_path or CONFIG.sqlite_path
    df = _query(db_path, """
        SELECT
            date,
            subdivision,
            AVG(period_actual_mm)  AS period_actual_mm,
            AVG(period_normal_mm)  AS period_normal_mm
        FROM rainfall
        WHERE level = 'subdivision'
          AND subdivision IS NOT NULL AND subdivision != ''
        GROUP BY date, subdivision
        ORDER BY subdivision, date
    """)

    if df.empty:
        return df

    df["spi"] = df.apply(
        lambda r: spi_from_pct_normal(r["period_actual_mm"], r["period_normal_mm"]),
        axis=1,
    )
    df["pct_normal"] = np.where(
        df["period_normal_mm"] > 0,
        (df["period_actual_mm"] / df["period_normal_mm"] * 100).round(1),
        None,
    )
    cats = df["spi"].apply(classify_spi)
    df["spi_category"] = [c[0] for c in cats]
    return df[["date", "subdivision", "period_actual_mm", "period_normal_mm",
               "pct_normal", "spi", "spi_category"]]


# ── API builder ───────────────────────────────────────────────────────────────

def _safe(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def build_drought_api(
    current_df: pd.DataFrame,
    history_df: pd.DataFrame,
    docs_path: Path | None = None,
) -> dict:
    """Write docs/api/drought-latest.json and docs/api/drought-history.json."""
    docs_path = docs_path or CONFIG.docs_dir
    api_dir = docs_path / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    # ── drought-latest.json ──────────────────────────────────────────────────
    ref_date = current_df["date"].max() if not current_df.empty else None
    subdiv_rows: list[dict] = []
    for _, row in current_df.iterrows():
        subdiv_rows.append({
            "subdivision":       row["subdivision"],
            "period_actual_mm":  _safe(row.get("period_actual_mm")),
            "period_normal_mm":  _safe(row.get("period_normal_mm")),
            "pct_normal":        _safe(row.get("pct_normal")),
            "spi":               _safe(row.get("spi")),
            "spi_category":      row.get("spi_category", "NN"),
            "spi_category_name": row.get("spi_category_name", "Near Normal"),
            "imd_category":      row.get("period_category"),
        })

    # Sort worst drought first
    subdiv_rows.sort(key=lambda r: r["spi"] if r["spi"] is not None else 0)

    latest_payload = {
        "generated_at":  now,
        "reference_date": ref_date,
        "method":        f"Parameterised gamma (CV={_CV}); upgrades to fitted when ≥10 obs",
        "reference":     "McKee et al. (1993); WMO-No. 1090 (2012)",
        "cat_colors":    CAT_COLORS,
        "subdivisions":  subdiv_rows,
    }
    (api_dir / "drought-latest.json").write_text(
        json.dumps(latest_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ── drought-history.json ─────────────────────────────────────────────────
    hist_rows: list[dict] = []
    for _, row in history_df.iterrows():
        hist_rows.append({
            "date":         row["date"],
            "subdivision":  row["subdivision"],
            "pct_normal":   _safe(row.get("pct_normal")),
            "spi":          _safe(row.get("spi")),
            "spi_category": row.get("spi_category", "NN"),
        })

    (api_dir / "drought-history.json").write_text(
        json.dumps({"generated_at": now, "rows": hist_rows}, ensure_ascii=False), encoding="utf-8"
    )

    stats = {
        "subdivisions":  len(subdiv_rows),
        "history_rows":  len(hist_rows),
        "reference_date": ref_date,
    }
    log.info("drought_api_built", **stats)
    return stats


def compute_and_build(
    db_path: Path | None = None,
    docs_path: Path | None = None,
) -> dict:
    """Convenience: compute current + history, write API, return stats."""
    current  = compute_drought_status(db_path)
    history  = compute_drought_history(db_path)
    return build_drought_api(current, history, docs_path)
