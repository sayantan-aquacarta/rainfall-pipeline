"""Tests for the static API builder."""
import json
from pathlib import Path

import pandas as pd
import pytest

from rainfall.api_builder import build_all, _slug
from rainfall.storage import upsert_rainfall


def test_slug():
    assert _slug("ANDHRA PRADESH") == "andhra-pradesh"
    assert _slug("Jammu & Kashmir (UT)") == "jammu-kashmir-ut"
    assert _slug("  ") == "unknown"


@pytest.fixture
def populated_env(tmp_path, monkeypatch):
    """Set up a temp data dir with one day of data."""
    db = tmp_path / "test.db"
    api_dir = tmp_path / "api"
    df = pd.DataFrame([
        {"date": "2026-04-01", "period_start": "2026-03-01", "period_end": "2026-04-01",
         "level": "district", "subdivision": "PUNJAB", "state": "PUNJAB", "district": "AMRITSAR",
         "day_actual_mm": 5.0, "day_normal_mm": 3.0, "day_departure_pct": 67.0, "day_category": "E",
         "period_actual_mm": 25.0, "period_normal_mm": 30.0, "period_departure_pct": -17.0, "period_category": "N",
         "scraped_at": "2026-04-01T06:30:00"},
        {"date": "2026-04-01", "period_start": "2026-03-01", "period_end": "2026-04-01",
         "level": "district", "subdivision": "PUNJAB", "state": "PUNJAB", "district": "LUDHIANA",
         "day_actual_mm": 0.0, "day_normal_mm": 2.0, "day_departure_pct": -100.0, "day_category": "NR",
         "period_actual_mm": 10.0, "period_normal_mm": 25.0, "period_departure_pct": -60.0, "period_category": "LD",
         "scraped_at": "2026-04-01T06:30:00"},
    ])
    upsert_rainfall(df, path=db)
    monkeypatch.setattr("rainfall.api_builder.query_db",
                        lambda sql, params=(), path=None: pd.read_sql_query(sql, __import__("sqlite3").connect(str(db))))
    return api_dir


def test_build_all_creates_files(populated_env):
    api_dir = populated_env
    stats = build_all(api_dir=api_dir)
    assert stats == {"dates": 1, "states": 1, "districts": 2}
    # Check files
    assert (api_dir / "index.json").exists()
    assert (api_dir / "latest.json").exists()
    assert (api_dir / "states.json").exists()
    assert (api_dir / "by-date" / "2026-04-01.json").exists()
    assert (api_dir / "by-state" / "punjab.json").exists()
    assert (api_dir / "by-district" / "punjab-amritsar.json").exists()


def test_index_contents(populated_env):
    api_dir = populated_env
    build_all(api_dir=api_dir)
    idx = json.loads((api_dir / "index.json").read_text())
    assert idx["n_dates"] == 1
    assert idx["n_districts"] == 2
    assert idx["first_date"] == "2026-04-01"


def test_by_district_contents(populated_env):
    api_dir = populated_env
    build_all(api_dir=api_dir)
    payload = json.loads((api_dir / "by-district" / "punjab-amritsar.json").read_text())
    assert payload["state"] == "PUNJAB"
    assert payload["district"] == "AMRITSAR"
    assert payload["row_count"] == 1
    assert payload["rows"][0]["day_actual_mm"] == 5.0
