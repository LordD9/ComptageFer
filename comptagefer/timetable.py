import csv
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
KINDS = {
    "TER": "TER",
    "TT": "TER",
    "CTE": "Car",
    "IC": "Intercités",
    "ICN": "Intercités",
    "OUI": "TGV",
    "OGO": "TGV",
}

# Les trips franciliens n'ont pas la forme du national : c'est
# `IDFM:TN:SNCF:<uuid>`, là où le national fait `OCESN…F1187_F:OUI:FR:Line::…`.
# Le garde plus bas cherche `_F:` ou `_R:`, que les trips IDFM ne contiennent
# pas : sans une porte à part, `kind_of` aurait rendu « Train » pour tous les
# franciliens. « TN » seul, ce n'est pas mieux — un code interne lu comme un nom
# de ligne dans la liste des trains. « Transilien » est le mot que cherche
# quelqu'un qui veut compter un RER.
TRANSILIEN = "IDFM:TN:SNCF:"


def kind_of(trip_id: str) -> str:
    if trip_id.startswith(TRANSILIEN):
        return "Transilien"
    marker = ""
    if "_F:" in trip_id or "_R:" in trip_id:
        marker = trip_id.split(":", 2)[1]
    return KINDS.get(marker, marker or "Train")


def etat_of(status: str | None, delay_seconds: int | None) -> str:
    if status in {"CANCELED", "DELETED"}:
        return "supprimé"
    if status is None:
        return "programmé"
    if delay_seconds and delay_seconds >= 60:
        return "retard"
    if delay_seconds and delay_seconds <= -60:
        return "avance"
    return "à l'heure"


def _opened(path: Path):
    """Lecture du GTFS en utf-8-sig : le national n'a pas de BOM aujourd'hui,
    mais une feed qui en aurait un ferait échouer la première colonne, et on
    ne veut pas que ça décide de l'apparition des pages ligne."""
    return Path(path).open(newline="", encoding="utf-8-sig")


def _court(row: dict) -> str:
    """Le nom court, sauf quand c'est un placeholder.

    53 lignes du GTFS national s'appellent « INCONNU » : c'est une valeur de
    la source, pas une ligne sans nom. On garde quand même la chaîne, pour ne
    pas inventer un identifiant, mais la recherche l'ignorera.
    """
    return (row.get("route_short_name") or "").strip() or (row.get("route_long_name") or "").strip()


def _long(row: dict) -> str | None:
    value = (row.get("route_long_name") or "").strip()
    return value or None


def _mode(row: dict) -> str | None:
    """GTFS : 0 tramway, 2 rail, 3 bus. On ne garde que ce qui nous parle."""
    return {"0": "tramway", "2": "train", "3": "car"}.get((row.get("route_type") or "").strip())


