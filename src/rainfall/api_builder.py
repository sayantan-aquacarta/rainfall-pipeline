"""
Static API generator: writes JSON files under docs/api/ that GitHub Pages serves.

Endpoints (all GET, all static):
  /api/index.json                       — metadata: dates available, last_updated, counts
  /api/latest.json                      — most recent day's full data
  /api/by-date/YYYY-MM-DD.json          — one file per data date
  /api/by-state/<slug>.json             — full time series for one state's districts
  /api/by-district/<slug>.json          — full time series for one district
  /api/states.json                      — list of all states with their districts
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import CONFIG
from .logging_setup import get_logger
from .storage import query_db

log = get_logger(__name__)


def _slug(s: str) -> str:
    """URL-safe slug. Lowercase, alphanumerics + hyphens only."""
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "unknown"


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """Convert DataFrame to JSON-serializable records (dates as strings, NaN as null)."""
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d")
    out = out.astype(object).where(pd.notna(out), None)
    return out.to_dict(orient="records")


def _write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def build_all(api_dir: Path | None = None) -> dict:
    """Regenerate the entire static API from SQLite. Idempotent."""
    api_dir = api_dir or CONFIG.api_dir
    # Wipe per-entity dirs so deleted entities (rare but possible) disappear
    for sub in ("by-date", "by-state", "by-district"):
        d = api_dir / sub
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    api_dir.mkdir(parents=True, exist_ok=True)

    df = query_db("SELECT * FROM rainfall ORDER BY date, level, subdivision, state, district")
    if df.empty:
        log.warning("api_build_no_data")
        _write_json(api_dir / "index.json", {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dates": [], "states": [], "districts": [], "row_count": 0,
        })
        return {"dates": 0, "states": 0, "districts": 0}

    df["date"] = pd.to_datetime(df["date"]).dt.date.astype(str)

    # 1. Per-date files
    dates = sorted(df["date"].unique())
    for d in dates:
        sub = df[df["date"] == d]
        _write_json(api_dir / "by-date" / f"{d}.json", {
            "date": d,
            "row_count": len(sub),
            "rows": _df_to_records(sub),
        })

    # 2. Latest
    latest_date = dates[-1]
    latest = df[df["date"] == latest_date]
    _write_json(api_dir / "latest.json", {
        "date": latest_date,
        "row_count": len(latest),
        "rows": _df_to_records(latest),
    })

    # 3. Per-state time series (districts only)
    districts_df = df[df["level"] == "district"].copy()
    state_index: dict[str, dict] = {}
    for state, group in districts_df.groupby("state"):
        slug = _slug(state)
        state_index[state] = {"slug": slug, "n_districts": group["district"].nunique()}
        _write_json(api_dir / "by-state" / f"{slug}.json", {
            "state": state,
            "row_count": len(group),
            "districts": sorted(group["district"].dropna().unique().tolist()),
            "rows": _df_to_records(group),
        })

    # 4. Per-district time series
    district_index: dict[str, dict] = {}
    for (state, district), group in districts_df.groupby(["state", "district"]):
        slug = _slug(f"{state}-{district}")
        district_index[f"{state}/{district}"] = {"slug": slug, "n_observations": len(group)}
        _write_json(api_dir / "by-district" / f"{slug}.json", {
            "state": state,
            "district": district,
            "row_count": len(group),
            "rows": _df_to_records(group),
        })

    # 5. Master index
    _write_json(api_dir / "states.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "states": [
            {"name": k, **v} for k, v in sorted(state_index.items())
        ],
    })
    _write_json(api_dir / "index.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "first_date": dates[0],
        "last_date": dates[-1],
        "n_dates": len(dates),
        "n_states": len(state_index),
        "n_districts": districts_df["district"].nunique(),
        "row_count": len(df),
        "endpoints": {
            "latest":      "/api/latest.json",
            "by_date":     "/api/by-date/{YYYY-MM-DD}.json",
            "by_state":    "/api/by-state/{slug}.json",
            "by_district": "/api/by-district/{slug}.json",
            "states_list": "/api/states.json",
        },
    })

    log.info(
        "api_built",
        n_dates=len(dates), n_states=len(state_index),
        n_districts=districts_df["district"].nunique(),
    )
    return {
        "dates": len(dates),
        "states": len(state_index),
        "districts": districts_df["district"].nunique(),
    }
