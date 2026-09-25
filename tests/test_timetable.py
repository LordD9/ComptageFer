import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from comptagefer.timetable import import_timetable, listed_trips

PARIS = ZoneInfo("Europe/Paris")


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_four_hour_list_keeps_type_delay_and_both_neighbour_pairs(tmp_path: Path):
    trips = tmp_path / "trips.txt"
    times = tmp_path / "stop_times.txt"
    days = tmp_path / "calendar_dates.txt"
    _write(trips, [
        {"route_id": "L", "service_id": "S", "trip_id": "EARLY_F:TER:1"},
        {"route_id": "L", "service_id": "S", "trip_id": "NOW_F:TER:1"},
        {"route_id": "L", "service_id": "S", "trip_id": "TGV_F:OUI:1"},
        {"route_id": "L", "service_id": "S", "trip_id": "CAR_R:CTE:1"},
        {"route_id": "L", "service_id": "S", "trip_id": "LATER_F:TER:1"},
    ])
    _write(times, [
        {"trip_id": "EARLY_F:TER:1", "arrival_time": "07:00:00", "departure_time": "07:00:00", "stop_id": "A", "stop_sequence": "1"},
        {"trip_id": "EARLY_F:TER:1", "arrival_time": "07:40:00", "departure_time": "07:40:00", "stop_id": "B", "stop_sequence": "2"},
        {"trip_id": "NOW_F:TER:1", "arrival_time": "09:30:00", "departure_time": "09:30:00", "stop_id": "A", "stop_sequence": "1"},
        {"trip_id": "NOW_F:TER:1", "arrival_time": "10:10:00", "departure_time": "10:10:00", "stop_id": "B", "stop_sequence": "2"},
        {"trip_id": "TGV_F:OUI:1", "arrival_time": "10:10:00", "departure_time": "10:10:00", "stop_id": "A", "stop_sequence": "1"},
        {"trip_id": "TGV_F:OUI:1", "arrival_time": "10:40:00", "departure_time": "10:40:00", "stop_id": "B", "stop_sequence": "2"},
        {"trip_id": "CAR_R:CTE:1", "arrival_time": "10:40:00", "departure_time": "10:40:00", "stop_id": "A", "stop_sequence": "1"},
        {"trip_id": "CAR_R:CTE:1", "arrival_time": "11:20:00", "departure_time": "11:20:00", "stop_id": "B", "stop_sequence": "2"},
        {"trip_id": "LATER_F:TER:1", "arrival_time": "13:00:00", "departure_time": "13:00:00", "stop_id": "A", "stop_sequence": "1"},
        {"trip_id": "LATER_F:TER:1", "arrival_time": "13:40:00", "departure_time": "13:40:00", "stop_id": "B", "stop_sequence": "2"},
    ])
    _write(days, [{"service_id": "S", "date": "20260925", "exception_type": "1"}])
    database = tmp_path / "timetable.db"
    import_timetable(database, trips, times, days)

    found = listed_trips(
        database,
        tmp_path / "missing-rt.db",
        "A",
        "B",
        at=datetime(2026, 9, 25, 10, 0, tzinfo=PARIS),
    )

    assert [item["trip_id"] for item in found] == ["NOW_F:TER:1", "TGV_F:OUI:1", "CAR_R:CTE:1"]
    assert [item["kind"] for item in found] == ["TER", "TGV", "Car"]
    current = found[0]
    assert current["precedent"]["trip_id"] == "EARLY_F:TER:1"
    assert current["suivant"]["kind"] == "TGV"
    assert current["suivant_meme_type"]["trip_id"] == "LATER_F:TER:1"
    assert current["precedent_meme_type"]["kind"] == "TER"


def test_flux_marks_delay_and_cancellation(tmp_path: Path):
    from datetime import timezone

    from google.transit import gtfs_realtime_pb2

    from comptagefer.rt import store_trip_updates

    trips = tmp_path / "trips.txt"
    times = tmp_path / "stop_times.txt"
    days = tmp_path / "calendar_dates.txt"
    _write(trips, [{"route_id": "L", "service_id": "S", "trip_id": "NOW_F:TER:1"}])
    _write(times, [
        {"trip_id": "NOW_F:TER:1", "arrival_time": "09:30:00", "departure_time": "09:30:00", "stop_id": "A", "stop_sequence": "1"},
        {"trip_id": "NOW_F:TER:1", "arrival_time": "10:10:00", "departure_time": "10:10:00", "stop_id": "B", "stop_sequence": "2"},
    ])
    _write(days, [{"service_id": "S", "date": "20260925", "exception_type": "1"}])
    database = tmp_path / "timetable.db"
    import_timetable(database, trips, times, days)
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1
    entity = feed.entity.add()
    entity.id = "1"
    entity.trip_update.trip.trip_id = "NOW_F:TER:1"
    entity.trip_update.trip.schedule_relationship = gtfs_realtime_pb2.TripDescriptor.CANCELED
    stop = entity.trip_update.stop_time_update.add()
    stop.stop_id = "A"
    stop.departure.delay = 600
    realtime = tmp_path / "rt.db"
    store_trip_updates(realtime, feed.SerializeToString(), datetime(2026, 9, 25, 8, tzinfo=timezone.utc))

    found = listed_trips(database, realtime, "A", "B", at=datetime(2026, 9, 25, 10, 0, tzinfo=PARIS))

    assert found[0]["etat"] == "supprimé"
    assert found[0]["delay_seconds"] == 600
    assert found[0]["status"] == "CANCELED"