def import_timetable(
    database: Path,
    trips_file: Path,
    times_file: Path,
    dates_file: Path,
    routes_file: Path | None = None,
) -> int:
    database.parent.mkdir(parents=True, exist_ok=True)
    stored = 0
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS circulation (
                trip_id TEXT PRIMARY KEY,
                service_id TEXT NOT NULL,
                kind TEXT NOT NULL
            )
            """
        )
        # Les lignes vivent à part, et le rattachement des trips aussi, plutôt
        # qu'une colonne ajoutée dans circulation : une base installée avant
        # cette PR garde ainsi un schéma valide, et n'est réimportée qu'au
        # prochain ensure_referential, qui détecte la table manquante.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ligne (
                route_id TEXT PRIMARY KEY,
                nom_court TEXT NOT NULL,
                nom_long TEXT,
                mode TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS trip_ligne (
                trip_id TEXT PRIMARY KEY,
                route_id TEXT NOT NULL
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS trip_ligne_ligne ON trip_ligne(route_id)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS passage (
                trip_id TEXT NOT NULL,
                stop_id TEXT NOT NULL,
                depart_sec INTEGER NOT NULL,
                PRIMARY KEY (trip_id, stop_id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS passage_stop ON passage(stop_id, depart_sec)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS service_day (
                service_id TEXT NOT NULL,
                day TEXT NOT NULL,
                PRIMARY KEY (service_id, day)
            )
            """
        )
        with trips_file.open(newline="") as handle:
            connection.executemany(
                "INSERT OR REPLACE INTO circulation (trip_id, service_id, kind) VALUES (?, ?, ?)",
                (
                    (row["trip_id"], row["service_id"], kind_of(row["trip_id"]))
                    for row in csv.DictReader(handle)
                ),
            )
            stored = connection.execute("SELECT COUNT(*) FROM circulation").fetchone()[0]
        passages = []
        if routes_file is not None and Path(routes_file).exists():
            connection.executemany(
                """
                INSERT OR REPLACE INTO ligne (route_id, nom_court, nom_long, mode)
                VALUES (?, ?, ?, ?)
                """,
                (
                    (row["route_id"], _court(row), _long(row), _mode(row))
                    for row in csv.DictReader(_opened(routes_file))
                    if row.get("route_id")
                ),
            )
            connection.executemany(
                "INSERT OR REPLACE INTO trip_ligne (trip_id, route_id) VALUES (?, ?)",
                (
                    (row["trip_id"], row["route_id"])
                    for row in csv.DictReader(_opened(trips_file))
                    if row.get("trip_id") and row.get("route_id")
                ),
            )
        with times_file.open(newline="") as handle:
            for row in csv.DictReader(handle):
                departure = row.get("departure_time") or row.get("arrival_time")
                if not departure or not row.get("stop_id"):
                    continue
                passages.append((row["trip_id"], row["stop_id"], _seconds(departure)))
                if len(passages) >= 5000:
                    connection.executemany(
                        "INSERT OR REPLACE INTO passage (trip_id, stop_id, depart_sec) VALUES (?, ?, ?)",
                        passages,
                    )
                    passages.clear()
            if passages:
                connection.executemany(
                    "INSERT OR REPLACE INTO passage (trip_id, stop_id, depart_sec) VALUES (?, ?, ?)",
                    passages,
                )
        with dates_file.open(newline="") as handle:
            connection.executemany(
                "INSERT OR REPLACE INTO service_day (service_id, day) VALUES (?, ?)",
                (
                    (row["service_id"], f"{row['date'][:4]}-{row['date'][4:6]}-{row['date'][6:8]}")
                    for row in csv.DictReader(handle)
                    if row.get("exception_type") in {None, "", "1"}
                ),
            )
    return stored


def listed_trips(
    database: Path,
    realtime: Path,
    origin_stop_id: str,
    destination_stop_id: str,
    at: datetime,
    window: timedelta = timedelta(hours=2),
    neighbor_window: timedelta = timedelta(hours=12),
    stops_database: Path | None = None,
) -> list[dict]:
    local = at.astimezone(PARIS)
    wider = _pairs(database, origin_stop_id, destination_stop_id, local, neighbor_window, stops_database)
    realtime_rows = _realtime(realtime, [item["trip_id"] for item in wider], origin_stop_id, stops_database)
    annotated = []
    for item in wider:
        status, delay = realtime_rows.get(item["trip_id"], (None, None))
        annotated.append(
            {
                **item,
                "departure_time": item["departure"].isoformat(),
                "status": status,
                "delay_seconds": delay,
                "etat": etat_of(status, delay),
            }
        )
    annotated.sort(key=lambda item: item["departure"])
    for index, item in enumerate(annotated):
        item["precedent"] = _brief(annotated[index - 1]) if index else None
        item["suivant"] = _brief(annotated[index + 1]) if index + 1 < len(annotated) else None
        item["precedent_meme_type"] = _brief(_same_before(annotated, index))
        item["suivant_meme_type"] = _brief(_same_after(annotated, index))
    start = local - window
    end = local + window
    shown = [item for item in annotated if start <= item["departure"] <= end]
    for item in shown:
        item.pop("departure", None)
    return shown


