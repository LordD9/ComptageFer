"""Trains d'un autre jour, nom des trains franciliens et jours du GTFS francilien."""

import sqlite3
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import comptagefer.app as app_module
from comptagefer.app import TIMETABLE_VERSION, _timetable_has_transilien
from comptagefer.timetable import TRANSILIEN, _calendar_days, import_timetable, kind_of
from tests.test_historique import TER, _application_avec_offre
from tests.test_transilien import _csv

PARIS = ZoneInfo("Europe/Paris")


def _route(court: str, route_type: str = "2") -> dict:
    return {"route_short_name": court, "route_type": route_type}


def test_un_rer_et_un_transilien_ne_portent_plus_le_meme_nom():
    trip = f"{TRANSILIEN}abc"
    assert kind_of(trip, _route("B")) == "RER B"
    assert kind_of(trip, _route("E")) == "RER E"
    assert kind_of(trip, _route("L")) == "Transilien L"
    assert kind_of(trip, _route("U")) == "Transilien U"
    assert kind_of(trip, _route("TER")) == "TER"
    assert kind_of(trip, _route("B", "3")) == "Car", "un car de remplacement n'est pas un RER"
    assert kind_of(trip) == "Transilien", "sans ligne connue, on garde le nom générique"


def test_le_national_ignore_la_ligne():
    assert kind_of("1_F:TER:1234", _route("B")) == "TER"


def test_calendar_ouvre_les_jours_de_la_semaine_type():
    row = {
        "service_id": "S",
        "monday": "1", "tuesday": "0", "wednesday": "0", "thursday": "0",
        "friday": "1", "saturday": "0", "sunday": "0",
        "start_date": "20261026", "end_date": "20261106",
    }
    assert _calendar_days(row) == ["2026-10-26", "2026-10-30", "2026-11-02", "2026-11-06"]
    assert _calendar_days({**row, "start_date": "pas une date"}) == []


def test_un_rer_circule_les_jours_de_calendar_et_pas_les_feries(tmp_path):
    """Le défaut mesuré : sans `calendar.txt`, 41 471 trips franciliens sur
    45 515 n'avaient aucun jour, et Gare du Nord → Châtelet sortait 0 RER."""
    trip = f"{TRANSILIEN}rer-b"
    (tmp_path / "trips.txt").write_text(
        _csv(["trip_id", "service_id", "route_id"], [trip, "TN:S", "IDFM:C01743"]), encoding="utf-8"
    )
    (tmp_path / "stop_times.txt").write_text(
        _csv(["trip_id", "stop_id", "departure_time", "arrival_time"],
             [trip, "IDFM:1", "08:00:00", "08:00:00"],
             [trip, "IDFM:2", "08:10:00", "08:10:00"]),
        encoding="utf-8",
    )
    (tmp_path / "calendar.txt").write_text(
        _csv(["service_id", "monday", "tuesday", "wednesday", "thursday", "friday",
              "saturday", "sunday", "start_date", "end_date"],
             ["TN:S", "1", "1", "1", "1", "1", "0", "0", "20261109", "20261113"]),
        encoding="utf-8",
    )
    (tmp_path / "calendar_dates.txt").write_text(
        _csv(["service_id", "date", "exception_type"],
             ["TN:S", "20261111", "2"], ["TN:S", "20261114", "1"]),
        encoding="utf-8",
    )
    (tmp_path / "routes.txt").write_text(
        _csv(["route_id", "route_short_name", "route_long_name", "route_type"],
             ["IDFM:C01743", "B", "B", "2"]),
        encoding="utf-8",
    )
    database = tmp_path / "timetable.db"
    import_timetable(
        database,
        tmp_path / "trips.txt",
        tmp_path / "stop_times.txt",
        tmp_path / "calendar_dates.txt",
        tmp_path / "routes.txt",
        tmp_path / "calendar.txt",
    )
    with sqlite3.connect(database) as connection:
        jours = [row[0] for row in connection.execute("SELECT day FROM service_day ORDER BY day")]
        kind = connection.execute("SELECT kind FROM circulation").fetchone()[0]
    assert jours == ["2026-11-09", "2026-11-10", "2026-11-12", "2026-11-13", "2026-11-14"]
    assert kind == "RER B"


def test_une_base_francilienne_dancienne_version_est_reimportee(tmp_path):
    database = tmp_path / "timetable.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE circulation (trip_id TEXT, service_id TEXT, kind TEXT)")
        connection.execute("INSERT INTO circulation VALUES (?, 'S', 'Transilien')", (f"{TRANSILIEN}x",))
    assert not _timetable_has_transilien(database), "une base sans calendar.txt doit être refaite"
    with sqlite3.connect(database) as connection:
        connection.execute(f"PRAGMA user_version = {TIMETABLE_VERSION}")
    assert _timetable_has_transilien(database)


