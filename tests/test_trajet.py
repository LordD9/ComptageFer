"""Ce qu'un comptage garde du train, au-delà du tronçon compté.

Le cas métier : on compte dans un train, entre deux gares. Le train, lui, a un
origine et une destination, et cette charge-làCompte aussi le test de migration : une base créée avant la colonne `trajet`
doit s'ouvrir sans erreur, sinon le déploiement casse au premier démarrage.
"""

import csv
import io
import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.timetable import import_timetable, trip_stops_all


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _hhmm(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


# Un aller-retour : Paris, Creil, Longueau, Amiens, puis retour par les mêmes
# gares. Un TER de ce genre existe, et c'est le cas où un tronçon Paris–Amiens
# cache la moitié du trajet.
TRAJET = [
    ("StopArea:ParisNord", 6 * 3600),
    ("StopPoint:ParisNord:1", 6 * 3600 + 120),
    ("StopPoint:Creil", 6 * 3600 + 900),
    ("StopPoint:Longueau", 7 * 3600 + 300),
    ("StopPoint:Amiens", 7 * 3600 + 1500),
    ("StopPoint:Longueau:retour", 7 * 3600 + 1800),
    ("StopPoint:Creil:retour", 7 * 3600 + 2400),
    ("StopArea:ParisNord:retour", 8 * 3600),
]


def _timetable(tmp_path: Path, arrets: list[tuple[str, int]] = TRAJET) -> Path:
    trips = tmp_path / "trips.txt"
    times = tmp_path / "stop_times.txt"
    days = tmp_path / "calendar_dates.txt"
    _write(trips, [{"route_id": "L", "service_id": "S", "trip_id": "T1"}])
    _write(
        times,
        [
            {
                "trip_id": "T1",
                "arrival_time": _hhmm(sec),
                "departure_time": _hhmm(sec),
                "stop_id": stop,
                "stop_sequence": str(index + 1),
            }
            for index, (stop, sec) in enumerate(arrets)
        ],
    )
    _write(days, [{"service_id": "S", "date": "20260925", "exception_type": "1"}])
    database = tmp_path / "timetable.db"
    import_timetable(database, trips, times, days)
    return database


def _count(client: TestClient, **extra) -> None:
    corps = {
        "client_id": "jeton",
        "origin_stop_id": "StopArea:ParisNord",
        "destination_stop_id": "StopPoint:Amiens",
        "origin_name": "Paris-Nord",
        "destination_name": "Amiens",
        "passengers": 80,
        "reliability": 70,
        **extra,
    }
    assert client.post("/api/sessions", json=corps).status_code == 200


def test_le_trajet_complet_est_bien_plus_long_que_le_troncon_compté(tmp_path: Path):
    base = _timetable(tmp_path)
    arrets = trip_stops_all(base, "T1")
    # Les 8 arrêts du GTFS : la gare et son quai ne sont dédoublonnés que si
    # `stops.db` connaît le parent. Sans lui, on garde tout, ce qui est le bon
    # parti : perdre un arrêt serait pire que d'en garder un de trop.
    assert [item["stop_id"] for item in arrets] == [stop for stop, _ in TRAJET]
    assert arrets[4]["stop_id"] == "StopPoint:Amiens"
    assert arrets[4]["depart_sec"] == 7 * 3600 + 1500
    # L'ordre est bien celui du GTFS, heures croissantes comprises.
    heures = [item["depart_sec"] for item in arrets]
    assert heures == sorted(heures)


def test_les_quais_et_leur_gare_ne_sont_comptes_qu_une_fois(tmp_path: Path):
    """Avec `stops.db`, un quai et sa gare ne doivent pas faire deux arrêts."""
    from comptagefer.offer import open_stops

    stops = tmp_path / "stops.db"
    with open_stops(stops) as connection:
        connection.executemany(
            "INSERT OR REPLACE INTO stop (stop_id, name, parent, is_area) VALUES (?, ?, ?, ?)",
            [
                ("StopArea:ParisNord", "Paris-Nord", None, 1),
                ("StopPoint:ParisNord:1", "Paris-Nord voie 1", "StopArea:ParisNord", 0),
                ("StopPoint:Creil", "Creil", None, 0),
                ("StopPoint:Longueau", "Longueau", None, 0),
                ("StopPoint:Amiens", "Amiens", None, 0),
                ("StopPoint:Longueau:retour", "Longueau", None, 0),
                ("StopPoint:Creil:retour", "Creil", None, 0),
                ("StopArea:ParisNord:retour", "Paris-Nord", None, 1),
            ],
        )
    base = _timetable(tmp_path)
    arrets = trip_stops_all(base, "T1", stops)
    noms = [item["name"] for item in arrets]
    assert noms == ["Paris-Nord", "Creil", "Longueau", "Amiens", "Longueau", "Creil", "Paris-Nord"]


def test_un_compte_enregistre_le_trajet_du_train_et_pas_seul_tronçon(tmp_path: Path):
    _timetable(tmp_path)
    client = TestClient(create_app(tmp_path))
    _count(client, trip_id="T1")

    saisie = client.get("/api/sessions").json()[0]
    trajet = saisie["trajet"]
    assert trajet is not None, "le trajet du train doit être figé avec le comptage"
    assert trajet["trip_id"] == "T1"
    # Le retour en boucle est bien là : c'est lui qu'un tronçon cacherait.
    assert len(trajet["arrets"]) == len(TRAJET)
    # Le comptage, lui, reste sur son tronçon.
    assert saisie["origin_name"] == "Paris-Nord"


def test_sans_trip_id_on_ne_devine_rien(tmp_path: Path):
    """Pas de train identifié, pas de trajet. Plutôt rien que du plausible."""
    _timetable(tmp_path)
    client = TestClient(create_app(tmp_path))
    _count(client)
    assert client.get("/api/sessions").json()[0]["trajet"] is None


def test_un_train_signale_n_a_pas_de_trajet(tmp_path: Path):
    """C'est un doute sur une ligne, pas une observation : rien à figer."""
    _timetable(tmp_path)
    client = TestClient(create_app(tmp_path))
    reponse = client.post(
        "/api/missing",
        json={
            "client_id": "jeton",
            "origin_stop_id": "StopArea:ParisNord",
            "destination_stop_id": "StopPoint:Amiens",
        },
    )
    assert reponse.status_code == 200
    assert client.get("/api/sessions").json()[0]["trajet"] is None


def test_une_base_sans_la_colonne_trajet_s_ouvre_puis_se_migre(tmp_path: Path):
    """Le déploiement réel : une base de l'année dernière, pas une base vide."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    database = tmp_path / "app.db"
    with sqlite3.connect(database) as connection:
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
                created_at TEXT NOT NULL,
                legs TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO saisie VALUES ('ancien', 'A', 'B', 'A', 'B', 'T', 12, 50, "
            "NULL, NULL, NULL, NULL, NULL, NULL, 'count', "
            "'2025-01-01T00:00:00+00:00', NULL)"
        )
    _timetable(tmp_path)
    client = TestClient(create_app(tmp_path))
    colonnes = {row[1] for row in sqlite3.connect(database).execute("PRAGMA table_info(saisie)")}
    assert "trajet" in colonnes
    anciens = client.get("/api/sessions").json()
    assert anciens[0]["trajet"] is None, "un comptage d'avant n'inventera pas de trajet"
    assert anciens[0]["passengers"] == 12, "la migration ne doit rien perdre"


def test_le_csv_embarque_le_trajet_et_le_trip_id(tmp_path: Path):
    """Le CSV est ce que l'estimation de fréquentation lira.

    Une donnée qu'on ne publie pas reste inexistante pour la personne qui
    voudra l'utiliser. Le `trip_id` sans le trajet ne sert à rien, et le
    trajet sans `trip_id` ne se rattache à rien.
    """
    from comptagefer.publish import render_csv

    _timetable(tmp_path)
    client = TestClient(create_app(tmp_path))
    _count(client, trip_id="T1")
    lignes = list(csv.reader(io.StringIO(render_csv(client.get("/api/sessions").json()))))
    # La première ligne est la licence, pas l'en-tête.
    assert lignes[0] == ["# Licence Ouverte 2.0"]
    entete, premiere = lignes[1], lignes[2]
    assert "trip_id" in entete and "trajet" in entete
    trajet = json.loads(premiere[entete.index("trajet")])
    assert premiere[entete.index("trip_id")] == "T1"
    assert trajet[0][0] == "StopArea:ParisNord"
    assert len(trajet) == len(TRAJET)
    assert trajet[4] == ["StopPoint:Amiens", 7 * 3600 + 1500]


def test_le_trajet_ne_dépend_pas_du_gtfs_rechargé_ensuite(tmp_path: Path):
    """Le rechargement du GTFS ne doit pas réécrire l'histoire.

    Une ligne peut changer de gares entre deux rechargements. La saisie doit
    continuer de dire ce qu'elle a vu le jour du comptage, sinon l'estimation
    d'après travaille sur un trajet qui n'a jamais existé.
    """
    _timetable(tmp_path)
    client = TestClient(create_app(tmp_path))
    _count(client, trip_id="T1")
    avant = client.get("/api/sessions").json()[0]["trajet"]

    # Le GTFS change : le train ne s'arrête plus à Creil.
    _timetable(
        tmp_path,
        [item for item in TRAJET if "Creil" not in item[0]],
    )
    apres = client.get("/api/sessions").json()[0]["trajet"]
    assert apres == avant
    assert any("Creil" in item["stop_id"] for item in apres["arrets"])
