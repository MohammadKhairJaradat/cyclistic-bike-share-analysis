"""Clean the 12 monthly files and export small tables for analysis and Power BI."""

from pathlib import Path
from zipfile import ZipFile
import hashlib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw"
CLEAN = ROOT / "data/cleaned"
OUTPUT = ROOT / "data/processed"
MONTHS = ["202510", "202511", "202512"] + [f"2026{m:02}" for m in range(1, 10)]
COLUMNS = ["ride_id", "rideable_type", "started_at", "ended_at",
           "start_station_name", "end_station_name", "member_casual"]
source_details = pd.read_csv(ROOT / "data/source_manifest.csv").set_index("file")

for folder in [RAW, CLEAN, OUTPUT]:
    folder.mkdir(parents=True, exist_ok=True)
missing_files = [m for m in MONTHS if not (RAW / f"{m}-divvy-tripdata.zip").exists()]
if missing_files:
    raise FileNotFoundError(f"Download these months first: {missing_files}")

seen_ids = set()
quality_rows, manifest, summaries, stations, sensitivity = [], [], [], [], []
durations = {"Casual": [], "Member": []}

for month in MONTHS:
    archive = RAW / f"{month}-divvy-tripdata.zip"
    with archive.open("rb") as stream:
        file_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    if file_hash != source_details.loc[archive.name, "sha256"]:
        raise ValueError(f"Source file does not match the manifest: {archive.name}")
    with ZipFile(archive) as zip_file:
        if zip_file.testzip() is not None:
            raise ValueError(f"Damaged ZIP: {archive.name}")
        csv_files = [n for n in zip_file.namelist()
                     if n.endswith(".csv") and not n.startswith("__MACOSX/")]
        if len(csv_files) != 1:
            raise ValueError(f"Expected one CSV in {archive.name}")
        with zip_file.open(csv_files[0]) as csv_file:
            data = pd.read_csv(csv_file, usecols=COLUMNS, dtype="string")
    log = {"month": month, "source_rows": len(data)}
    for col in ["ride_id", "rideable_type", "member_casual",
                "start_station_name", "end_station_name"]:
        data[col] = data[col].str.strip().replace("", pd.NA)

    # Removal counts are sequential, so each removed row has one reason.
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
    bad = ~data["started_at"].between(pd.Timestamp("2025-10-01"), pd.Timestamp("2026-10-01"), inclusive="left")
    log["outside_analysis_period"] = int(bad.sum())
    data = data.loc[~bad].copy()
    data["ride_length_minutes"] = (data["ended_at"] - data["started_at"]).dt.total_seconds() / 60
    data["rider_type"] = data["member_casual"].str.title()
    data["weekday_order"] = data["started_at"].dt.dayofweek + 1
    data["day_type"] = np.where(data["weekday_order"] >= 6, "Weekend", "Weekday")

    # Check whether duration filters change the comparison between rider groups.
    for label, mask in {
        "Positive duration": data["ride_length_minutes"] > 0,
        "1 to 1440 minutes": data["ride_length_minutes"].between(1, 1440),
        "1 to 60 minutes": data["ride_length_minutes"].between(1, 60),
    }.items():
        subset = data.loc[mask]
        result = subset.groupby("rider_type").agg(
            ride_count=("ride_id", "size"), duration_minutes=("ride_length_minutes", "sum"))
        result["weekend_rides"] = subset.loc[subset["day_type"].eq("Weekend")].groupby("rider_type").size()
        result["duration_rule"] = label
        sensitivity.append(result.reset_index().fillna(0))

    bad = data["ride_length_minutes"] < 1
    log["under_one_minute"] = int(bad.sum())
    data = data.loc[~bad].copy()
    bad = data["ride_length_minutes"] > 1440
    log["over_24_hours"] = int(bad.sum())
    data = data.loc[~bad].copy()
    log["clean_rows"] = len(data)
    log["outside_source_month_kept"] = int(data["started_at"].dt.strftime("%Y%m").ne(month).sum())
    log["missing_start_station_kept"] = int(data["start_station_name"].isna().sum())
    log["missing_end_station_kept"] = int(data["end_station_name"].isna().sum())
    for col in ["start_station_name", "end_station_name"]:
        data[col] = data[col].fillna("Unknown station")
    data["ride_date"] = data["started_at"].dt.strftime("%Y-%m-%d")
    data["year_month"] = data["started_at"].dt.strftime("%Y-%m")
    data["weekday"] = data["started_at"].dt.day_name()
    data["day_of_week"] = data["started_at"].dt.dayofweek.add(1).mod(7).add(1)
    data["hour"] = data["started_at"].dt.hour
    data.to_csv(CLEAN / f"{month}-cleaned.csv", index=False)
    keys = ["ride_date", "year_month", "weekday", "weekday_order", "day_type",
            "hour", "rider_type", "rideable_type"]
    summaries.append(data.groupby(keys, observed=True).agg(
        ride_count=("ride_id", "size"),
        duration_minutes=("ride_length_minutes", "sum")).reset_index())
    stations.append(data.groupby(["year_month", "rider_type", "rideable_type", "start_station_name"]).agg(
        ride_count=("ride_id", "size")).reset_index())
    for rider, group in data.groupby("rider_type"):
        durations[rider].append(group["ride_length_minutes"].to_numpy())
    quality_rows.append(log)
    manifest.append({"month": month, "file": archive.name,
                     "source_url": f"https://divvy-tripdata.s3.amazonaws.com/{archive.name}",
                     "downloaded_on": source_details.loc[archive.name, "downloaded_on"],
                     "download_method": source_details.loc[archive.name, "download_method"],
                     "size_bytes": archive.stat().st_size,
                     "sha256": file_hash,
                     "source_rows": log["source_rows"]})
    print(f"{month}: {log['source_rows']:,} source rows -> {len(data):,} clean rows", flush=True)

