import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.compte import COOKIE_COMPTE, creer_compte, ouvrir_session
from comptagefer.score import score_de


def _count(client: TestClient, client_id: str, kind: str = "count") -> None:
    body = {
        "client_id": client_id,
        "kind": kind,
        "origin_stop_id": "A",
        "destination_stop_id": "B",
        "origin_name": "Lyon",
        "destination_name": "Vienne",
        "passengers": 10,
        "reliability": 80,
        "pseudo": "Auteur",
        "comment": "Note originale",
        "standing": 2,
        "seats_free": 1,
        "imbalance": 0,
        "materiel": "Z 23500",
        "composition": "US",
        "perimetre": "voiture",
        "trip_id": "trip-fige",
        "snapshot": {"courant": {"trip_id": "trip-fige"}},
    }
    if kind == "missing":
        client.post("/api/missing", json=body)
    else:
        client.post("/api/sessions", json=body)


def _session(client: TestClient, database, pseudo: str = "Auteur") -> str:
    compte_id, _ = creer_compte(database, pseudo)
    token, _ = ouvrir_session(database, compte_id)
    client.cookies.set(COOKIE_COMPTE, token)
    return compte_id


def _row(database, client_id: str, kind: str = "count") -> dict:
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM saisie WHERE client_id=? AND kind=?", (client_id, kind)
        ).fetchone()
    assert row is not None
    return dict(row)


