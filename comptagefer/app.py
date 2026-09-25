import json
import os
import sqlite3
import threading
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

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
                trip_id TEXT,
                passengers INTEGER,
                reliability INTEGER,
                pseudo TEXT,
                comment TEXT,
                standing INTEGER,
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
    standing = body.get("standing")
    if standing is not None and (not isinstance(standing, int) or not 0 <= standing <= 100):
        raise HTTPException(status_code=422, detail="indicateur invalide")
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
                client_id, origin_stop_id, destination_stop_id, trip_id,
                passengers, reliability, pseudo, comment, standing, snapshot, kind, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_id,
                origin,
                destination,
                body.get("trip_id"),
                passengers if kind == "count" else None,
                reliability if kind == "count" else None,
                (body.get("pseudo") or "")[:40] or None,
                (body.get("comment") or "")[:280] or None,
                standing,
                snapshot_text,
                kind,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return {"client_id": client_id, "kind": kind, "stored": True}