summary = pd.concat(summaries, ignore_index=True).groupby(keys, observed=True)[["ride_count", "duration_minutes"]].sum().reset_index()
station_keys = ["year_month", "rider_type", "rideable_type", "start_station_name"]
station_summary = pd.concat(stations, ignore_index=True).groupby(station_keys)["ride_count"].sum().reset_index()
quality = pd.DataFrame(quality_rows)
rules = ["missing_ride_id", "duplicate_ride_id", "invalid_rider_type", "invalid_bike_type",
         "invalid_timestamp", "outside_analysis_period", "under_one_minute", "over_24_hours"]
assert quality["source_rows"].sum() == quality["clean_rows"].sum() + quality[rules].sum().sum()
assert summary["ride_count"].sum() == quality["clean_rows"].sum()
assert station_summary["ride_count"].sum() == summary["ride_count"].sum()
assert summary["year_month"].nunique() == 12
summary.to_csv(OUTPUT / "trip_summary.csv", index=False)
station_summary.to_csv(OUTPUT / "station_summary.csv", index=False)
quality.to_csv(OUTPUT / "cleaning_log.csv", index=False)
pd.DataFrame(manifest).to_csv(ROOT / "data/source_manifest.csv", index=False)
checks = pd.concat(sensitivity).groupby(["duration_rule", "rider_type"]).sum(numeric_only=True).reset_index()
checks["average_minutes"] = checks["duration_minutes"] / checks["ride_count"]
checks["weekend_share"] = checks["weekend_rides"] / checks["ride_count"]
checks.to_csv(OUTPUT / "duration_sensitivity.csv", index=False)
stats = []
for rider in ["Casual", "Member"]:
    values = np.concatenate(durations[rider])
    stats.append({"rider_type": rider, "ride_count": len(values),
                  "average_minutes": values.mean(), "median_minutes": np.median(values),
                  "maximum_minutes": values.max()})
pd.DataFrame(stats).to_csv(OUTPUT / "rider_summary.csv", index=False)
print(f"Preparation complete: {summary['ride_count'].sum():,} rides.")
