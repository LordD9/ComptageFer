import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.transit import gtfs_realtime_pb2

from comptagefer.offer import import_stop_names, search_stops, trips_serving
from comptagefer.rt import store_trip_updates


def test_search_finds_a_stop_area_by_name(tmp_path: Path):
    stops = tmp_path / "stops.txt"
    with stops.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"]
        )
        writer.writerow(["StopArea:OCE87756056", "Nice-Ville", "43.7", "7.26", "1", ""])
        writer.writerow(["StopPoint:OCE-87756056", "Nice-Ville", "43.7", "7.26", "0", "StopArea:OCE87756056"])

    database = tmp_path / "stops.db"
    assert import_stop_names(database, stops) == 2
    assert search_stops(database, "nice") == [
        {"stop_id": "StopArea:OCE87756056", "name": "Nice-Ville"}
    ]


def _timed_feed() -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1
    entity = feed.entity.add()
    entity.id = "1"
    entity.trip_update.trip.trip_id = "TRIP1"
    origin = entity.trip_update.stop_time_update.add()
    origin.stop_id = "A"
    origin.departure.time = 1_700_000_000
    origin.departure.delay = 120
    destination = entity.trip_update.stop_time_update.add()
    destination.stop_id = "B"
    destination.departure.time = 1_700_003_600
    return feed.SerializeToString()


def test_lists_a_trip_serving_both_stops_inside_the_window(tmp_path: Path):
    database = tmp_path / "rt.db"
    store_trip_updates(
        database,
        _timed_feed(),
        datetime.fromtimestamp(1_700_000_000, timezone.utc),
    )

    found = trips_serving(
        database,
        "A",
        "B",
        at=datetime.fromtimestamp(1_700_000_000, timezone.utc),
        window=timedelta(hours=2),
    )

    assert [item["trip_id"] for item in found] == ["TRIP1"]
    assert found[0]["delay_seconds"] == 120
    assert found[0]["status"] == "SCHEDULED"
