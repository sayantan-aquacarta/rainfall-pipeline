"""
Scraper: downloads the daily IMD district rainfall PDF.

Why PDF, not HTML?
The IMD page rainfall_statistics_3.php does NOT contain the data in HTML — it
embeds the data as 12 PNG images plus a downloadable PDF. The PDF is the
machine-readable source of truth and is what IMD intends for download.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
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
