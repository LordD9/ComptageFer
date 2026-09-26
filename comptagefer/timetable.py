import csv
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
KINDS = {
    "TER": "TER",
    "TT": "TER",
    "CTE": "Car",
    "IC": "Intercités",
    "ICN": "Intercités",
    "OUI": "TGV",
    "OGO": "TGV",
}


def kind_of(trip_id: str) -> str:
    marker = ""
    if "_F:" in trip_id or "_R:" in trip_id:
        marker = trip_id.split(":", 2)[1]
    return KINDS.get(marker, marker or "Train")


def etat_of(status: str | None, delay_seconds: int | None) -> str:
    if status in {"CANCELED", "DELETED"}:
        return "supprimé"
    if status is None:
        return "programmé"
    if delay_seconds and delay_seconds >= 60:
        return "retard"
    if delay_seconds and delay_seconds <= -60:
        return "avance"
    return "à l'heure"


def import_timetable(database: Path, trips_file: Path, times_file: Path, dates_file: Path) -> int:
    database.parent.mkdir(parents=True, exist_ok=True)
    stored = 0
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS circulation (
                trip_id TEXT PRIMARY KEY,
                service_id TEXT NOT NULL,
                kind TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS passage (
                trip_id TEXT NOT NULL,
                stop_id TEXT NOT NULL,
                depart_sec INTEGER NOT NULL,
                PRIMARY KEY (trip_id, stop_id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS passage_stop ON passage(stop_id, depart_sec)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS service_day (
                service_id TEXT NOT NULL,
                day TEXT NOT NULL,
                PRIMARY KEY (service_id, day)
            )
            """
        )
        with trips_file.open(newline="") as handle:
            connection.executemany(
                "INSERT OR REPLACE INTO circulation (trip_id, service_id, kind) VALUES (?, ?, ?)",
                (
                    (row["trip_id"], row["service_id"], kind_of(row["trip_id"]))
                    for row in csv.DictReader(handle)
                ),
            )
            stored = connection.execute("SELECT COUNT(*) FROM circulation").fetchone()[0]
        passages = []
        with times_file.open(newline="") as handle:
            for row in csv.DictReader(handle):
                departure = row.get("departure_time") or row.get("arrival_time")
                if not departure or not row.get("stop_id"):
                    continue
                passages.append((row["trip_id"], row["stop_id"], _seconds(departure)))
                if len(passages) >= 5000:
                    connection.executemany(
                        "INSERT OR REPLACE INTO passage (trip_id, stop_id, depart_sec) VALUES (?, ?, ?)",
                        passages,
                    )
                    passages.clear()
            if passages:
                connection.executemany(
                    "INSERT OR REPLACE INTO passage (trip_id, stop_id, depart_sec) VALUES (?, ?, ?)",
                    passages,
                )
        with dates_file.open(newline="") as handle:
            connection.executemany(
                "INSERT OR REPLACE INTO service_day (service_id, day) VALUES (?, ?)",
                (
                    (row["service_id"], f"{row['date'][:4]}-{row['date'][4:6]}-{row['date'][6:8]}")
                    for row in csv.DictReader(handle)
                    if row.get("exception_type") in {None, "", "1"}
                ),
            )
    return stored


def listed_trips(
    database: Path,
    realtime: Path,
    origin_stop_id: str,
    destination_stop_id: str,
    at: datetime,
    window: timedelta = timedelta(hours=2),
    neighbor_window: timedelta = timedelta(hours=12),
    stops_database: Path | None = None,
) -> list[dict]:
    local = at.astimezone(PARIS)
    wider = _pairs(database, origin_stop_id, destination_stop_id, local, neighbor_window, stops_database)
    realtime_rows = _realtime(realtime, [item["trip_id"] for item in wider], origin_stop_id, stops_database)
    annotated = []
    for item in wider:
        status, delay = realtime_rows.get(item["trip_id"], (None, None))
        annotated.append(
            {
                **item,
                "departure_time": item["departure"].isoformat(),
                "status": status,
                "delay_seconds": delay,
                "etat": etat_of(status, delay),
            }
        )
    annotated.sort(key=lambda item: item["departure"])
    for index, item in enumerate(annotated):
        item["precedent"] = _brief(annotated[index - 1]) if index else None
        item["suivant"] = _brief(annotated[index + 1]) if index + 1 < len(annotated) else None
        item["precedent_meme_type"] = _brief(_same_before(annotated, index))
        item["suivant_meme_type"] = _brief(_same_after(annotated, index))
    start = local - window
    end = local + window
    shown = [item for item in annotated if start <= item["departure"] <= end]
    for item in shown:
        item.pop("departure", None)
    return shown


def _pairs(database, origin_stop_id, destination_stop_id, local, window, stops_database):
    origins = _family(stops_database, origin_stop_id)
    destinations = _family(stops_database, destination_stop_id)
    start = local - window
    end = local + window
    days = {(start.date()).isoformat(), local.date().isoformat(), end.date().isoformat()}
    origin_marks = ",".join("?" for _ in origins)
    destination_marks = ",".join("?" for _ in destinations)
    day_marks = ",".join("?" for _ in days)
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            f"""
            SELECT circulation.trip_id, circulation.kind, origin.depart_sec,
                   destination.depart_sec, service_day.day
            FROM passage AS origin
            JOIN passage AS destination
              ON destination.trip_id = origin.trip_id
            JOIN circulation ON circulation.trip_id = origin.trip_id
            JOIN service_day ON service_day.service_id = circulation.service_id
            WHERE origin.stop_id IN ({origin_marks})
              AND destination.stop_id IN ({destination_marks})
              AND service_day.day IN ({day_marks})
              AND destination.depart_sec > origin.depart_sec
            """,
            (*origins, *destinations, *days),
        ).fetchall()
    found = []
    for trip_id, kind, origin_sec, _destination_sec, day in rows:
        midnight = datetime.fromisoformat(day).replace(tzinfo=PARIS)
        departure = midnight + timedelta(seconds=origin_sec)
        if start <= departure <= end:
            found.append({"trip_id": trip_id, "kind": kind, "departure": departure})
    return found


def stops_between(
    database: Path,
    trip_id: str,
    origin: str,
    destination: str,
    stops_database: Path | None = None,
) -> list[dict]:
    origins = set(_family(stops_database, origin))
    destinations = set(_family(stops_database, destination))
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(passage)")}
        order = "COALESCE(seq, depart_sec), depart_sec" if "seq" in columns else "depart_sec"
        rows = connection.execute(
            f"SELECT stop_id FROM passage WHERE trip_id = ? ORDER BY {order}",
            (trip_id,),
        ).fetchall()
    start = next((index for index, row in enumerate(rows) if row[0] in origins), None)
    if start is None:
        return []
    end = next(
        (index for index, row in enumerate(rows) if index > start and row[0] in destinations),
        None,
    )
    if end is None:
        return []
    names = _station_names(stops_database)
    found = []
    seen = None
    for (stop_id,) in rows[start : end + 1]:
        name, key = names.get(stop_id, (stop_id, stop_id))
        if key == seen:
            continue
        seen = key
        found.append({"stop_id": stop_id, "name": name})
    return found


def _station_names(stops_database: Path | None) -> dict[str, tuple[str, str]]:
    if stops_database is None or not Path(stops_database).exists():
        return {}
    from comptagefer.offer import open_stops

    with open_stops(stops_database) as connection:
        rows = connection.execute("SELECT stop_id, name, parent FROM stop").fetchall()
    by_id = {row[0]: row for row in rows}
    names = {}
    for stop_id, name, parent in rows:
        if parent and parent in by_id:
            names[stop_id] = (by_id[parent][1], parent)
        else:
            names[stop_id] = (name, stop_id)
    return names


def _realtime(database: Path, trip_ids: list[str], origin_stop_id: str, stops_database) -> dict:
    if not trip_ids or not database.exists():
        return {}
    origins = _family(stops_database, origin_stop_id)
    trip_marks = ",".join("?" for _ in trip_ids)
    origin_marks = ",".join("?" for _ in origins)
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            f"""
            WITH latest AS (
                SELECT trip_id, MAX(fetched_at) AS fetched_at
                FROM trip_update
                WHERE trip_id IN ({trip_marks})
                GROUP BY trip_id
            )
            SELECT latest.trip_id, trip.schedule_relationship, stop.departure_delay
            FROM latest
            JOIN trip_update AS trip
              ON trip.trip_id = latest.trip_id AND trip.fetched_at = latest.fetched_at
            LEFT JOIN stop_update AS stop
              ON stop.trip_id = latest.trip_id
             AND stop.fetched_at = latest.fetched_at
             AND stop.stop_id IN ({origin_marks})
            """,
            (*trip_ids, *origins),
        ).fetchall()
    return {trip_id: (status, delay) for trip_id, status, delay in rows}


def _family(stops_database: Path | None, stop_id: str) -> list[str]:
    if stops_database is None or not Path(stops_database).exists():
        return [stop_id]
    from comptagefer.offer import _stop_family

    return _stop_family(stops_database, stop_id)


def _same_before(items, index):
    kind = items[index]["kind"]
    for item in reversed(items[:index]):
        if item["kind"] == kind:
            return item
    return None


def _same_after(items, index):
    kind = items[index]["kind"]
    for item in items[index + 1 :]:
        if item["kind"] == kind:
            return item
    return None


def _brief(item):
    if item is None:
        return None
    return {
        "trip_id": item["trip_id"],
        "kind": item["kind"],
        "departure_time": item["departure_time"],
        "status": item["status"],
        "delay_seconds": item["delay_seconds"],
        "etat": item["etat"],
    }


def _seconds(value: str) -> int:
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    return hours * 3600 + minutes * 60 + seconds
