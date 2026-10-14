-- Tables contain grouped trips. SUM(ride_count) counts rides.

-- 1. Rider comparison
SELECT rider_type, SUM(ride_count) AS rides,
       ROUND(SUM(duration_minutes) / SUM(ride_count), 2) AS average_minutes,
       ROUND(100.0 * SUM(CASE WHEN day_type = 'Weekend' THEN ride_count ELSE 0 END)
             / SUM(ride_count), 2) AS weekend_percent
FROM trip_summary
GROUP BY rider_type;

-- 2. Monthly demand
SELECT year_month, rider_type, SUM(ride_count) AS rides
FROM trip_summary
GROUP BY year_month, rider_type
ORDER BY year_month, rider_type;

-- 3. Weekday use
SELECT weekday_order, weekday, rider_type, SUM(ride_count) AS rides,
       ROUND(SUM(duration_minutes) / SUM(ride_count), 2) AS average_minutes
FROM trip_summary
GROUP BY weekday_order, weekday, rider_type
ORDER BY weekday_order, rider_type;

-- 4. Hourly use
SELECT hour, rider_type, SUM(ride_count) AS rides
FROM trip_summary
GROUP BY hour, rider_type
ORDER BY hour, rider_type;

-- 5. Top 10 recorded start stations for casual trips
SELECT start_station_name, SUM(ride_count) AS rides
FROM station_summary
WHERE rider_type = 'Casual' AND start_station_name <> 'Unknown station'
GROUP BY start_station_name
ORDER BY rides DESC, start_station_name
LIMIT 10;

-- 6. Join monthly trip totals with recorded station coverage
WITH monthly_trips AS (
    SELECT year_month, rider_type, SUM(ride_count) AS rides
    FROM trip_summary
    GROUP BY year_month, rider_type
), known_starts AS (
    SELECT year_month, rider_type, SUM(ride_count) AS known_station_rides
    FROM station_summary
    WHERE start_station_name <> 'Unknown station'
    GROUP BY year_month, rider_type
)
SELECT t.year_month, t.rider_type, t.rides,
       COALESCE(s.known_station_rides, 0) AS known_station_rides,
       ROUND(100.0 * COALESCE(s.known_station_rides, 0) / t.rides, 2) AS known_station_percent
FROM monthly_trips AS t
LEFT JOIN known_starts AS s
    ON t.year_month = s.year_month AND t.rider_type = s.rider_type
ORDER BY t.year_month, t.rider_type;

-- 7. Month-to-month ride changes; the first month has no comparison
WITH monthly_rides AS (
    SELECT year_month, SUM(ride_count) AS rides
    FROM trip_summary
    GROUP BY year_month
), previous_month AS (
    SELECT year_month, rides,
           LAG(rides) OVER (ORDER BY year_month) AS previous_rides
    FROM monthly_rides
)
SELECT year_month, rides, previous_rides,
       ROUND(100.0 * (rides - previous_rides) / NULLIF(previous_rides, 0), 2) AS change_percent
FROM previous_month
ORDER BY year_month;
