from fastapi.testclient import TestClient

from comptagefer.app import create_app


def _count(client: TestClient, client_id: str = "jeton") -> None:
    response = client.post(
        "/api/sessions",
        json={
            "client_id": client_id,
            "origin_stop_id": "A",
            "destination_stop_id": "B",
            "origin_name": "Lyon",
            "destination_name": "Nîmes",
            "passengers": 10,
            "reliability": 80,
        },
    )
    assert response.status_code == 200


def test_admin_deletes_a_count_only_with_the_token(tmp_path):
    app = create_app(tmp_path, admin_token="secret-admin")
    client = TestClient(app)
    _count(client)

    refused = client.post("/admin/login", data={"token": "mauvais"})
    assert refused.status_code == 401
    assert client.get("/admin").text.count("Supprimer") == 0

    opened = client.post("/admin/login", data={"token": "secret-admin"})
    assert opened.status_code == 200
    assert "Lyon" in opened.text
    assert "Supprimer" in opened.text

    deleted = client.post("/admin/supprimer", data={"client_id": "jeton"})
    assert deleted.status_code == 200
    assert client.get("/api/sessions").json() == []
    assert "Lyon" not in client.get("/comptages").text
    assert "Lyon" not in client.get("/api/export.csv").text


def test_missing_admin_token_never_opens_the_window(tmp_path):
    client = TestClient(create_app(tmp_path, admin_token=""))
    response = client.post("/admin/login", data={"token": ""})
    assert response.status_code == 401
