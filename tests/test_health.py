from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from comptagefer.app import create_app


def test_health_opens_database(tmp_path: Path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert (tmp_path / "app.db").is_file()


def test_home_names_the_project(tmp_path: Path):
    client = TestClient(create_app(data_dir=tmp_path))

    response = client.get("/")

    assert response.status_code == 200
    assert "ComptageFer" in response.text


def test_startup_creates_saisie_table(tmp_path: Path):
    create_app(data_dir=tmp_path)

    with sqlite3.connect(tmp_path / "app.db") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert "saisie" in tables
