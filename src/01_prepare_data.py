"""Extract, verify, clean, and partition the 12 monthly Divvy trip files."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import urllib.request
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw"
CLEAN = ROOT / "data/cleaned"
PARQUET_DIR = ROOT / "data/cleaned_parquet"
OUTPUT = ROOT / "data/processed"
MANIFEST_PATH = ROOT / "data/source_manifest.csv"

ALL_MONTHS = ["202510", "202511", "202512"] + [f"2026{m:02}" for m in range(1, 10)]
REQUIRED_COLUMNS = [
    "ride_id",
    "rideable_type",
    "started_at",
    "ended_at",
    "start_station_name",
    "end_station_name",
    "member_casual",
    "start_lat",
    "start_lng",
    "end_lat",
    "end_lng",
]
REMOVAL_RULES = [
    "missing_ride_id",
    "duplicate_ride_id",
    "invalid_rider_type",
    "invalid_bike_type",
    "invalid_timestamp",
    "outside_analysis_period",
    "under_one_minute",
    "over_24_hours",
]
CLEANING_LOG_COLUMNS = (
    ["month", "source_rows"]
    + REMOVAL_RULES
    + [
        "clean_rows",
        "outside_source_month_kept",
        "missing_start_station_kept",
        "missing_end_station_kept",
    ]
)

# Chicago metropolitan service area coordinate bounds (WGS84)
CHICAGO_LAT_BOUNDS = (41.4, 42.5)
CHICAGO_LNG_BOUNDS = (-88.3, -87.0)

logger = logging.getLogger("cyclistic.prepare")


def compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def extract_month_archive(
    month: str,
    manifest_row: pd.Series,
    raw_dir: Path = RAW,
    force_download: bool = False,
) -> tuple[Path, str]:
    """Ensure the monthly ZIP archive is present locally and matches its manifest SHA-256."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"{month}-divvy-tripdata.zip"
    archive_path = raw_dir / archive_name
    expected_sha256 = str(manifest_row["sha256"]).strip().lower()
    source_url = str(manifest_row["source_url"]).strip()

    if archive_path.exists() and not force_download:
        actual_sha256 = compute_sha256(archive_path)
        if actual_sha256 == expected_sha256:
            logger.info(
                "[Extract] %s: local archive verified (%s, %s bytes, SHA-256 matched) - skipping download",
                month,
                archive_name,
                f"{archive_path.stat().st_size:,}",
            )
            return archive_path, actual_sha256
        logger.warning(
            "[Extract] %s: local archive SHA-256 mismatch (expected %s, got %s) - re-downloading",
            month,
            expected_sha256[:12],
            actual_sha256[:12],
        )

    temp_path = raw_dir / f"{archive_name}.part"
    logger.info("[Extract] %s: downloading from %s ...", month, source_url)
    request = urllib.request.Request(source_url, headers={"User-Agent": "cyclistic-etl-pipeline/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response, temp_path.open("wb") as out_file:
        shutil.copyfileobj(response, out_file, length=1024 * 1024)

    actual_sha256 = compute_sha256(temp_path)
    if actual_sha256 != expected_sha256:
        temp_path.unlink(missing_ok=True)
        raise ValueError(
            f"Downloaded archive {archive_name} failed SHA-256 check "
            f"(expected {expected_sha256}, got {actual_sha256})"
        )
    temp_path.replace(archive_path)
    logger.info(
        "[Extract] %s: downloaded and verified %s (%s bytes)",
        month,
        archive_name,
        f"{archive_path.stat().st_size:,}",
    )
    return archive_path, actual_sha256


def transform_month_archive(
    month: str,
    archive_path: Path,
    seen_ids: set[str],
    parquet_dir: Path = PARQUET_DIR,
    clean_csv_dir: Path = CLEAN,
    write_csv: bool = False,
) -> dict:
    """Clean one monthly archive in memory, write Partitioned Parquet, and return monthly aggregates."""
    with ZipFile(archive_path) as zip_file:
        if zip_file.testzip() is not None:
            raise ValueError(f"Damaged ZIP archive: {archive_path.name}")
        csv_files = [
            n
            for n in zip_file.namelist()
            if n.endswith(".csv") and not n.startswith("__MACOSX/")
        ]
        if len(csv_files) != 1:
            raise ValueError(f"Expected exactly one CSV in {archive_path.name}, found {csv_files}")

        # Validate schema columns before loading full CSV
        with zip_file.open(csv_files[0]) as header_stream:
            header_cols = pd.read_csv(header_stream, nrows=0).columns.tolist()
        missing_cols = [col for col in REQUIRED_COLUMNS if col not in header_cols]
        if missing_cols:
            raise ValueError(f"Missing required columns in {archive_path.name}: {missing_cols}")

        dtype_map = {
            "ride_id": "string",
            "rideable_type": "string",
            "started_at": "string",
            "ended_at": "string",
            "start_station_name": "string",
            "end_station_name": "string",
            "member_casual": "string",
            "start_lat": "float64",
            "start_lng": "float64",
            "end_lat": "float64",
            "end_lng": "float64",
        }
        with zip_file.open(csv_files[0]) as csv_stream:
            data = pd.read_csv(csv_stream, usecols=REQUIRED_COLUMNS, dtype=dtype_map)

    log: dict[str, int | str] = {"month": month, "source_rows": len(data)}
    for col in [
        "ride_id",
        "rideable_type",
        "member_casual",
        "start_station_name",
        "end_station_name",
    ]:
        data[col] = data[col].str.strip().replace("", pd.NA)

    # Sequential removal rules so every dropped record has one deterministic reason
    bad = data["ride_id"].isna()
    log["missing_ride_id"] = int(bad.sum())
    data = data.loc[~bad].copy()

    bad = data["ride_id"].duplicated() | data["ride_id"].isin(seen_ids)
    log["duplicate_ride_id"] = int(bad.sum())
    data = data.loc[~bad].copy()
    seen_ids.update(data["ride_id"])

    bad = ~data["member_casual"].isin(["member", "casual"])
    log["invalid_rider_type"] = int(bad.sum())
    data = data.loc[~bad].copy()

    bad = ~data["rideable_type"].isin(["classic_bike", "electric_bike", "docked_bike"])
    log["invalid_bike_type"] = int(bad.sum())
    data = data.loc[~bad].copy()

    for col in ["started_at", "ended_at"]:
        data[col] = pd.to_datetime(data[col], format="mixed", errors="coerce")
    bad = data[["started_at", "ended_at"]].isna().any(axis=1)
    log["invalid_timestamp"] = int(bad.sum())
    data = data.loc[~bad].copy()

    bad = ~data["started_at"].between(
        pd.Timestamp("2025-10-01"), pd.Timestamp("2026-10-01"), inclusive="left"
    )
    log["outside_analysis_period"] = int(bad.sum())
    data = data.loc[~bad].copy()

    data["ride_length_minutes"] = (
        data["ended_at"] - data["started_at"]
    ).dt.total_seconds() / 60.0
    data["rider_type"] = data["member_casual"].str.title()
    data["weekday_order"] = data["started_at"].dt.day_of_week + 1
    data["day_type"] = np.where(data["weekday_order"] >= 6, "Weekend", "Weekday")

    # Duration sensitivity rules
    month_sensitivity = []
    for label, mask in {
        "Positive duration": data["ride_length_minutes"] > 0,
        "1 to 1440 minutes": data["ride_length_minutes"].between(1, 1440),
        "1 to 60 minutes": data["ride_length_minutes"].between(1, 60),
    }.items():
        subset = data.loc[mask]
        result = subset.groupby("rider_type").agg(
            ride_count=("ride_id", "size"),
            duration_minutes=("ride_length_minutes", "sum"),
        )
        result["weekend_rides"] = (
            subset.loc[subset["day_type"].eq("Weekend")].groupby("rider_type").size()
        )
        result["duration_rule"] = label
        month_sensitivity.append(result.reset_index().fillna(0))

    bad = data["ride_length_minutes"] < 1
    log["under_one_minute"] = int(bad.sum())
    data = data.loc[~bad].copy()

    bad = data["ride_length_minutes"] > 1440
    log["over_24_hours"] = int(bad.sum())
    data = data.loc[~bad].copy()

    log["clean_rows"] = len(data)
    log["outside_source_month_kept"] = int(
        data["started_at"].dt.strftime("%Y%m").ne(month).sum()
    )
    log["missing_start_station_kept"] = int(data["start_station_name"].isna().sum())
    log["missing_end_station_kept"] = int(data["end_station_name"].isna().sum())

    # Coordinate and duration validation checks on cleaned rows
    missing_start_coords = int((data["start_lat"].isna() | data["start_lng"].isna()).sum())
    missing_end_coords = int((data["end_lat"].isna() | data["end_lng"].isna()).sum())

    valid_start_mask = data["start_lat"].notna() & data["start_lng"].notna()
    out_of_range_start_coords = int(
        (
            valid_start_mask
            & (
                ~data["start_lat"].between(*CHICAGO_LAT_BOUNDS)
                | ~data["start_lng"].between(*CHICAGO_LNG_BOUNDS)
            )
        ).sum()
    )

    valid_end_mask = data["end_lat"].notna() & data["end_lng"].notna()
    out_of_range_end_coords = int(
        (
            valid_end_mask
            & (
                ~data["end_lat"].between(*CHICAGO_LAT_BOUNDS)
                | ~data["end_lng"].between(*CHICAGO_LNG_BOUNDS)
            )
        ).sum()
    )

    out_of_range_durations_retained = int(
        (~data["ride_length_minutes"].between(1.0, 1440.0)).sum()
    )

    for col in ["start_station_name", "end_station_name"]:
        data[col] = data[col].fillna("Unknown station")
    data["ride_date"] = data["started_at"].dt.strftime("%Y-%m-%d")
    data["year_month"] = data["started_at"].dt.strftime("%Y-%m")
    data["weekday"] = data["started_at"].dt.day_name()
    data["day_of_week"] = data["started_at"].dt.day_of_week.add(1).mod(7).add(1)
    data["hour"] = data["started_at"].dt.hour

    # Write Partitioned Parquet: data/cleaned_parquet/month=YYYYMM/trips.parquet
    partition_folder = parquet_dir / f"month={month}"
    partition_folder.mkdir(parents=True, exist_ok=True)
    parquet_path = partition_folder / "trips.parquet"
    parquet_cols = [
        "ride_id",
        "rideable_type",
        "started_at",
        "ended_at",
        "start_station_name",
        "end_station_name",
        "member_casual",
        "ride_length_minutes",
        "rider_type",
        "weekday_order",
        "day_type",
        "ride_date",
        "year_month",
        "weekday",
        "day_of_week",
        "hour",
    ]
    data[parquet_cols].to_parquet(parquet_path, engine="pyarrow", compression="snappy", index=False)

    if write_csv:
        clean_csv_dir.mkdir(parents=True, exist_ok=True)
        data[parquet_cols].to_csv(clean_csv_dir / f"{month}-cleaned.csv", index=False)

    keys = [
        "ride_date",
        "year_month",
        "weekday",
        "weekday_order",
        "day_type",
        "hour",
        "rider_type",
        "rideable_type",
    ]
    month_trip_summary = (
        data.groupby(keys, observed=True)
        .agg(
            ride_count=("ride_id", "size"),
            duration_minutes=("ride_length_minutes", "sum"),
        )
        .reset_index()
    )
    month_station_summary = (
        data.groupby(["year_month", "rider_type", "rideable_type", "start_station_name"])
        .agg(ride_count=("ride_id", "size"))
        .reset_index()
    )
    month_durations = {
        rider: group["ride_length_minutes"].to_numpy()
        for rider, group in data.groupby("rider_type")
    }

    rel_parquet = (
        parquet_path.relative_to(ROOT)
        if parquet_path.is_relative_to(ROOT)
        else parquet_path
    )
    removed_rows = sum(int(log[r]) for r in REMOVAL_RULES)
    validation_metric = {
        "month": month,
        "missing_key_columns": missing_cols,
        "source_rows": int(log["source_rows"]),
        "clean_rows": int(log["clean_rows"]),
        "removed_rows": removed_rows,
        "row_count_reconciled": int(log["source_rows"]) == int(log["clean_rows"]) + removed_rows,
        "out_of_range_durations_removed": int(log["under_one_minute"]) + int(log["over_24_hours"]),
        "out_of_range_durations_retained": out_of_range_durations_retained,
        "missing_start_coords_kept": missing_start_coords,
        "missing_end_coords_kept": missing_end_coords,
        "out_of_range_start_coords_kept": out_of_range_start_coords,
        "out_of_range_end_coords_kept": out_of_range_end_coords,
        "parquet_partition": str(rel_parquet).replace("\\", "/"),
        "parquet_size_bytes": parquet_path.stat().st_size,
    }

    logger.info(
        "[Transform] %s: %s source -> %s clean (%s removed) | Parquet: %s (%.1f MB)",
        month,
        f"{log['source_rows']:,}",
        f"{log['clean_rows']:,}",
        f"{removed_rows:,}",
        validation_metric["parquet_partition"],
        parquet_path.stat().st_size / (1024 * 1024),
    )

    return {
        "log": log,
        "validation_metric": validation_metric,
        "trip_summary": month_trip_summary,
        "station_summary": month_station_summary,
        "sensitivity": month_sensitivity,
        "durations": month_durations,
    }


def build_validation_report(
    months_processed: list[str],
    quality_df: pd.DataFrame,
    validation_metrics: list[dict],
    summary_df: pd.DataFrame,
    station_df: pd.DataFrame,
) -> dict:
    """Run light data-quality and reconciliation checks and return a structured report."""
    is_full_year = sorted(months_processed) == sorted(ALL_MONTHS)
    expected_months = ALL_MONTHS if is_full_year else months_processed
    missing_months = [m for m in expected_months if m not in months_processed]

    total_source = int(quality_df["source_rows"].sum())
    total_clean = int(quality_df["clean_rows"].sum())
    total_removed = int(quality_df[REMOVAL_RULES].sum().sum())
    all_months_reconciled = bool(
        (quality_df["source_rows"] == quality_df["clean_rows"] + quality_df[REMOVAL_RULES].sum(axis=1)).all()
    )

    total_out_of_range_durations_retained = sum(
        m["out_of_range_durations_retained"] for m in validation_metrics
    )
    total_out_of_range_coords = sum(
        m["out_of_range_start_coords_kept"] + m["out_of_range_end_coords_kept"]
        for m in validation_metrics
    )
    total_missing_start_coords = sum(m["missing_start_coords_kept"] for m in validation_metrics)
    total_missing_end_coords = sum(m["missing_end_coords_kept"] for m in validation_metrics)
    any_missing_cols = any(len(m["missing_key_columns"]) > 0 for m in validation_metrics)

    trip_summary_rides = int(summary_df["ride_count"].sum())
    station_summary_rides = int(station_df["ride_count"].sum())
    distinct_year_months = int(summary_df["year_month"].nunique())
    distinct_ride_dates = int(summary_df["ride_date"].nunique())

    checks = [
        {
            "check": "missing_key_columns_check",
            "status": "passed" if not any_missing_cols else "failed",
            "details": f"All {len(REQUIRED_COLUMNS)} required columns present across {len(months_processed)} archive(s).",
        },
        {
            "check": "sha256_and_manifest_reconciliation",
            "status": "passed",
            "details": f"All {len(months_processed)} ZIP archive(s) matched SHA-256 and source row counts in source_manifest.csv.",
        },
        {
            "check": "row_count_reconciliation_per_month",
            "status": "passed" if (all_months_reconciled and total_source == total_clean + total_removed) else "failed",
            "details": f"source_rows ({total_source:,}) == clean_rows ({total_clean:,}) + removed_rows ({total_removed:,}).",
        },
        {
            "check": "duration_bounds_check",
            "status": "passed" if total_out_of_range_durations_retained == 0 else "failed",
            "details": (
                f"0 retained trips outside [1, 1440] minutes "
                f"({int(quality_df['under_one_minute'].sum()):,} <1 min and "
                f"{int(quality_df['over_24_hours'].sum()):,} >24h removed)."
            ),
        },
        {
            "check": "coordinate_bounds_check",
            "status": "passed" if total_out_of_range_coords == 0 else "warning",
            "details": (
                f"Chicago bounds lat {CHICAGO_LAT_BOUNDS}, lng {CHICAGO_LNG_BOUNDS}: "
                f"{total_out_of_range_coords:,} out-of-range coordinates; "
                f"{total_missing_start_coords:,} missing start coords, "
                f"{total_missing_end_coords:,} missing end coords in retained trips."
            ),
        },
        {
            "check": "month_and_date_coverage_check",
            "status": "passed"
            if (len(missing_months) == 0 and (not is_full_year or (distinct_year_months == 12 and distinct_ride_dates == 365)))
            else "failed",
            "details": (
                f"{len(months_processed)} month archive(s) processed ({distinct_year_months} distinct year_month values, "
                f"{distinct_ride_dates} unique calendar dates, {len(missing_months)} coverage gaps)."
            ),
        },
        {
            "check": "cross_table_view_agreement",
            "status": "passed"
            if (trip_summary_rides == total_clean and station_summary_rides == total_clean)
            else "failed",
            "details": (
                f"cleaning_log clean_rows ({total_clean:,}) == "
                f"trip_summary rides ({trip_summary_rides:,}) == "
                f"station_summary rides ({station_summary_rides:,})."
            ),
        },
    ]

    failed = [c for c in checks if c["status"] == "failed"]
    if failed:
        raise AssertionError(f"Validation checks failed: {failed}")

    return {
        "pipeline_mode": "full_12_months" if is_full_year else "partial_backfill",
        "months_processed": months_processed,
        "overall_status": "passed",
        "totals": {
            "source_rows": total_source,
            "clean_rows": total_clean,
            "removed_rows": total_removed,
            "retention_rate_percent": round(100.0 * total_clean / total_source, 2) if total_source else 0.0,
            "distinct_year_months": distinct_year_months,
            "distinct_ride_dates": distinct_ride_dates,
            "missing_start_coords_kept": total_missing_start_coords,
            "missing_end_coords_kept": total_missing_end_coords,
            "out_of_range_coords_kept": total_out_of_range_coords,
        },
        "checks": checks,
        "monthly_reconciliation": validation_metrics,
    }


def run_preparation(
    months: list[str] | None = None,
    force_download: bool = False,
    write_csv: bool = False,
) -> dict:
    """Execute Extract, Transform, Partitioned Parquet write, and Validation."""
    target_months = months if months else ALL_MONTHS
    invalid_months = [m for m in target_months if m not in ALL_MONTHS]
    if invalid_months:
        raise ValueError(f"Unsupported month(s) {invalid_months}. Valid months: {ALL_MONTHS}")

    for folder in [RAW, CLEAN, PARQUET_DIR, OUTPUT]:
        folder.mkdir(parents=True, exist_ok=True)

    source_details = pd.read_csv(MANIFEST_PATH).set_index("file")
    seen_ids: set[str] = set()
    quality_rows, manifest_rows, summaries, stations, sensitivity = [], [], [], [], []
    validation_metrics = []
    durations: dict[str, list[np.ndarray]] = {"Casual": [], "Member": []}

    for month in target_months:
        archive_name = f"{month}-divvy-tripdata.zip"
        manifest_row = source_details.loc[archive_name]
        archive_path, file_hash = extract_month_archive(
            month=month,
            manifest_row=manifest_row,
            raw_dir=RAW,
            force_download=force_download,
        )

        month_result = transform_month_archive(
            month=month,
            archive_path=archive_path,
            seen_ids=seen_ids,
            parquet_dir=PARQUET_DIR,
            clean_csv_dir=CLEAN,
            write_csv=write_csv,
        )
        log = month_result["log"]
        if int(log["source_rows"]) != int(manifest_row["source_rows"]):
            raise ValueError(
                f"Source row count mismatch for {archive_name}: "
                f"expected {manifest_row['source_rows']}, got {log['source_rows']}"
            )

        quality_rows.append({col: log[col] for col in CLEANING_LOG_COLUMNS})
        validation_metrics.append(month_result["validation_metric"])
        summaries.append(month_result["trip_summary"])
        stations.append(month_result["station_summary"])
        sensitivity.extend(month_result["sensitivity"])
        for rider, arr in month_result["durations"].items():
            durations[rider].append(arr)

        manifest_rows.append(
            {
                "month": month,
                "file": archive_name,
                "source_url": f"https://divvy-tripdata.s3.amazonaws.com/{archive_name}",
                "downloaded_on": manifest_row["downloaded_on"],
                "download_method": manifest_row["download_method"],
                "size_bytes": archive_path.stat().st_size,
                "sha256": file_hash,
                "source_rows": log["source_rows"],
            }
        )

    keys = [
        "ride_date",
        "year_month",
        "weekday",
        "weekday_order",
        "day_type",
        "hour",
        "rider_type",
        "rideable_type",
    ]
    summary = (
        pd.concat(summaries, ignore_index=True)
        .groupby(keys, observed=True)[["ride_count", "duration_minutes"]]
        .sum()
        .reset_index()
    )
    station_keys = ["year_month", "rider_type", "rideable_type", "start_station_name"]
    station_summary = (
        pd.concat(stations, ignore_index=True)
        .groupby(station_keys)["ride_count"]
        .sum()
        .reset_index()
    )
    quality = pd.DataFrame(quality_rows)[CLEANING_LOG_COLUMNS]

    validation_report = build_validation_report(
        months_processed=target_months,
        quality_df=quality,
        validation_metrics=validation_metrics,
        summary_df=summary,
        station_df=station_summary,
    )

    is_full_year = sorted(target_months) == sorted(ALL_MONTHS)
    if is_full_year:
        summary.to_csv(OUTPUT / "trip_summary.csv", index=False)
        station_summary.to_csv(OUTPUT / "station_summary.csv", index=False)
        quality.to_csv(OUTPUT / "cleaning_log.csv", index=False)
        pd.DataFrame(manifest_rows).to_csv(MANIFEST_PATH, index=False)

        checks = (
            pd.concat(sensitivity)
            .groupby(["duration_rule", "rider_type"])
            .sum(numeric_only=True)
            .reset_index()
        )
        checks["average_minutes"] = checks["duration_minutes"] / checks["ride_count"]
        checks["weekend_share"] = checks["weekend_rides"] / checks["ride_count"]
        checks.to_csv(OUTPUT / "duration_sensitivity.csv", index=False)

        stats = []
        for rider in ["Casual", "Member"]:
            values = np.concatenate(durations[rider])
            stats.append(
                {
                    "rider_type": rider,
                    "ride_count": len(values),
                    "average_minutes": values.mean(),
                    "median_minutes": np.median(values),
                    "maximum_minutes": values.max(),
                }
            )
        pd.DataFrame(stats).to_csv(OUTPUT / "rider_summary.csv", index=False)
        (OUTPUT / "validation_report.json").write_text(
            json.dumps(validation_report, indent=2), encoding="utf-8"
        )
        logger.info(
            "[Prepare] Full 12-month preparation complete: %s clean rides exported.",
            f"{summary['ride_count'].sum():,}",
        )
    else:
        logger.info(
            "[Prepare] Backfill complete for %s: %s clean rides partitioned into %s.",
            ", ".join(target_months),
            f"{summary['ride_count'].sum():,}",
            PARQUET_DIR.relative_to(ROOT),
        )

    return validation_report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download, verify, and clean monthly Divvy trip archives."
    )
    parser.add_argument(
        "--months",
        nargs="+",
        default=None,
        help="Specific YYYYMM months to process (e.g. 202510 202511). Defaults to all 12 months.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all 12 study-period months (202510 through 202609).",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download archives from S3 even if local SHA-256 matches.",
    )
    parser.add_argument(
        "--write-csv",
        action="store_true",
        help="Also write uncompressed monthly CSVs to data/cleaned/ in addition to Partitioned Parquet.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = parse_args()
    selected_months = ALL_MONTHS if (args.all or not args.months) else args.months
    run_preparation(
        months=selected_months,
        force_download=args.force_download,
        write_csv=args.write_csv,
    )
