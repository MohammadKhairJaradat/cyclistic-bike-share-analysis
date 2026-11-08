"""Single entry point for the Cyclistic Divvy Bike-Share ETL pipeline."""

from __future__ import annotations

import argparse
import importlib.util
import logging
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module {name} from {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare_mod = _load_module("prepare_data", ROOT / "src/01_prepare_data.py")
analyze_mod = _load_module("analyze_data", ROOT / "src/02_analyze_data.py")

logger = logging.getLogger("cyclistic.pipeline")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Cyclistic ETL Pipeline: Extract from Divvy S3 / verify SHA-256 -> "
            "Transform month-by-month -> Validate -> Load Partitioned Parquet & SQLite."
        )
    )
    parser.add_argument(
        "--months",
        nargs="+",
        default=None,
        help="Run pipeline backfill for specific YYYYMM months (e.g. --months 202510 202511).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run the complete 12-month ETL and SQL/chart analysis pipeline (default).",
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="Skip raw ZIP extraction/cleaning and run SQLite load, SQL queries, and charts from data/processed/.",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Force re-downloading monthly ZIP archives from S3 even if local SHA-256 matches.",
    )
    parser.add_argument(
        "--write-csv",
        action="store_true",
        help="Also write uncompressed monthly CSVs to data/cleaned/ alongside Partitioned Parquet.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = parse_args(argv)
    start_time = time.perf_counter()

    if args.analyze_only:
        logger.info("Starting Cyclistic pipeline in --analyze-only mode ...")
        metrics = analyze_mod.run_analysis()
        elapsed = time.perf_counter() - start_time
        logger.info(
            "Pipeline completed in %.2fs (%s clean rides analyzed).",
            elapsed,
            f"{metrics['clean_rides']:,}",
        )
        return 0

    selected_months = (
        prepare_mod.ALL_MONTHS
        if (args.all or not args.months)
        else args.months
    )
    logger.info(
        "Starting Cyclistic ETL pipeline for %d month(s): %s",
        len(selected_months),
        ", ".join(selected_months),
    )

    validation_report = prepare_mod.run_preparation(
        months=selected_months,
        force_download=args.force_download,
        write_csv=args.write_csv,
    )

    if sorted(selected_months) == sorted(prepare_mod.ALL_MONTHS):
        analyze_mod.run_analysis()

    elapsed = time.perf_counter() - start_time
    logger.info(
        "ETL pipeline finished in %.2fs | Status: %s | Source rows: %s | Clean rows: %s",
        elapsed,
        validation_report["overall_status"].upper(),
        f"{validation_report['totals']['source_rows']:,}",
        f"{validation_report['totals']['clean_rows']:,}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
