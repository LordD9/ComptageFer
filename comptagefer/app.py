import os
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from comptagefer.rt import (
    ALERTS_URL,
    SIRI_URL,
    TU_URL,
    cache_status,
    poll_once,
)


def create_app(data_dir: Path) -> FastAPI:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    database = data_dir / "app.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS session (
                id INTEGER PRIMARY KEY
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
        return "<!doctype html><title>ComptageFer</title><h1>ComptageFer</h1>"

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
