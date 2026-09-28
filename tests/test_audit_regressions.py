"""Les constats de l'audit de septembre 2026, un test chacun.

Ces tests ne visent pas une fonctionnalité : ils visent un bug qui a été trouvé,
exécuté, et corrigé. Chacun dit ce qui n'allait pas, pour que la correction ne
puisse pas être annulée sans que quelqu'un lise pourquoi.

Le rapport qui les motive est dans `docs/audit-2026-09.md`.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from google.transit import gtfs_realtime_pb2

from comptagefer.app import create_app
from comptagefer.offer import _distance_m, nearest_stops, open_stops
from comptagefer.rt import (
    alert_count,
    poll_once,
    store_siri,
    store_trip_updates,
    trip_count,
)

# --- 1. XSS persistant sur /carte --------------------------------------------
# Verrou dans tests/test_carte.py : le `client_id` était le seul champ non
# échappé, et il atterrissait dans un `<script>`.


# --- 2. Un « train signalé » faisait perdre le comptage suivant --------------


def _count(client_id: str = "jeton", **extra) -> dict:
    body = {
        "client_id": client_id,
        "origin_stop_id": "A",
        "destination_stop_id": "B",
        "origin_name": "Lyon Part Dieu",
        "destination_name": "Nîmes Pont du Gard",
        "trip_id": "TRIP1",
        "passengers": 40,
        "reliability": 70,
    }
    body.update(extra)
    return body


def _missing(client_id: str = "jeton") -> dict:
    return {
        "client_id": client_id,
        "origin_stop_id": "A",
        "destination_stop_id": "B",
    }


def test_reporting_a_missing_train_does_not_swallow_the_next_count(tmp_path):
    """Le contrôle d'idempotence portait sur `client_id` seul, alors que
    `client_id` est la clé primaire de `saisie`. Signaler un train manquant
    consommait le jeton du navigateur, et le comptage réel qui suivait avec le
    même jeton était renvoyé en 200 avec `stored: false` — sans rien écrire.
    L'écran disait « c'est noté », et le comptage existait.

    Le doublon n'a de sens qu'à genre égal : les deux lignes sont légitimes."""
    client = TestClient(create_app(tmp_path))

    assert client.post("/api/missing", json=_missing()).json()["stored"] is True

    compte = client.post("/api/sessions", json=_count())
    assert compte.status_code == 200
    assert compte.json()["stored"] is True, "le comptage a été jeté sans rien dire"

    kinds = sorted(row["kind"] for row in client.get("/api/sessions").json())
    assert kinds == ["count", "missing"]


def test_the_same_count_twice_is_still_idempotent(tmp_path):
    """Le correctif ne doit pas ouvrir la porte à un doublon du même genre :
    c'est lui qui rend la file hors ligne sûr quand le réseau coupe au milieu
    d'un envoi."""
    client = TestClient(create_app(tmp_path))

    assert client.post("/api/sessions", json=_count()).json()["stored"] is True
    assert client.post("/api/sessions", json=_count()).json()["stored"] is False

    assert len(client.get("/api/sessions").json()) == 1


