"""Tests for SQLite storage: idempotency and revision tracking."""
from pathlib import Path

import pandas as pd
import pytest

from rainfall.storage import init_db, query_db, upsert_rainfall, write_csv


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def _sample_df(actual=12.5):
    return pd.DataFrame([{
        "date": "2026-04-01", "period_start": "2026-03-01", "period_end": "2026-04-01",
        "level": "district", "subdivision": "PUNJAB", "state": "PUNJAB", "district": "AMRITSAR",
        "day_actual_mm": actual, "day_normal_mm": 5.0, "day_departure_pct": 150.0, "day_category": "LE",
        "period_actual_mm": 50.0, "period_normal_mm": 40.0, "period_departure_pct": 25.0, "period_category": "E",
        "scraped_at": "2026-04-01T06:30:00",
    }])


def test_upsert_inserts_new(tmp_db):
    df = _sample_df()
    stats = upsert_rainfall(df, path=tmp_db)
    assert stats == {"inserted": 1, "updated": 0, "unchanged": 0}
    out = query_db("SELECT * FROM rainfall", path=tmp_db)
    assert len(out) == 1


def test_upsert_idempotent(tmp_db):
    df = _sample_df()
    upsert_rainfall(df, path=tmp_db)
    stats = upsert_rainfall(df, path=tmp_db)
    assert stats["inserted"] == 0
    assert stats["unchanged"] == 1
    assert len(query_db("SELECT * FROM rainfall", path=tmp_db)) == 1


def test_upsert_logs_revision_on_value_change(tmp_db):
    upsert_rainfall(_sample_df(actual=12.5), path=tmp_db)
    upsert_rainfall(_sample_df(actual=15.0), path=tmp_db)  # revised value
    revisions = query_db("SELECT * FROM revisions", path=tmp_db)
    assert len(revisions) == 1
    row = revisions.iloc[0]
    assert row["column_name"] == "day_actual_mm"
    assert float(row["old_value"]) == 12.5
    assert float(row["new_value"]) == 15.0


def test_init_db_creates_indices(tmp_db):
    init_db(tmp_db)
    idx = query_db(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'",
        path=tmp_db,
    )
    names = set(idx["name"])
    assert {"idx_rainfall_date", "idx_rainfall_state", "idx_rainfall_district", "idx_rainfall_level"}.issubset(names)


def test_write_csv(tmp_path):
    df = _sample_df()
    path = write_csv(df, "2026-04-01", out_dir=tmp_path)
    assert path.exists()
    loaded = pd.read_csv(path)
    assert len(loaded) == 1
    assert loaded.iloc[0]["district"] == "AMRITSAR"
