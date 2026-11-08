"""Load processed summaries into SQLite, verify SQL vs. pandas agreement, and save charts."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/processed"
REPORTS = ROOT / "reports"
CHARTS = REPORTS / "charts"
SQL_RESULTS = DATA / "sql_results"

COLORS = {"Casual": "#E07A32", "Member": "#087F8C"}
logger = logging.getLogger("cyclistic.analyze")


def summarize(trips: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Group trip summaries by the requested dimensions and compute weighted average duration."""
    result = (
        trips.groupby(columns, observed=True)[["ride_count", "duration_minutes"]]
        .sum()
        .reset_index()
    )
    result["average_minutes"] = result["duration_minutes"] / result["ride_count"]
    return result


def save_chart(
    table: pd.DataFrame,
    index: str,
    value: str,
    title: str,
    filename: str,
    ylabel: str,
    kind: str = "bar",
) -> None:
    """Render and export a standardized portfolio chart."""
    pivot = table.pivot(index=index, columns="rider_type", values=value).reindex(
        columns=["Casual", "Member"]
    )
    fig, ax = plt.subplots(figsize=(11, 5.5), layout="constrained")
    options = {"width": 0.72} if kind == "bar" else {"marker": "o"}
    pivot.plot(kind=kind, color=[COLORS[c] for c in pivot.columns], ax=ax, **options)
    ax.set_title(title, loc="left", fontsize=18, fontweight="bold", pad=18)
    ax.set_xlabel("")
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", alpha=0.18)
    ax.set_axisbelow(True)
    ax.tick_params(axis="x", rotation=0 if index == "hour" else 25, length=0)
    if value == "ride_count":
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x/1000:,.0f}K"))
    ax.legend(title=None, frameon=False)
    fig.savefig(CHARTS / filename, dpi=170, facecolor="white")
    plt.close(fig)


