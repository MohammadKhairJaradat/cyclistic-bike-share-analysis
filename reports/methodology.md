# Methodology

## Ask

Compare casual and member bike use to support membership campaign planning. The audience is Cyclistic's marketing director and executive team in the course scenario.

## Prepare

Use the 12 monthly public files for October 2025-September 2026. Each row is a trip. Downloads were made through a browser on 13 October 2026. File URLs, sizes, SHA-256 hashes, and row counts are recorded in `data/source_manifest.csv`.

The source is relevant and provided by the bike-share operator. It covers recorded service use, not all travel in Chicago. It cannot identify customers, tourists, residents, purchase motives, or membership conversion. The course scenario's profitability claim is background, not a measured result.

## Process

1. Verify ZIP integrity and preserve each original CSV.
2. Trim text fields and check ride IDs, rider types, bike types, and timestamps.
3. Remove repeated ride IDs across all files, retaining the first source record.
4. Keep trips starting within the analysis period. A trip is assigned to its start date, even if it ends the next day or appears in a different monthly source file.
5. Calculate ride length in minutes. Exclude durations below 1 minute and above 24 hours. This removes false starts, non-positive durations, and extreme durations from the primary comparison. The 24-hour cutoff is an analytical choice, not a confirmed label for an invalid trip.
6. Derive date, month, weekday, hour, and weekday/weekend fields. `day_of_week` follows the packet: Sunday = 1. `weekday_order` uses Monday = 1 for chart sorting.
7. Keep missing station names as `Unknown station`. Exclude only this label from station rankings.

Timestamps are used as supplied; no timezone conversion is applied. Only fields needed for this analysis are retained in cleaned files. Coordinates are not used. No missing stations or durations are estimated.

### Cleaning totals

- Source rows: **6,171,785**.
- Retained rows: **6,007,154 (97.33%)**.
- Removed rows: **164,631**.

| Removal rule | Rows |
|---|---:|
| Missing ride ID | 0 |
| Repeated ride ID | 35 |
| Invalid rider type | 0 |
| Invalid bike type | 0 |
| Invalid timestamp | 0 |
| Start outside the analysis period | 71 |
| Duration below 1 minute | 159,221 |
| Duration above 24 hours | 5,304 |

Removal counts are sequential and do not overlap. Monthly counts are in `data/processed/cleaning_log.csv`. Trips that start in a different source month are flagged separately; valid trips within the study period are retained.

Missing start-station names affect **20.5%** of casual trips and **19.8%** of member trips. Station findings cover only named starts and can be affected by this missingness. Station labels are grouped by their supplied names; renaming and aliases may split one location across labels.

## Analyze

Compare volume, mean and median ride length, monthly demand, weekdays, hours, bike types, and recorded start stations. Counts are trips, not customers. Hourly charts also use within-group shares to compare groups of different sizes. Weekdays and weekends contain different numbers of calendar dates; their total volumes should not be interpreted as equal-duration exposure.

Power BI and SQL use grouped trip tables. Monthly summaries are combined by their final table keys, including valid trips that appear outside their source month. Total rides = sum of `ride_count`. Mean duration = sum of `duration_minutes` / sum of `ride_count`. Group means are never averaged without weights. Medians are calculated from the cleaned trip-level durations in Python; they are not treated as additive dashboard measures.

### Duration sensitivity

| Rule | Rider | Rides | Average minutes | Weekend share |
|---|---|---:|---:|---:|
| 1 to 1440 minutes | Casual | 2,110,304 | 18.17 | 37.2% |
| 1 to 1440 minutes | Member | 3,896,850 | 12.17 | 23.3% |
| 1 to 60 minutes | Casual | 2,028,158 | 14.20 | 36.8% |
| 1 to 60 minutes | Member | 3,871,268 | 11.27 | 23.2% |
| Positive duration | Casual | 2,188,968 | 20.40 | 37.3% |
| Positive duration | Member | 3,982,682 | 12.34 | 23.3% |

Casual trips remain longer on average and more concentrated on weekends under all three duration rules. Positive-duration results are a sensitivity check, not the primary dashboard population. Medians help show the effect of the long-duration tail.

## Share and Act

Use the Power BI report and chart exports to present the comparison. The three recommendations are pilot proposals. Evaluate them with membership sign-ups, incremental conversion against a control group, and cost per incremental conversion, using consented customer and campaign data.

## Validation

Both scripts fail if their checks do not pass. Source rows reconcile to retained rows plus removals. Trip and station summary counts agree. The period covers 12 months and 365 start dates. SQL rider counts and weighted averages agree with pandas. Total duration agrees with the trip-level rider statistics. A separate report check verifies visual fields, measures, dimensions, relationships, and JSON schemas.

## References

- Google Data Analytics Capstone, Case Study 1: the supplied eight-page packet and its Coursera lesson were checked on 13 October 2026.
- [Course lesson](https://www.coursera.org/learn/google-data-analytics-capstone/supplement/7PGIT/case-study-1-how-does-a-bike-share-navigate-speedy-success).
- [Divvy data](https://divvybikes.com/system-data) and [data license](https://divvybikes.com/data-license-agreement).
