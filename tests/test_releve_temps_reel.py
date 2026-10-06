"""La photo figée distingue le train compté de ses quatre voisins."""

from html import escape

from fastapi.testclient import TestClient

from comptagefer.app import _photo_detail, create_app


def test_photo_detail_nomme_chaque_role_et_conserve_les_etats():
    snapshot = {
        "precedent": {"kind": "TGV", "etat": "supprimé"},
        "precedent_meme_type": {"kind": "TER", "etat": "à l'heure"},
        "courant": {"kind": "TER", "etat": "en retard", "delay_seconds": 300},
        "suivant": {"kind": "Intercités", "etat": "à l'heure"},
        "suivant_meme_type": {"kind": "TER", "etat": "à l'heure"},
    }
    html = _photo_detail({"snapshot": snapshot})
    for role, valeur in (
        ("Train compté", "TER en retard 5 min"),
        ("Précédent, tous types", "TGV supprimé"),
        ("Précédent du même type", "TER à l'heure"),
        ("Suivant, tous types", "Intercités à l'heure"),
        ("Suivant du même type", "TER à l'heure"),
    ):
        assert f"<li><strong>{role}</strong> : {escape(valeur)}</li>" in html
    assert "même trajet" in html


def test_photo_detail_tolere_les_anciennes_photos_et_echappe_les_donnees():
    html = _photo_detail({"snapshot": {"courant": {"kind": "<script>", "status": "SCHEDULED"}, "suivant": "invalide"}})
    assert "&lt;script&gt; SCHEDULED" in html
    assert "<script>" not in html
    assert html.count("non renseigné") == 4
    assert _photo_detail({"snapshot": None}) == "aucune photo du temps réel"


def test_releve_affiche_la_photo_detaillee_enregistree(tmp_path):
    client = TestClient(create_app(tmp_path))
    snapshot = {key: {"kind": "TER", "etat": "à l'heure"} for key in (
        "courant", "precedent", "precedent_meme_type", "suivant", "suivant_meme_type"
    )}
    response = client.post("/api/sessions", json={
        "client_id": "photo-detail", "origin_stop_id": "A", "destination_stop_id": "B",
        "origin_name": "Alpha", "destination_name": "Beta", "trip_id": "T",
        "passengers": 12, "reliability": 80, "snapshot": snapshot,
    })
    assert response.status_code == 200
    html = client.get("/releve?client_id=photo-detail&kind=count").text
    assert "Temps réel au moment du comptage" in html
    assert "<strong>Train compté</strong> : TER à l&#x27;heure" in html
    assert "<strong>Suivant du même type</strong> : TER à l&#x27;heure" in html
    assert "TER à l&#x27;heure · TER à l&#x27;heure" not in html


def test_methode_elargit_le_libelle_transilien(tmp_path):
    html = TestClient(create_app(tmp_path)).get("/methode").text
    assert "Transiliens et RER." in html
    assert "Transiliens (RER et ligne U)" not in html
