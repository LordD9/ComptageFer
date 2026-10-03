import csv
import re
from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer import carte as carte_module
from comptagefer.app import create_app
from comptagefer.page import PAGE

# Le tutoiement revient par une seule phrase, et les tests fonctionnels ne le
# voient pas. Issue #24 : on le cherche donc explicitement dans tout ce que
# l'utilisateur lit, HTML comme JavaScript.
_TU_TOIEMENT = re.compile(
    r"\b(ton|ta|tes|toi|tu|choisis|écris|verifie|réessaie|réessaye)\b",
    re.IGNORECASE,
)


def test_no_page_talks_to_the_visitor_in_the_second_person(tmp_path):
    client = TestClient(create_app(tmp_path))
    pages = {
        "/": client.get("/").text,
        "/methode": client.get("/methode").text,
        "/comptages": client.get("/comptages").text,
    }
    for chemin, texte in pages.items():
        trouves = sorted(set(_TU_TOIEMENT.findall(texte)))
        assert not trouves, f"{chemin} tutoie encore : {trouves}"
    # Le nom du projet reste en dur dans la page de carte, hors de la variable.
    carte = carte_module.map_page([], 0)
    assert not _TU_TOIEMENT.search(carte), "la carte tutoie"
    # Le script de la page de comptage est embarqué dans PAGE.
    assert not _TU_TOIEMENT.search(PAGE), "la page de comptage tutoie"


def _stops(data: Path) -> None:
    path = data / "stops.txt"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"]
        )
        writer.writerow(["A", "Lyon Part-Dieu", "45.76", "4.86", "1", ""])
        writer.writerow(["B", "Lyon Perrache", "45.75", "4.83", "1", ""])
    from comptagefer.offer import import_stop_names

    import_stop_names(data / "stops.db", path)


def test_home_is_a_mobile_form(tmp_path: Path):
    client = TestClient(create_app(tmp_path))
    page = client.get("/")
    assert page.status_code == 200
    assert 'name="viewport"' in page.text
    assert "Origine" in page.text
    assert "Voyageurs dans le train" in page.text


def test_the_form_asks_for_the_od_of_the_counted_trip(tmp_path):
    """Issue #25 : la fenêtre de saisie doit dire que les gares sont celles du
    trajet COMPTÉ, pas l'OD de la ligne, et rappeler le cas terminus/origine
    quand on compte en gare sans monter dans le train."""
    page = TestClient(create_app(tmp_path)).get("/").text
    for attendu in (
        "du trajet compté",
        "et non pas celles de la ligne",
        "terminus",
        "origine",
        'id="od-rappel"',
    ):
        assert attendu in page, f"la saisie ne dit pas : {attendu!r}"
    for retire in (
        "les deux gares encadrantes",
        "même si la ligne est plus longue",
        "Comptage en gare sans monter dans le train",
    ):
        assert retire not in page, f"le texte redondant est toujours visible : {retire!r}"


def test_form_keeps_a_failed_count_and_asks_for_the_load_indicators(tmp_path):
    page = TestClient(create_app(tmp_path)).get("/").text
    assert 'src="/offline.js"' in page
    assert "comptagefer-queue" in page
    assert "places assises" in page
    assert "Écart de charge" in page
    assert "addEventListener(\"online\"" in page


def test_stop_search_and_count_are_stored_once(tmp_path: Path):
    _stops(tmp_path)
    client = TestClient(create_app(tmp_path))
    found = client.get("/api/stops", params={"q": "part"}).json()
    assert found == [{"stop_id": "A", "name": "Lyon Part-Dieu"}]

    body = {
        "client_id": "jeton",
        "origin_stop_id": "A",
        "destination_stop_id": "B",
        "trip_id": "TRIP1",
        "passengers": 42,
        "reliability": 80,
        "snapshot": [{"trip_id": "TRIP1", "status": "SCHEDULED"}],
    }
    first = client.post("/api/sessions", json=body)
    second = client.post("/api/sessions", json=body)
    assert first.status_code == 200
    assert first.json()["stored"] is True
    assert second.json()["stored"] is False

    refused = client.post("/api/sessions", json={**body, "client_id": "autre", "passengers": -1})
    assert refused.status_code == 422
