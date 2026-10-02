import csv
from io import StringIO
from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.publish import Config, Publication

ETATS = {
    "precedent": {"status": "PREVIOUS", "delay_seconds": 0},
    "courant": {"status": "CURRENT"},
    "precedent_meme_type": {"status": "PREVIOUS_SAME_TYPE"},
    "suivant": {"status": "NEXT"},
    "suivant_meme_type": {"status": "NEXT_SAME_TYPE"},
}


def _lignes(csv_text: str) -> tuple[list[str], dict[str, str]]:
    rows = list(csv.reader(StringIO(csv_text)))
    headers = rows[1]
    values = rows[2]
    return headers, dict(zip(headers, values))


def test_export_preserve_les_cinq_etats_figes_apres_post(tmp_path: Path):
    """Les cinq états choisis sont figés dans le snapshot et exportés sans relire le temps réel."""
    client = TestClient(create_app(tmp_path))
    response = client.post(
        "/api/sessions",
        json={
            "client_id": "cinq-etats",
            "origin_stop_id": "A",
            "destination_stop_id": "B",
            "origin_name": "Gare A",
            "destination_name": "Gare B",
            "trip_id": "TRIP1",
            "passengers": 0,
            "reliability": 100,
            "snapshot": ETATS,
        },
    )
    assert response.status_code == 200

    exported = client.get("/api/export.csv").text
    headers, values = _lignes(exported)

    # Les trois colonnes historiques gardent leur place ; les deux nouvelles
    # sont ajoutées à la fin pour ne pas décaler les consommateurs existants.
    assert headers[-2:] == ["precedent_meme_type", "suivant_meme_type"]
    assert [values[key] for key in ETATS] == [
        "PREVIOUS",
        "CURRENT",
        "PREVIOUS_SAME_TYPE",
        "NEXT",
        "NEXT_SAME_TYPE",
    ]
    assert values["passengers"] == "0"
    assert "compte_id" not in headers


def test_etats_absents_partiels_et_malformes_restent_vides(tmp_path: Path):
    """Les anciens snapshots incomplets ne deviennent pas des états inventés."""
    from comptagefer.publish import render_csv

    ligne = {
        "created_at": "2026-10-01T12:00:00+00:00",
        "origin_name": "A",
        "destination_name": "B",
        "passengers": 0,
        "reliability": 100,
        "pseudo": "",
        "comment": "",
        "standing": None,
        "seats_free": None,
        "imbalance": None,
        "kind": "count",
        "legs": None,
        "trip_id": "TRIP1",
        "trajet": None,
    }
    snapshots = [
        None,
        {"courant": {"status": "ONLY_CURRENT"}},
        {"precedent_meme_type": "malforme", "suivant_meme_type": {"status": 0}},
        ["ancien format"],
    ]
    for snapshot in snapshots:
        headers, values = _lignes(render_csv([{**ligne, "snapshot": snapshot}]))
        assert headers[-2:] == ["precedent_meme_type", "suivant_meme_type"]
        assert values["precedent_meme_type"] == ""
        assert values["suivant_meme_type"] == ("0" if isinstance(snapshot, dict) and isinstance(snapshot.get("suivant_meme_type"), dict) else "")


def test_publication_envoie_le_meme_csv_que_export(tmp_path: Path):
    """La publication utilise le CSV complet produit depuis les snapshots SQLite."""
    client = TestClient(create_app(tmp_path))
    client.post(
        "/api/sessions",
        json={
            "client_id": "publication-etats",
            "origin_stop_id": "A",
            "destination_stop_id": "B",
            "origin_name": "Gare A",
            "destination_name": "Gare B",
            "trip_id": "TRIP1",
            "passengers": 4,
            "reliability": 90,
            "snapshot": ETATS,
        },
    )
    telecharge = client.get("/api/export.csv").text

    class Reponse:
        status = 200

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    requetes = []

    def opener(request, timeout=None):
        requetes.append(request)
        return Reponse()

    publication = Publication(
        Config(api_key="test", dataset_id="dataset"),
        tmp_path / "publish.json",
        lambda: telecharge,
    )
    resultat = publication.publish_now(opener=opener)

    assert resultat["ok"] is True
    assert len(requetes) == 1
    boundary = requetes[0].get_header("Content-type").split("boundary=", 1)[1]
    payload = requetes[0].data.split(b"\r\n\r\n", 1)[1].rsplit(
        f"\r\n--{boundary}--\r\n".encode(), 1
    )[0].decode("utf-8")
    assert payload == telecharge
    headers, values = _lignes(payload)
    assert headers[-2:] == ["precedent_meme_type", "suivant_meme_type"]
    assert [values[key] for key in ETATS] == [
        "PREVIOUS",
        "CURRENT",
        "PREVIOUS_SAME_TYPE",
        "NEXT",
        "NEXT_SAME_TYPE",
    ]
