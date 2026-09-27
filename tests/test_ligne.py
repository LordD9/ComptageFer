"""Les lignes : import, recherche, et page.

Le GTFS national attribue le même nom court à plusieurs lignes — « C13 »
désigne six lignes différentes — donc tout ce qui identifie une ligne passe
par le `route_id`. Ces tests le verrouillent : une URL sur le nom court
ouvrirait une page au hasard, et c'est le genre de faute qu'aucun test
d'affichage ne rattrape.
"""

import csv
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.offer import import_stop_names
from comptagefer.timetable import import_timetable, search_lines

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

# import_stop_names lit par nom d'en-tête, pas par position : l'ordre des
# colonnes est celui de stops.txt, parent avant lat/lon.
STOPS = [
    ("StopArea:Annecy", "Annecy", "", "1", 45.9, 6.1),
    ("StopPoint:AnnecyA", "Annecy", "StopArea:Annecy", "0", 45.9, 6.1),
    ("StopArea:Compiègne", "Compiègne", "", "1", 49.45, 2.8),
    ("StopPoint:CompiègneA", "Compiègne", "StopArea:Compiègne", "0", 49.45, 2.8),
    ("StopArea:Bening", "Bening", "", "1", 49.11, 6.94),
    ("StopPoint:BeningA", "Bening", "StopArea:Bening", "0", 49.11, 6.94),
    ("StopArea:Sarreguemines", "Sarreguemines", "", "1", 49.11, 7.07),
    ("StopPoint:SarregueminesA", "Sarreguemines", "StopArea:Sarreguemines", "0", 49.11, 7.07),
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


def _site(tmp_path: Path) -> TestClient:
    data = tmp_path / "data"
    data.mkdir()
    _write_stops_file(tmp_path)
    _gtfs(tmp_path)
    import_stop_names(data / "stops.db", tmp_path / "stops.txt")
    import_timetable(
        data / "timetable.db",
        tmp_path / "trips.txt",
        tmp_path / "stop_times.txt",
        tmp_path / "calendar_dates.txt",
        tmp_path / "routes.txt",
    )
    return TestClient(create_app(data))


def _write_stops_file(tmp_path: Path) -> None:
    _write(
        tmp_path / "stops.txt",
        ["stop_id", "stop_name", "parent_station", "location_type", "stop_lat", "stop_lon"],
        STOPS,
    )


# --- import -----------------------------------------------------------------


def test_the_import_stores_lines_and_binds_trips(tmp_path):
    _write_stops_file(tmp_path)
    _gtfs(tmp_path)
    database = tmp_path / "timetable.db"
    import_timetable(
        database,
        tmp_path / "trips.txt",
        tmp_path / "stop_times.txt",
        tmp_path / "calendar_dates.txt",
        tmp_path / "routes.txt",
    )
    client = TestClient(create_app(tmp_path / "data"))
    assert len(search_lines(database, "C13")) == 2
    # 53 lignes du GTFS national s'appellent INCONNU : on ne les invente pas,
    # mais on ne les perd pas non plus.
    assert [found["titre"] for found in search_lines(database, "INCONNU")] == [
        "INCONNU · Ligne sans nom court"
    ]


def test_the_mode_comes_from_the_gtfs_route_type(tmp_path):
    _write_stops_file(tmp_path)
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
    """Une base importée avant les pages ligne n'a pas les tables : rien ne doit
    casser, la recherche doit juste ne rien trouver."""
    _write_stops_file(tmp_path)
    _gtfs(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    import_timetable(
        data / "timetable.db", tmp_path / "trips.txt", tmp_path / "stop_times.txt", tmp_path / "calendar_dates.txt"
    )
    client = TestClient(create_app(data))
    assert client.get("/api/lignes?q=C13").json() == []
    assert client.get("/rechercher?q=C13").status_code == 200
    assert client.get("/ligne?ligne=R-TER-1").status_code == 200


# --- recherche --------------------------------------------------------------


def test_a_search_finds_lines_by_short_name_and_by_long_name(tmp_path):
    client = _site(tmp_path)
    assert [f["route_id"] for f in client.get("/api/lignes?q=C13").json()] == ["R-TER-1", "R-TER-2"]
    assert [f["route_id"] for f in client.get("/api/lignes?q=Compiègne").json()] == ["R-TER-2"]
    assert [f["route_id"] for f in client.get("/api/lignes?q=Sarreguemines").json()] == ["R-CAR-1"]


def test_the_title_shows_the_long_name_because_the_short_one_is_not_unique(tmp_path):
    """« C13 » ne veut rien dire pour quelqu'un qui ne connaît pas la
    numérotation SNCF. Sans le nom long, la page est un cul-de-sac."""
    client = _site(tmp_path)
    titres = [f["titre"] for f in client.get("/api/lignes?q=C13").json()]
    assert titres == ["C13 · Annecy - St-Gervais", "C13 · Paris Nord - Compiègne"]


def test_a_one_letter_search_returns_nothing(tmp_path):
    client = _site(tmp_path)
    assert client.get("/api/lignes?q=C").json() == []


def test_the_search_page_shows_gares_and_lines_for_the_same_word(tmp_path):
    client = _site(tmp_path)
    page = client.get("/rechercher?q=Annecy")
    assert page.status_code == 200
    assert "Annecy" in page.text
    assert "R-TER-1" in page.text, "le lien de la ligne doit porter le route_id"


def test_the_search_page_invites_before_any_query(tmp_path):
    page = _site(tmp_path).get("/rechercher")
    assert page.status_code == 200
    assert "nom de gare ou de ligne" in page.text


# --- page ligne -------------------------------------------------------------


def test_a_line_page_lists_its_stops_and_invites_to_count(tmp_path):
    client = _site(tmp_path)
    page = client.get("/ligne?ligne=R-CAR-1")
    assert page.status_code == 200
    assert "P53" in page.text
    assert "Bening" in page.text
    assert "Aucun comptage sur cette ligne" in page.text, "une ligne muette appelle, elle n'est pas vide"
    assert 'href="/"' in page.text


def test_a_count_appears_on_its_own_line_and_on_nothing_else(tmp_path):
    client = _site(tmp_path)
    photo = {"precedent": None, "courant": {"trip_id": "1_F:TER:1", "status": "SCHEDULED"}, "suivant": None}
    stored = client.post(
        "/api/sessions",
        json={
            "client_id": "jeton",
            "origin_stop_id": "StopArea:Annecy",
            "destination_stop_id": "StopArea:Sarreguemines",
            "origin_name": "Annecy",
            "destination_name": "Sarreguemines",
            "trip_id": "1_F:TER:1",
            "passengers": 40,
            "reliability": 70,
            "pseudo": "railfan",
            "snapshot": photo,
        },
    )
    assert stored.status_code == 200

    bonne = client.get("/ligne?ligne=R-TER-1")
    assert "Annecy → Sarreguemines" in bonne.text
    assert "railfan" in bonne.text

    # R-TER-2 partage le nom court C13 : c'est bien la preuve que la page
    # porte sur une ligne, pas sur un nom.
    autre = client.get("/ligne?ligne=R-TER-2")
    assert "Aucun comptage sur cette ligne" in autre.text


def test_a_missing_line_says_so_instead_of_guessing(tmp_path):
    page = _site(tmp_path).get("/ligne?ligne=R-TER-999")
    assert "pas dans le GTFS national" in page.text
    assert 'href="/rechercher"' in page.text


def test_a_line_page_without_a_parameter_asks_which_one(tmp_path):
    page = _site(tmp_path).get("/ligne")
    assert page.status_code == 200
    assert "Quelle ligne" in page.text


def test_a_reported_missing_train_has_no_line(tmp_path):
    """Un « train signalé » n'a pas de trip_id : on ne lui invente pas une
    ligne, sinon la page en afficherait un comptage qui n'est pas un comptage."""
    client = _site(tmp_path)
    client.post(
        "/api/sessions",
        json={
            "client_id": "jeton",
            "origin_stop_id": "StopArea:Annecy",
            "destination_stop_id": "StopArea:Compiègne",
            "kind": "missing",
        },
    )
    for route_id in ("R-TER-1", "R-TER-2", "R-CAR-1"):
        assert "Aucun comptage" in client.get(f"/ligne?ligne={route_id}").text
