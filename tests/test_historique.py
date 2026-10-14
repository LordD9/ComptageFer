"""Contrat serveur des comptages historiques et de leur exposition publique."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import comptagefer.app as app_module
from comptagefer.app import create_app
from comptagefer.rt import _connect
from tests.conftest import TER, _ecrire_gtfs, _ecrire_stops

PARIS = ZoneInfo("Europe/Paris")


def _application_avec_offre(tmp_path: Path) -> tuple[TestClient, Path, datetime]:
    data = tmp_path / "data"
    data.mkdir()
    _ecrire_stops(tmp_path, data)
    _ecrire_gtfs(tmp_path, data)
    jour = (datetime.now(PARIS).date() - timedelta(days=4)).isoformat()
    with sqlite3.connect(data / "timetable.db") as connection:
        service_id = connection.execute(
            "SELECT service_id FROM circulation WHERE trip_id = ?", (TER,)
        ).fetchone()[0]
        connection.execute(
            "INSERT OR REPLACE INTO service_day (service_id, day) VALUES (?, ?)",
            (service_id, jour),
        )
        depart_sec = connection.execute(
            "SELECT depart_sec FROM passage WHERE trip_id = ? AND stop_id = ?",
            (TER, "StopPoint:LyonA"),
        ).fetchone()[0]
    depart = datetime.combine(datetime.fromisoformat(jour).date(), datetime.min.time(), PARIS)
    depart += timedelta(seconds=depart_sec)
    return TestClient(create_app(data)), data, depart


def _corps_historique(depart: datetime, **changes) -> dict:
    return {
        "client_id": "historique-1",
        "origin_stop_id": "StopArea:Lyon",
        "destination_stop_id": "StopArea:Valence",
        "origin_name": "Lyon Part-Dieu",
        "destination_name": "Valence",
        "trip_id": TER,
        "passengers": 42,
        "reliability": 80,
        "is_historical": True,
        "observed_at": depart.isoformat(),
        "snapshot": {
            "courant": {
                "trip_id": TER,
                "status": "CANCELED",
                "delay_seconds": 9999,
                "etat": "supprimé",
            }
        },
        **changes,
    }


def test_recherche_historique_retourne_offre_theorique_sans_temps_reel(tmp_path):
    client, data, depart = _application_avec_offre(tmp_path)
    fetched_at = datetime.now(UTC).isoformat()
    with _connect(data / "rt.db") as connection:
        connection.execute(
            "INSERT INTO trip_update (trip_id, start_date, schedule_relationship, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            (TER, depart.astimezone(PARIS).strftime("%Y%m%d"), "CANCELED", fetched_at),
        )
        connection.execute(
            "INSERT INTO stop_update (trip_id, fetched_at, stop_id, departure_delay) "
            "VALUES (?, ?, ?, ?)",
            (TER, fetched_at, "StopPoint:LyonA", 9999),
        )

    response = client.get(
        "/api/trips/historical",
        params={
            "from": "StopArea:Lyon",
            "to": "StopArea:Valence",
            "at": depart.isoformat(),
        },
    )

    assert response.status_code == 200
    train = next(item for item in response.json() if item["trip_id"] == TER)
    assert train["status"] is None
    assert train["delay_seconds"] is None
    assert train["departure_time"] == depart.isoformat()


def test_enregistrement_historique_verifie_offre_et_remplace_photo_client(tmp_path):
    client, _data, depart = _application_avec_offre(tmp_path)

    response = client.post("/api/sessions", json=_corps_historique(depart))

    assert response.status_code == 200, response.text
    row = client.get("/api/sessions").json()[0]
    assert row["is_historical"] is True
    assert datetime.fromisoformat(row["observed_at"]).astimezone(UTC) == depart.astimezone(UTC)
    assert datetime.fromisoformat(row["created_at"]).astimezone(UTC) > depart.astimezone(UTC)
    assert row["snapshot"]["source"] == "offre théorique"
    assert row["snapshot"]["courant"]["trip_id"] == TER
    assert row["snapshot"]["courant"]["status"] is None
    assert row["snapshot"]["courant"]["delay_seconds"] is None
    assert row["snapshot"]["courant"]["etat"] == "inconnu"
    assert "CANCELED" not in json.dumps(row["snapshot"])
    assert row["trajet"]["service_date"] == depart.astimezone(PARIS).date().isoformat()


def test_enregistrement_historique_refuse_trip_inconnu_ou_mauvaise_desserte(tmp_path):
    client, _data, depart = _application_avec_offre(tmp_path)

    mauvais_trip = client.post(
        "/api/sessions", json=_corps_historique(depart, client_id="trip", trip_id="inconnu")
    )
    mauvaise_desserte = client.post(
        "/api/sessions",
        json=_corps_historique(
            depart,
            client_id="desserte",
            destination_stop_id="StopArea:absente",
        ),
    )
    mauvaise_date = client.post(
        "/api/sessions",
        json=_corps_historique(
            depart + timedelta(days=1), client_id="date-invalide"
        ),
    )

    assert mauvais_trip.status_code == 422
    assert mauvaise_desserte.status_code == 422
    assert mauvaise_date.status_code == 422
    assert client.get("/api/sessions").json() == []


def test_bornes_quinze_jours_appliquees_a_la_recherche_et_a_lenregistrement(tmp_path):
    client, _data, depart = _application_avec_offre(tmp_path)
    trop_ancien = (datetime.now(UTC) - timedelta(days=15, seconds=1)).isoformat()
    futur = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()

    for at in (trop_ancien, futur):
        response = client.get(
            "/api/trips/historical",
            params={"from": "StopArea:Lyon", "to": "StopArea:Valence", "at": at},
        )
        assert response.status_code == 422

    for index, observed_at in enumerate((trop_ancien, futur)):
        response = client.post(
            "/api/sessions",
            json=_corps_historique(
                depart, client_id=f"hors-borne-{index}", observed_at=observed_at
            ),
        )
        assert response.status_code == 422
    assert client.get("/api/sessions").json() == []


def test_timestamp_naif_refuse_et_comptage_courant_garde_observation_serveur(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.post(
        "/api/sessions",
        json={
            "client_id": "courant",
            "origin_stop_id": "A",
            "destination_stop_id": "B",
            "passengers": 8,
            "reliability": 80,
            "observed_at": "2001-01-01T12:00:00",
        },
    )

    assert response.status_code == 200
    row = client.get("/api/sessions").json()[0]
    assert row["is_historical"] is False
    observed_at = datetime.fromisoformat(row["observed_at"])
    assert observed_at.tzinfo is not None
    assert abs((datetime.now(UTC) - observed_at.astimezone(UTC)).total_seconds()) < 10

    old = client.post(
        "/api/sessions",
        json=_corps_historique(
            datetime.now(PARIS).replace(tzinfo=None) - timedelta(days=3),
            client_id="naif",
        ),
    )
    assert old.status_code == 422


def test_historique_et_od_d_un_train_signale_sont_lisibles_publiquement(tmp_path):
    client, _data, depart = _application_avec_offre(tmp_path)
    response = client.post(
        "/api/missing",
        json={
            "client_id": "missing-historique",
            "origin_stop_id": "StopArea:Lyon",
            "destination_stop_id": "StopArea:Valence",
            "origin_name": "Lyon Part-Dieu",
            "destination_name": "Valence",
            "trip_id": "trip-client-hostile",
            "snapshot": {"courant": {"status": "CANCELED", "delay_seconds": 9000}},
            "is_historical": True,
            "observed_at": depart.isoformat(),
        },
    )
    assert response.status_code == 200, response.text

    api_row = client.get("/api/sessions").json()[0]
    liste = client.get("/comptages").text
    tableau = client.get("/comptages", params={"vue": "table"}).text
    releve = client.get(
        "/releve", params={"client_id": "missing-historique", "kind": "missing"}
    ).text

    assert api_row["is_historical"] is True
    assert api_row["observed_at"]
    assert api_row["trip_id"] is None
    assert api_row["snapshot"]["source"] == "offre théorique"
    assert api_row["snapshot"]["courant"] is None
    assert "CANCELED" not in json.dumps(api_row["snapshot"])
    assert "Lyon Part-Dieu → Valence" in liste
    assert "Train signalé sur Lyon Part-Dieu → Valence" in liste
    assert "temps réel inconnu" in liste.lower()
    assert "historique" in liste.lower()
    assert "Historique · saisi le" in tableau
    assert "Lyon Part-Dieu → Valence" in releve
    assert "Temps réel inconnu" in releve
    assert "Train signalé" in releve


def test_bornes_exactes_de_la_fenetre_historique_sont_inclusives(tmp_path, monkeypatch):
    client, _data, depart = _application_avec_offre(tmp_path)
    maintenant = depart.astimezone(UTC) + timedelta(days=15)
    monkeypatch.setattr(app_module, "_maintenant_utc", lambda: maintenant)

    minimum = client.get(
        "/api/trips/historical",
        params={
            "from": "StopArea:Lyon",
            "to": "StopArea:Valence",
            "at": depart.isoformat(),
        },
    )
    maximum = client.get(
        "/api/trips/historical",
        params={
            "from": "StopArea:Lyon",
            "to": "StopArea:Valence",
            "at": maintenant.isoformat(),
        },
    )
    saved_minimum = client.post(
        "/api/missing",
        json={
            "client_id": "borne-min",
            "origin_stop_id": "StopArea:Lyon",
            "destination_stop_id": "StopArea:Valence",
            "is_historical": True,
            "observed_at": depart.isoformat(),
        },
    )
    saved_maximum = client.post(
        "/api/missing",
        json={
            "client_id": "borne-max",
            "origin_stop_id": "StopArea:Lyon",
            "destination_stop_id": "StopArea:Valence",
            "is_historical": True,
            "observed_at": maintenant.isoformat(),
        },
    )

    assert minimum.status_code == maximum.status_code == 200
    assert saved_minimum.status_code == saved_maximum.status_code == 200
    assert len(client.get("/api/sessions").json()) == 2


def test_migration_ajoute_historique_sans_perdre_les_releves(tmp_path):
    database = tmp_path / "app.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE saisie (client_id TEXT PRIMARY KEY, kind TEXT NOT NULL, "
            "origin_stop_id TEXT NOT NULL, destination_stop_id TEXT NOT NULL, "
            "created_at TEXT NOT NULL, legs TEXT)"
        )
        connection.execute(
            "INSERT INTO saisie (client_id, kind, origin_stop_id, destination_stop_id, created_at) "
            "VALUES ('ancien', 'count', 'A', 'B', '2026-01-02T03:04:05')"
        )

    create_app(tmp_path)

    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT client_id, kind, is_historical, observed_at FROM saisie"
        ).fetchone()
        columns = {row[1] for row in connection.execute("PRAGMA table_info(saisie)")}

    assert stored == ("ancien", "count", 0, "2026-01-02T03:04:05+00:00")
    assert {"is_historical", "observed_at"} <= columns
    create_app(tmp_path)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM saisie").fetchone()[0] == 1
