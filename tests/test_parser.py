"""Tests for the PDF parser using a saved IMD PDF fixture."""
from pathlib import Path

import pytest

from rainfall.parser import parse_pdf, to_dataframe

FIXTURE = Path(__file__).parent / "fixtures" / "sample_imd.pdf"


@pytest.fixture(scope="module")
def pdf_bytes():
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def parsed(pdf_bytes):
    return parse_pdf(pdf_bytes)


def test_parses_dates(parsed):
    assert parsed.day_start.year >= 2024
    assert parsed.day_start <= parsed.day_end
    assert parsed.period_start <= parsed.period_end
    assert parsed.period_end == parsed.day_end


def test_minimum_districts(parsed):
    districts = [r for r in parsed.rows if r["level"] == "district"]
    # India has ~700+ districts; IMD covers most of them
    assert len(districts) >= 500


def test_minimum_subdivisions(parsed):
    subs = [r for r in parsed.rows if r["level"] == "subdivision"]
    # IMD has 36 met-subdivisions
    assert 30 <= len(subs) <= 50


def test_states_present(parsed):
    districts = [r for r in parsed.rows if r["level"] == "district"]
    states = {r["state"] for r in districts if r["state"]}
    # Must include major states using canonical names (not IMD subdivision names)
    expected = {
        "ASSAM", "MEGHALAYA", "ARUNACHAL PRADESH",
        "WEST BENGAL",       # not "GANGETIC WEST BENGAL"
        "UTTAR PRADESH",     # not "EAST UTTAR PRADESH" / "WEST UTTAR PRADESH"
        "RAJASTHAN",         # not "EAST RAJASTHAN" / "WEST RAJASTHAN"
        "MADHYA PRADESH",    # not split names
        "MAHARASHTRA",       # not "VIDARBHA" / "MARATHWADA" etc.
        "KARNATAKA",         # not split names
        "ANDHRA PRADESH",    # not "COASTAL A. P. & YANAM" / "RAYALASEEMA"
    }
    assert expected.issubset(states), f"Missing canonical states: {expected - states}"
    # Ensure subdivision names did NOT leak into state column
    leaked = {
        "GANGETIC WEST BENGAL", "EAST UTTAR PRADESH", "WEST UTTAR PRADESH",
        "EAST RAJASTHAN", "WEST RAJASTHAN", "RAYALASEEMA",
        "VIDARBHA", "MARATHWADA", "MADHYA MAHARASHTRA", "KONKAN & GOA",
    }
    leaking = leaked & states
    assert not leaking, f"Subdivision names leaked into state column: {leaking}"


def test_district_has_subdivision(parsed):
    """Every district must have a subdivision context."""
    districts = [r for r in parsed.rows if r["level"] == "district"]
    missing = [r for r in districts if not r["subdivision"]]
    assert not missing, f"{len(missing)} districts have no subdivision"


def test_district_has_state(parsed):
    districts = [r for r in parsed.rows if r["level"] == "district"]
    missing = [r for r in districts if not r["state"]]
    assert not missing, f"{len(missing)} districts have no state"


def test_no_district_has_only_zeros(parsed):
    """At least some districts in the period should have non-zero rainfall."""
    districts = [r for r in parsed.rows if r["level"] == "district"]
    non_zero = [r for r in districts if (r["period_actual_mm"] or 0) > 0]
    assert len(non_zero) >= len(districts) * 0.3, "Suspiciously few non-zero rainfall values"


def test_categories_valid(parsed):
    valid = {"LE", "E", "N", "D", "LD", "NR", None}
    for r in parsed.rows:
        assert r["day_category"] in valid
        assert r["period_category"] in valid


def test_assam_districts_classified_correctly(parsed):
    """ASSAM is under composite subdivision ASSAM & MEGHALAYA — verify split worked."""
    assam = [r for r in parsed.rows if r["level"] == "district" and r["state"] == "ASSAM"]
    meghalaya = [r for r in parsed.rows if r["level"] == "district" and r["state"] == "MEGHALAYA"]
    assert len(assam) >= 20, f"Only {len(assam)} ASSAM districts"
    assert len(meghalaya) >= 8, f"Only {len(meghalaya)} MEGHALAYA districts"
    for r in assam + meghalaya:
        assert r["subdivision"] == "ASSAM & MEGHALAYA"


def test_to_dataframe(parsed):
    df = to_dataframe(parsed)
    assert not df.empty
    expected_cols = {
        "date", "period_start", "period_end", "level", "subdivision",
        "state", "district", "day_actual_mm", "day_normal_mm",
        "day_departure_pct", "day_category", "period_actual_mm",
        "period_normal_mm", "period_departure_pct", "period_category", "scraped_at",
    }
    assert expected_cols.issubset(df.columns)
