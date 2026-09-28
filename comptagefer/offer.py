import csv
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Le GTFS national décrit une même gare par deux zones quand la SNCF y sépare le
# coach du train. « Grenoble » existe comme aire CTE (101 passages) et comme aire
# TER/TGV (1115) : la recherche en affiche deux, et celle du coach ne renvoie
# presque rien vers une vraie gare. Six noms sont dans ce cas, dont
# « Saint-Hilaire-De-Riez » écrit avec deux capitalisations.
#
# On fusionne les aires de même nom (casse et espaces insignifiants) distantes de
# moins de FUSION_METRES, lien transitif. Au-delà, ce sont deux lieux distincts
# qui portent le même nom, et les fusionner mentirait sur l'interstation
# comptée : Lérouville est à 641 m, Pouzauges à 3,6 km.
FUSION_METRES = 500


def _nom_cle(name: str) -> str:
    return " ".join(name.casefold().split())


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance à vol d'oiseau, assez fine pour deux quais d'une même gare."""
    return math.hypot(
        (lat1 - lat2) * 111_320,
        (lon1 - lon2) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2)),
    )


def open_stops(database: Path) -> sqlite3.Connection:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS stop (
            stop_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            lat REAL,
            lon REAL,
            parent TEXT,
            is_area INTEGER NOT NULL
        )
        """
    )
    # `parent` n'était pas indexé, alors que la moitié des requêtes le
    # cherchent : le décompte des enfants par gare, et la famille d'un arrêt.
    # Sans cet index, chacune parcourait les ~36 000 arrêts du GTFS national.
    connection.execute("CREATE INDEX IF NOT EXISTS stop_parent ON stop(parent)")
    return connection


