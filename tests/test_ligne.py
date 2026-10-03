"""Les lignes : import et rattachement des comptages aux filtres.

Le GTFS national attribue le même nom court à plusieurs lignes. Ces tests
verrouillent l'import des lignes et leur rattachement aux circulations,
utilisés par le filtre des comptages.
"""

import csv
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.timetable import import_timetable

# Deux lignes qui partagent volontairement le même nom court.
ROUTES = [
    ("R-TER-1", "C13", "Annecy - St-Gervais", "2"),
    ("R-TER-2", "C13", "Paris Nord - Compiègne", "2"),
    ("R-CAR-1", "P53", "Bening - Sarreguemines", "3"),
    ("R-TRAM-1", "T1", "Lyon - Vaulx", "0"),
    ("R-UNKNOWN", "INCONNU", "Ligne sans nom court", "2"),
]

TRIPS = [
    ("R-TER-1", "S1", "1_F:TER:1", ""),
    ("R-TER-2", "S1", "1_F:TER:2", ""),
    ("R-CAR-1", "S1", "1_F:CTE:1", ""),
    ("R-TRAM-1", "S1", "1_F:TRAM:1", ""),
    ("R-UNKNOWN", "S1", "1_F:TER:9", ""),
]

def _write(path: Path, header: list[str], rows: list[tuple]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _gtfs(folder: Path) -> None:
    _write(
        folder / "routes.txt",
        ["route_id", "route_short_name", "route_long_name", "route_type"],
        ROUTES,
    )
    _write(folder / "trips.txt", ["route_id", "service_id", "trip_id", "trip_headsign"], TRIPS)
    _write(
        folder / "stop_times.txt",
        ["trip_id", "arrival_time", "departure_time", "stop_id"],
        [
            ("1_F:TER:1", "08:00:00", "08:00:00", "StopPoint:AnnecyA"),
            ("1_F:TER:1", "08:40:00", "08:40:00", "StopPoint:SarregueminesA"),
            ("1_F:TER:2", "09:00:00", "09:00:00", "StopPoint:CompiègneA"),
            ("1_F:CTE:1", "10:00:00", "10:00:00", "StopPoint:BeningA"),
            ("1_F:TRAM:1", "11:00:00", "11:00:00", "StopPoint:AnnecyA"),
        ],
    )
    _write(folder / "calendar_dates.txt", ["service_id", "date", "exception_type"], [("S1", "20260101", "1")])


# --- import -----------------------------------------------------------------


def test_the_import_stores_lines_and_binds_trips(tmp_path):
    _gtfs(tmp_path)
    database = tmp_path / "timetable.db"
    import_timetable(
        database,
        tmp_path / "trips.txt",
        tmp_path / "stop_times.txt",
        tmp_path / "calendar_dates.txt",
        tmp_path / "routes.txt",
    )
    with sqlite3.connect(database) as connection:
        routes = connection.execute("SELECT route_id FROM ligne ORDER BY route_id").fetchall()
        trips = connection.execute("SELECT route_id FROM trip_ligne ORDER BY route_id").fetchall()
    assert routes == [("R-CAR-1",), ("R-TER-1",), ("R-TER-2",), ("R-TRAM-1",), ("R-UNKNOWN",)]
    assert trips == routes


def test_the_mode_comes_from_the_gtfs_route_type(tmp_path):
    _gtfs(tmp_path)
    database = tmp_path / "timetable.db"
    import_timetable(
        database,
        tmp_path / "trips.txt",
        tmp_path / "stop_times.txt",
        tmp_path / "calendar_dates.txt",
        tmp_path / "routes.txt",
    )
    with sqlite3.connect(database) as connection:
        modes = dict(connection.execute("SELECT nom_court, mode FROM ligne"))
    assert modes["P53"] == "car", "route_type 3 est un bus"
    assert modes["T1"] == "tramway", "route_type 0 est un tramway"
    assert modes["C13"] == "train", "route_type 2 est du rail"


def test_a_timetable_without_routes_still_works(tmp_path):
    """Une base importée avant les lignes reste compatible avec l'application."""
    _gtfs(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    import_timetable(
        data / "timetable.db", tmp_path / "trips.txt", tmp_path / "stop_times.txt", tmp_path / "calendar_dates.txt"
    )
    client = TestClient(create_app(data))
    for path in ("/rechercher", "/ligne", "/api/lignes"):
        assert client.get(path).status_code == 404
