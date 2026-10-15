from fastapi.testclient import TestClient

from comptagefer import retours as retours_module
from comptagefer.app import create_app


def _compter(client: TestClient, client_id: str = "admin-onglets") -> None:
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


def _ouvrir_admin(client: TestClient) -> None:
    response = client.post("/admin/login", data={"token": "secret"})
    assert response.status_code == 200


def test_admin_separe_comptages_et_retours_et_reste_protege(tmp_path):
    client = TestClient(create_app(tmp_path, admin_token="secret"))
    _compter(client)
    assert client.post(
        "/retours",
        data={"message": "message-confidentiel", "email": "contact@example.org"},
        follow_redirects=False,
    ).status_code == 303

    non_connecte = client.get("/admin?onglet=retours")
    assert non_connecte.status_code == 200
    assert "Jeton" in non_connecte.text
    assert "message-confidentiel" not in non_connecte.text
    assert "contact@example.org" not in non_connecte.text

    _ouvrir_admin(client)
    comptages = client.get("/admin")
    assert "Lyon" in comptages.text
    assert "Signalements" in comptages.text
    assert "message-confidentiel" not in comptages.text
    assert "contact@example.org" not in comptages.text
    assert "Retours privés" not in comptages.text
    assert 'href="/admin" aria-current="page"' in comptages.text
    assert 'href="/admin?onglet=retours"' in comptages.text

    retours = client.get("/admin?onglet=retours")
    assert "message-confidentiel" in retours.text
    assert "contact@example.org" in retours.text
    assert "Lyon" not in retours.text
    assert "Signalements" not in retours.text
    assert "Publier maintenant" not in retours.text
    assert 'href="/admin?onglet=retours" aria-current="page"' in retours.text
    assert 'href="/admin"' in retours.text
    assert 'aria-label="Navigation d’administration"' in retours.text
    assert "display: flex" in retours.text
    assert "flex-wrap: wrap" in retours.text

    inconnu = client.get("/admin?onglet=inconnu")
    assert "Lyon" in inconnu.text
    assert "message-confidentiel" not in inconnu.text
    parametre_seul = client.get("/admin?retours_page=2")
    assert "Lyon" in parametre_seul.text
    assert "message-confidentiel" not in parametre_seul.text
    parametre_inconnu = client.get("/admin?onglet=retours&inconnu=oui")
    assert "Lyon" in parametre_inconnu.text
    assert "message-confidentiel" not in parametre_inconnu.text


def test_pagination_et_actions_des_retours_conservent_l_onglet(tmp_path):
    database = tmp_path / "app.db"
    client = TestClient(create_app(tmp_path, admin_token="secret"))
    for numero in range(51):
        assert retours_module.enregistrer(database, f"retour-{numero}") is None
    _ouvrir_admin(client)

    page_deux = client.get("/admin?onglet=retours&retours_page=2")
    assert page_deux.text.count('class="card retour-admin"') == 1
    assert "retour-0" in page_deux.text
    assert "retour-50" not in page_deux.text
    assert "onglet=retours&amp;retours_page=1" in page_deux.text
    assert 'action="/admin/retours/1/traiter?retours_page=2"' in page_deux.text
    assert 'action="/admin/retours/1/supprimer?retours_page=2"' in page_deux.text

    traitee = client.post("/admin/retours/1/traiter?retours_page=2", follow_redirects=False)
    assert traitee.status_code == 303
    assert traitee.headers["location"] == "/admin?onglet=retours&retours_page=2"
    assert "Traité" in client.get(traitee.headers["location"]).text

    supprime = client.post("/admin/retours/1/supprimer?retours_page=2", follow_redirects=False)
    assert supprime.status_code == 303
    assert supprime.headers["location"].startswith("/admin?onglet=retours")
    page_apres_suppression = client.get(supprime.headers["location"])
    assert "Retours privés" in page_apres_suppression.text
    assert "retour-0" not in page_apres_suppression.text
