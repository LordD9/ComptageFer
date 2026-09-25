import csv
import json
import os
import sqlite3
import threading
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from html import escape
from io import BytesIO, StringIO
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

from comptagefer.offer import nearest_stops, search_stops, trips_serving
from comptagefer.page import PAGE
from comptagefer.rt import (
    ALERTS_URL,
    SIRI_URL,
    TU_URL,
    cache_status,
    poll_once,
)

STOPS_URL = "https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip"


def create_app(data_dir: Path) -> FastAPI:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    database = data_dir / "app.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS saisie (
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

    app = FastAPI(title="ComptageFer")

    @app.get("/health")
    def health() -> dict[str, str]:
        with sqlite3.connect(database) as connection:
            connection.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/api/rt")
    def rt_status() -> dict[str, int | str | None]:
        return cache_status(data_dir / "rt.db")

    @app.get("/offline.js")
    def offline_js() -> PlainTextResponse:
        script = Path(__file__).parent / "offline.js"
        return PlainTextResponse(script.read_text(), media_type="text/javascript")

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return PAGE

    @app.get("/api/stops")
    def stops(q: str = "") -> list[dict]:
        return search_stops(data_dir / "stops.db", q)

    @app.get("/api/stops/nearest")
    def nearby(lat: float, lon: float) -> list[dict]:
        return nearest_stops(data_dir / "stops.db", lat, lon)

    @app.get("/api/trips")
    def trips(from_: str = Query(alias="from"), to: str = Query(), at: str = Query()) -> list[dict]:
        parsed = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return trips_serving(data_dir / "rt.db", from_, to, parsed, stops_database=data_dir / "stops.db")

    @app.get("/api/sessions")
    def list_sessions() -> list[dict]:
        return _list_saisies(database)

    @app.get("/comptages", response_class=HTMLResponse)
    def comptages() -> str:
        return _reading_page(_list_saisies(database))

    @app.get("/api/export.csv")
    def export_csv() -> PlainTextResponse:
        return PlainTextResponse(
            _export_csv(_list_saisies(database)),
            media_type="text/csv; charset=utf-8",
        )

    @app.post("/api/sessions")
    def sessions(body: dict) -> dict:
        return _save_saisie(database, body, kind="count")

    @app.post("/api/missing")
    def missing(body: dict) -> dict:
        return _save_saisie(database, body, kind="missing")

    return app


def create_production_app() -> FastAPI:
    data_dir = Path(os.environ.get("COMPTAGEFER_DATA", "data"))
    app = create_app(data_dir)
    thread = threading.Thread(
        target=_poll_forever,
        args=(data_dir / "rt.db",),
        name="gtfs-rt",
        daemon=True,
    )
    thread.start()
    return app


