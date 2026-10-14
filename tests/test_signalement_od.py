import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.offer import open_stops

ORIGINE_ID = "StopArea:OD_TEST_ORIGINE"
DESTINATION_ID = "StopArea:OD_TEST_DESTINATION"
ORIGINE = "Gare Origine exacte"
DESTINATION = "Gare Destination exacte"


def _ecrire_stops(data_dir: Path) -> None:
    with open_stops(data_dir / "stops.db") as connection:
        connection.executemany(
            "INSERT INTO stop (stop_id, name, is_area) VALUES (?, ?, 1)",
            [
                (ORIGINE_ID, ORIGINE),
                (DESTINATION_ID, DESTINATION),
                (f"{ORIGINE_ID}:voisine", "Gare Origine voisine"),
            ],
        )


def _creer_signalement(client: TestClient, client_id: str) -> None:
    response = client.post(
        "/api/missing",
        json={
            "client_id": client_id,
            "origin_stop_id": ORIGINE_ID,
            "destination_stop_id": DESTINATION_ID,
        },
    )
    assert response.status_code == 200
    assert response.json()["stored"] is True


def test_les_anciens_signalements_ids_seuls_affichent_les_noms_du_referentiel(tmp_path):
    _ecrire_stops(tmp_path)
    with TestClient(create_app(tmp_path)) as client:
        _creer_signalement(client, "ancien")
        with sqlite3.connect(tmp_path / "app.db") as connection:
            connection.execute(
                "UPDATE saisie SET origin_name = NULL, destination_name = NULL "
                "WHERE client_id = ? AND kind = 'missing'",
                ("ancien",),
            )

        liste = client.get("/comptages").text
        sessions = client.get("/api/sessions").json()
        gare = client.get("/gare", params={"stop": ORIGINE_ID}).text
        detail = client.get(
            "/releve", params={"client_id": "ancien", "kind": "missing"}
        ).text

    assert f"Train signalé sur {ORIGINE} → {DESTINATION}." in liste
    assert f"{ORIGINE} → {DESTINATION}</a>" in liste
    session = next(row for row in sessions if row["client_id"] == "ancien")
    assert (session["origin_name"], session["destination_name"]) == (ORIGINE, DESTINATION)
    assert f"Train signalé sur {ORIGINE} → {DESTINATION}." in gare
    assert f"<dt>Trajet</dt><dd>{ORIGINE} → {DESTINATION}</dd>" in detail
    for page in (liste, gare, detail):
        assert ORIGINE_ID not in page
        assert DESTINATION_ID not in page


def test_un_nouveau_signalement_sans_noms_enregistre_ceux_du_referentiel(tmp_path):
    _ecrire_stops(tmp_path)
    with TestClient(create_app(tmp_path)) as client:
        _creer_signalement(client, "nouveau")

    with sqlite3.connect(tmp_path / "app.db") as connection:
        row = connection.execute(
            "SELECT origin_name, destination_name FROM saisie "
            "WHERE client_id = ? AND kind = 'missing'",
            ("nouveau",),
        ).fetchone()

    assert row == (ORIGINE, DESTINATION)


def test_les_noms_enregistres_survivent_a_la_disparition_des_ids_du_referentiel(tmp_path):
    _ecrire_stops(tmp_path)
    with TestClient(create_app(tmp_path)) as client:
        _creer_signalement(client, "ids-disparus")
        with open_stops(tmp_path / "stops.db") as connection:
            connection.execute(
                "DELETE FROM stop WHERE stop_id IN (?, ?)",
                (ORIGINE_ID, DESTINATION_ID),
            )

        liste = client.get("/comptages").text
        sessions = client.get("/api/sessions").json()
        detail = client.get(
            "/releve", params={"client_id": "ids-disparus", "kind": "missing"}
        ).text

    assert f"{ORIGINE} → {DESTINATION}" in liste
    assert f"{ORIGINE} → {DESTINATION}" in detail
    row = next(row for row in sessions if row["client_id"] == "ids-disparus")
    assert (row["origin_name"], row["destination_name"]) == (ORIGINE, DESTINATION)
    assert ORIGINE_ID not in liste + detail
    assert DESTINATION_ID not in liste + detail


