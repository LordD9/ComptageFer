"""Les constats de l'audit de septembre 2026, un test chacun.

Ces tests ne visent pas une fonctionnalité : ils visent un bug qui a été trouvé,
exécuté, et corrigé. Chacun dit ce qui n'allait pas, pour que la correction ne
puisse pas être annulée sans que quelqu'un lise pourquoi.

Le rapport qui les motive est dans `docs/audit-2026-09.md`.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from google.transit import gtfs_realtime_pb2

from comptagefer.app import create_app
from comptagefer.compte import (
    COOKIE_COMPTE,
    creer_compte,
    ouvrir_session,
)
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


# --- 12. La cle primaire de `saisie` n'existait sur aucune base neuve ---------
#
# Constats de la revue de la PR #42, reproduits et corriges. Chacun de ces
# tests echouait avant la correction.


def test_une_base_neuve_a_bien_la_cle_primaire_composee(tmp_path):
    """Une base creee par l'application porte la cle `(client_id, kind)`.

    Le defaut : `_clef_par_genre` testait `colonnes["client_id"][5] == 0` pour
    conclure « la cle est deja composite ». Or `PRAGMA table_info` met 0 dans sa
    cinquieme colonne pour « **hors cle** », pas pour « cle simple ». Le
    `CREATE TABLE` de `create_app` ne pose aucune cle, donc le test lisait 0,
    croyait la table migree, et rendait la main.

    Aucune base neuve n'a donc jamais eu de cle primaire. C'est invisible tant
    que la seule garantie d'idempotence est un `SELECT` applicatif — et ce
    `SELECT` etait justement defaillant (constat 13). Les deux fautes se
    masquaient : le code applicatif cachait l'absence de contrainte, et
    l'absence de contrainte rendait le defaut du `SELECT` inoffensif sur le
    chemin le plus courant.
    """
    create_app(tmp_path)
    database = tmp_path / "app.db"

    with sqlite3.connect(database) as connection:
        table = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'saisie'"
        ).fetchone()[0]
        rang = {row[1]: row[5] for row in connection.execute("PRAGMA table_info(saisie)")}

    assert "PRIMARY KEY (client_id, kind)" in table, (
        f"une base neuve n'a pas la cle composite : {table}"
    )
    assert rang["client_id"] == 1 and rang["kind"] == 2, (
        f"la cle primaire ne porte pas les deux colonnes : {rang}"
    )


def test_une_base_avec_une_ancienne_clef_est_migree_sans_perdre_de_ligne(tmp_path):
    """La migration fait ce qu'elle promet, et elle est idempotente.

    Le cas nominal — une base qui avait bien la cle simple — fonctionnait deja.
    Ce test verifie que le correctif ne l'a pas casse, et qu'une **seconde**
    ouverture ne recree pas la table : sinon chaque redemarrage du conteneur
    recopierait `saisie` au lieu de la migrer une fois.
    """
    from comptagefer.app import SCHEMA_SAISIE

    database = tmp_path / "app.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE saisie ("
            + ", ".join(f"{nom} {type_}" for nom, type_ in SCHEMA_SAISIE)
            + ", PRIMARY KEY (client_id))"
        )
        connection.execute(
            "INSERT INTO saisie (client_id, kind, origin_stop_id, destination_stop_id, "
            "created_at) VALUES ('avant', 'count', 'A', 'B', '2026-01-01')"
        )

    create_app(tmp_path)
    with sqlite3.connect(database) as connection:
        apres = list(connection.execute("SELECT client_id, kind FROM saisie"))
        rang = {row[1]: row[5] for row in connection.execute("PRAGMA table_info(saisie)")}

    assert apres == [("avant", "count")], f"la migration a perdu la ligne : {apres}"
    assert rang["client_id"] == 1 and rang["kind"] == 2

    create_app(tmp_path)
    with sqlite3.connect(database) as connection:
        assert list(connection.execute("SELECT client_id, kind FROM saisie")) == apres


# --- 13. Un doublon passait quand le meme jeton avait deux genres ------------


def _lignes_de(client_id: str, database: Path) -> dict:
    """Le nombre de lignes par genre pour un jeton. L'echantillon des trois tests."""
    with sqlite3.connect(database) as connection:
        return {
            str(kind): total
            for kind, total in connection.execute(
                "SELECT kind, COUNT(*) FROM saisie WHERE client_id = ? GROUP BY kind",
                (client_id,),
            )
        }