def test_owner_can_edit_own_count_without_replacing_frozen_context(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    owner = _session(client, tmp_path / "app.db")
    _count(client, "owner-key")
    before = _row(tmp_path / "app.db", "owner-key")

    response = client.post(
        "/compte/modifier",
        data={"client_id": "owner-key", "kind": "count", "passengers": "24",
              "reliability": "90", "pseudo": "<script>x</script>", "comment": "Corrige",
              "standing": "1", "seats_free": "2", "imbalance": "0",
              "materiel": "Z 23500", "composition": "UM2", "perimetre": "um"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    after = _row(tmp_path / "app.db", "owner-key")
    assert after["passengers"] == 24 and after["reliability"] == 90
    assert after["comment"] == "Corrige" and after["pseudo"] == "<script>x</script>"
    assert after["composition"] == "UM2" and after["perimetre"] == "um"
    for key in ("client_id", "kind", "origin_stop_id", "destination_stop_id", "trip_id",
                "snapshot", "trajet", "created_at", "compte_id"):
        assert after[key] == before[key]
    history = client.get("/compte").text
    assert "modifier" in history.lower()
    edit_page = client.get("/compte/modifier", params={"client_id": "owner-key", "kind": "count"}).text
    assert "&lt;script&gt;" in edit_page and "<script>x</script>" not in edit_page
    assert owner == after["compte_id"]
    assert "24" in client.get("/api/export.csv").text
    assert score_de(tmp_path / "app.db", owner).releves == 1


def test_other_owner_and_anonymous_cannot_read_or_change_count(tmp_path):
    app = create_app(tmp_path)
    owner_client = TestClient(app)
    owner = _session(owner_client, tmp_path / "app.db")
    _count(owner_client, "private-key")
    outsider = TestClient(app)
    _session(outsider, tmp_path / "app.db", "Autre")

    page = outsider.get("/compte/modifier", params={"client_id": "private-key", "kind": "count"})
    assert page.status_code == 404
    changed = outsider.post("/compte/modifier", data={"client_id": "private-key", "kind": "count",
                                                       "passengers": "99"})
    assert changed.status_code == 404
    anonymous = TestClient(app)
    assert anonymous.get("/compte/modifier", params={"client_id": "private-key", "kind": "count"}).status_code == 401
    assert anonymous.post("/compte/modifier", data={"client_id": "private-key", "kind": "count",
                                                      "passengers": "99"}).status_code == 401
    assert _row(tmp_path / "app.db", "private-key")["passengers"] == 10
    assert _row(tmp_path / "app.db", "private-key")["compte_id"] == owner


def test_owner_delete_requires_explicit_confirmation_and_preserves_other_kind(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    owner = _session(client, tmp_path / "app.db")
    _count(client, "same-key", "count")
    _count(client, "same-key", "missing")

    refused = client.post("/compte/supprimer", data={"client_id": "same-key", "kind": "count"})
    assert refused.status_code == 422
    assert _row(tmp_path / "app.db", "same-key", "count")
    assert _row(tmp_path / "app.db", "same-key", "missing")
    deleted = client.post("/compte/supprimer", data={"client_id": "same-key", "kind": "count",
                                                     "confirmation": "oui"}, follow_redirects=False)
    assert deleted.status_code == 303
    with pytest.raises(AssertionError):
        _row(tmp_path / "app.db", "same-key", "count")
    assert _row(tmp_path / "app.db", "same-key", "missing")
    assert "same-key" not in client.get("/api/export.csv").text
    assert score_de(tmp_path / "app.db", owner).releves == 0


def test_admin_edit_requires_admin_session_and_does_not_mutate_without_it(tmp_path):
    app = create_app(tmp_path, admin_token="admin-secret")
    client = TestClient(app)
    _count(client, "admin-protected")
    before = _row(tmp_path / "app.db", "admin-protected")

    assert client.get("/admin/modifier", params={"client_id": "admin-protected", "kind": "count"}).status_code == 401
    refused = client.post("/admin/modifier", data={"client_id": "admin-protected", "kind": "count",
        "passengers": "999", "reliability": "99"})

    assert refused.status_code == 401
    assert _row(tmp_path / "app.db", "admin-protected") == before


def test_admin_can_edit_and_delete_any_count_including_unowned(tmp_path):
    app = create_app(tmp_path, admin_token="admin-secret")
    client = TestClient(app)
    _count(client, "admin-key")
    client.post("/admin/login", data={"token": "admin-secret"})
    page = client.get("/admin").text
    assert "/admin/modifier" in page and "Supprimer" in page
    edited = client.post("/admin/modifier", data={"client_id": "admin-key", "kind": "count",
                                                   "passengers": "33", "reliability": "60",
                                                   "pseudo": "Admin", "comment": "Nettoye",
                                                   "standing": "", "seats_free": "", "imbalance": "",
                                                   "materiel": "", "composition": "", "perimetre": ""})
    assert edited.status_code == 200
    assert _row(tmp_path / "app.db", "admin-key")["passengers"] == 33
    assert _row(tmp_path / "app.db", "admin-key")["compte_id"] is None
    deleted = client.post("/admin/supprimer", data={"client_id": "admin-key", "kind": "count"})
    assert deleted.status_code == 200
    assert client.get("/api/sessions").json() == []


def test_serpent_edit_uses_first_leg_onboard_and_validates_before_update(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    _session(client, tmp_path / "app.db")
    client.post("/api/sessions", json={"client_id": "snake", "kind": "serpent",
        "origin_stop_id": "A", "destination_stop_id": "C", "origin_name": "A", "destination_name": "C",
        "reliability": 70, "legs": [{"stop_id": "A", "onboard": 10},
        {"stop_id": "B", "boarded": 2, "alighted": 1}, {"stop_id": "C", "boarded": 0, "alighted": 3}]})
    old = _row(tmp_path / "app.db", "snake", "serpent")
    response = client.post("/compte/modifier", data={"client_id": "snake", "kind": "serpent",
        "passengers": "999", "reliability": "75", "pseudo": "Auteur", "comment": "",
        "standing": "", "seats_free": "", "imbalance": "", "materiel": "",
        "composition": "", "perimetre": "", "legs": json.dumps([
            {"stop_id": "A", "onboard": 12}, {"stop_id": "B", "boarded": 4, "alighted": 2},
            {"stop_id": "C", "boarded": 0, "alighted": 5}])}, follow_redirects=False)
    assert response.status_code == 303
    changed = _row(tmp_path / "app.db", "snake", "serpent")
    assert changed["passengers"] == 12
    assert json.loads(changed["legs"])[1]["boarded"] == 4
    bad = client.post("/compte/modifier", data={"client_id": "snake", "kind": "serpent",
        "reliability": "75", "legs": "[]"})
    assert bad.status_code == 422
    assert _row(tmp_path / "app.db", "snake", "serpent")["legs"] == changed["legs"]
    assert changed["created_at"] == old["created_at"] and changed["snapshot"] == old["snapshot"]


@pytest.mark.parametrize("replacement", [
    [{"stop_id": "X", "onboard": 10}, {"stop_id": "B", "boarded": 2, "alighted": 1},
     {"stop_id": "C", "boarded": 0, "alighted": 3}],
    [{"stop_id": "A", "onboard": 10}, {"stop_id": "C", "boarded": 2, "alighted": 1},
     {"stop_id": "B", "boarded": 0, "alighted": 3}],
    [{"stop_id": "A", "onboard": 10}, {"stop_id": "B", "boarded": 2, "alighted": 1}],
])
def test_serpent_edit_rejects_changed_stop_identity_without_mutation(tmp_path, replacement):
    app = create_app(tmp_path)
    client = TestClient(app)
    _session(client, tmp_path / "app.db")
    client.post("/api/sessions", json={"client_id": "fixed-stops", "kind": "serpent",
        "origin_stop_id": "A", "destination_stop_id": "C", "origin_name": "A", "destination_name": "C",
        "reliability": 70, "legs": [{"stop_id": "A", "stop_name": "Alpha", "onboard": 10},
        {"stop_id": "B", "stop_name": "Beta", "boarded": 2, "alighted": 1},
        {"stop_id": "C", "stop_name": "Gamma", "boarded": 0, "alighted": 3}]})
    before = _row(tmp_path / "app.db", "fixed-stops", "serpent")

    response = client.post("/compte/modifier", data={"client_id": "fixed-stops", "kind": "serpent",
        "reliability": "80", "legs": json.dumps(replacement)})

    assert response.status_code == 422
    assert _row(tmp_path / "app.db", "fixed-stops", "serpent") == before


def test_account_id_from_form_cannot_reassign_serpent_and_expired_session_is_refused(tmp_path):
    app = create_app(tmp_path)
    owner_client = TestClient(app)
    owner = _session(owner_client, tmp_path / "app.db")
    owner_client.post("/api/sessions", json={"client_id": "owned-serpent", "kind": "serpent",
        "origin_stop_id": "A", "destination_stop_id": "B", "origin_name": "A", "destination_name": "B",
        "reliability": 70, "legs": [{"stop_id": "A", "onboard": 10}, {"stop_id": "B", "boarded": 1, "alighted": 0}]})
    before = _row(tmp_path / "app.db", "owned-serpent", "serpent")
    outsider = TestClient(app)
    _session(outsider, tmp_path / "app.db", "Autre")
    refused = outsider.post("/compte/modifier", data={"client_id": "owned-serpent", "kind": "serpent",
        "compte_id": "forged", "reliability": "99", "legs": json.dumps([
            {"stop_id": "A", "onboard": 99}, {"stop_id": "B", "boarded": 0, "alighted": 0}])})
    assert refused.status_code == 404
    assert _row(tmp_path / "app.db", "owned-serpent", "serpent") == before
    with sqlite3.connect(tmp_path / "app.db") as db:
        db.execute("UPDATE session SET expire = 0 WHERE compte_id = ?", (owner,))
    expired = owner_client.post("/compte/modifier", data={"client_id": "owned-serpent", "kind": "serpent",
        "reliability": "99", "legs": json.dumps([
            {"stop_id": "A", "onboard": 99}, {"stop_id": "B", "boarded": 0, "alighted": 0}])})
    assert expired.status_code == 401
    assert _row(tmp_path / "app.db", "owned-serpent", "serpent") == before


def test_invalid_stop_field_is_refused_without_partial_update(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    _session(client, tmp_path / "app.db")
    client.post("/api/sessions", json={"client_id": "invalid-stop-field", "kind": "serpent",
        "origin_stop_id": "A", "destination_stop_id": "B", "origin_name": "A", "destination_name": "B",
        "reliability": 70, "legs": [{"stop_id": "A", "onboard": 10}, {"stop_id": "B", "boarded": 1, "alighted": 0}]})
    before = _row(tmp_path / "app.db", "invalid-stop-field", "serpent")

    response = client.post("/compte/modifier", data={"client_id": "invalid-stop-field", "kind": "serpent",
        "reliability": "80", "legs_0_onboard": "12", "legs_1_boarded": "pas-un-nombre",
        "legs_1_alighted": "0"})

    assert response.status_code == 422
    assert _row(tmp_path / "app.db", "invalid-stop-field", "serpent") == before


def test_invalid_kind_and_non_integer_boolean_like_values_do_not_change_data(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    _session(client, tmp_path / "app.db")
    _count(client, "validation")
    before = _row(tmp_path / "app.db", "validation")
    response = client.post("/compte/modifier", data={"client_id": "validation", "kind": "other",
        "passengers": "1", "reliability": "80"})
    assert response.status_code == 422
    response = client.post("/compte/modifier", data={"client_id": "validation", "kind": "count",
        "passengers": "1.5", "reliability": "80"})
    assert response.status_code == 422
    assert _row(tmp_path / "app.db", "validation") == before


@pytest.mark.parametrize("acteur,statut", [("anonyme", 401), ("autre", 404), ("expire", 401)])
def test_suppression_refuse_un_autre_auteur_ou_une_session_absente(tmp_path, acteur, statut):
    app = create_app(tmp_path)
    owner_client = TestClient(app)
    owner = _session(owner_client, tmp_path / "app.db")
    _count(owner_client, "suppression-protegee")
    before = _row(tmp_path / "app.db", "suppression-protegee")
    demandeur = TestClient(app)
    if acteur == "autre":
        _session(demandeur, tmp_path / "app.db", "Autre auteur")
    elif acteur == "expire":
        demandeur = owner_client
        with sqlite3.connect(tmp_path / "app.db") as db:
            db.execute("UPDATE session SET expire=0 WHERE compte_id=?", (owner,))
    response = demandeur.post("/compte/supprimer", data={
        "client_id": "suppression-protegee", "kind": "count",
        "compte_id": owner, "confirmation": "oui",
    })
    assert response.status_code == statut
    assert _row(tmp_path / "app.db", "suppression-protegee") == before


@pytest.mark.parametrize("chemin", ["/compte/modifier", "/compte/supprimer", "/admin/modifier", "/admin/supprimer"])
def test_ecriture_refuse_un_formulaire_etranger_meme_avec_session_valide(tmp_path, chemin):
    app = create_app(tmp_path, admin_token="admin-secret")
    client = TestClient(app)
    _session(client, tmp_path / "app.db")
    _count(client, "csrf-protege")
    client.post("/admin/login", data={"token": "admin-secret"})
    before = _row(tmp_path / "app.db", "csrf-protege")
    response = client.post(chemin, data={
        "client_id": "csrf-protege", "kind": "count", "passengers": "99",
        "reliability": "80", "confirmation": "oui",
    }, headers={"Origin": "https://site-etranger.example"})
    assert response.status_code == 403
    assert _row(tmp_path / "app.db", "csrf-protege") == before
