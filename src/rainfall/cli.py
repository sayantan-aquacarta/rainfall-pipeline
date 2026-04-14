"""
CLI for the rainfall pipeline.

Commands:
  rainfall scrape                       — fetch + parse + store today's PDF
  rainfall rebuild-api                  — regenerate static JSON API from SQLite
  rainfall snapshot                     — write Parquet snapshot of full DB
  rainfall backfill --pdf path1 path2   — re-process saved PDFs (e.g., archived dates)
  rainfall info                         — print DB stats
"""
from __future__ import annotations

import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import click

from .api_builder import build_all
from .config import CONFIG
from .logging_setup import configure_logging, get_logger
from .parser import parse_pdf, to_dataframe
from .scraper import ScrapeError, fetch_pdf, prune_old_pdfs, save_pdf
from .storage import init_db, log_run, query_db, upsert_rainfall, write_csv, write_parquet_snapshot
from .validator import ValidationError, validate

log = get_logger(__name__)


@click.group()
@click.option("--log-level", default="INFO", help="Logging level")
def cli(log_level: str) -> None:
    """Rainfall data pipeline CLI."""
    configure_logging(log_level)
    CONFIG.ensure_dirs()


@cli.command()
@click.option("--rebuild-api/--no-rebuild-api", default=True,
              help="Rebuild static JSON API after successful scrape")
@click.option("--snapshot/--no-snapshot", default=True,
              help="Write Parquet snapshot after successful scrape")
def scrape(rebuild_api: bool, snapshot: bool) -> None:
    """Fetch the daily IMD PDF, parse, validate, and store it."""
    started = time.monotonic()
    scraped_at = datetime.now(timezone.utc).isoformat()
    pdf_sha: str | None = None
    data_date: str | None = None
    n_rows = 0

    try:
        pdf_bytes = fetch_pdf()
        pdf_path, pdf_sha = save_pdf(pdf_bytes)
        log.info("scrape_pdf_saved", path=str(pdf_path), sha256=pdf_sha)

        parsed = parse_pdf(pdf_bytes)
        df = to_dataframe(parsed)
        df = validate(df)
        n_rows = len(df)
        data_date = parsed.day_end.isoformat()

        write_csv(df, data_date)
        stats = upsert_rainfall(df)
        log.info("scrape_db_upserted", **stats)

        if snapshot:
            write_parquet_snapshot()
        if rebuild_api:
            build_all()

        # Prune old PDFs — defensive: a failure here must never fail the scrape
        try:
            prune_stats = prune_old_pdfs()
            if prune_stats["deleted"]:
                log.info("pdf_retention_pruned", **prune_stats)
        except Exception as prune_exc:
            log.warning("pdf_retention_prune_error", error=str(prune_exc))

        duration = time.monotonic() - started
        log_run(scraped_at, data_date, pdf_sha, n_rows, "success", None, duration)
        click.echo(f"OK  date={data_date}  rows={n_rows}  duration={duration:.1f}s")

    except (ScrapeError, ValidationError) as e:
        duration = time.monotonic() - started
        log_run(scraped_at, data_date, pdf_sha, n_rows, "failure", str(e), duration)
        log.error("scrape_failed", error=str(e))
        click.echo(f"FAIL  {type(e).__name__}: {e}", err=True)
        sys.exit(2)
    except Exception as e:
        duration = time.monotonic() - started
        log_run(scraped_at, data_date, pdf_sha, n_rows, "failure", repr(e), duration)
        log.exception("scrape_unexpected_error")
        click.echo(f"FAIL  unexpected: {e}\n{traceback.format_exc()}", err=True)
        sys.exit(3)


@cli.command("rebuild-api")
def rebuild_api_cmd() -> None:
    """Regenerate the static JSON API from the current SQLite contents."""
    stats = build_all()
    click.echo(f"API rebuilt: {stats}")


@cli.command()
def snapshot() -> None:
    """Write a fresh Parquet snapshot of the full DB."""
    path = write_parquet_snapshot()
    click.echo(f"Snapshot written to {path}")


@cli.command()
@click.option("--pdf", "pdfs", multiple=True, type=click.Path(exists=True, path_type=Path),
              help="Paths to saved PDFs (repeatable)")
@click.option("--rebuild-api/--no-rebuild-api", default=True)
def backfill(pdfs: tuple[Path, ...], rebuild_api: bool) -> None:
    """Re-process locally saved PDFs (e.g., archived past-date PDFs)."""
    if not pdfs:
        click.echo("No PDFs supplied. Use --pdf path/to/file.pdf (repeatable).", err=True)
        sys.exit(1)
    init_db()
    for p in pdfs:
        click.echo(f"Processing {p}...")
        data = p.read_bytes()
        parsed = parse_pdf(data)
        df = validate(to_dataframe(parsed))
        write_csv(df, parsed.day_end.isoformat())
        stats = upsert_rainfall(df)
        click.echo(f"  date={parsed.day_end}  rows={len(df)}  {stats}")
    if rebuild_api:
        build_all()
    write_parquet_snapshot()


@cli.command()
def info() -> None:
    """Print database statistics."""
    init_db()
    summary = query_db("""
        SELECT
            MIN(date) AS first_date,
            MAX(date) AS last_date,
            COUNT(DISTINCT date) AS n_dates,
            COUNT(*) AS n_rows,
            COUNT(DISTINCT CASE WHEN level='state' THEN state END) AS n_states,
            COUNT(DISTINCT CASE WHEN level='district' THEN district END) AS n_districts
        FROM rainfall
    """)
    click.echo(summary.to_string(index=False))
    runs = query_db(
        "SELECT scraped_at, data_date, status, n_rows, duration_s FROM scrape_runs "
        "ORDER BY id DESC LIMIT 10"
    )
    click.echo("\nLast 10 runs:")
    click.echo(runs.to_string(index=False) if not runs.empty else "  (none)")


if __name__ == "__main__":
    cli()
