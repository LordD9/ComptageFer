from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.offer import open_stops
from comptagefer.timetable import stops_between


def _referential(folder):
    stops = folder / "stops.db"
    with open_stops(stops) as connection:
        connection.executemany(
            "INSERT INTO stop (stop_id, name, lat, lon, parent, is_area) VALUES (?, ?, NULL, NULL, ?, ?)",
            [
                ("StopArea:A", "Lyon", None, 1),
                ("StopPoint:A", "Lyon quai", "StopArea:A", 0),
                ("StopArea:C", "Vienne", None, 1),
                ("StopPoint:C", "Vienne quai", "StopArea:C", 0),
                ("StopArea:B", "Valence", None, 1),
                ("StopPoint:B", "Valence quai", "StopArea:B", 0),
                ("StopPoint:D", "Avignon", None, 0),
            ],
        )
    timetable = folder / "timetable.db"
    import sqlite3

    with sqlite3.connect(timetable) as connection:
        connection.execute(
            "CREATE TABLE passage (trip_id TEXT, stop_id TEXT, depart_sec INTEGER, PRIMARY KEY (trip_id, stop_id))"
        )
        connection.executemany(
            "INSERT INTO passage VALUES (?, ?, ?)",
            [
                ("TRIP", "StopPoint:Z", 8 * 3600),
                ("TRIP", "StopPoint:A", 9 * 3600),
                ("TRIP", "StopPoint:C", 10 * 3600),
                ("TRIP", "StopPoint:B", 11 * 3600),
                ("TRIP", "StopPoint:D", 12 * 3600),
            ],
        )
    return timetable, stops


def test_snake_lists_stops_from_boarding_to_alighting(tmp_path):
    timetable, stops = _referential(tmp_path)
    found = stops_between(timetable, "TRIP", "StopArea:A", "StopArea:B", stops_database=stops)
    assert [item["name"] for item in found] == ["Lyon", "Vienne", "Valence"]


def test_snake_keeps_boardings_without_the_feed(tmp_path):
    timetable, stops = _referential(tmp_path)
    client = TestClient(create_app(tmp_path))
    (tmp_path / "timetable.db").write_bytes(timetable.read_bytes())
    (tmp_path / "stops.db").write_bytes(stops.read_bytes())
    listed = client.get("/api/trip-stops?trip=TRIP&from=StopArea:A&to=StopArea:B")
    assert [item["name"] for item in listed.json()] == ["Lyon", "Vienne", "Valence"]

    stored = client.post(
        "/api/sessions",
        json={
            "client_id": "serpent-1",
            "kind": "serpent",
            "origin_stop_id": "StopArea:A",
            "destination_stop_id": "StopArea:B",
            "origin_name": "Lyon",
            "destination_name": "Valence",
            "trip_id": "TRIP",
            "passengers": 40,
            "reliability": 70,
            "legs": [
                {"stop_id": "StopPoint:A", "stop_name": "Lyon", "onboard": 40},
                {"stop_id": "StopPoint:C", "stop_name": "Vienne", "boarded": 3, "alighted": 1},
                {"stop_id": "StopPoint:B", "stop_name": "Valence", "boarded": 0, "alighted": None},
            ],
        },
    )
    assert stored.status_code == 200
    page = client.get("/comptages").text
    assert "serpent" in page
    assert "Vienne" in page
    assert "3" in page
