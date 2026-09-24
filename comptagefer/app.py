import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


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

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return "<!doctype html><title>ComptageFer</title><h1>ComptageFer</h1>"

    return app


def create_production_app() -> FastAPI:
    data_dir = Path(os.environ.get("COMPTAGEFER_DATA", "data"))
    return create_app(data_dir)
