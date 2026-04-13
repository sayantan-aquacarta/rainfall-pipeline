"""Centralized configuration. All paths and URLs in one place."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# Repo root = parent of src/
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Config:
    # --- Source ---
    imd_pdf_url: str = (
        "https://mausam.imd.gov.in/Rainfall/"
        "DISTRICT_RAINFALL_DISTRIBUTION_COUNTRY_INDIA_cd.pdf"
    )
    imd_page_url: str = (
        "https://mausam.imd.gov.in/imd_latest/contents/rainfall_statistics_3.php"
    )
    user_agent: str = (
        "rainfall-pipeline/0.1 (+https://github.com/your-org/rainfall-pipeline; "
        "research; contact: your-email@example.com)"
    )
    request_timeout_s: float = 60.0
    max_retries: int = 5

    # --- Storage paths ---
    data_dir: Path = REPO_ROOT / "data"
    raw_csv_dir: Path = REPO_ROOT / "data" / "raw"
    raw_pdf_dir: Path = REPO_ROOT / "data" / "raw_pdf"
    sqlite_path: Path = REPO_ROOT / "data" / "rainfall.db"
    parquet_path: Path = REPO_ROOT / "data" / "rainfall.parquet"

    # --- API output (served by GitHub Pages) ---
    docs_dir: Path = REPO_ROOT / "docs"
    api_dir: Path = REPO_ROOT / "docs" / "api"

    # --- Behavior ---
    # If True, skip writing if a row for (date, state, district) already exists with same values.
    # Always upsert on PK conflict, but log when values change (data revisions).
    enable_revision_log: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        """Allow overrides via env vars; useful for testing."""
        kwargs = {}
        if v := os.getenv("RAINFALL_PDF_URL"):
            kwargs["imd_pdf_url"] = v
        if v := os.getenv("RAINFALL_DATA_DIR"):
            base = Path(v)
            kwargs["data_dir"] = base
            kwargs["raw_csv_dir"] = base / "raw"
            kwargs["raw_pdf_dir"] = base / "raw_pdf"
            kwargs["sqlite_path"] = base / "rainfall.db"
            kwargs["parquet_path"] = base / "rainfall.parquet"
        return cls(**kwargs)

    def ensure_dirs(self) -> None:
        for p in (
            self.data_dir,
            self.raw_csv_dir,
            self.raw_pdf_dir,
            self.docs_dir,
            self.api_dir,
            self.api_dir / "by-date",
            self.api_dir / "by-state",
            self.api_dir / "by-district",
        ):
            p.mkdir(parents=True, exist_ok=True)


CONFIG = Config.from_env()