def _corps(client_id: str = "X") -> tuple[dict, dict]:
    """Le releve minimal, et sa variante `missing` (sans effectif)."""
    commun = {
        "client_id": client_id,
        "origin_stop_id": "A",
        "destination_stop_id": "B",
        "origin_name": "Lyon",
        "destination_name": "Nimes",
    }
    return {**commun, "passengers": 10, "reliability": 80}, commun


def test_un_doublon_ne_passe_pas_apres_un_train_manquant(tmp_path):
    """`missing` puis `count` puis `count` : une seule ligne de `count`.

    Le defaut : la requete d'idempotence portait sur `client_id` seul. Apres un
    `missing`, `fetchone` rendait cette ligne-la, `existing[1] == kind` etait
    faux pour le `count`, le doublon passait, et — la base n'ayant pas de cle
    primaire, voir constat 12 — la seconde ligne s'ecrivait reellement. Deux
    « count » pour un meme navigateur, tous deux comptes au score.
    """
    client = TestClient(create_app(tmp_path))
    base, compte = _corps()

    assert client.post("/api/missing", json=compte).json()["stored"] is True
    assert client.post("/api/sessions", json=base).json()["stored"] is True
    assert client.post("/api/sessions", json=base).json()["stored"] is False

    assert _lignes_de("X", tmp_path / "app.db") == {"missing": 1, "count": 1}, (
        f"le jeton a produit {_lignes_de('X', tmp_path / 'app.db')}"
    )


def test_un_doublon_annonce_comme_ecrit_une_fois_de_trop(tmp_path):
    """La reponse et la base doivent dire la meme chose.

    Le code casse renvoyait `stored: false` sur le doublon **et** ecrivait la
    ligne : la reponse disait « rien ecrit » pendant que la donnee partait. C'est
    la faute que la regle 2 de `docs/regles.md` interdit, et elle est invisible
    au test qui ne regarde que le code de retour.
    """
    client = TestClient(create_app(tmp_path))
    base, compte = _corps()

    client.post("/api/missing", json=compte)
    client.post("/api/sessions", json=base)
    reponse = client.post("/api/sessions", json=base).json()

    assert reponse["stored"] is False, "le doublon a ete annonce comme ecrit"
    assert _lignes_de("X", tmp_path / "app.db") == {"missing": 1, "count": 1}


def test_un_doublon_ne_passe_pas_dans_l_ordre_inverse(tmp_path):
    """`count` puis `missing` puis `count` : toujours une seule ligne de `count`.

    Separe du precedent parce que cet ordre-ci echouait deja : `fetchone`
    rendait la ligne du `count`, le test passait donc sur le code casse. Le
    fusionner donnerait l'illusion d'une couverture que le premier ordre ne
    fournit pas.
    """
    client = TestClient(create_app(tmp_path))
    base, compte = _corps()

    assert client.post("/api/sessions", json=base).json()["stored"] is True
    assert client.post("/api/missing", json=compte).json()["stored"] is True
    assert client.post("/api/sessions", json=base).json()["stored"] is False

    assert _lignes_de("X", tmp_path / "app.db") == {"missing": 1, "count": 1}


# --- 14. Un compte pouvait etre cree sans pseudo -----------------------------


def test_creer_un_compte_sans_pseudo_est_refuse(tmp_path):
    """`POST /compte/creer` sans pseudo rend 422, et n'ecrit rien.

    Le defaut : la route acceptait un corps vide. Le `required` du champ ne
    protege que le navigateur — or la route est appelable sans lui. Le compte
    etait cree sans pseudo, et le classement l'affichait sous « un compte sans
    pseudo » : un rang sans auteur, dans une page dont le denominateur est la
    seule garantie de lecture.

    Les quatre formes sont testees parce que `.strip()` est ce qui les
    distingue, et qu'un `.strip()` oublie est le defaut le plus probable du
    correctif.
    """
    client = TestClient(create_app(tmp_path))

    for corps in ({}, {"pseudo": ""}, {"pseudo": "   "}, {"pseudo": "\t\n"}):
        reponse = client.post("/compte/creer", data=corps, follow_redirects=False)
        assert reponse.status_code == 422, f"{corps} a ete accepte ({reponse.status_code})"

    with sqlite3.connect(tmp_path / "app.db") as connection:
        total = connection.execute("SELECT COUNT(*) FROM compte").fetchone()[0]
    assert total == 0, f"{total} compte(s) sans pseudo ont ete crees"


