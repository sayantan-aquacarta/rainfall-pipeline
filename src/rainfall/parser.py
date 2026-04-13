"""
Parser: turns the IMD PDF into a clean DataFrame.

Reliable classification signal: subdivision and state-aggregate rows are rendered in BOLD
(Trebuchet MS,Bold). District rows are rendered in Helvetica (regular). We extract
per-line font information directly via pdfplumber's char-level API.

PDF layout (interleaved, as of 2026):

    1 A & N ISLAND          0.0  ...        <- subdivision (BOLD)
    1 NICOBAR               0.0  ...        <- district (regular)
    2 NORTH & MIDDLE ANDAMAN ...
    3 SOUTH ANDAMAN          ...
    2 ARUNACHAL PRADESH     3.2  ...        <- subdivision (BOLD)
    1 ANJAW                 ...             <- district
    ...
    3 ASSAM & MEGHALAYA     3.8  ...        <- subdivision (BOLD)
      ASSAM                 4.9  ...        <- state header (BOLD, no number)
    1 BAJALI                0.0  ...        <- district
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO

import pandas as pd
import pdfplumber

from .logging_setup import get_logger

log = get_logger(__name__)


_DAY_RE = re.compile(
    r"DAY\s*:\s*(\d{2}\.\d{2}\.\d{4})\s*TO\s*(\d{2}\.\d{2}\.\d{4})", re.IGNORECASE
)
_PERIOD_RE = re.compile(
    r"PERIOD\s*:\s*(\d{2}\.\d{2}\.\d{4})\s*TO\s*(\d{2}\.\d{2}\.\d{4})", re.IGNORECASE
)

_VALID_CATS = {"LE", "E", "N", "D", "LD", "NR"}

_NUM = r"(?:-?\d+\.?\d*|\*)"
_PCT = r"(?:-?\d+%|\*)"
_CAT = r"(?:LE|E|N|D|LD|NR|\*)"

_TRAILING_RE = re.compile(
    rf"\s+({_NUM})\s+({_NUM})\s+({_PCT})\s+({_CAT})"
    rf"\s+({_NUM})\s+({_NUM})\s+({_PCT})\s+({_CAT})\s*$"
)

_LEAD_NUM_RE = re.compile(r"^\s*(\d+)\s+(.*)$")


@dataclass(frozen=True)
class ParsedLine:
    text: str
    is_bold: bool


@dataclass(frozen=True)
class ParsedDocument:
    day_start: date
    day_end: date
    period_start: date
    period_end: date
    rows: list[dict]


def _parse_dot_date(s: str) -> date:
    return datetime.strptime(s, "%d.%m.%Y").date()


def _to_float(token: str) -> float | None:
    if token in ("*", ""):
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _to_pct(token: str) -> float | None:
    if token in ("*", ""):
        return None
    try:
        return float(token.rstrip("%"))
    except ValueError:
        return None


def _to_cat(token: str) -> str | None:
    if token in ("*", ""):
        return None
    return token if token in _VALID_CATS else None


def _extract_lines_with_font(pdf_bytes: bytes) -> list[ParsedLine]:
    """
    Walk the PDF char-by-char, group into lines by y-position, and emit
    (line_text, is_bold) for each line.

    A line is "bold" if a majority of its alphabetic characters are in a bold font.
    """
    lines: list[ParsedLine] = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            buckets: dict[int, list] = defaultdict(list)
            for c in page.chars:
                ykey = round(c["top"])
                buckets[ykey].append(c)
            for ykey in sorted(buckets.keys()):
                chars = sorted(buckets[ykey], key=lambda c: c["x0"])
                pieces: list[str] = []
                bold_count = 0
                alpha_count = 0
                prev_x_end: float | None = None
                for c in chars:
                    if prev_x_end is not None:
                        gap = c["x0"] - prev_x_end
                        if gap > 1.5:
                            pieces.append(" ")
                    pieces.append(c["text"])
                    prev_x_end = c["x1"]
                    fontname = (c.get("fontname") or "").lower()
                    if c["text"].isalpha():
                        alpha_count += 1
                        if "bold" in fontname:
                            bold_count += 1
                line_text = "".join(pieces).strip()
                if not line_text:
                    continue
                is_bold = alpha_count > 0 and (bold_count / alpha_count) >= 0.5
                lines.append(ParsedLine(text=line_text, is_bold=is_bold))
    return lines


def extract_text(pdf_bytes: bytes) -> str:
    """Plain text dump (used to find date headers)."""
    return "\n".join(line.text for line in _extract_lines_with_font(pdf_bytes))


def _find_dates(text: str) -> tuple[date, date, date, date]:
    day_match = _DAY_RE.search(text)
    period_match = _PERIOD_RE.search(text)
    if not day_match or not period_match:
        raise ValueError(
            "Could not locate DAY/PERIOD headers in PDF — IMD format may have changed"
        )
    return (
        _parse_dot_date(day_match.group(1)),
        _parse_dot_date(day_match.group(2)),
        _parse_dot_date(period_match.group(1)),
        _parse_dot_date(period_match.group(2)),
    )


def _is_header_line(line: str) -> bool:
    upper = line.upper()
    return (
        "DISTRICTWISE RAINFALL" in upper
        or upper.startswith("S.NO.")
        or "STATE/DISTRICT" in upper
        or ("ACTUAL" in upper and "NORMAL" in upper)
        or "(MM)" in upper
    )


def parse_pdf(pdf_bytes: bytes) -> ParsedDocument:
    """Main entrypoint: PDF bytes -> structured rows."""
    parsed_lines = _extract_lines_with_font(pdf_bytes)
    if not parsed_lines:
        raise ValueError("PDF text extraction returned no lines")

    full_text = "\n".join(p.text for p in parsed_lines)
    day_start, day_end, period_start, period_end = _find_dates(full_text)
    log.info(
        "pdf_dates",
        day_start=str(day_start),
        day_end=str(day_end),
        period_start=str(period_start),
        period_end=str(period_end),
    )

    rows: list[dict] = []
    current_subdivision: str | None = None
    current_state: str | None = None

    for pl in parsed_lines:
        line = pl.text
        if _is_header_line(line):
            continue
        m = _TRAILING_RE.search(line)
        if not m:
            continue
        prefix = line[: m.start()].strip()
        (
            day_actual_s, day_normal_s, day_pct_s, day_cat_s,
            per_actual_s, per_normal_s, per_pct_s, per_cat_s,
        ) = m.groups()

        lead = _LEAD_NUM_RE.match(prefix)
        if lead:
            serial = int(lead.group(1))
            name = lead.group(2).strip()
        else:
            serial = None
            name = prefix.strip()

        if pl.is_bold:
            if serial is None:
                # state header within composite subdivision
                current_state = name
                rows.append(_row(
                    level="state",
                    subdivision=current_subdivision,
                    state=current_state,
                    district=None,
                    day=(day_actual_s, day_normal_s, day_pct_s, day_cat_s),
                    period=(per_actual_s, per_normal_s, per_pct_s, per_cat_s),
                ))
            else:
                # subdivision row
                current_subdivision = name
                current_state = None
                rows.append(_row(
                    level="subdivision",
                    subdivision=current_subdivision,
                    state=None,
                    district=None,
                    day=(day_actual_s, day_normal_s, day_pct_s, day_cat_s),
                    period=(per_actual_s, per_normal_s, per_pct_s, per_cat_s),
                ))
        else:
            district_state = current_state if current_state is not None else current_subdivision
            rows.append(_row(
                level="district",
                subdivision=current_subdivision,
                state=district_state,
                district=name,
                day=(day_actual_s, day_normal_s, day_pct_s, day_cat_s),
                period=(per_actual_s, per_normal_s, per_pct_s, per_cat_s),
            ))

    return ParsedDocument(
        day_start=day_start,
        day_end=day_end,
        period_start=period_start,
        period_end=period_end,
        rows=rows,
    )


def _row(
    level: str,
    subdivision: str | None,
    state: str | None,
    district: str | None,
    day: tuple[str, str, str, str],
    period: tuple[str, str, str, str],
) -> dict:
    return {
        "level": level,
        "subdivision": subdivision,
        "state": state,
        "district": district,
        "day_actual_mm": _to_float(day[0]),
        "day_normal_mm": _to_float(day[1]),
        "day_departure_pct": _to_pct(day[2]),
        "day_category": _to_cat(day[3]),
        "period_actual_mm": _to_float(period[0]),
        "period_normal_mm": _to_float(period[1]),
        "period_departure_pct": _to_pct(period[2]),
        "period_category": _to_cat(period[3]),
    }


def to_dataframe(parsed: ParsedDocument) -> pd.DataFrame:
    """Convert parsed document to a tidy DataFrame."""
    df = pd.DataFrame(parsed.rows)
    if df.empty:
        return df
    df.insert(0, "date", parsed.day_end)
    df.insert(1, "period_start", parsed.period_start)
    df.insert(2, "period_end", parsed.period_end)
    df["scraped_at"] = pd.Timestamp.now(tz="UTC")
    df = df.sort_values(
        ["level", "subdivision", "state", "district"], na_position="first"
    ).reset_index(drop=True)
    return df
