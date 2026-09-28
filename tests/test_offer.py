import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.transit import gtfs_realtime_pb2

from comptagefer.offer import (
    _stop_family,
    import_stop_names,
    search_stops,
    trips_serving,
)
from comptagefer.rt import store_trip_updates


def _stops_file(path: Path, rows: list[list[str]]) -> Path:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"]
        )
        writer.writerows(rows)
    return path


def test_search_finds_a_stop_area_by_name(tmp_path: Path):
    stops = _stops_file(
        tmp_path / "stops.txt",
        [
            ["StopArea:OCE87756056", "Nice-Ville", "43.7", "7.26", "1", ""],
            ["StopPoint:OCE-87756056", "Nice-Ville", "43.7", "7.26", "0", "StopArea:OCE87756056"],
        ],
    )

    database = tmp_path / "stops.db"
    assert import_stop_names(database, stops) == 2
    assert search_stops(database, "nice") == [
        {"stop_id": "StopArea:OCE87756056", "name": "Nice-Ville"}
    ]


def test_search_returns_one_row_for_a_gare_split_in_two_areas(tmp_path: Path):
    """Le GTFS national décrit « Grenoble » comme une aire CTE et une aire
    TER/TGV. Les deux doivent se lire comme une gare, pas comme deux gares dont
    une ne rend aucun train."""
    stops = _stops_file(
        tmp_path / "stops.txt",
        [
            ["StopArea:OCE87335521", "Grenoble", "45.192693", "5.714366", "1", ""],
            ["StopPoint:OCECar TER-87335521", "Grenoble", "45.192693", "5.714366", "0", "StopArea:OCE87335521"],
            ["StopArea:OCE87747006", "Grenoble", "45.191491", "5.714548", "1", ""],
            ["StopPoint:OCETrain TER-87747006", "Grenoble", "45.191491", "5.714548", "0", "StopArea:OCE87747006"],
            ["StopPoint:OCEOUIGO-87747006", "Grenoble", "45.191491", "5.714548", "0", "StopArea:OCE87747006"],
        ],
    )

    database = tmp_path / "stops.db"
    import_stop_names(database, stops)
    found = search_stops(database, "grenoble")

    assert found == [{"stop_id": "StopArea:OCE87747006", "name": "Grenoble"}]


def test_stop_family_covers_the_coach_area_of_a_split_gare(tmp_path: Path):
    """Choisir la gare affichée doit donner accès aux trains des deux aires :
    c'est l'aire de coach qui disparaissait de la liste des offres."""
    stops = _stops_file(
        tmp_path / "stops.txt",
        [
            ["StopArea:OCE87335521", "Grenoble", "45.192693", "5.714366", "1", ""],
            ["StopPoint:OCECar TER-87335521", "Grenoble", "45.192693", "5.714366", "0", "StopArea:OCE87335521"],
            ["StopArea:OCE87747006", "Grenoble", "45.191491", "5.714548", "1", ""],
            ["StopPoint:OCETrain TER-87747006", "Grenoble", "45.191491", "5.714548", "0", "StopArea:OCE87747006"],
        ],
    )

    database = tmp_path / "stops.db"
    import_stop_names(database, stops)

    assert set(_stop_family(database, "StopArea:OCE87747006")) == {
        "StopArea:OCE87747006",
        "StopPoint:OCETrain TER-87747006",
        "StopArea:OCE87335521",
        "StopPoint:OCECar TER-87335521",
    }
    # L'aire de coach, si elle est sélectionnée, donne le même accès.
    assert set(_stop_family(database, "StopArea:OCE87335521")) == set(
        _stop_family(database, "StopArea:OCE87747006")
    )
    # Un StopPoint demandé seul ne tire pas les quais voisins.
    assert _stop_family(database, "StopPoint:OCETrain TER-87747006") == [
        "StopPoint:OCETrain TER-87747006"
    ]


def test_search_keeps_two_distinct_places_sharing_a_name(tmp_path: Path):
    """`Lérouville` est à 641 m de son homonyme : deux gares, pas une. Les
    confondre compterait une interstation qui n'existe pas."""
    stops = _stops_file(
        tmp_path / "stops.txt",
        [
            ["StopArea:OCE87175240", "Lérouville", "48.796902", "5.538437", "1", ""],
            ["StopPoint:OCETrain TER-87175240", "Lérouville", "48.796902", "5.538437", "0", "StopArea:OCE87175240"],
            ["StopArea:OCE87534065", "Lérouville", "48.791881", "5.542713", "1", ""],
            ["StopPoint:OCECRE-87534065", "Lérouville", "48.791881", "5.542713", "0", "StopArea:OCE87534065"],
        ],
    )

    database = tmp_path / "stops.db"
    import_stop_names(database, stops)

    assert len(search_stops(database, "lérouville")) == 2
    assert _stop_family(database, "StopArea:OCE87175240") == [
        "StopArea:OCE87175240",
        "StopPoint:OCETrain TER-87175240",
    ]


def test_search_merges_two_capitalisations_of_one_gare(tmp_path: Path):
    """`Saint-Hilaire-De-Riez` et `Saint-Hilaire-de-Riez`, à 78 m : une gare."""
    stops = _stops_file(
        tmp_path / "stops.txt",
        [
            ["StopArea:OCE87389924", "Saint-Hilaire-De-Riez", "46.716760", "-1.948609", "1", ""],
            ["StopPoint:OCECar TER-87389924", "Saint-Hilaire-De-Riez", "46.716760", "-1.948609", "0", "StopArea:OCE87389924"],
            ["StopArea:OCE87486563", "Saint-Hilaire-de-Riez", "46.716629", "-1.949617", "1", ""],
            ["StopPoint:OCETrain TER-87486563", "Saint-Hilaire-de-Riez", "46.716629", "-1.949617", "0", "StopArea:OCE87486563"],
        ],
    )

    database = tmp_path / "stops.db"
    import_stop_names(database, stops)

    assert search_stops(database, "saint-hilaire") == [
        {"stop_id": "StopArea:OCE87486563", "name": "Saint-Hilaire-de-Riez"}
    ]


def test_search_keeps_an_area_without_coordinates_apart(tmp_path: Path):
    """Sans coordonnées, rien ne dit que deux homonymes sont le même lieu."""
    stops = _stops_file(
        tmp_path / "stops.txt",
        [
            ["StopArea:A", "Clermont", "", "", "1", ""],
            ["StopPoint:OCEA", "Clermont", "", "", "0", "StopArea:A"],
            ["StopArea:B", "Clermont", "45.7", "3.1", "1", ""],
            ["StopPoint:OCEB", "Clermont", "45.7", "3.1", "0", "StopArea:B"],
        ],
    )

    database = tmp_path / "stops.db"
    import_stop_names(database, stops)

    assert len(search_stops(database, "clermont")) == 2


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