def _fetch(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def _poll_forever(database: Path) -> None:
    try:
        ensure_stop_names(database.parent / "stops.db")
    except Exception:
        import logging
        logging.getLogger("comptagefer.rt").exception("import des noms de gares échoué")
    while True:
        try:
            poll_once(
                database,
                fetch_trips=lambda: _fetch(TU_URL),
                fetch_siri=lambda: _fetch(SIRI_URL, timeout=120),
                fetch_alerts=lambda: _fetch(ALERTS_URL),
                now=datetime.now(timezone.utc),
            )
        except Exception:
            import logging
            logging.getLogger("comptagefer.rt").exception("poll GTFS-RT échoué")
        time.sleep(120)


def ensure_stop_names(database: Path) -> None:
    from comptagefer.offer import import_stop_names, open_stops

    with open_stops(database) as connection:
        if connection.execute("SELECT COUNT(*) FROM stop").fetchone()[0]:
            return
    payload = _fetch(STOPS_URL, timeout=120)
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        text = archive.read("stops.txt")
    temporary = database.with_suffix(".txt")
    temporary.write_bytes(text)
    try:
        import_stop_names(database, temporary)
    finally:
        temporary.unlink(missing_ok=True)


def _save_saisie(database: Path, body: dict, kind: str) -> dict:
    client_id = str(body.get("client_id") or "")
    origin = str(body.get("origin_stop_id") or "")
    destination = str(body.get("destination_stop_id") or "")
    if not client_id or not origin or not destination:
        raise HTTPException(status_code=422, detail="origine, destination et jeton requis")
    passengers = body.get("passengers")
    reliability = body.get("reliability")
    if kind == "count":
        if not isinstance(passengers, int) or passengers < 0:
            raise HTTPException(status_code=422, detail="effectif invalide")
        if not isinstance(reliability, int) or not 0 <= reliability <= 100:
            raise HTTPException(status_code=422, detail="fiabilité invalide")
    standing = _indicator(body.get("standing"))
    seats_free = _indicator(body.get("seats_free"))
    imbalance = _indicator(body.get("imbalance"))
    snapshot = body.get("snapshot")
    snapshot_text = json.dumps(snapshot, ensure_ascii=False) if snapshot is not None else None
    if snapshot_text and len(snapshot_text) > 20_000:
        snapshot_text = None
    with sqlite3.connect(database) as connection:
        existing = connection.execute(
            "SELECT client_id, kind FROM saisie WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        if existing:
            return {"client_id": existing[0], "kind": existing[1], "stored": False}
        connection.execute(
            """
            INSERT INTO saisie (
                client_id, origin_stop_id, destination_stop_id, origin_name, destination_name, trip_id,
                passengers, reliability, pseudo, comment, standing, seats_free, imbalance, snapshot, kind, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_id,
                origin,
                destination,
                (body.get("origin_name") or "")[:80] or None,
                (body.get("destination_name") or "")[:80] or None,
                body.get("trip_id"),
                passengers if kind == "count" else None,
                reliability if kind == "count" else None,
                (body.get("pseudo") or "")[:40] or None,
                (body.get("comment") or "")[:280] or None,
                standing,
                seats_free,
                imbalance,
                snapshot_text,
                kind,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return {"client_id": client_id, "kind": kind, "stored": True}


def _indicator(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or not 0 <= value <= 100:
        raise HTTPException(status_code=422, detail="indicateur invalide")
    return value


def _list_saisies(database: Path) -> list[dict]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT client_id, origin_name, destination_name, trip_id, passengers,
                   reliability, pseudo, standing, seats_free, imbalance, snapshot, kind, created_at
            FROM saisie
            ORDER BY created_at
            """
        ).fetchall()
    listed = []
    for row in rows:
        snapshot = json.loads(row[10]) if row[10] else None
        listed.append(
            {
                "client_id": row[0],
                "origin_name": row[1],
                "destination_name": row[2],
                "trip_id": row[3],
                "passengers": row[4],
                "reliability": row[5],
                "pseudo": row[6],
                "standing": row[7],
                "seats_free": row[8],
                "imbalance": row[9],
                "snapshot": snapshot,
                "kind": row[11],
                "created_at": row[12],
            }
        )
    return listed


def _photo_status(snapshot: object, key: str) -> str:
    if not isinstance(snapshot, dict):
        return ""
    item = snapshot.get(key) or {}
    if not isinstance(item, dict):
        return ""
    return str(item.get("status") or "")


def _reading_page(rows: list[dict]) -> str:
    cards = []
    for row in rows:
        who = escape(row["pseudo"]) if row["pseudo"] else "anonyme"
        origin = escape(row["origin_name"] or "")
        destination = escape(row["destination_name"] or "")
        passengers = "" if row["passengers"] is None else row["passengers"]
        cards.append(
            "<article class='card'>"
            f"<strong>{origin} → {destination}</strong>"
            f"<p>{passengers} voyageurs · {who}</p>"
            f"<p class='status'>précédent {_photo_status(row['snapshot'], 'precedent')} · "
            f"choisi {_photo_status(row['snapshot'], 'courant')} · "
            f"suivant {_photo_status(row['snapshot'], 'suivant')}</p>"
            "</article>"
        )
    body = "\n".join(cards) or "<p>Aucun comptage pour l'instant.</p>"
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Comptages</title>
<style>
  body {{ margin: 0; font: 18px/1.35 system-ui, sans-serif; background: #f4f1ea; color: #1c1915; }}
  main {{ max-width: 32rem; margin: 0 auto; padding: 1rem; }}
  .card {{ background: #fff; border-radius: 0.8rem; padding: 0.8rem; margin: 0.6rem 0; }}
  a {{ color: #1c1915; }}
</style>
</head>
<body>
<main>
  <h1>Comptages</h1>
  <p>Ce n'est pas une fréquentation officielle. Les partages sont sous Licence Ouverte 2.0.</p>
  <p><a href="/api/export.csv">Télécharger le CSV</a> · <a href="/">Compter</a></p>
  {body}
</main>
</body>
</html>
"""


def _export_csv(rows: list[dict]) -> str:
    buffer = StringIO()
    buffer.write("# Licence Ouverte 2.0\n")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "created_at",
            "origin",
            "destination",
            "passengers",
            "reliability",
            "pseudo",
            "standing",
            "seats_free",
            "imbalance",
            "precedent",
            "courant",
            "suivant",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["created_at"],
                row["origin_name"] or "",
                row["destination_name"] or "",
                row["passengers"] if row["passengers"] is not None else "",
                row["reliability"] if row["reliability"] is not None else "",
                row["pseudo"] or "",
                row["standing"] if row["standing"] is not None else "",
                row["seats_free"] if row["seats_free"] is not None else "",
                row["imbalance"] if row["imbalance"] is not None else "",
                _photo_status(row["snapshot"], "precedent"),
                _photo_status(row["snapshot"], "courant"),
                _photo_status(row["snapshot"], "suivant"),
            ]
        )
    return buffer.getvalue()
