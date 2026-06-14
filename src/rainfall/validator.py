"""Schema validation via Pandera. Catches IMD format drift early."""
from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Column, DataFrameSchema, Check

from .logging_setup import get_logger

log = get_logger(__name__)


SCHEMA = DataFrameSchema(
    {
        "date": Column(pa.DateTime, nullable=False, coerce=True),
        "period_start": Column(pa.DateTime, nullable=False, coerce=True),
        "period_end": Column(pa.DateTime, nullable=False, coerce=True),
        "level": Column(str, Check.isin(["subdivision", "state", "district"])),
        "subdivision": Column(str, nullable=True),
        "state": Column(str, nullable=True),
        "district": Column(str, nullable=True),
        "day_actual_mm": Column(float, Check.in_range(0, 5000), nullable=True),
        "day_normal_mm": Column(float, Check.in_range(0, 5000), nullable=True),
        "day_departure_pct": Column(float, Check.in_range(-100, 100000000), nullable=True),
        "day_category": Column(
            str, Check.isin(["LE", "E", "N", "D", "LD", "NR"]), nullable=True
        ),
        "period_actual_mm": Column(float, Check.in_range(0, 20000), nullable=True),
        "period_normal_mm": Column(float, Check.in_range(0, 20000), nullable=True),
        "period_departure_pct": Column(float, Check.in_range(-100, 100000000), nullable=True),
        "period_category": Column(
            str, Check.isin(["LE", "E", "N", "D", "LD", "NR"]), nullable=True
        ),
        "scraped_at": Column(pa.DateTime, coerce=True),
    },
    strict=False,  # allow extra columns in the future
    coerce=True,
)


class ValidationError(RuntimeError):
    pass


def validate(df: pd.DataFrame) -> pd.DataFrame:
    """Validate and return the (possibly coerced) DataFrame."""
    if df.empty:
        raise ValidationError("DataFrame is empty — parser returned no rows")

    # Sanity checks on row counts
    n_districts = (df["level"] == "district").sum()
    n_subs = (df["level"] == "subdivision").sum()
    # Monsoon format (June+) emits level="state" for all aggregates, so subdivision count
    # may be 0. Accept if combined aggregate rows cover the expected ~36 IMD subdivisions.
    n_states = (df["level"] == "state").sum()
    if n_districts < 500:
        raise ValidationError(
            f"Only {n_districts} district rows — expected >= 500. Possible parsing failure."
        )
    if n_subs + n_states < 30:
        raise ValidationError(
            f"Only {n_subs} subdivision + {n_states} state aggregate rows — expected >= 30. "
            "Possible parsing failure."
        )

    try:
        validated = SCHEMA.validate(df, lazy=True)
    except pa.errors.SchemaErrors as e:
        log.error("schema_validation_failed", failures=str(e.failure_cases.head(20)))
        raise ValidationError(f"Schema validation failed: {e}") from e

    log.info(
        "validation_ok",
        n_rows=len(validated),
        n_districts=int(n_districts),
        n_subdivisions=int(n_subs),
        n_states=int(n_states),
    )
    return validated
