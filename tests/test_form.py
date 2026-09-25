import csv
from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer.app import create_app


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