def test_un_jour_donne_la_liste_de_toute_la_journee(tmp_path):
    client, _data, depart = _application_avec_offre(tmp_path)
    loin = depart + timedelta(hours=5)
    params = {"from": "StopArea:Lyon", "to": "StopArea:Valence"}

    autour_de_loin = client.get("/api/trips/historical", params={**params, "at": loin.isoformat()})
    assert autour_de_loin.status_code == 200
    assert TER not in {item["trip_id"] for item in autour_de_loin.json()}, (
        "le test n'a de sens que si la fenêtre de 4 heures rate le train"
    )

    journee = client.get("/api/trips/historical", params={**params, "jour": depart.date().isoformat()})
    assert journee.status_code == 200, journee.text
    trains = journee.json()
    train = next(item for item in trains if item["trip_id"] == TER)
    assert train["departure_time"] == depart.isoformat()
    assert train["status"] is None and train["delay_seconds"] is None


def test_le_train_choisi_dans_la_journee_senregistre(tmp_path):
    from tests.test_historique import _corps_historique

    client, _data, depart = _application_avec_offre(tmp_path)
    trains = client.get(
        "/api/trips/historical",
        params={"from": "StopArea:Lyon", "to": "StopArea:Valence", "jour": depart.date().isoformat()},
    ).json()
    train = next(item for item in trains if item["trip_id"] == TER)
    corps = _corps_historique(depart, observed_at=train["departure_time"])
    response = client.post("/api/sessions", json=corps)
    assert response.status_code == 200, response.text


def test_un_jour_hors_borne_ou_mal_forme_est_refuse(tmp_path, monkeypatch):
    client, _data, _depart = _application_avec_offre(tmp_path)
    maintenant = datetime(2026, 10, 23, 10, 0, tzinfo=UTC)
    monkeypatch.setattr(app_module, "_maintenant_utc", lambda: maintenant)
    params = {"from": "StopArea:Lyon", "to": "StopArea:Valence"}
    for jour in ("2026-10-07", "2026-10-24", "23/10/2026", "2026-02-30"):
        response = client.get("/api/trips/historical", params={**params, "jour": jour})
        assert response.status_code == 422, (jour, response.text)
    for jour in ("2026-10-08", "2026-10-23"):
        response = client.get("/api/trips/historical", params={**params, "jour": jour})
        assert response.status_code == 200, (jour, response.text)


def test_sans_jour_ni_heure_la_recherche_est_refusee(tmp_path):
    client, _data, _depart = _application_avec_offre(tmp_path)
    response = client.get("/api/trips/historical", params={"from": "StopArea:Lyon", "to": "StopArea:Valence"})
    assert response.status_code == 422


def test_la_page_annonce_la_fenetre_et_lautre_jour(tmp_path):
    from fastapi.testclient import TestClient

    from comptagefer.app import create_app

    html = TestClient(create_app(tmp_path)).get("/").text
    assert "fenêtre de 4&nbsp;heures" in html
    assert 'id="historical-day" type="date"' in html
    assert "Voir les trains du jour" in html
    assert 'type="datetime-local"' not in html, "on ne demande plus l'heure du train"
    assert html.index('id="historical-picker"') < html.index('id="trains"'), (
        "l'autre jour doit se voir avant la liste, pas sous elle"
    )


def test_les_stations_de_metro_ne_sont_pas_des_gares(tmp_path):
    """`Châtelet` (métro) n'a aucun train : elle ne doit pas sortir de la recherche."""
    from comptagefer.offer import arrets_ferroviaires, import_stop_names, search_stops

    (tmp_path / "stops.txt").write_text(
        _csv(["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"],
             ["IDFM:71264", "Châtelet", "48.858", "2.348", "1", ""],
             ["IDFM:22364", "Châtelet", "48.856", "2.348", "0", "IDFM:71264"],
             ["IDFM:474151", "Châtelet - Les Halles", "48.861", "2.346", "1", ""],
             ["IDFM:45102", "Châtelet - Les Halles", "48.861", "2.346", "0", "IDFM:474151"],
             ["IDFM:9", "Arrêt du car", "48.0", "2.0", "0", ""]),
        encoding="utf-8",
    )
    (tmp_path / "routes.txt").write_text(
        _csv(["route_id", "route_short_name", "route_type"], ["R:B", "B", "2"], ["R:CAR", "B", "3"]),
        encoding="utf-8",
    )
    (tmp_path / "trips.txt").write_text(
        _csv(["trip_id", "service_id", "route_id"], ["T1", "S", "R:B"], ["T2", "S", "R:CAR"]),
        encoding="utf-8",
    )
    (tmp_path / "stop_times.txt").write_text(
        _csv(["trip_id", "stop_id", "departure_time"], ["T1", "IDFM:45102", "08:00:00"], ["T2", "IDFM:9", "08:00:00"]),
        encoding="utf-8",
    )
    garder = arrets_ferroviaires(tmp_path)
    assert garder == {"IDFM:45102", "IDFM:474151"}
    import_stop_names(tmp_path / "stops.db", tmp_path / "stops.txt", garder=garder)
    noms = [item["name"] for item in search_stops(tmp_path / "stops.db", "chatelet")]
    assert noms == ["Châtelet - Les Halles"]
