"""Tests for scraper utilities, including PDF retention pruning and URL discovery."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rainfall.scraper import _url_candidates, discover_pdf_url, prune_old_pdfs


def _make_fake_pdfs(tmp_path: Path, n_days: int) -> list[Path]:
    """
    Create *n_days* fake PDF files in *tmp_path* with names matching the
    pipeline convention: imd_YYYY-MM-DD_deadbeef.pdf.

    Files span the most recent *n_days* ending today (UTC-independent: we use
    a fixed anchor so tests are deterministic).
    """
    anchor = date(2026, 4, 14)  # fixed "today" for determinism
    paths = []
    for i in range(n_days):
        d = anchor - timedelta(days=i)
        p = tmp_path / f"imd_{d.isoformat()}_deadbeef.pdf"
        p.write_bytes(b"%PDF-fake")
        paths.append(p)
    return paths


def test_prune_removes_old_keeps_recent(tmp_path: Path) -> None:
    """40 files in, keep_days=30 → exactly 30 remain."""
    _make_fake_pdfs(tmp_path, 40)

    # Freeze "today" to the anchor date used when creating the files
    anchor = date(2026, 4, 14)
    result = prune_old_pdfs(keep_days=30, pdf_dir=tmp_path, _today=anchor)

    remaining = list(tmp_path.glob("*.pdf"))
    assert len(remaining) == 30, (
        f"Expected 30 files, got {len(remaining)}. "
        f"deleted={result['deleted']}, kept={result['kept']}"
    )
    assert result["deleted"] == 10
    assert result["kept"] == 30
    assert len(result["deleted_files"]) == 10


def test_prune_return_dict_counts_correct(tmp_path: Path) -> None:
    """The returned dict sums to total file count."""
    _make_fake_pdfs(tmp_path, 35)
    anchor = date(2026, 4, 14)
    result = prune_old_pdfs(keep_days=30, pdf_dir=tmp_path, _today=anchor)

    assert result["deleted"] + result["kept"] == 35
    assert result["deleted"] == 5
    assert result["kept"] == 30


def test_prune_keeps_all_when_fewer_than_keep_days(tmp_path: Path) -> None:
    """Fewer files than keep_days → nothing is deleted."""
    _make_fake_pdfs(tmp_path, 10)
    anchor = date(2026, 4, 14)
    result = prune_old_pdfs(keep_days=30, pdf_dir=tmp_path, _today=anchor)

    assert result["deleted"] == 0
    assert result["kept"] == 10
    assert len(list(tmp_path.glob("*.pdf"))) == 10


def test_prune_zero_keep_days_skips_entirely(tmp_path: Path) -> None:
    """keep_days=0 means 'keep forever' — no files deleted."""
    _make_fake_pdfs(tmp_path, 40)
    result = prune_old_pdfs(keep_days=0, pdf_dir=tmp_path)

    assert result["deleted"] == 0
    assert len(list(tmp_path.glob("*.pdf"))) == 40


def test_prune_missing_dir_returns_zeros(tmp_path: Path) -> None:
    """Non-existent directory doesn't raise; returns zero counts."""
    missing = tmp_path / "does_not_exist"
    result = prune_old_pdfs(keep_days=30, pdf_dir=missing)

    assert result["deleted"] == 0
    assert result["kept"] == 0


# ── URL discovery tests ──────────────────────────────────────────────────────

def test_url_candidates_excludes_known_url() -> None:
    """The known primary URL must not appear in the fallback candidates list."""
    known = "https://mausam.imd.gov.in/Rainfall/DISTRICT_RAINFALL_DISTRIBUTION_COUNTRY_INDIA_cd.pdf"
    candidates = _url_candidates(known)
    assert known not in candidates
    assert len(candidates) >= 3


def test_url_candidates_returns_absolute_urls() -> None:
    candidates = _url_candidates("https://mausam.imd.gov.in/Rainfall/other.pdf")
    assert all(c.startswith("https://") for c in candidates)


def test_discover_pdf_url_finds_district_link() -> None:
    """discover_pdf_url should prefer links with 'district' in the href."""
    html = """
    <html><body>
      <a href="/Rainfall/SOME_OTHER_REPORT.pdf">Other</a>
      <a href="/Rainfall/DISTRICT_RAINFALL_DISTRIBUTION_COUNTRY_INDIA_cd.pdf">District PDF</a>
    </body></html>
    """
    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status = MagicMock()

    with patch("rainfall.scraper.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = discover_pdf_url("https://mausam.imd.gov.in/stats.php", timeout=10.0)

    assert result is not None
    assert "district" in result.lower()


def test_discover_pdf_url_falls_back_to_any_pdf() -> None:
    """When no 'district' link found, discover_pdf_url returns the first PDF link."""
    html = '<html><body><a href="/data/rainfall.pdf">Report</a></body></html>'
    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status = MagicMock()

    with patch("rainfall.scraper.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = discover_pdf_url("https://mausam.imd.gov.in/stats.php", timeout=10.0)

    assert result is not None
    assert result.endswith(".pdf")


def test_discover_pdf_url_returns_none_on_page_error() -> None:
    """Network failure fetching the stats page returns None, not an exception."""
    import httpx as _httpx
    with patch("rainfall.scraper.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(side_effect=_httpx.TransportError("connection refused"))
        mock_client_cls.return_value = mock_client

        result = discover_pdf_url("https://mausam.imd.gov.in/stats.php", timeout=5.0)

    assert result is None


def test_discover_pdf_url_returns_none_when_no_pdf_links() -> None:
    """Page with no PDF hrefs returns None."""
    html = "<html><body><p>No PDFs here</p></body></html>"
    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status = MagicMock()

    with patch("rainfall.scraper.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = discover_pdf_url("https://mausam.imd.gov.in/stats.php", timeout=5.0)

    assert result is None


# ── Pruning tests ─────────────────────────────────────────────────────────────

def test_prune_ignores_unknown_filenames(tmp_path: Path) -> None:
    """Files that don't match imd_YYYY-MM-DD_*.pdf pattern are left alone."""
    # Create one conforming old file and one non-conforming file
    anchor = date(2026, 4, 14)
    old_date = anchor - timedelta(days=60)
    (tmp_path / f"imd_{old_date.isoformat()}_deadbeef.pdf").write_bytes(b"%PDF")
    (tmp_path / "mystery.pdf").write_bytes(b"%PDF")

    result = prune_old_pdfs(keep_days=30, pdf_dir=tmp_path, _today=anchor)

    # The old conforming file should be deleted; mystery.pdf should survive
    assert result["deleted"] == 1
    assert (tmp_path / "mystery.pdf").exists()