def test_an_existing_database_keeps_its_rows_through_the_migration(tmp_path):
    """La clé primaire passe de `client_id` à `(client_id, kind)`, ce que SQLite
    ne sait pas faire : on recrée la table. Une base déjà en service ne doit
    rien perdre au passage."""
    database = tmp_path / "app.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE saisie (
            client_id TEXT PRIMARY KEY,
            origin_stop_id TEXT NOT NULL,
            destination_stop_id TEXT NOT NULL,
            origin_name TEXT,
            destination_name TEXT,
            trip_id TEXT,
            passengers INTEGER,
            reliability INTEGER,
            pseudo TEXT,
            comment TEXT,
            standing INTEGER,
            seats_free INTEGER,
            imbalance INTEGER,
            snapshot TEXT,
            kind TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO saisie VALUES
            ('ancien', 'A', 'B', 'Lyon', 'Nîmes', 'T1', 12, 60, NULL, NULL,
             NULL, NULL, NULL, NULL, 'count', '2026-09-01T08:00:00+00:00')
        """
    )
    connection.commit()
    connection.close()

    client = TestClient(create_app(tmp_path))
    rows = client.get("/api/sessions").json()
    assert len(rows) == 1
    assert rows[0]["client_id"] == "ancien"
    assert rows[0]["passengers"] == 12

    # Et la base reste fonctionnelle : on peut écrire les deux genres.
    assert client.post("/api/sessions", json=_count(client_id="neuf")).json()["stored"] is True
    assert client.post("/api/missing", json=_missing(client_id="neuf")).json()["stored"] is True


# --- 3 et 4. Collisions de clé primaire dans le cache temps réel -------------


def _feed(*entities: tuple[str, list[str]]) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1
    for trip_id, stops in entities:
        entity = feed.entity.add()
        entity.id = f"e-{trip_id}"
        entity.trip_update.trip.trip_id = trip_id
        for stop_id in stops:
            stop = entity.trip_update.stop_time_update.add()
            stop.stop_id = stop_id
            stop.departure.delay = 0
    return feed.SerializeToString()


def test_two_entities_with_the_same_trip_id_do_not_break_the_poll(tmp_path):
    """Deux entities du même feed peuvent porter le même `trip_id`, et
    `(trip_id, fetched_at)` est la clé de `trip_update`. L'`IntegrityError`
    remontait de `poll_once` et annulait le cycle entier : plus d'alertes
    stockées, plus de purge."""
    database = tmp_path / "rt.db"
    maintenant = datetime(2026, 9, 25, 8, tzinfo=timezone.utc)

    stored = store_trip_updates(
        database, _feed(("T1", ["S1"]), ("T1", ["S1", "S2"])), maintenant
    )

    assert stored == 2
    with sqlite3.connect(database) as connection:
        lignes = connection.execute("SELECT COUNT(*) FROM trip_update").fetchone()[0]
    assert lignes == 1, "un trip_id, une ligne"


_SIRI = b"""<Siri xmlns="http://www.siri.org.uk/siri">
  <ServiceDelivery>
    <EstimatedTimetableDelivery>
      <EstimatedJourneyVersionFrame>
        <EstimatedVehicleJourney>
          <FramedVehicleJourneyRef>
            <DatedVehicleJourneyRef>JOURNEY1</DatedVehicleJourneyRef>
          </FramedVehicleJourneyRef>
          <EstimatedCalls>
            <EstimatedCall>
              <StopPointRef>S1</StopPointRef>
              <AimedDepartureTime>2026-09-24T12:00:00+02:00</AimedDepartureTime>
              <ExpectedDepartureTime>2026-09-24T12:05:00+02:00</ExpectedDepartureTime>
            </EstimatedCall>
            <EstimatedCall>
              <StopPointRef>S1</StopPointRef>
              <AimedDepartureTime>2026-09-24T12:10:00+02:00</AimedDepartureTime>
              <ExpectedDepartureTime>2026-09-24T12:10:00+02:00</ExpectedDepartureTime>
            </EstimatedCall>
            <EstimatedCall>
              <StopPointRef>S2</StopPointRef>
              <AimedDepartureTime>2026-09-24T13:00:00+02:00</AimedDepartureTime>
              <ExpectedDepartureTime>2026-09-24T13:00:00+02:00</ExpectedDepartureTime>
            </EstimatedCall>
          </EstimatedCalls>
        </EstimatedVehicleJourney>
      </EstimatedJourneyVersionFrame>
    </EstimatedTimetableDelivery>
  </ServiceDelivery>
</Siri>"""


def test_siri_with_the_same_stop_twice_does_not_break_the_poll(tmp_path):
    """Un journey SIRI peut nommer deux fois le même `StopPointRef`, et
    `(trip_id, fetched_at, stop_id)` est la clé de `stop_update`. Le chemin
    GTFS-RT dédupliquait ses `stop_id`, SIRI non : l'`IntegrityError` faisait
    tomber le cycle complet, plus d'alertes, plus de purge."""
    database = tmp_path / "rt.db"
    maintenant = datetime(2026, 9, 25, 8, tzinfo=timezone.utc)

    assert store_siri(database, _SIRI, maintenant) == 1

    with sqlite3.connect(database) as connection:
        lignes = connection.execute(
            "SELECT stop_id FROM stop_update WHERE trip_id = 'JOURNEY1' ORDER BY stop_id"
        ).fetchall()
    assert lignes == [("S1",), ("S2",)]


# --- 5. SIRI retesté à chaque tour -------------------------------------------


def test_siri_is_not_refetched_on_every_empty_poll(tmp_path):
    """La méthode dit « SIRI ET Lite est tenté une fois ». Le code faisait
    `if stored == 0` à chaque tour de 120 s : sur un flux GTFS-RT vide, c'était
    le même XML téléchargé indéfiniment."""
    database = tmp_path / "rt.db"
    appeles: list[str] = []

    def feed_vide() -> bytes:
        appeles.append("tu")
        return _feed()

    def siri() -> bytes:
        appeles.append("siri")
        return _SIRI

    def alertes() -> bytes:
        appeles.append("alerts")
        return _feed()

    depart = datetime(2026, 9, 25, 8, tzinfo=timezone.utc)
    tente = False
    for _ in range(5):
        tente = poll_once(database, feed_vide, siri, alertes, depart + timedelta(minutes=2), tente)

    assert appeles.count("siri") == 1
    assert appeles.count("tu") == 5
    assert appeles.count("alerts") == 5, "le reste du cycle tourne toujours"


