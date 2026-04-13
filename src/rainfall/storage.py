"""
Storage layer: writes to SQLite (primary), per-day CSV (versioned in git), and
a full-history Parquet snapshot (compressed, fast for analytics).

Idempotency: PRIMARY KEY on (date, level, subdivision, state, district) with INSERT...ON
CONFLICT DO UPDATE. Re-running the same day overwrites cleanly — no duplicates ever.

Revisions: when an upsert changes existing values, we log to the `revisions` table.
This gives us a full audit trail of IMD data revisions over time — valuable for research.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd

from .config import CONFIG
from .logging_setup import get_logger

log = get_logger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rainfall (
    date                  TEXT NOT NULL,         -- ISO YYYY-MM-DD (data date)
    period_start          TEXT NOT NULL,
    period_end            TEXT NOT NULL,
    level                 TEXT NOT NULL,         -- subdivision | state | district
    subdivision           TEXT,
    state                 TEXT,
    district              TEXT,
    day_actual_mm         REAL,
    day_normal_mm         REAL,
    day_departure_pct     REAL,
    day_category          TEXT,
    period_actual_mm      REAL,
    period_normal_mm      REAL,
    period_departure_pct  REAL,
    period_category       TEXT,
    scraped_at            TEXT NOT NULL,
    PRIMARY KEY (date, level, subdivision, state, district)
);

CREATE INDEX IF NOT EXISTS idx_rainfall_date     ON rainfall(date);
CREATE INDEX IF NOT EXISTS idx_rainfall_state    ON rainfall(state);
CREATE INDEX IF NOT EXISTS idx_rainfall_district ON rainfall(district);
CREATE INDEX IF NOT EXISTS idx_rainfall_level    ON rainfall(level);

-- Audit log of every scrape attempt
CREATE TABLE IF NOT EXISTS scrape_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at   TEXT NOT NULL,
    data_date    TEXT,
    pdf_sha256   TEXT,
    n_rows       INTEGER,
    status       TEXT,                           -- success | failure
    error        TEXT,
    duration_s   REAL
);

-- Audit log of value changes (when IMD revises previously-published numbers)
CREATE TABLE IF NOT EXISTS revisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    revised_at   TEXT NOT NULL,
    date         TEXT NOT NULL,
    level        TEXT NOT NULL,
    subdivision  TEXT,
    state        TEXT,
    district     TEXT,
    column_name  TEXT NOT NULL,
    old_value    TEXT,
    new_value    TEXT
);
"""

