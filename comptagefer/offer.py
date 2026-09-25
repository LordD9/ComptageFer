import csv
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def open_stops(database: Path) -> sqlite3.Connection:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS stop (
            stop_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            lat REAL,
            lon REAL,
            parent TEXT,
            is_area INTEGER NOT NULL
        )
        """
    )
    return connection


def import_stop_names(database: Path, stops_file: Path) -> int:
    stored = 0
    with open_stops(database) as connection, stops_file.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if not row.get("stop_name"):
                continue
            connection.execute(
                """
                INSERT OR REPLACE INTO stop
                    (stop_id, name, lat, lon, parent, is_area)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["stop_id"],
                    row["stop_name"],
                    float(row["stop_lat"]) if row.get("stop_lat") else None,
                    float(row["stop_lon"]) if row.get("stop_lon") else None,
                    row.get("parent_station") or None,
                    1 if row.get("location_type") == "1" else 0,
                ),
            )
            stored += 1
    return stored


def nearest_stops(database: Path, lat: float, lon: float, limit: int = 5) -> list[dict]:
    with open_stops(database) as connection:
        rows = connection.execute(
            "SELECT stop_id, name, lat, lon FROM stop WHERE is_area = 1 AND lat IS NOT NULL AND lon IS NOT NULL"
        ).fetchall()
    ranked = sorted(rows, key=lambda row: (row[2] - lat) ** 2 + (row[3] - lon) ** 2)
    return [{"stop_id": row[0], "name": row[1]} for row in ranked[:limit]]


def search_stops(database: Path, query: str, limit: int = 8) -> list[dict]:
    needle = query.strip()
    if len(needle) < 2:
        return []
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with open_stops(database) as connection:
        rows = connection.execute(
            """
            SELECT stop_id, name FROM stop
            WHERE is_area = 1 AND name LIKE ? ESCAPE '\\'
            ORDER BY name
            LIMIT ?
            """,
            (f"%{escaped}%", limit),
        ).fetchall()
    return [{"stop_id": row[0], "name": row[1]} for row in rows]


def _stop_family(stops_database: Path | None, stop_id: str) -> list[str]:
    if stops_database is None or not stops_database.exists():
        return [stop_id]
    with open_stops(stops_database) as connection:
        rows = connection.execute(
            "SELECT stop_id FROM stop WHERE stop_id = ? OR parent = ?",
            (stop_id, stop_id),
        ).fetchall()
    found = [row[0] for row in rows]
    return found or [stop_id]


def trips_serving(
    database: Path,
    origin_stop_id: str,
    destination_stop_id: str,
    at: datetime,
    window: timedelta = timedelta(hours=2),
    stops_database: Path | None = None,
) -> list[dict]:
    start = int((at - window).timestamp())
    end = int((at + window).timestamp())
    origins = _stop_family(stops_database, origin_stop_id)
    destinations = _stop_family(stops_database, destination_stop_id)
    origin_marks = ",".join("?" for _ in origins)
    destination_marks = ",".join("?" for _ in destinations)
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            f"""
            WITH latest AS (
                SELECT trip_id, MAX(fetched_at) AS fetched_at
                FROM trip_update
                GROUP BY trip_id
            )
            SELECT
                origin.trip_id,
                origin.departure_time,
                origin.departure_delay,
                trip.schedule_relationship
            FROM latest
            JOIN stop_update AS origin
              ON origin.trip_id = latest.trip_id
             AND origin.fetched_at = latest.fetched_at
            JOIN stop_update AS destination
              ON destination.trip_id = latest.trip_id
             AND destination.fetched_at = latest.fetched_at
            JOIN trip_update AS trip
              ON trip.trip_id = latest.trip_id
             AND trip.fetched_at = latest.fetched_at
            WHERE origin.stop_id IN ({origin_marks})
              AND destination.stop_id IN ({destination_marks})
              AND origin.departure_time BETWEEN ? AND ?
              AND destination.departure_time > origin.departure_time
            ORDER BY origin.departure_time
            """,
            (*origins, *destinations, start, end),
        ).fetchall()
    return [
        {
            "trip_id": trip_id,
            "departure_time": datetime.fromtimestamp(departure_time, timezone.utc).isoformat(),
            "delay_seconds": delay,
            "status": relationship,
        }
        for trip_id, departure_time, delay, relationship in rows
        if departure_time is not None
    ]