def test_unchanged_siri_does_not_stack_rows(tmp_path):
    """Sans refresh en place, SIRI empilait une ligne par cycle : 180 fois le
    nombre de journeys en 6 h de rétention."""
    database = tmp_path / "rt.db"
    depart = datetime(2026, 9, 25, 8, tzinfo=timezone.utc)

    for _ in range(4):
        store_siri(database, _SIRI, depart + timedelta(minutes=2))

    assert trip_count(database) == 1


def _alerts(nombre: int) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1
    for index in range(nombre):
        entity = feed.entity.add()
        entity.id = f"alerte-{index}"
        entity.alert.header_text.translation.add().text = f"Alerte {index}"
    return feed.SerializeToString()


def test_unchanged_alerts_do_not_stack_rows(tmp_path):
    """50 alertes inchangées donnaient 500 lignes après 10 tours."""
    from comptagefer.rt import store_service_alerts

    database = tmp_path / "rt.db"
    depart = datetime(2026, 9, 25, 8, tzinfo=timezone.utc)

    for _ in range(4):
        store_service_alerts(database, _alerts(50), depart + timedelta(minutes=2))

    assert alert_count(database) == 50


# --- 7. /api/trips renvoyait 500 sur un `at` malformé -------------------------


def test_a_malformed_at_is_a_422_not_a_traceback(tmp_path):
    client = TestClient(create_app(tmp_path))

    response = client.get("/api/trips", params={"from": "A", "to": "B", "at": "pas-une-date"})

    assert response.status_code == 422
    assert "Traceback" not in response.text


# --- 8. nearest_stops classait en degrés au carré ----------------------------


def test_nearest_stops_orders_by_real_distance(tmp_path):
    """Le tri portait sur `(dlat² + dlon²)`, sans `cos(lat)`. Un degré de
    longitude ne vaut que 0,67 degré de latitude à 48° N, donc le carré des
    degrés surestime tout ce qui est est-ouest.

    Les deux gares ci-dessous sont à égale distance apparente en degrés carrés
    (0,10 contre 0,11), donc le tri en degrés place « Nord » devant « Est ».
    En distance réelle, 0,11° de longitude font 8,1 km contre 11,1 km pour
    0,10° de latitude : c'est l'inverse. C'est exactement le genre de gare que
    la géolocalisation propose en premier à quelqu'un qui se trompe de quai."""
    database = tmp_path / "stops.db"
    gares = [("StopArea:Nord", "Nord", 49.00, 2.30), ("StopArea:Est", "Est", 48.90, 2.41)]
    with open_stops(database) as connection:
        connection.executemany(
            "INSERT INTO stop (stop_id, name, lat, lon, parent, is_area) VALUES (?, ?, ?, ?, NULL, 1)",
            gares,
        )

    trouvees = [gare["name"] for gare in nearest_stops(database, 48.90, 2.30, limit=2)]

    assert trouvees == ["Est", "Nord"]


def test_nearest_stops_matches_the_distance_helper(tmp_path):
    """Le classement doit être exactement celui de `_distance_m`, la fonction
    que le reste du module utilise déjà. C'était la divergence."""
    database = tmp_path / "stops.db"
    gares = [
        ("StopArea:A", "A", 48.8550, 2.3700),
        ("StopArea:B", "B", 48.8700, 2.4200),
        ("StopArea:C", "C", 48.8400, 2.4000),
        ("StopArea:D", "D", 49.0200, 2.2900),
    ]
    with open_stops(database) as connection:
        connection.executemany(
            "INSERT INTO stop (stop_id, name, lat, lon, parent, is_area) VALUES (?, ?, ?, ?, NULL, 1)",
            gares,
        )

    lat, lon = 48.8600, 2.3500
    trouvees = [gare["name"] for gare in nearest_stops(database, lat, lon, limit=4)]
    attendu = [nom for _, nom, _, _ in sorted(gares, key=lambda g: _distance_m(lat, lon, g[2], g[3]))]

    assert trouvees == attendu


# --- 11. Les sessions d'administration expirent -----------------------------


def test_an_admin_session_expires(tmp_path, monkeypatch):
    """Le dictionnaire des sessions ne était purgé à aucun moment, et le cookie
    n'avait pas de `max_age` : une session volée restait valable jusqu'au
    redémarrage du conteneur."""
    import comptagefer.app as app_module

    clock = [1_000_000.0]
    monkeypatch.setattr(app_module.time, "time", lambda: clock[0])

    client = TestClient(create_app(tmp_path, admin_token="secret"))
    assert client.post("/admin/login", data={"token": "secret"}).status_code == 200
    assert client.get("/admin").status_code == 200

    clock[0] += app_module.SESSION_SECONDS + 1
    assert client.get("/admin").status_code == 200
    assert "Connexion" in client.get("/admin").text or "<form" in client.get("/admin").text

    reopened = TestClient(create_app(tmp_path, admin_token="secret"))
    assert reopened.get("/admin").text.count("<form") >= 1