# Cols used as the natural key (NULL-safe via COALESCE in SQL)
_PK_COLS = ["date", "level", "subdivision", "state", "district"]
_VALUE_COLS = [
    "period_start", "period_end",
    "day_actual_mm", "day_normal_mm", "day_departure_pct", "day_category",
    "period_actual_mm", "period_normal_mm", "period_departure_pct", "period_category",
    "scraped_at",
]
_ALL_COLS = _PK_COLS + _VALUE_COLS


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Context manager for SQLite connection with sane defaults."""
    path = path or CONFIG.sqlite_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)  # autocommit; we manage txns
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")  # better concurrent reads
        conn.execute("PRAGMA synchronous = NORMAL;")
        yield conn
    finally:
        conn.close()


def init_db(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
    log.info("db_initialized", path=str(path or CONFIG.sqlite_path))


def _df_to_records(df: pd.DataFrame) -> list[tuple]:
    """Coerce DataFrame rows to tuples in _ALL_COLS order, with proper null handling."""
    out = df.copy()
    # Convert dates/timestamps to ISO strings
    for col in ("date", "period_start", "period_end"):
        out[col] = pd.to_datetime(out[col]).dt.strftime("%Y-%m-%d")
    out["scraped_at"] = pd.to_datetime(out["scraped_at"]).dt.strftime("%Y-%m-%dT%H:%M:%S")
    # NaN -> None for SQL nullability
    out = out.where(pd.notna(out), None)
    return [tuple(row[c] for c in _ALL_COLS) for _, row in out.iterrows()]


def upsert_rainfall(df: pd.DataFrame, path: Path | None = None) -> dict:
    """
    Upsert all rows. Returns {inserted, updated, unchanged} counts.
    Logs revisions when existing values change.
    """
    if df.empty:
        return {"inserted": 0, "updated": 0, "unchanged": 0}

    init_db(path)
    records = _df_to_records(df)
    cols_csv = ",".join(_ALL_COLS)
    placeholders = ",".join(["?"] * len(_ALL_COLS))
    update_set = ",".join(
        f"{c}=excluded.{c}" for c in _VALUE_COLS
    )

    inserted = updated = unchanged = 0
    revisions: list[tuple] = []

    with connect(path) as conn:
        conn.execute("BEGIN")
        try:
            for rec in records:
                key = {c: rec[i] for i, c in enumerate(_ALL_COLS) if c in _PK_COLS}
                # Existing row?
                where = " AND ".join(
                    f"{c} IS ?" if key[c] is None else f"{c} = ?" for c in _PK_COLS
                )
                params = [key[c] for c in _PK_COLS]
                cur = conn.execute(
                    f"SELECT {','.join(_VALUE_COLS)} FROM rainfall WHERE {where}",
                    params,
                )
                existing = cur.fetchone()

                if existing is None:
                    inserted += 1
                else:
                    new_vals = {c: rec[_ALL_COLS.index(c)] for c in _VALUE_COLS}
                    changed = False
                    for c in _VALUE_COLS:
                        if c == "scraped_at":  # ignore — always changes
                            continue
                        old = existing[c]
                        new = new_vals[c]
                        # Float comparison with tolerance
                        if isinstance(old, float) and isinstance(new, float):
                            if abs(old - new) > 1e-6:
                                changed = True
                                revisions.append((
                                    new_vals["scraped_at"],
                                    key["date"], key["level"],
                                    key["subdivision"], key["state"], key["district"],
                                    c, str(old), str(new),
                                ))
                        elif old != new:
                            changed = True
                            revisions.append((
                                new_vals["scraped_at"],
                                key["date"], key["level"],
                                key["subdivision"], key["state"], key["district"],
                                c, str(old), str(new),
                            ))
                    if changed:
                        updated += 1
                    else:
                        unchanged += 1

                conn.execute(
                    f"INSERT INTO rainfall ({cols_csv}) VALUES ({placeholders}) "
                    f"ON CONFLICT (date, level, subdivision, state, district) "
                    f"DO UPDATE SET {update_set}",
                    rec,
                )

            if revisions:
                conn.executemany(
                    "INSERT INTO revisions (revised_at, date, level, subdivision, state, "
                    "district, column_name, old_value, new_value) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    revisions,
                )

            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    log.info(
        "upsert_complete",
        inserted=inserted, updated=updated, unchanged=unchanged,
        n_revisions=len(revisions),
    )
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged}


def log_run(
    scraped_at: str,
    data_date: str | None,
    pdf_sha256: str | None,
    n_rows: int,
    status: str,
    error: str | None,
    duration_s: float,
    path: Path | None = None,
) -> None:
    init_db(path)
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO scrape_runs (scraped_at, data_date, pdf_sha256, n_rows, "
            "status, error, duration_s) VALUES (?,?,?,?,?,?,?)",
            (scraped_at, data_date, pdf_sha256, n_rows, status, error, duration_s),
        )


def write_csv(df: pd.DataFrame, data_date: str, out_dir: Path | None = None) -> Path:
    """Write per-day CSV, naming convention rainfall_YYYY-MM-DD.csv."""
    out_dir = out_dir or CONFIG.raw_csv_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"rainfall_{data_date}.csv"
    df.to_csv(path, index=False)
    log.info("csv_written", path=str(path), rows=len(df))
    return path


def write_parquet_snapshot(path: Path | None = None, db_path: Path | None = None) -> Path:
    """Dump the full rainfall table to compressed Parquet for analytics use."""
    path = path or CONFIG.parquet_path
    with connect(db_path) as conn:
        df = pd.read_sql_query("SELECT * FROM rainfall ORDER BY date, level, subdivision, state, district", conn)
    if df.empty:
        log.warning("parquet_snapshot_empty")
        return path
    df.to_parquet(path, compression="snappy", index=False)
    log.info("parquet_written", path=str(path), rows=len(df), bytes=path.stat().st_size)
    return path


def query_db(sql: str, params: tuple = (), path: Path | None = None) -> pd.DataFrame:
    """Generic query helper."""
    with connect(path) as conn:
        return pd.read_sql_query(sql, conn, params=params)