def _pairs(database, origin_stop_id, destination_stop_id, local, window, stops_database):
    origins = _family(stops_database, origin_stop_id)
    destinations = _family(stops_database, destination_stop_id)
    start = local - window
    end = local + window
    days = {(start.date()).isoformat(), local.date().isoformat(), end.date().isoformat()}
    origin_marks = ",".join("?" for _ in origins)
    destination_marks = ",".join("?" for _ in destinations)
    day_marks = ",".join("?" for _ in days)
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            f"""
            SELECT circulation.trip_id, circulation.kind, origin.depart_sec,
                   destination.depart_sec, service_day.day
            FROM passage AS origin
            JOIN passage AS destination
              ON destination.trip_id = origin.trip_id
            JOIN circulation ON circulation.trip_id = origin.trip_id
            JOIN service_day ON service_day.service_id = circulation.service_id
            WHERE origin.stop_id IN ({origin_marks})
              AND destination.stop_id IN ({destination_marks})
              AND service_day.day IN ({day_marks})
              AND destination.depart_sec > origin.depart_sec
            """,
            (*origins, *destinations, *days),
        ).fetchall()
    found = []
    for trip_id, kind, origin_sec, _destination_sec, day in rows:
        midnight = datetime.fromisoformat(day).replace(tzinfo=PARIS)
        departure = midnight + timedelta(seconds=origin_sec)
        if start <= departure <= end:
            found.append({"trip_id": trip_id, "kind": kind, "departure": departure})
    return found


def find_line(database: Path, route_id: str) -> dict | None:
    if not Path(database).exists() or not _has_lines(database):
        return None
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT route_id, nom_court, nom_long, mode FROM ligne WHERE route_id = ?", (route_id,)
        ).fetchone()
    if row is None:
        return None
    return {
        "route_id": row[0],
        "nom_court": row[1],
        "nom_long": row[2],
        "mode": row[3],
        "titre": _titre(row[1], row[2]),
    }


def _has_lines(database: Path) -> bool:
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {"ligne", "trip_ligne"} <= tables


def _titre(nom_court: str, nom_long: str | None) -> str:
    """Ce qu'on lit en titre. Le nom court seul ne dit rien à quelqu'un qui
    ne connaît pas la numérotation SNCF : « C13 » veut dire Lyon - Bourg."""
    if nom_long and nom_long != nom_court:
        return f"{nom_court} · {nom_long}"
    return nom_court or "Ligne sans nom"


def stops_between(
    database: Path,
    trip_id: str,
    origin: str,
    destination: str,
    stops_database: Path | None = None,
) -> list[dict]:
    origins = set(_family(stops_database, origin))
    destinations = set(_family(stops_database, destination))
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(passage)")}
        order = "COALESCE(seq, depart_sec), depart_sec" if "seq" in columns else "depart_sec"
        rows = connection.execute(
            f"SELECT stop_id FROM passage WHERE trip_id = ? ORDER BY {order}",
            (trip_id,),
        ).fetchall()
    start = next((index for index, row in enumerate(rows) if row[0] in origins), None)
    if start is None:
        return []
    end = next(
        (index for index, row in enumerate(rows) if index > start and row[0] in destinations),
        None,
    )
    if end is None:
        return []
    names = _station_names(stops_database)
    found = []
    seen = None
    for (stop_id,) in rows[start : end + 1]:
        name, key = names.get(stop_id, (stop_id, stop_id))
        if key == seen:
            continue
        seen = key
        found.append({"stop_id": stop_id, "name": name})
    return found


