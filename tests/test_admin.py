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

    deleted = client.post("/admin/supprimer", data={"client_id": "jeton", "kind": "count"})
    assert deleted.status_code == 200
    assert client.get("/api/sessions").json() == []
    assert "Lyon" not in client.get("/comptages").text
    assert "Lyon" not in client.get("/api/export.csv").text


def test_admin_delete_ne_touche_qu_au_genre_demande(tmp_path):
    """Le même navigateur, deux genres, une seule suppression.

    La clé primaire de `saisie` est `(client_id, kind)` : un navigateur peut
    compter un train réel et signaler un train manquant. Supprimer par
    `client_id` seul effacerait les deux, et le signalement de train manquant est
    une information en soi — le supprimer pour corriger un effectif serait une
    perte de données invisible.

    C'est le défaut que ce test attrape : il était possible parce que la route
    ignorait `kind`, alors que la clé ne l'ignorait pas.
    """
    app = create_app(tmp_path, admin_token="secret-admin")
    client = TestClient(app)
    _count(client)
    client.post(
        "/api/missing",
        json={
            "client_id": "jeton",
            "origin_stop_id": "A",
            "destination_stop_id": "B",
            "origin_name": "Lyon",
            "destination_name": "Nîmes",
            "reliability": 50,
        },
    )

    client.post("/admin/login", data={"token": "secret-admin"})
    client.post("/admin/supprimer", data={"client_id": "jeton", "kind": "count"})

    restants = client.get("/api/sessions").json()
    assert [r["kind"] for r in restants] == ["missing"], (
        "le train manquant du même navigateur doit survivre à la suppression du "
        "comptage : ce sont deux lignes, pas une"
    )


def test_admin_delete_refuse_un_genre_absent(tmp_path):
    """Pas de `kind`, pas de suppression.

    Le refus est explicite plutôt que silencieux : une requête qui n'envoie que
    le `client_id` — une page qui aurait oublié le champ caché, un script
    d'avant — ne doit pas repartir avec un « 200 » qui a l'air d'avoir marché.
    """
    app = create_app(tmp_path, admin_token="secret-admin")
    client = TestClient(app)
    _count(client)
    client.post("/admin/login", data={"token": "secret-admin"})

    refused = client.post("/admin/supprimer", data={"client_id": "jeton"})

    assert refused.status_code == 422
    assert len(client.get("/api/sessions").json()) == 1, "le relevé doit être intact"


def test_missing_admin_token_never_opens_the_window(tmp_path):
    client = TestClient(create_app(tmp_path, admin_token=""))
    response = client.post("/admin/login", data={"token": ""})
    assert response.status_code == 401