def test_les_noms_enregistres_survivent_a_l_absence_du_referentiel(tmp_path):
    _ecrire_stops(tmp_path)
    with TestClient(create_app(tmp_path)) as client:
        _creer_signalement(client, "referentiel-absent")
        (tmp_path / "stops.db").unlink()

        liste = client.get("/comptages").text
        detail = client.get(
            "/releve", params={"client_id": "referentiel-absent", "kind": "missing"}
        ).text

    assert f"{ORIGINE} → {DESTINATION}" in liste
    assert f"{ORIGINE} → {DESTINATION}" in detail
    assert ORIGINE_ID not in liste + detail
    assert DESTINATION_ID not in liste + detail


def test_un_nouveau_signalement_ignore_les_noms_fournis_par_le_client(tmp_path):
    _ecrire_stops(tmp_path)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/missing",
            json={
                "client_id": "noms-forges",
                "origin_stop_id": ORIGINE_ID,
                "destination_stop_id": DESTINATION_ID,
                "origin_name": "Fausse origine",
                "destination_name": "Fausse destination",
            },
        )

    assert response.status_code == 200
    with sqlite3.connect(tmp_path / "app.db") as connection:
        row = connection.execute(
            "SELECT origin_name, destination_name FROM saisie "
            "WHERE client_id = ? AND kind = 'missing'",
            ("noms-forges",),
        ).fetchone()

    assert row == (ORIGINE, DESTINATION)


def test_un_arret_inconnu_ne_revele_ni_id_ni_nom_client(tmp_path):
    _ecrire_stops(tmp_path)
    origine_id = "StopArea:OD_INCONNU_ORIGINE"
    destination_id = "StopArea:OD_INCONNU_DESTINATION"
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/missing",
            json={
                "client_id": "inconnu",
                "origin_stop_id": origine_id,
                "destination_stop_id": destination_id,
                "origin_name": "Nom inventé",
                "destination_name": "Autre nom inventé",
            },
        )
        liste = client.get("/comptages").text
        detail = client.get(
            "/releve", params={"client_id": "inconnu", "kind": "missing"}
        ).text

    assert response.status_code == 200
    with sqlite3.connect(tmp_path / "app.db") as connection:
        row = connection.execute(
            "SELECT origin_name, destination_name FROM saisie "
            "WHERE client_id = ? AND kind = 'missing'",
            ("inconnu",),
        ).fetchone()
    assert row == (None, None)
    assert "— → —" in liste
    for page in (liste, detail):
        assert origine_id not in page
        assert destination_id not in page
        assert "Nom inventé" not in page
        assert "Autre nom inventé" not in page


def test_les_noms_du_referentiel_restent_echappes_dans_les_pages(tmp_path):
    _ecrire_stops(tmp_path)
    origine_id = "StopArea:OD_HTML_ORIGINE"
    destination_id = "StopArea:OD_HTML_DESTINATION"
    origine = "<script>alert('x')</script>"
    destination = "A & B <gare>"
    with open_stops(tmp_path / "stops.db") as connection:
        connection.executemany(
            "INSERT INTO stop (stop_id, name, is_area) VALUES (?, ?, 1)",
            [(origine_id, origine), (destination_id, destination)],
        )

    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/missing",
            json={
                "client_id": "echappement",
                "origin_stop_id": origine_id,
                "destination_stop_id": destination_id,
            },
        )
        liste = client.get("/comptages").text
        detail = client.get(
            "/releve", params={"client_id": "echappement", "kind": "missing"}
        ).text

    assert response.status_code == 200
    texte_echappe = "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt; → A &amp; B &lt;gare&gt;"
    assert texte_echappe in liste
    assert texte_echappe in detail
    assert origine not in liste
    assert origine not in detail
