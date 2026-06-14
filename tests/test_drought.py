"""Tests for SPI drought computation."""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import pytest

from rainfall.drought import (
    CAT_COLORS,
    build_drought_api,
    classify_spi,
    compute_drought_history,
    compute_drought_status,
    spi_from_pct_normal,
)


# ── classify_spi ──────────────────────────────────────────────────────────────

def test_classify_spi_categories():
    assert classify_spi(-2.5)[0] == "D4"
    assert classify_spi(-1.8)[0] == "D3"
    assert classify_spi(-1.45)[0] == "D2"
    assert classify_spi(-1.0)[0] == "D1"
    assert classify_spi(-0.65)[0] == "D0"
    assert classify_spi(0.0)[0]  == "NN"
    assert classify_spi(0.65)[0] == "W0"
    assert classify_spi(1.0)[0]  == "W1"
    assert classify_spi(1.5)[0]  == "W2"


def test_classify_spi_none_returns_nn():
    code, name = classify_spi(None)
    assert code == "NN"
    assert "Normal" in name


def test_classify_spi_nan_returns_nn():
    assert classify_spi(float("nan"))[0] == "NN"


# ── spi_from_pct_normal ───────────────────────────────────────────────────────

def test_spi_below_normal_is_negative():
    spi = spi_from_pct_normal(actual_mm=200.0, normal_mm=400.0)
    assert spi is not None
    assert spi < 0.0


def test_spi_above_normal_is_positive():
    spi = spi_from_pct_normal(actual_mm=600.0, normal_mm=400.0)
    assert spi is not None
    assert spi > 0.0


def test_spi_near_normal_near_zero():
    """Rainfall at LPA should give SPI close to 0 (parameterised gamma CDF ≈ 0.5)."""
    spi = spi_from_pct_normal(actual_mm=400.0, normal_mm=400.0)
    assert spi is not None
    assert abs(spi) < 0.5


def test_spi_large_deficit_is_drought():
    """50% of normal should classify as at least D1 (moderate drought)."""
    spi = spi_from_pct_normal(actual_mm=200.0, normal_mm=400.0)
    assert spi is not None
    code, _ = classify_spi(spi)
    assert code in ("D1", "D2", "D3", "D4")


def test_spi_large_excess_is_wet():
    """200% of normal should classify as at least W1."""
    spi = spi_from_pct_normal(actual_mm=800.0, normal_mm=400.0)
    assert spi is not None
    code, _ = classify_spi(spi)
    assert code in ("W1", "W2")


def test_spi_invalid_inputs_return_none():
    assert spi_from_pct_normal(None, 400.0) is None
    assert spi_from_pct_normal(200.0, None) is None
    assert spi_from_pct_normal(200.0, 0.0)  is None
    assert spi_from_pct_normal(-1.0, 400.0) is None


def test_spi_with_historical_data_fitted():
    """With ≥10 historical values, gamma is fitted from observations."""
    import numpy as np
    rng = np.random.default_rng(42)
    historical = list(rng.gamma(shape=10, scale=40, size=20))  # mean≈400
    spi = spi_from_pct_normal(actual_mm=200.0, normal_mm=400.0, historical_mm=historical)
    assert spi is not None
    assert spi < 0  # below the historical mean


# ── DB helpers ────────────────────────────────────────────────────────────────