def run_analysis() -> dict:
    """Load summaries into SQLite, run SQL queries, reconcile results, and export charts."""
    CHARTS.mkdir(parents=True, exist_ok=True)
    SQL_RESULTS.mkdir(parents=True, exist_ok=True)

    trips = pd.read_csv(DATA / "trip_summary.csv")
    stations = pd.read_csv(DATA / "station_summary.csv")
    riders = pd.read_csv(DATA / "rider_summary.csv").set_index("rider_type")
    quality = pd.read_csv(DATA / "cleaning_log.csv")

    monthly = summarize(trips, ["year_month", "rider_type"])
    weekly = summarize(trips, ["weekday_order", "weekday", "rider_type"])
    hourly = summarize(trips, ["hour", "rider_type"])
    bikes = summarize(trips, ["rideable_type", "rider_type"])
    day_types = summarize(trips, ["day_type", "rider_type"])
    for name, table in [
        ("monthly", monthly),
        ("weekday", weekly),
        ("hourly", hourly),
        ("bike_type", bikes),
        ("day_type", day_types),
    ]:
        table.to_csv(DATA / f"{name}_summary.csv", index=False)

    riders["ride_share"] = riders["ride_count"] / riders["ride_count"].sum()
    riders["weekend_share"] = (
        day_types.loc[day_types["day_type"].eq("Weekend")].set_index("rider_type")[
            "ride_count"
        ]
        / riders["ride_count"]
    )
    riders["missing_start_station_share"] = (
        stations.loc[stations["start_station_name"].eq("Unknown station")]
        .groupby("rider_type")["ride_count"]
        .sum()
        / riders["ride_count"]
    )
    riders.to_csv(DATA / "rider_summary.csv")

    known_stations = stations.loc[stations["start_station_name"].ne("Unknown station")]
    top_stations = (
        known_stations.loc[known_stations["rider_type"].eq("Casual")]
        .groupby("start_station_name")["ride_count"]
        .sum()
        .nlargest(10)
    )
    top_stations.to_csv(DATA / "top_casual_stations.csv")

    # Small dimension tables keep both Power BI fact tables under the same filters
    pd.DataFrame({"year_month": sorted(trips["year_month"].unique())}).to_csv(
        DATA / "dim_month.csv", index=False
    )
    pd.DataFrame({"rider_type": ["Casual", "Member"]}).to_csv(
        DATA / "dim_rider.csv", index=False
    )
    pd.DataFrame({"rideable_type": sorted(trips["rideable_type"].unique())}).to_csv(
        DATA / "dim_bike.csv", index=False
    )

    db_path = DATA / "cyclistic.db"
    logger.info("[Load] Loading aggregated tables into SQLite (%s) ...", db_path.relative_to(ROOT))
    with sqlite3.connect(db_path) as connection:
        trips.to_sql("trip_summary", connection, if_exists="replace", index=False)
        stations.to_sql("station_summary", connection, if_exists="replace", index=False)
        quality.to_sql("cleaning_log", connection, if_exists="replace", index=False)
        riders.reset_index().to_sql("rider_summary", connection, if_exists="replace", index=False)

        sql_lines = (ROOT / "sql/analysis.sql").read_text(encoding="utf-8").splitlines()
        statements = "\n".join(
            line for line in sql_lines if not line.lstrip().startswith("--")
        ).split(";")
        executed_queries = 0
        for number, query in enumerate(statements, start=1):
            if "SELECT" not in query:
                continue
            result = pd.read_sql_query(query, connection)
            result.to_csv(SQL_RESULTS / f"sql_result_{number}.csv", index=False)
            executed_queries += 1

        sql_riders = pd.read_sql_query(
            "SELECT rider_type, SUM(ride_count) AS rides, "
            "SUM(duration_minutes)/SUM(ride_count) AS average_minutes "
            "FROM trip_summary GROUP BY rider_type",
            connection,
        ).set_index("rider_type")

    joined_months = pd.read_csv(SQL_RESULTS / "sql_result_6.csv")
    assert joined_months["rides"].sum() == trips["ride_count"].sum()
    assert joined_months["known_station_rides"].sum() == known_stations["ride_count"].sum()
    assert sql_riders["rides"].eq(riders["ride_count"]).all()
    assert np.allclose(sql_riders["average_minutes"], riders["average_minutes"])
    assert np.isclose(
        trips["duration_minutes"].sum(),
        (riders["average_minutes"] * riders["ride_count"]).sum(),
    )
    assert trips["ride_date"].nunique() == 365
    logger.info(
        "[Validate] SQLite (%d queries) and pandas agree on %s clean rides across 365 dates.",
        executed_queries,
        f"{int(riders['ride_count'].sum()):,}",
    )

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.spines.left": False,
            "axes.edgecolor": "#CBD5E1",
            "axes.labelcolor": "#334155",
            "text.color": "#172B4D",
        }
    )

    save_chart(
        monthly,
        "year_month",
        "ride_count",
        "Ride demand rises in warmer months",
        "monthly_rides.png",
        "Rides",
        "line",
    )
    save_chart(
        weekly.assign(
            weekday=pd.Categorical(
                weekly["weekday"],
                categories=[
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                    "Saturday",
                    "Sunday",
                ],
                ordered=True,
            )
        ),
        "weekday",
        "ride_count",
        "Weekly riding patterns differ by rider type",
        "weekday_rides.png",
        "Rides",
    )
    save_chart(
        hourly,
        "hour",
        "ride_count",
        "Compare when each rider group travels",
        "hourly_rides.png",
        "Rides",
        "line",
    )
    save_chart(
        riders.reset_index().assign(group="Average ride"),
        "group",
        "average_minutes",
        "Casual trips last longer on average",
        "ride_duration.png",
        "Minutes",
    )
    save_chart(
        bikes,
        "rideable_type",
        "ride_count",
        "Bike use by rider type",
        "bike_types.png",
        "Rides",
    )

    metrics = {
        "source_rides": int(quality["source_rows"].sum()),
        "clean_rides": int(riders["ride_count"].sum()),
        "removed_rides": int(quality["source_rows"].sum() - quality["clean_rows"].sum()),
        "period_start": "2025-10-01",
        "period_end": "2026-09-30",
        "riders": riders.reset_index().to_dict("records"),
        "peak_month": monthly.groupby("year_month")["ride_count"].sum().idxmax(),
        "top_casual_station": top_stations.index[0],
    }
    (REPORTS / "metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )

    validation_report_path = DATA / "validation_report.json"
    if validation_report_path.exists():
        val_report = json.loads(validation_report_path.read_text(encoding="utf-8"))
        sql_check = {
            "check": "sqlite_vs_pandas_reconciliation",
            "status": "passed",
            "details": (
                f"SQLite cyclistic.db loaded ({executed_queries} queries in sql/analysis.sql); "
                f"SQLite and pandas match on {metrics['clean_rides']:,} rides and weighted durations."
            ),
        }
        val_report["checks"] = [
            c for c in val_report.get("checks", []) if c["check"] != "sqlite_vs_pandas_reconciliation"
        ] + [sql_check]
        validation_report_path.write_text(
            json.dumps(val_report, indent=2), encoding="utf-8"
        )

    print(riders.round(4).to_string())
    print(
        f"SQL and pandas agree. Coverage: 365 dates, 12 months, {metrics['clean_rides']:,} clean rides."
    )
    return metrics


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    run_analysis()
