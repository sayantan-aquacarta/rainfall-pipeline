"""
Scraper: downloads the daily IMD district rainfall PDF.

Why PDF, not HTML?
The IMD page rainfall_statistics_3.php does NOT contain the data in HTML — it
embeds the data as 12 PNG images plus a downloadable PDF. The PDF is the
machine-readable source of truth and is what IMD intends for download.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import CONFIG
from .logging_setup import get_logger

log = get_logger(__name__)


class ScrapeError(RuntimeError):
    """Raised when the PDF cannot be retrieved after all retries."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        # Retry on 5xx and 429 only; 4xx (other) is permanent
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


@retry(
    reraise=True,
    stop=stop_after_attempt(CONFIG.max_retries),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    retry=retry_if_exception_type((httpx.HTTPError,)),
)
def _download(url: str, timeout: float) -> bytes:
    """Single download attempt. Tenacity will retry on any HTTPError."""
    headers = {
        "User-Agent": CONFIG.user_agent,
        "Accept": "application/pdf,*/*;q=0.8",
    }
    log.info("download_attempt", url=url)
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        if not resp.content.startswith(b"%PDF"):
            raise ScrapeError(
                f"Response from {url} is not a PDF (got {resp.content[:8]!r})"
            )
        return resp.content


def fetch_pdf(url: str | None = None, timeout: float | None = None) -> bytes:
    """
    Fetch the IMD PDF. Raises ScrapeError on permanent failure.

    The PDF URL is stable across days — IMD overwrites the same file each day.
    """
    url = url or CONFIG.imd_pdf_url
    timeout = timeout or CONFIG.request_timeout_s
    try:
        data = _download(url, timeout)
    except Exception as e:
        log.error("download_failed_permanently", url=url, error=str(e))
        raise ScrapeError(f"Failed to download {url}: {e}") from e
    log.info("download_success", url=url, bytes=len(data))
    return data


def save_pdf(data: bytes, out_dir: Path | None = None) -> tuple[Path, str]:
    """
    Save the raw PDF to disk for forensic auditability.
    Returns (path, sha256_hex).
    """
    out_dir = out_dir or CONFIG.raw_pdf_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    # Use UTC scrape date in the filename — actual data date is parsed later
    # and may differ (e.g., IMD publishes data for "yesterday").
    scrape_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sha = hashlib.sha256(data).hexdigest()
    path = out_dir / f"imd_{scrape_date}_{sha[:8]}.pdf"
    if not path.exists():
        path.write_bytes(data)
        log.info("pdf_saved", path=str(path), sha256=sha)
    else:
        log.info("pdf_unchanged", path=str(path), sha256=sha)
    return path, sha


# Matches filenames like imd_2026-04-13_318be450.pdf
_PDF_DATE_RE = re.compile(r"^imd_(\d{4}-\d{2}-\d{2})_[0-9a-f]+\.pdf$")


def prune_old_pdfs(
    keep_days: int | None = None,
    pdf_dir: Path | None = None,
    *,
    _today: date | None = None,
) -> dict:
    """
    Delete raw PDF files in *pdf_dir* whose filename date is older than
    *keep_days* days ago (UTC).  The current UTC date's PDF is never deleted
    regardless of the keep_days value.

    Returns:
        {"deleted": N, "kept": M, "deleted_files": [str, ...]}

    Callers should treat a non-zero ``deleted`` count as informational, not
    an error.  Pass ``keep_days=0`` to disable pruning entirely (keeps all).

    ``_today`` is a private escape hatch for unit tests; production code
    should always leave it as None (defaults to UTC today).
    """
    if keep_days is None:
        keep_days = CONFIG.pdf_retention_days

    # keep_days=0 means "keep forever" — bail out early
    if keep_days <= 0:
        log.info("pdf_prune_skipped", reason="keep_days=0 (retention disabled)")
        return {"deleted": 0, "kept": 0, "deleted_files": []}

    pdf_dir = pdf_dir or CONFIG.raw_pdf_dir
    if not pdf_dir.exists():
        log.info("pdf_prune_skipped", reason="pdf_dir does not exist", path=str(pdf_dir))
        return {"deleted": 0, "kept": 0, "deleted_files": []}

    today_utc: date = _today if _today is not None else datetime.now(timezone.utc).date()
    cutoff: date = today_utc - timedelta(days=keep_days)

    deleted: list[str] = []
    kept = 0

    for pdf_path in sorted(pdf_dir.glob("*.pdf")):
        m = _PDF_DATE_RE.match(pdf_path.name)
        if not m:
            # Filename doesn't match expected pattern — leave it alone
            log.warning("pdf_prune_unknown_filename", path=str(pdf_path))
            kept += 1
            continue

        file_date = date.fromisoformat(m.group(1))

        # Keep if within the retention window (strictly after the cutoff date)
        # or if this is today's file (extra safeguard regardless of keep_days).
        if file_date > cutoff or file_date == today_utc:
            kept += 1
            continue

        try:
            pdf_path.unlink()
            deleted.append(pdf_path.name)
            log.info("pdf_pruned", file=pdf_path.name, file_date=str(file_date), cutoff=str(cutoff))
        except OSError as exc:
            log.warning("pdf_prune_delete_failed", file=pdf_path.name, error=str(exc))
            kept += 1

    log.info(
        "pdf_prune_complete",
        deleted=len(deleted),
        kept=kept,
        keep_days=keep_days,
        cutoff=str(cutoff),
    )
    return {"deleted": len(deleted), "kept": kept, "deleted_files": deleted}
