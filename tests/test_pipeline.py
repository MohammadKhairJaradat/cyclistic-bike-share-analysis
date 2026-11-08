"""Light unit and reconciliation tests for the Cyclistic ETL pipeline."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def _load_prepare_module():
    spec = importlib.util.spec_from_file_location(
        "prepare_data", ROOT / "src/01_prepare_data.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare_mod = _load_prepare_module()


def test_source_manifest_integrity():
    """Verify source_manifest.csv covers all 12 months with SHA-256 digests and 6,171,785 rows."""
    manifest = pd.read_csv(ROOT / "data/source_manifest.csv")
    assert len(manifest) == 12
    assert manifest["month"].astype(str).tolist() == prepare_mod.ALL_MONTHS
    assert int(manifest["source_rows"].sum()) == 6_171_785
    assert manifest["sha256"].str.fullmatch(r"[0-9a-f]{64}").all()
    assert (manifest["size_bytes"] > 0).all()


def test_transform_month_archive_and_parquet(tmp_path: Path):
    """Test sequential cleaning rules, coordinate bounds check, and Partitioned Parquet output."""
    sample_rows = pd.DataFrame(
        [
            # Valid member trip (10 min, valid Chicago coords)
            {
                "ride_id": "R1",
                "rideable_type": "classic_bike",
                "started_at": "2025-10-15 08:00:00",
                "ended_at": "2025-10-15 08:10:00",
                "start_station_name": "Wells St & Polk St",
                "end_station_name": "Clark St & Lake St",
                "member_casual": "member",
                "start_lat": 41.8727,
                "start_lng": -87.6335,
                "end_lat": 41.8860,
                "end_lng": -87.6308,
            },
            # Valid casual trip with missing start station and out-of-bounds start coordinate
            {
                "ride_id": "R2",
                "rideable_type": "electric_bike",
                "started_at": "2025-10-18 14:00:00",
                "ended_at": "2025-10-18 14:25:00",
                "start_station_name": "",
                "end_station_name": "Navy Pier",
                "member_casual": "casual",
                "start_lat": 45.0000,
                "start_lng": -87.6335,
                "end_lat": 41.8922,
                "end_lng": -87.6120,
            },
            # Duplicate ride_id (should be removed)
            {
                "ride_id": "R1",
                "rideable_type": "classic_bike",
                "started_at": "2025-10-15 09:00:00",
                "ended_at": "2025-10-15 09:15:00",
                "start_station_name": "Wells St & Polk St",
                "end_station_name": "Clark St & Lake St",
                "member_casual": "member",
                "start_lat": 41.8727,
                "start_lng": -87.6335,
                "end_lat": 41.8860,
                "end_lng": -87.6308,
            },
            # Under 1 minute (should be removed)
            {
                "ride_id": "R3",
                "rideable_type": "classic_bike",
                "started_at": "2025-10-15 10:00:00",
                "ended_at": "2025-10-15 10:00:20",
                "start_station_name": "Wells St & Polk St",
                "end_station_name": "Wells St & Polk St",
                "member_casual": "member",
                "start_lat": 41.8727,
                "start_lng": -87.6335,
                "end_lat": 41.8727,
                "end_lng": -87.6335,
            },
            # Over 24 hours (should be removed)
            {
                "ride_id": "R4",
                "rideable_type": "docked_bike",
                "started_at": "2025-10-15 10:00:00",
                "ended_at": "2025-10-17 12:00:00",
                "start_station_name": "Navy Pier",
                "end_station_name": "Navy Pier",
                "member_casual": "casual",
                "start_lat": 41.8922,
                "start_lng": -87.6120,
                "end_lat": 41.8922,
                "end_lng": -87.6120,
            },
        ]
    )

    archive_path = tmp_path / "202510-divvy-tripdata.zip"
    with ZipFile(archive_path, "w") as zf:
        zf.writestr("202510-divvy-tripdata.csv", sample_rows.to_csv(index=False))

    parquet_dir = tmp_path / "cleaned_parquet"
    result = prepare_mod.transform_month_archive(
        month="202510",
        archive_path=archive_path,
        seen_ids=set(),
        parquet_dir=parquet_dir,
        write_csv=False,
    )

    log = result["log"]
    val = result["validation_metric"]
    assert log["source_rows"] == 5
    assert log["duplicate_ride_id"] == 1
    assert log["under_one_minute"] == 1
    assert log["over_24_hours"] == 1
    assert log["clean_rows"] == 2
    assert val["row_count_reconciled"] is True
    assert val["out_of_range_durations_retained"] == 0
    assert val["out_of_range_start_coords_kept"] == 1

    parquet_file = parquet_dir / "month=202510" / "trips.parquet"
    assert parquet_file.exists()
    df_parquet = pd.read_parquet(parquet_file)
    assert len(df_parquet) == 2
    assert set(df_parquet["ride_id"]) == {"R1", "R2"}
    assert (
        df_parquet.loc[df_parquet["ride_id"] == "R2", "start_station_name"].iloc[0]
        == "Unknown station"
    )


def test_reconciled_outputs_and_validation_report():
    """Verify full-year processed outputs and validation_report.json agree on 6,007,154 clean trips."""
    processed = ROOT / "data/processed"
    cleaning_log = pd.read_csv(processed / "cleaning_log.csv")
    trip_summary = pd.read_csv(processed / "trip_summary.csv")
    station_summary = pd.read_csv(processed / "station_summary.csv")
    report = json.loads(
        (processed / "validation_report.json").read_text(encoding="utf-8")
    )

    assert int(cleaning_log["source_rows"].sum()) == 6_171_785
    assert int(cleaning_log["clean_rows"].sum()) == 6_007_154
    assert int(trip_summary["ride_count"].sum()) == 6_007_154
    assert int(station_summary["ride_count"].sum()) == 6_007_154
    assert trip_summary["year_month"].nunique() == 12
    assert trip_summary["ride_date"].nunique() == 365
    assert report["overall_status"] == "passed"
    assert all(c["status"] in ("passed", "warning") for c in report["checks"])
