"""Temps réel des RER et Transiliens, lu dans le SIRI Lite SNCF.

Le GTFS-RT national ne porte aucun trip francilien. Le SIRI Lite Estimated
Timetable de la SNCF, si : lignes A à V, retards et suppressions. Mais ses
identifiants ne joignent rien directement — ni `DatedVehicleJourneyRef` sur le
`trip_id` du GTFS transilien, ni `StopPointRef` (un code UIC) sur ses
`stop_id`. Deux ponts, mesurés sur les flux du 23 octobre 2026 :

- le train : `TrainNumberRef` = `trip_short_name` du GTFS. Avec le jour
  (`DataFrameRef`), 707 trains sur 714 trouvent exactement un trip ;
- la gare : la table de transcodification IDFM relie l'UIC8 aux zones
  d'arrêt `IDFM:<ZDEr>` et `IDFM:monomodalStopPlace:<ZDLr>` du GTFS. 9 081
  passages sur 9 677 trouvent exactement un arrêt du trip.

Ce qui ne joint pas est laissé de côté : un train ambigu n'est annoté de rien
plutôt que d'un retard qui serait celui d'un autre.
"""

import csv
import io
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from comptagefer.rt import _connect, _refresh_if_unchanged, _stamp

SIRI_NS = "{http://www.siri.org.uk/siri}"
TRANSCO_URL = (
    "https://data.iledefrance.fr/api/v2/catalog/datasets/"
    "horaires-des-lignes-transilien/attachments/transcodification_csv"
)
PREFIXE = "IDFM:TN:SNCF:"


def import_transcodification(timetable: Path, payload: bytes) -> int:
    """UIC8 → arrêts GTFS franciliens, dans `timetable.db`.

    Le fichier est un CSV `;` en latin-1 : le lire en utf-8 casse à la
    première gare accentuée.
    """
    texte = payload.decode("latin-1")
    paires = set()
    for row in csv.DictReader(io.StringIO(texte), delimiter=";"):
        uic = (row.get("UIC8") or "").strip()
        if not uic:
            continue
        zder = (row.get("__ZDEr_ID") or "").strip()
        zdlr = (row.get("__ZDLr_ID") or "").strip()
        if zder:
            paires.add((uic, f"IDFM:{zder}"))
        if zdlr:
            paires.add((uic, f"IDFM:monomodalStopPlace:{zdlr}"))
    with sqlite3.connect(timetable) as connection:
        _schema(connection)
        connection.execute("DELETE FROM uic_arret")
        connection.executemany("INSERT OR IGNORE INTO uic_arret (uic, stop_id) VALUES (?, ?)", paires)
    return len(paires)


def _schema(connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS uic_arret (uic TEXT NOT NULL, stop_id TEXT NOT NULL, "
        "PRIMARY KEY (uic, stop_id))"
    )


def _texte(element, nom: str) -> str:
    return (element.findtext(SIRI_NS + nom) or "").strip()


def _instant(valeur: str) -> datetime | None:
    if not valeur:
        return None
    try:
        return datetime.fromisoformat(valeur)
    except ValueError:
        return None


def _trip(connection, numero: str, jour: str) -> str | None:
    rows = connection.execute(
        """
        SELECT numero_train.trip_id
        FROM numero_train
        JOIN circulation ON circulation.trip_id = numero_train.trip_id
        JOIN service_day ON service_day.service_id = circulation.service_id
        WHERE numero_train.numero = ? AND service_day.day = ?
          AND numero_train.trip_id LIKE 'IDFM:TN:SNCF:%'
        LIMIT 2
        """,
        (numero, jour),
    ).fetchall()
    return rows[0][0] if len(rows) == 1 else None


def _passages(call) -> tuple[str, int | None, int | None]:
    """UIC, retard en secondes, départ attendu en epoch, pour un passage."""
    ref = _texte(call, "StopPointRef")
    uic = ref.rsplit(":", 1)[-1].strip()
    prevu = _instant(_texte(call, "AimedDepartureTime") or _texte(call, "AimedArrivalTime"))
    attendu = _instant(_texte(call, "ExpectedDepartureTime") or _texte(call, "ExpectedArrivalTime"))
    if prevu is None or attendu is None:
        return uic, None, None
    return uic, int((attendu - prevu).total_seconds()), int(attendu.timestamp())


def store_siri_francilien(rt: Path, timetable: Path, payload: bytes, fetched_at: datetime) -> int:
    """Écrit dans `rt.db`, sous le `trip_id` du GTFS, les trains franciliens joints.

    Renvoie le nombre de trains écrits. Un train sans numéro, absent du jour ou
    ambigu est ignoré ; un passage dont l'UIC ne désigne pas exactement un arrêt
    du trip aussi.
    """
    if not timetable.exists():
        return 0
    root = ET.fromstring(payload)
    stamp = _stamp(fetched_at)
    stored = 0
    with sqlite3.connect(timetable) as horaires, _connect(rt) as connection:
        tables = {row[0] for row in horaires.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"numero_train", "uic_arret"} <= tables:
            return 0
        for journey in root.iter(SIRI_NS + "EstimatedVehicleJourney"):
            numero = (journey.findtext(f"{SIRI_NS}TrainNumbers/{SIRI_NS}TrainNumberRef") or "").strip()
            jour = (
                journey.findtext(f"{SIRI_NS}FramedVehicleJourneyRef/{SIRI_NS}DataFrameRef") or ""
            ).strip()
            if not numero or not jour:
                continue
            trip_id = _trip(horaires, numero, jour)
            if trip_id is None:
                continue
            supprime = _texte(journey, "Cancellation").lower() == "true"
            relationship = "CANCELED" if supprime else "SCHEDULED"
            arrets = {
                row[0]
                for row in horaires.execute("SELECT stop_id FROM passage WHERE trip_id = ?", (trip_id,))
            }
            stops: dict[str, tuple] = {}
            for call in journey.iter(SIRI_NS + "EstimatedCall"):
                uic, retard, depart = _passages(call)
                if not uic:
                    continue
                candidats = {
                    row[0]
                    for row in horaires.execute("SELECT stop_id FROM uic_arret WHERE uic = ?", (uic,))
                } & arrets
                if len(candidats) != 1:
                    continue
                stop_id = candidats.pop()
                stops[stop_id] = (stop_id, None, retard, depart)
            lignes = [stops[key] for key in sorted(stops)]
            if _refresh_if_unchanged(connection, trip_id, relationship, lignes, stamp):
                stored += 1
                continue
            connection.execute(
                "INSERT OR IGNORE INTO trip_update (trip_id, start_date, schedule_relationship, fetched_at) "
                "VALUES (?, ?, ?, ?)",
                (trip_id, jour.replace("-", ""), relationship, stamp),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO stop_update "
                "(trip_id, fetched_at, stop_id, arrival_delay, departure_delay, departure_time) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(trip_id, stamp, *ligne) for ligne in lignes],
            )
            stored += 1
    return stored