def test_un_compte_avec_un_pseudo_valide_est_toujours_cree(tmp_path):
    """Le refus ne doit pas avoir trop corrige.

    Le test qui accompagne une contrainte doit prouver ce qu'elle ne doit pas
    casser. Un pseudo de 40 caracteres est la borne du champ : il doit passer,
    et c'est aussi le seul cas ou la troncature silencieuse importerait.
    """
    client = TestClient(create_app(tmp_path))
    long_pseudo = "x" * 40

    assert client.post(
        "/compte/creer", data={"pseudo": "romain"}, follow_redirects=False
    ).status_code == 303
    assert client.post(
        "/compte/creer", data={"pseudo": long_pseudo}, follow_redirects=False
    ).status_code == 303

    with sqlite3.connect(tmp_path / "app.db") as connection:
        pseudos = [row[0] for row in connection.execute("SELECT pseudo FROM compte")]
    assert pseudos == ["romain", long_pseudo]


# --- 15. `/classement` rendait 500 sur une base verrouillee -------------------


@contextmanager
def _base_occupee(database: Path):
    """Une connexion qui tient un verrou exclusif, pour simuler un ecrivant.

    Un contexte et non un couple d'appels : le test doit pouvoir lire
    **pendant** que la base est bloquee, puis la liberer, quoi qu'il arrive
    entre les deux.
    """
    bloqueur = sqlite3.connect(database, isolation_level=None, timeout=0.3)
    bloqueur.execute("BEGIN EXCLUSIVE")
    bloqueur.execute(
        "INSERT INTO compte (id, pseudo, secret, cree_le) VALUES ('x', 'y', 'z', 0)"
    )
    try:
        yield
    finally:
        bloqueur.execute("ROLLBACK")
        bloqueur.close()


def test_le_classement_dit_une_base_occupee_au_lieu_de_500(tmp_path):
    """Une base verrouillee rend la page, et la page dit qu'elle n'a pas pu lire.

    Le defaut : `score.classement` ne traitait pas `DatabaseError`, donc
    `/classement` rendait un 500 nu — la page la plus lue du site, et celle que
    le workflow Docker verifie, disparue sans explication. Le visiteur ne pouvait
    pas distinguer « le site est casse » de « il n'y a personne ».

    Le test exige deux choses : un 200, et un texte qui distingue l'echec de la
    liste vide. Le second point est le vrai — une page qui affiche « personne
    n'a encore de compte » quand elle n'a pas pu lire ment sur une absence de
    donnees, et c'est le meme mensonge que `tools/calibrer_score.py` refuse.
    """
    client = TestClient(create_app(tmp_path), raise_server_exceptions=False)
    # Un releve rattache : sans lui, la page vide et l'echec se ressembleraient.
    identifiant, _secret = creer_compte(tmp_path / "app.db", "romain")
    jeton, _expire = ouvrir_session(tmp_path / "app.db", identifiant)
    client.cookies.set(COOKIE_COMPTE, jeton)
    base, _compte = _corps("jeton")
    assert client.post("/api/sessions", json=base).status_code == 200

    with _base_occupee(tmp_path / "app.db"):
        reponse = client.get("/classement")

    assert reponse.status_code == 200, f"/classement a rendu {reponse.status_code}"
    assert "pas pu" in reponse.text, "la page ne dit pas qu'elle n'a pas pu lire"
    assert "Personne n" not in reponse.text, (
        "la page affirme une absence de comptes alors qu'elle n'a pas pu lire"
    )


def test_le_classement_vide_reste_son_etat_a_lui(tmp_path):
    """Sans verrou, la page dit « personne », et elle le dit toujours.

    Le test-jumeau du precedent. Il existe parce que le correctif touche le rendu
    de la page vide : une correction de « ne plus de 500 » qui transformerait la
    liste vide en message d'erreur serait invisible sur un site neuf, ou personne
    n'a encore de compte.
    """
    client = TestClient(create_app(tmp_path))

    reponse = client.get("/classement")

    assert reponse.status_code == 200
    assert "Personne n" in reponse.text
    assert "pas pu" not in reponse.text
