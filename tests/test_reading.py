from fastapi.testclient import TestClient

from comptagefer.app import create_app


def _count(photo: dict) -> dict:
    return {
        "client_id": "jeton",
        "origin_stop_id": "A",
        "destination_stop_id": "B",
        "origin_name": "Lyon Part Dieu",
        "destination_name": "Nîmes Pont du Gard",
        "trip_id": "TRIP1",
        "passengers": 40,
        "reliability": 70,
        "pseudo": "railfan",
        "snapshot": photo,
    }


def test_saved_count_keeps_the_three_trains_without_the_feed(tmp_path):
    client = TestClient(create_app(tmp_path))
    photo = {
        "precedent": {"trip_id": "PREV", "status": "CANCELED"},
        "courant": {"trip_id": "TRIP1", "status": "SCHEDULED", "delay_seconds": 120},
        "suivant": {"trip_id": "NEXT", "status": "SCHEDULED"},
    }

    stored = client.post("/api/sessions", json=_count(photo))
    listed = client.get("/api/sessions")

    assert stored.status_code == 200
    assert listed.status_code == 200
    row = listed.json()[0]
    assert row["snapshot"] == photo
    assert row["pseudo"] == "railfan"
    assert row["origin_name"] == "Lyon Part Dieu"
    assert not (tmp_path / "rt.db").exists()


def test_optional_load_indicators_are_kept_and_bounded(tmp_path):
    client = TestClient(create_app(tmp_path))
    photo = {"precedent": None, "courant": {"trip_id": "TRIP1", "status": "SCHEDULED"}, "suivant": None}
    body = _count(photo)
    body["seats_free"] = 30
    body["imbalance"] = 15

    stored = client.post("/api/sessions", json=body)
    listed = client.get("/api/sessions").json()

    assert stored.status_code == 200
    assert listed[0]["seats_free"] == 30
    assert listed[0]["imbalance"] == 15

    refused = client.post(
        "/api/sessions",
        json={**body, "client_id": "autre", "imbalance": 140},
    )
    assert refused.status_code == 422


def test_export_and_page_show_the_count_and_the_licence(tmp_path):
    client = TestClient(create_app(tmp_path))
    photo = {
        "precedent": {"trip_id": "PREV", "status": "CANCELED"},
        "courant": {"trip_id": "TRIP1", "status": "SCHEDULED"},
        "suivant": None,
    }
    client.post("/api/sessions", json=_count(photo))

    page = client.get("/comptages")
    exported = client.get("/api/export.csv")

    assert page.status_code == 200
    assert "pas une fréquentation officielle" in page.text
    assert "railfan" in page.text
    assert "Lyon Part Dieu" in page.text
    assert "Licence Ouverte 2.0" in exported.text
    assert "railfan" in exported.text
    assert "CANCELED" in exported.text