def import_stop_names(database: Path, stops_file: Path) -> int:
    stored = 0
    with open_stops(database) as connection, stops_file.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if not row.get("stop_name"):
                continue
            connection.execute(
                """
                INSERT OR REPLACE INTO stop
                    (stop_id, name, lat, lon, parent, is_area)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["stop_id"],
                    row["stop_name"],
                    float(row["stop_lat"]) if row.get("stop_lat") else None,
                    float(row["stop_lon"]) if row.get("stop_lon") else None,
                    row.get("parent_station") or None,
                    1 if row.get("location_type") == "1" else 0,
                ),
            )
            stored += 1
    return stored


def nearest_stops(database: Path, lat: float, lon: float, limit: int = 5) -> list[dict]:
    with open_stops(database) as connection:
        rows = connection.execute(
            "SELECT stop_id, name, lat, lon FROM stop WHERE is_area = 1 AND lat IS NOT NULL AND lon IS NOT NULL"
        ).fetchall()
    # On classe sur la distance en mètres, pas sur des degrés carrés. À 48° N un
    # degré de longitude ne vaut que 0,67 degré de latitude, et l'écart change
    # l'ordre des gares proposées : sur un jeu de gares françaises, l'ordre des
    # trois plus proches différait pour 61 % des positions testées. Le tri est
    # en Python, donc peu importe que la clé soit une distance : c'est la même
    # que celle de `_fusionnees`.
    ranked = sorted(rows, key=lambda row: _distance_m(lat, lon, row[2], row[3]))
    return [{"stop_id": row[0], "name": row[1]} for row in ranked[:limit]]


def _fusionnees(rows: list[dict], limit: int) -> list[dict]:
    """Une entrée par gare, l'aire de coach et celle de train réunies.

    `rows` sont des aires déjà filtrées par le nom. Dans un même nom normalisé,
    deux aires distantes de moins de FUSION_METRES sont un seul lieu vu deux fois
    par le GTFS :

    - le lien est transitif, deux aires chacune à 400 m de l'aire retenue
      forment un seul groupe ;
    - l'aire gardée est celle qui a le plus d'enfants StopPoint, donc celle qui
      dessert le plus de trains ;
    - les autres ne sont pas perdues : la gare affichée n'est qu'une des deux
      moitiés, mais `_stop_family` regroupe les deux, donc les offres, le serpent
      et le temps réel voient les trains des deux aires ;
    - deux aires du même nom trop éloignées restent deux entrées distinctes,
      parce qu'une interstation comptée entre deux lieux distincts serait fausse.

    Une aire sans coordonnées n'est jamais fusionnée : sans distance, rien ne
    dit que c'est le même lieu.
    """
    par_nom: dict[str, list[dict]] = {}
    for row in rows:
        par_nom.setdefault(_nom_cle(row["name"]), []).append(row)

    found = []
    for membres in par_nom.values():
        groupes: list[list[dict]] = []
        for row in membres:
            if row["lat"] is None or row["lon"] is None:
                groupes.append([row])
                continue
            for groupe in groupes:
                if any(
                    membre["lat"] is not None
                    and membre["lon"] is not None
                    and _distance_m(membre["lat"], membre["lon"], row["lat"], row["lon"])
                    <= FUSION_METRES
                    for membre in groupe
                ):
                    groupe.append(row)
                    break
            else:
                groupes.append([row])
        for groupe in groupes:
            principale = max(groupe, key=lambda item: (item["enfants"], item["stop_id"]))
            found.append({"stop_id": principale["stop_id"], "name": principale["name"]})
    return found[:limit]


def search_stops(database: Path, query: str, limit: int = 8) -> list[dict]:
    needle = query.strip()
    if len(needle) < 2:
        return []
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with open_stops(database) as connection:
        rows = connection.execute(
            """
            SELECT stop_id, name, lat, lon FROM stop
            WHERE is_area = 1 AND name LIKE ? ESCAPE '\\'
            ORDER BY name
            LIMIT ?
            """,
            (f"%{escaped}%", limit * 4),
        ).fetchall()
        # Un seul GROUP BY remplace 32 COUNT(*). Les `stop_id` sont ceux déjà
        # trouvés par la requête du dessus, donc les deux ne peuvent pas
        # diverger sur le filtrage. Avec l'index sur `parent`, la requête
        # passe de 94 ms à quelques millisecondes sur 36 000 arrêts.
        enfants = dict(
            connection.execute(
                "SELECT parent, COUNT(*) FROM stop WHERE parent IN ({}) GROUP BY parent".format(
                    ",".join("?" * len(rows))
                ),
                tuple(row[0] for row in rows),
            ).fetchall()
        ) if rows else {}
        areas = [
            {
                "stop_id": stop_id,
                "name": name,
                "lat": lat,
                "lon": lon,
                "enfants": enfants.get(stop_id, 0),
            }
            for stop_id, name, lat, lon in rows
        ]
    return _fusionnees(areas, limit)


def _proximites(connection, stop_id: str) -> list[str]:
    """Les aires de gare que `stop_id` décrit, quand le GTFS en donne plusieurs.

    Même gare vue deux fois : une aire de coach et une aire de train, à quelques
    dizaines de mètres. On ne renvoie que celles dont le nom normalisé est
    identique ET qui sont à moins de FUSION_METRES de l'aire demandée — sinon
    deux lieux distincts du même nom (`Lérouville` à 641 m) seraient confondus, et
    l'interstation comptée entre les deux serait fausse.

    L'aire demandée est toujours dans le résultat, coordonnées absentes ou non :
    une recherche qui ne trouve aucune sœur rend l'aire seule, pas le vide.

    Seules les aires fusionnent. Un StopPoint demandé seul garde sa famille
    d'avant, le point et rien d'autre : l'appel qui le fournit désigne déjà un
    quai précis, on n'y ajoute pas les quais voisins sans qu'on l'ait demandé.
    """
    anchor = connection.execute(
        "SELECT name, lat, lon, is_area FROM stop WHERE stop_id = ?", (stop_id,)
    ).fetchone()
    if anchor is None:
        return [stop_id]
    name, lat, lon, is_area = anchor
    if not is_area:
        return [stop_id]
    candidates = connection.execute(
        "SELECT stop_id, name, lat, lon FROM stop WHERE is_area = 1 AND lower(name) = lower(?)",
        (name,),
    ).fetchall()
    key = _nom_cle(name)
    groupe = [stop_id]
    if lat is not None and lon is not None:
        for other_id, other_name, other_lat, other_lon in candidates:
            if other_id == stop_id or _nom_cle(other_name) != key:
                continue
            if other_lat is None or other_lon is None:
                continue
            if _distance_m(lat, lon, other_lat, other_lon) <= FUSION_METRES:
                groupe.append(other_id)
    return groupe


def _stop_family(stops_database: Path | None, stop_id: str) -> list[str]:
    """Les StopPoint d'une gare, ceux de l'aire de coach comprise.

    C'est le seul endroit où une gare se décompose en points : les offres, le
    serpent et le temps réel passent tous par là, donc une aire de coach
    oubliée ici disparaissait de la liste sans qu'aucune page ne le dise.
    """
    if stops_database is None or not stops_database.exists():
        return [stop_id]
    with open_stops(stops_database) as connection:
        aires = _proximites(connection, stop_id)
        marks = ",".join("?" for _ in aires)
        rows = connection.execute(
            f"SELECT stop_id FROM stop WHERE stop_id IN ({marks}) OR parent IN ({marks})",
            (*aires, *aires),
        ).fetchall()
    found = [row[0] for row in rows]
    return found or [stop_id]


def trips_serving(
    database: Path,
    origin_stop_id: str,
    destination_stop_id: str,
    at: datetime,
    window: timedelta = timedelta(hours=2),
    stops_database: Path | None = None,
) -> list[dict]:
    start = int((at - window).timestamp())
    end = int((at + window).timestamp())
    origins = _stop_family(stops_database, origin_stop_id)
    destinations = _stop_family(stops_database, destination_stop_id)
    origin_marks = ",".join("?" for _ in origins)
    destination_marks = ",".join("?" for _ in destinations)
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            f"""
            WITH latest AS (
                SELECT trip_id, MAX(fetched_at) AS fetched_at
                FROM trip_update
                GROUP BY trip_id
            )
            SELECT
                origin.trip_id,
                origin.departure_time,
                origin.departure_delay,
                trip.schedule_relationship
            FROM latest
            JOIN stop_update AS origin
              ON origin.trip_id = latest.trip_id
             AND origin.fetched_at = latest.fetched_at
            JOIN stop_update AS destination
              ON destination.trip_id = latest.trip_id
             AND destination.fetched_at = latest.fetched_at
            JOIN trip_update AS trip
              ON trip.trip_id = latest.trip_id
             AND trip.fetched_at = latest.fetched_at
            WHERE origin.stop_id IN ({origin_marks})
              AND destination.stop_id IN ({destination_marks})
              AND origin.departure_time BETWEEN ? AND ?
              AND destination.departure_time > origin.departure_time
            ORDER BY origin.departure_time
            """,
            (*origins, *destinations, start, end),
        ).fetchall()
    return [
        {
            "trip_id": trip_id,
            "departure_time": datetime.fromtimestamp(departure_time, timezone.utc).isoformat(),
            "delay_seconds": delay,
            "status": relationship,
        }
        for trip_id, departure_time, delay, relationship in rows
        if departure_time is not None
    ]
