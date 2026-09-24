import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.transit import gtfs_realtime_pb2

RETENTION = timedelta(hours=6)


def _connect(database: Path) -> sqlite3.Connection:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS trip_update (
            trip_id TEXT NOT NULL,
            start_date TEXT,
            schedule_relationship TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (trip_id, fetched_at)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS stop_update (
            trip_id TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            stop_id TEXT NOT NULL,
            arrival_delay INTEGER,
            departure_delay INTEGER,
            PRIMARY KEY (trip_id, fetched_at, stop_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS service_alert (
            alert_id TEXT NOT NULL,
            header TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (alert_id, fetched_at)
        )
        """
    )
    return connection


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _refresh_if_unchanged(connection, trip_id: str, relationship: str, stops, stamp: str) -> bool:
    latest = connection.execute(
        """
        SELECT schedule_relationship, fetched_at FROM trip_update
        WHERE trip_id = ?
        ORDER BY fetched_at DESC
        LIMIT 1
        """,
        (trip_id,),
    ).fetchone()
    if latest is None or latest[0] != relationship:
        return False
    previous = connection.execute(
        """
        SELECT stop_id, arrival_delay, departure_delay FROM stop_update
        WHERE trip_id = ? AND fetched_at = ?
        ORDER BY stop_id
        """,
        (trip_id, latest[1]),
    ).fetchall()
    current = sorted(stops, key=lambda item: item[0])
    if list(previous) != current:
        return False
    connection.execute(
        "UPDATE trip_update SET fetched_at = ? WHERE trip_id = ? AND fetched_at = ?",
        (stamp, trip_id, latest[1]),
    )
    connection.execute(
        "UPDATE stop_update SET fetched_at = ? WHERE trip_id = ? AND fetched_at = ?",
        (stamp, trip_id, latest[1]),
    )
    return True


def store_trip_updates(database: Path, payload: bytes, fetched_at: datetime) -> int:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(payload)
    stamp = _stamp(fetched_at)
    stored = 0
    with _connect(database) as connection:
        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            trip = entity.trip_update.trip
            relationship = gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship.Name(
                trip.schedule_relationship
            )
            stops = [
                (
                    stop.stop_id,
                    stop.arrival.delay if stop.HasField("arrival") else None,
                    stop.departure.delay if stop.HasField("departure") else None,
                )
                for stop in entity.trip_update.stop_time_update
            ]
            if _refresh_if_unchanged(connection, trip.trip_id, relationship, stops, stamp):
                stored += 1
                continue
            connection.execute(
                """
                INSERT INTO trip_update
                    (trip_id, start_date, schedule_relationship, fetched_at)
                VALUES (?, ?, ?, ?)
                """,
                (trip.trip_id, trip.start_date, relationship, stamp),
            )
            for stop_id, arrival_delay, departure_delay in stops:
                connection.execute(
                    """
                    INSERT INTO stop_update
                        (trip_id, fetched_at, stop_id, arrival_delay, departure_delay)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (trip.trip_id, stamp, stop_id, arrival_delay, departure_delay),
                )
            stored += 1
    return stored


def trip_count(database: Path) -> int:
    with _connect(database) as connection:
        row = connection.execute("SELECT COUNT(*) FROM trip_update").fetchone()
    return int(row[0])


def schedule_relationship(database: Path, trip_id: str) -> str:
    with _connect(database) as connection:
        row = connection.execute(
            """
            SELECT schedule_relationship FROM trip_update
            WHERE trip_id = ?
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (trip_id,),
        ).fetchone()
    return row[0]


def departure_delay(database: Path, trip_id: str, stop_id: str) -> int | None:
    with _connect(database) as connection:
        row = connection.execute(
            """
            SELECT departure_delay FROM stop_update
            WHERE trip_id = ? AND stop_id = ?
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (trip_id, stop_id),
        ).fetchone()
    return None if row is None else row[0]


def purge_older_than(database: Path, now: datetime) -> None:
    cutoff = _stamp(now - RETENTION)
    with _connect(database) as connection:
        connection.execute("DELETE FROM trip_update WHERE fetched_at < ?", (cutoff,))
        connection.execute("DELETE FROM stop_update WHERE fetched_at < ?", (cutoff,))
        connection.execute("DELETE FROM service_alert WHERE fetched_at < ?", (cutoff,))


SIRI_NS = {"s": "http://www.siri.org.uk/siri"}
TU_URL = "https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates"
ALERTS_URL = "https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-service-alerts"
SIRI_URL = "https://proxy.transport.data.gouv.fr/resource/sncf-siri-lite-estimated-timetable"


def store_service_alerts(database: Path, payload: bytes, fetched_at: datetime) -> int:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(payload)
    stamp = _stamp(fetched_at)
    stored = 0
    with _connect(database) as connection:
        for entity in feed.entity:
            if not entity.HasField("alert"):
                continue
            header = ""
            if entity.alert.header_text.translation:
                header = entity.alert.header_text.translation[0].text
            connection.execute(
                """
                INSERT INTO service_alert (alert_id, header, fetched_at)
                VALUES (?, ?, ?)
                """,
                (entity.id, header, stamp),
            )
            stored += 1
    return stored


def alert_count(database: Path) -> int:
    with _connect(database) as connection:
        row = connection.execute("SELECT COUNT(*) FROM service_alert").fetchone()
    return int(row[0])


def _delay_seconds(aimed: str | None, expected: str | None) -> int | None:
    if not aimed or not expected:
        return None
    aimed_at = datetime.fromisoformat(aimed)
    expected_at = datetime.fromisoformat(expected)
    return int((expected_at - aimed_at).total_seconds())


def store_siri(database: Path, payload: bytes, fetched_at: datetime) -> int:
    root = ET.fromstring(payload)
    stamp = _stamp(fetched_at)
    stored = 0
    with _connect(database) as connection:
        for journey in root.findall(".//s:EstimatedVehicleJourney", SIRI_NS):
            trip_id = journey.findtext(
                "s:FramedVehicleJourneyRef/s:DatedVehicleJourneyRef",
                default="",
                namespaces=SIRI_NS,
            )
            if not trip_id:
                continue
            cancelled = (
                journey.findtext("s:Cancellation", default="", namespaces=SIRI_NS) or ""
            ).lower() == "true"
            relationship = "CANCELED" if cancelled else "SCHEDULED"
            connection.execute(
                """
                INSERT INTO trip_update
                    (trip_id, start_date, schedule_relationship, fetched_at)
                VALUES (?, ?, ?, ?)
                """,
                (trip_id, "", relationship, stamp),
            )
            for call in journey.findall(".//s:EstimatedCall", SIRI_NS):
                stop_id = call.findtext("s:StopPointRef", default="", namespaces=SIRI_NS)
                delay = _delay_seconds(
                    call.findtext("s:AimedDepartureTime", default="", namespaces=SIRI_NS),
                    call.findtext("s:ExpectedDepartureTime", default="", namespaces=SIRI_NS),
                )
                connection.execute(
                    """
                    INSERT INTO stop_update
                        (trip_id, fetched_at, stop_id, arrival_delay, departure_delay)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (trip_id, stamp, stop_id, None, delay),
                )
            stored += 1
    return stored


def poll_once(
    database: Path,
    fetch_trips,
    fetch_siri,
    fetch_alerts,
    now: datetime,
) -> None:
    stored = store_trip_updates(database, fetch_trips(), now)
    if stored == 0:
        store_siri(database, fetch_siri(), now)
    store_service_alerts(database, fetch_alerts(), now)
    purge_older_than(database, now)


def cache_status(database: Path) -> dict[str, int | str | None]:
    with _connect(database) as connection:
        trips = connection.execute("SELECT COUNT(*) FROM trip_update").fetchone()[0]
        alerts = connection.execute("SELECT COUNT(*) FROM service_alert").fetchone()[0]
        last = connection.execute(
            """
            SELECT MAX(fetched_at) FROM (
                SELECT fetched_at FROM trip_update
                UNION ALL
                SELECT fetched_at FROM service_alert
            )
            """
        ).fetchone()[0]
    return {"trip_updates": int(trips), "alerts": int(alerts), "last_fetch": last}
