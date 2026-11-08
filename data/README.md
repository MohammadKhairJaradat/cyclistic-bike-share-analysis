# Data Files & Storage Layers

Source: [public Divvy trip data](https://divvybikes.com/system-data) (`https://divvy-tripdata.s3.amazonaws.com/`). Study period: October 2025 – September 2026 (`6,171,785` raw rows → `6,007,154` cleaned rows).

## 1. Raw Archive Cache (`data/raw/` — Git-ignored)

`raw/` stores the 12 monthly `.zip` archives (`202510-divvy-tripdata.zip` through `202609-divvy-tripdata.zip`, `225.7 MB` total).
- [`source_manifest.csv`](source_manifest.csv) tracks each file's `source_url`, download date, `size_bytes`, `sha256` checksum, and `source_rows`.
- `src/01_prepare_data.py` verifies each archive's SHA-256 digest before processing, skips already-verified local files on reruns, and downloads missing archives directly from the Divvy S3 bucket.
- CSV streams are read directly from each `.zip` archive one month at a time without extracting uncompressed raw copies to disk.

## 2. Partitioned Parquet Store (`data/cleaned_parquet/` — Git-ignored)

During transformation, each cleaned monthly chunk is written to a Hive-style Partitioned Parquet layout using PyArrow (`snappy` compression):

```text
data/cleaned_parquet/
├── month=202510/trips.parquet
├── month=202511/trips.parquet
...
└── month=202609/trips.parquet
```

- Covers all `6,007,154` cleaned trips across 12 partitions (`263.1 MB` total on disk).
- Passing `--write-csv` to `run_pipeline.py` also exports uncompressed monthly CSVs to `data/cleaned/{YYYYMM}-cleaned.csv` (excluded from Git).

## 3. Processed Analytical Tables & Validation (`data/processed/` — Tracked in Git)

| File | Description |
|---|---|
| `validation_report.json` | Automated ETL validation report (schema check, SHA-256 verification, per-month row reconciliation, duration/coordinate bounds, 12-month/365-day coverage, and SQLite vs. pandas agreement) |
| `cleaning_log.csv` | Source row count, 8 sequential removal rule counts, and retained row count for each source month |
| `trip_summary.csv` | Grouped fact table by `ride_date`, `year_month`, `weekday`, `weekday_order`, `day_type`, `hour`, `rider_type`, and `rideable_type` (`34,512` rows) |
| `station_summary.csv` | Grouped fact table by `year_month`, `rider_type`, `rideable_type`, and `start_station_name` (`50,977` rows) |
| `rider_summary.csv` | Trip-level count, mean, median, max duration, weekend share, and missing-station share by rider group |
| `duration_sensitivity.csv` | Rider group comparison under three duration filter rules (`Positive duration`, `1 to 1440 minutes`, `1 to 60 minutes`) |
| `monthly_summary.csv`, `weekday_summary.csv`, `hourly_summary.csv`, `bike_type_summary.csv`, `day_type_summary.csv` | Pre-aggregated dimensional summaries |
| `top_casual_stations.csv` | Top 10 recorded start stations for casual riders |
| `dim_month.csv`, `dim_rider.csv`, `dim_bike.csv` | Shared dimension tables for Power BI star-schema filtering |
| `sql_results/sql_result_1.csv` .. `sql_result_7.csv` | Query outputs exported from executing `sql/analysis.sql` against `cyclistic.db` |

### Main Fields

- `ride_count`: number of trips represented by a grouped row. Sum it to count rides (`6,007,154` total).
- `duration_minutes`: sum of trip durations in the row. Divide `SUM(duration_minutes)` by `SUM(ride_count)` for a weighted mean.
- `rider_type`: `Casual` or `Member`, from the source's `member_casual` label.
- `rideable_type`: `classic_bike`, `electric_bike`, or `docked_bike`.
- `ride_date`, `year_month`, `hour`: start time fields, used as supplied.
- `weekday_order`: Monday = 1 through Sunday = 7 for sorting.
- `day_type`: Saturday and Sunday are `Weekend`; other days are `Weekday`.
- `start_station_name`: recorded station name, or `Unknown station` when missing.

## 4. Local SQLite Database (`data/processed/cyclistic.db` — Git-ignored)

`cyclistic.db` is rebuilt automatically by `src/02_analyze_data.py` (or `python run_pipeline.py --analyze-only`). It loads `trip_summary`, `station_summary`, `cleaning_log`, and `rider_summary`, executes all seven queries in [`sql/analysis.sql`](../sql/analysis.sql), and verifies that SQL results match pandas aggregations.
