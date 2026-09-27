"""La carte : ce qu'elle dessine, et ce qu'elle refuse de dessiner.

La v1 relie les arrêts par un segment droit. Ce n'est pas la voie réelle du
train, et la page le dit. Ces tests verrouillent le contrat : deux points
placeables au minimum, l'ordre du serpent conservé, et l'honnêteté du pied de
page quand un comptage n'a pas de coordonnées.
"""

import csv
from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.carte import counted_features
from comptagefer.offer import import_stop_names

STOPS = [
    ("StopArea:Lyon", "Lyon Part-Dieu", "", 45.7608, 4.8557),
    ("StopPoint:LyonA", "Lyon Part-Dieu voie A", "StopArea:Lyon", 45.7608, 4.8557),
    ("StopArea:Vienne", "Vienne", "", 45.4481, 4.8789),
    ("StopPoint:VienneA", "Vienne voie 1", "StopArea:Vienne", 45.4481, 4.8789),
    ("StopArea:Valence", "Valence", "", 44.9294, 4.9925),
    ("StopPoint:ValenceA", "Valence voie A", "StopArea:Valence", 44.9294, 4.9925),
    # Une voie sans coordonnées : c'est le cas réel dans le GTFS national, et
    # elle doit hériter de la position de sa gare.
    ("StopPoint:VienneB", "Vienne voie 2", "StopArea:Vienne", "", ""),
    # Une gare sans position du tout : rien à dessiner pour elle.
    ("StopArea:Nulle", "Gare fantôme", "", "", ""),
]


def _stops_db(tmp_path: Path) -> Path:
    csv_path = tmp_path / "stops.txt"
    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"])
        for stop_id, name, parent, lat, lon in STOPS:
            writer.writerow([stop_id, name, lat, lon, "0" if parent else "1", parent])
    database = tmp_path / "stops.db"
    import_stop_names(database, csv_path)
    return database


def _count(**extra) -> dict:
    body = {
        "client_id": "jeton",
        "origin_stop_id": "StopArea:Lyon",
        "destination_stop_id": "StopArea:Valence",
        "origin_name": "Lyon Part-Dieu",
        "destination_name": "Valence",
        "passengers": 40,
        "reliability": 70,
        "pseudo": "railfan",
    }
    body.update(extra)
    return body


def test_a_count_becomes_a_segment_between_its_two_stops(tmp_path):
    stops = _stops_db(tmp_path)
    rows = [
        {
            "client_id": "jeton",
            "kind": "count",
            "origin_stop_id": "StopArea:Lyon",
            "destination_stop_id": "StopArea:Valence",
            "origin_name": "Lyon Part-Dieu",
            "destination_name": "Valence",
            "passengers": 40,
            "pseudo": "railfan",
            "legs": None,
        }
    ]
    features = counted_features(stops, rows)
    assert len(features) == 1
    assert features[0]["points"] == [[45.7608, 4.8557], [44.9294, 4.9925]]
    assert features[0]["stops"] == ["Lyon Part-Dieu", "Valence"]


def test_the_snake_draws_every_stop_it_recorded_in_order(tmp_path):
    """Un serpent a un parcours, pas un segment : le tracer comme un couple
    origine-destination effacerait exactement ce qui a été compté."""
    stops = _stops_db(tmp_path)
    rows = [
        {
            "client_id": "serpent",
            "kind": "serpent",
            "origin_stop_id": "StopArea:Lyon",
            "destination_stop_id": "StopArea:Valence",
            "origin_name": "Lyon Part-Dieu",
            "destination_name": "Valence",
            "passengers": 40,
            "pseudo": None,
            "legs": [
                {"stop_id": "StopPoint:LyonA", "stop_name": "Lyon Part-Dieu", "onboard": 40},
                {"stop_id": "StopPoint:VienneA", "stop_name": "Vienne", "boarded": 3},
                {"stop_id": "StopPoint:ValenceA", "stop_name": "Valence", "boarded": 0},
            ],
        }
    ]
    feature = counted_features(stops, rows)[0]
    assert feature["stops"] == ["Lyon Part-Dieu", "Vienne", "Valence"]
    assert len(feature["points"]) == 3
    assert feature["points"][1] == [45.4481, 4.8789]


def test_a_stop_point_without_coordinates_inherits_its_station(tmp_path):
    stops = _stops_db(tmp_path)
    rows = [
        {
            "client_id": "serpent",
            "kind": "serpent",
            "origin_stop_id": "StopPoint:LyonA",
            "destination_stop_id": "StopPoint:ValenceA",
            "passengers": 10,
            "legs": [
                {"stop_id": "StopPoint:LyonA", "stop_name": "Lyon Part-Dieu"},
                {"stop_id": "StopPoint:VienneB", "stop_name": "Vienne voie 2"},
                {"stop_id": "StopPoint:ValenceA", "stop_name": "Valence"},
            ],
        }
    ]
    feature = counted_features(stops, rows)[0]
    assert feature["points"][1] == [45.4481, 4.8789], "la voie sans position prend celle de sa gare"


def test_a_count_without_two_placeable_stops_is_left_out(tmp_path):
    """Mieux vaut une carte honnête qu'un point posé au hasard sur la France."""
    stops = _stops_db(tmp_path)
    rows = [
        {
            "client_id": "fantome",
            "kind": "count",
            "origin_stop_id": "StopArea:Lyon",
            "destination_stop_id": "StopArea:Nulle",
            "origin_name": "Lyon Part-Dieu",
            "destination_name": "Gare fantôme",
            "passengers": 5,
            "legs": None,
        }
    ]
    assert counted_features(stops, rows) == []


def test_the_page_says_how_many_counts_it_could_not_place(tmp_path):
    stops = _stops_db(tmp_path)
    client = TestClient(create_app(tmp_path))
    client.post("/api/sessions", json=_count())
    client.post(
        "/api/sessions",
        json=_count(
            client_id="fantome",
            destination_stop_id="StopArea:Nulle",
            destination_name="Gare fantôme",
        ),
    )

    page = client.get("/carte")
    assert page.status_code == 200
    assert page.text.count("2 comptages au total") == 1
    assert "1 sur la carte" in page.text
    assert "Lyon Part-Dieu → Valence" in page.text


def test_the_map_page_links_back_to_the_list_and_the_method(tmp_path):
    _stops_db(tmp_path)
    page = TestClient(create_app(tmp_path)).get("/carte")
    assert 'href="/comptages"' in page.text
    assert 'href="/methode"' in page.text
    assert "pas une fréquentation officielle" in page.text
    # Le segment droit est une limite de la v1, pas un détail : elle est écrite.
    assert "ne suit pas la voie réelle" in page.text


def test_an_empty_map_invites_to_count_instead_of_showing_an_empty_frame(tmp_path):
    _stops_db(tmp_path)
    page = TestClient(create_app(tmp_path)).get("/carte")
    assert "Aucun comptage à placer" in page.text
    assert 'href="/"' in page.text
    assert "0 comptage au total" in page.text


def test_a_stop_name_cannot_inject_html_into_the_page(tmp_path):
    _stops_db(tmp_path)
    client = TestClient(create_app(tmp_path))
    client.post("/api/sessions", json=_count(pseudo="<img src=x onerror=alert(1)>"))

    page = client.get("/carte")
    assert "<img src=x" not in page.text
    assert "&lt;img src=x" in page.text
