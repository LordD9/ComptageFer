"""Le snapshot des cinq états survit au formulaire et à la file hors ligne."""

import csv
import json
from io import StringIO

from tests.test_browser import TER, _reach_form

ETATS = {
    "courant": "SCHEDULED",
    "precedent": "CANCELED",
    "suivant": "ADDED",
    "precedent_meme_type": "DELETED",
    "suivant_meme_type": "UNSCHEDULED",
}


def test_formulaire_hors_ligne_conserve_les_cinq_etats_dans_le_csv(page, site):
    # Le serveur fournit ses vrais trains de fixture. Seuls leurs états et les
    # quatre voisins sont posés explicitement pour distinguer les cinq cellules.
    def contexte(route):
        response = route.fetch()
        trains = response.json()
        for train in trains:
            if train["trip_id"] != TER:
                continue
            train["status"] = ETATS["courant"]
            for key, status in ETATS.items():
                if key == "courant":
                    continue
                train[key] = {
                    "trip_id": key,
                    "kind": train["kind"],
                    "status": status,
                    "departure_time": train["departure_time"],
                    "delay_seconds": 0,
                }
        route.fulfill(response=response, json=trains)

    page.route("**/api/trips?*", contexte)
    _reach_form(page, site)
    page.click("#plus10")
    page.route("**/api/sessions", lambda route: route.abort())
    page.click("#send")
    page.wait_for_function(
        "() => JSON.parse(localStorage.getItem('comptagefer-queue') || '[]').length === 1"
    )
    queued = page.evaluate("JSON.parse(localStorage.getItem('comptagefer-queue'))[0]")
    assert {key: queued["snapshot"][key]["status"] for key in ETATS} == ETATS

    page.unroute("**/api/sessions")
    page.evaluate("window.dispatchEvent(new Event('online'))")
    page.wait_for_function(
        "() => JSON.parse(localStorage.getItem('comptagefer-queue') || '[]').length === 0"
    )
    response = page.request.get(site + "/api/export.csv")
    assert response.status == 200
    rows = list(csv.DictReader(StringIO(response.text().split("\n", 1)[1])))
    assert len(rows) == 1
    columns = {"courant": "courant", "precedent": "precedent", "suivant": "suivant",
               "precedent_meme_type": "precedent_meme_type", "suivant_meme_type": "suivant_meme_type"}
    assert {key: rows[0][column] for key, column in columns.items()} == ETATS
    assert rows[0]["passengers"] == "10"
    persisted = json.loads(page.request.get(site + "/api/sessions").text())[0]
    assert persisted["snapshot"] == queued["snapshot"]
    assert not page.errors