def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE rainfall (
            date TEXT, level TEXT, subdivision TEXT, state TEXT, district TEXT,
            day_actual_mm REAL, day_normal_mm REAL,
            period_actual_mm REAL, period_normal_mm REAL,
            period_departure_pct REAL, period_category TEXT
        )
    """)
    rows = [
        # Latest date
        ("2026-06-13", "subdivision", "KERALA",              None, None, 12.0, 20.0, 280.0, 400.0, -30.0, "D"),
        ("2026-06-13", "subdivision", "RAJASTHAN",           None, None,  0.0,  2.0,   5.0,  80.0, -93.8, "LD"),
        ("2026-06-13", "subdivision", "NORTH MADHYA PRADESH",None, None,  5.0,  6.0, 320.0, 300.0,   6.7, "N"),
        # Earlier date (for history)
        ("2026-06-10", "subdivision", "KERALA",              None, None, 10.0, 18.0, 268.0, 380.0, -29.5, "D"),
        ("2026-06-10", "subdivision", "RAJASTHAN",           None, None,  0.0,  2.0,   3.0,  75.0, -96.0, "LD"),
    ]
    conn.executemany("INSERT INTO rainfall VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


# ── compute_drought_status ────────────────────────────────────────────────────

def test_compute_drought_status_row_count(tmp_path):
    db = _make_db(tmp_path)
    df = compute_drought_status(db_path=db)
    assert len(df) == 3  # 3 subdivisions on the latest date


def test_compute_drought_status_kerala_below_normal(tmp_path):
    db = _make_db(tmp_path)
    df = compute_drought_status(db_path=db)
    kerala = df[df["subdivision"] == "KERALA"].iloc[0]
    assert kerala["spi"] < 0
    assert kerala["pct_normal"] < 100.0


def test_compute_drought_status_rajasthan_severe(tmp_path):
    """Rajasthan at ~6% of normal should land in D3 or D4."""
    db = _make_db(tmp_path)
    df = compute_drought_status(db_path=db)
    raj = df[df["subdivision"] == "RAJASTHAN"].iloc[0]
    assert raj["spi_category"] in ("D3", "D4")


def test_compute_drought_status_near_normal(tmp_path):
    """North MP at 106% of normal should be NN or W0."""
    db = _make_db(tmp_path)
    df = compute_drought_status(db_path=db)
    nmp = df[df["subdivision"] == "NORTH MADHYA PRADESH"].iloc[0]
    assert nmp["spi_category"] in ("NN", "W0", "W1")


# ── compute_drought_history ───────────────────────────────────────────────────

def test_compute_drought_history_rows(tmp_path):
    db = _make_db(tmp_path)
    df = compute_drought_history(db_path=db)
    # 3 rows on 2026-06-13 + 2 rows on 2026-06-10 = 5
    assert len(df) == 5


def test_compute_drought_history_columns(tmp_path):
    db = _make_db(tmp_path)
    df = compute_drought_history(db_path=db)
    assert {"date", "subdivision", "pct_normal", "spi", "spi_category"}.issubset(df.columns)


# ── build_drought_api ─────────────────────────────────────────────────────────

def test_build_drought_api_creates_files(tmp_path):
    db = _make_db(tmp_path)
    current = compute_drought_status(db_path=db)
    history = compute_drought_history(db_path=db)
    result = build_drought_api(current, history, docs_path=tmp_path)

    assert (tmp_path / "api" / "drought-latest.json").exists()
    assert (tmp_path / "api" / "drought-history.json").exists()
    assert result["subdivisions"] == 3


def test_build_drought_api_latest_json_valid(tmp_path):
    db = _make_db(tmp_path)
    current = compute_drought_status(db_path=db)
    history = compute_drought_history(db_path=db)
    build_drought_api(current, history, docs_path=tmp_path)

    data = json.loads((tmp_path / "api" / "drought-latest.json").read_text())
    assert "subdivisions" in data
    assert len(data["subdivisions"]) == 3
    # Sorted worst first
    spis = [r["spi"] for r in data["subdivisions"] if r["spi"] is not None]
    assert spis == sorted(spis)


def test_build_drought_api_cat_colors_present(tmp_path):
    db = _make_db(tmp_path)
    current = compute_drought_status(db_path=db)
    history = compute_drought_history(db_path=db)
    build_drought_api(current, history, docs_path=tmp_path)
    data = json.loads((tmp_path / "api" / "drought-latest.json").read_text())
    assert "cat_colors" in data
    assert set(data["cat_colors"].keys()) == set(CAT_COLORS.keys())


def test_build_drought_api_empty_dfs_dont_crash(tmp_path):
    import pandas as pd
    empty = pd.DataFrame()
    result = build_drought_api(empty, empty, docs_path=tmp_path)
    assert result["subdivisions"] == 0