def trip_stops_all(
    database: Path,
    trip_id: str,
    stops_database: Path | None = None,
) -> list[dict]:
    """Les arrêts du train entier, dans l'ordre, avec son heure de départ.

    `stops_between` ne rend que le tronçon entre deux gares, parce que c'est ce
    qu'on affiche à l'écran. Ici on veut le trajet complet : un comptage ne se
    rattache qu'à un arrêt, mais l'estimation d'une fréquentation a besoin de
    savoir d'où venait le train et où il va — le reste du trajet, c'est de la
    charge qu'on ne voit pas.

    L'ordre est celui du GTFS, pas celui des horaires : un train qui repasse
    par une gare rare peut avoir un `depart_sec` plus petit à son retour. La
    colonne `seq` est là pour ça, quand la feed la fournit.

    Chaque arrêt porte `avant` : les gares desservies juste avant, dans l'ordre.
    C'est ce qui permet de rattacher un effectif à ce qui entre et ce qui sort,
    sans refaire un groupby sur les arrêts plus loin.
    """
    if not Path(database).exists() or not trip_id:
        return []
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(passage)")}
        order = "COALESCE(seq, depart_sec), depart_sec" if "seq" in columns else "depart_sec"
        rows = connection.execute(
            f"SELECT stop_id, depart_sec FROM passage WHERE trip_id = ? ORDER BY {order}",
            (trip_id,),
        ).fetchall()
    names = _station_names(stops_database)
    found: list[dict] = []
    seen: str | None = None
    for stop_id, depart_sec in rows:
        name, key = names.get(stop_id, (stop_id, stop_id))
        if key == seen:
            continue
        seen = key
        found.append({"stop_id": stop_id, "name": name, "depart_sec": depart_sec})
    for index, item in enumerate(found):
        item["avant"] = [autre["stop_id"] for autre in found[max(0, index - 3) : index]]
    return found


def _station_names(stops_database: Path | None) -> dict[str, tuple[str, str]]:
    if stops_database is None or not Path(stops_database).exists():
        return {}
    from comptagefer.offer import open_stops

    with open_stops(stops_database) as connection:
        rows = connection.execute("SELECT stop_id, name, parent FROM stop").fetchall()
    by_id = {row[0]: row for row in rows}
    names = {}
    for stop_id, name, parent in rows:
        if parent and parent in by_id:
            names[stop_id] = (by_id[parent][1], parent)
        else:
            names[stop_id] = (name, stop_id)
    return names


def _realtime(database: Path, trip_ids: list[str], origin_stop_id: str, stops_database) -> dict:
    if not trip_ids or not database.exists():
        return {}
    origins = _family(stops_database, origin_stop_id)
    trip_marks = ",".join("?" for _ in trip_ids)
    origin_marks = ",".join("?" for _ in origins)
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            f"""
            WITH latest AS (
                SELECT trip_id, MAX(fetched_at) AS fetched_at
                FROM trip_update
                WHERE trip_id IN ({trip_marks})
                GROUP BY trip_id
            )
            SELECT latest.trip_id, trip.schedule_relationship, stop.departure_delay
            FROM latest
            JOIN trip_update AS trip
              ON trip.trip_id = latest.trip_id AND trip.fetched_at = latest.fetched_at
            LEFT JOIN stop_update AS stop
              ON stop.trip_id = latest.trip_id
             AND stop.fetched_at = latest.fetched_at
             AND stop.stop_id IN ({origin_marks})
            """,
            (*trip_ids, *origins),
        ).fetchall()
    return {trip_id: (status, delay) for trip_id, status, delay in rows}


def _family(stops_database: Path | None, stop_id: str) -> list[str]:
    if stops_database is None or not Path(stops_database).exists():
        return [stop_id]
    from comptagefer.offer import _stop_family

    return _stop_family(stops_database, stop_id)


def _same_before(items, index):
    kind = items[index]["kind"]
    for item in reversed(items[:index]):
        if item["kind"] == kind:
            return item
    return None


def _same_after(items, index):
    kind = items[index]["kind"]
    for item in items[index + 1 :]:
        if item["kind"] == kind:
            return item
    return None


def _brief(item):
    if item is None:
        return None
    return {
        "trip_id": item["trip_id"],
        "kind": item["kind"],
        "departure_time": item["departure_time"],
        "status": item["status"],
        "delay_seconds": item["delay_seconds"],
        "etat": item["etat"],
    }


def _seconds(value: str) -> int:
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    return hours * 3600 + minutes * 60 + seconds
