# Data Files

Source: [public Divvy trip data](https://divvybikes.com/system-data). Study period: October 2025-September 2026.

## Local originals

`raw/` contains the 12 unchanged browser-downloaded ZIPs. The preparation script reads CSV data directly from each ZIP, without creating duplicate extracted files. `source_manifest.csv` records each source URL, download date, size, hash, and source row count. Raw data is excluded from Git; download it from the official archive to rerun preparation.

`cleaned/` contains one cleaned trip-level CSV per source month. These large files are also excluded from Git. A valid trip is assigned to its actual start date, rather than the source filename.

## Analysis tables

| File | Row meaning |
|---|---|
| `trip_summary.csv` | Date, hour, rider group, and bike type |
| `station_summary.csv` | Month, rider group, bike type, and recorded start-station name |
| `rider_summary.csv` | One rider group, with trip-level mean and median duration |
| `cleaning_log.csv` | Source and cleaning counts for one source month |
| `duration_sensitivity.csv` | Rider group under one duration rule |
| `monthly_summary.csv` | Month and rider group |
| `weekday_summary.csv` | Weekday and rider group |
| `hourly_summary.csv` | Start hour and rider group |
| `bike_type_summary.csv` | Bike type and rider group |
| `day_type_summary.csv` | Weekday/weekend and rider group |
| `dim_month.csv`, `dim_rider.csv`, `dim_bike.csv` | Shared Power BI filter values |

The tables are supporting outputs of this educational analysis, not a stand-alone distribution of the original trip dataset. Their use remains subject to the source data terms.

## Main fields

- `ride_count`: number of trips represented by a grouped row. Sum it to count rides.
- `duration_minutes`: sum of trip durations in the row. Divide its sum by the sum of `ride_count` for a weighted mean.
- `rider_type`: `Casual` or `Member`, from the source's `member_casual` label.
- `rideable_type`: the source bike category, such as `classic_bike` or `electric_bike`.
- `ride_date`, `year_month`, `hour`: start time fields, used as supplied.
- `weekday_order`: Monday = 1 through Sunday = 7 for sorting.
- `day_type`: Saturday and Sunday are `Weekend`; other days are `Weekday`.
- `start_station_name`: a recorded name, or `Unknown station` when missing.

`processed/cyclistic.db` is a local SQLite copy of the two grouped fact tables. It is excluded from Git and rebuilt by the analysis script. `processed/sql_results/` holds `sql_result_1.csv` through `sql_result_7.csv`, the outputs of `sql/analysis.sql`. The last two queries demonstrate a `LEFT JOIN` with CTEs and a `LAG` window function.
