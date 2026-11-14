"""Les rames franciliennes, et le comptage partiel voiture par voiture.

Les formations RER et tram-train d'Île-de-France entrent dans la liste fermée.
Sans matériel reconnu, l'usager peut dire combien de voitures a sa rame : le
comptage se fait alors voiture par voiture, comme avec une formation. Avec ou
sans matériel, il peut passer des voitures ; il dit à la fin si les voitures
comptées sont représentatives, moins ou plus fréquentées que les autres. Sans
ce renseignement, 40 voyageurs dans deux voitures sur cinq ne se rapportent à
rien.
"""

import csv
from io import StringIO

from fastapi.testclient import TestClient

from comptagefer.app import _materiel_texte, create_app
from comptagefer.materiel import CAISSE, VOITURE, formation

PHOTO = {"precedent": None, "courant": {"trip_id": "TRIP1", "status": "SCHEDULED"}, "suivant": None}

FRANCILIENNES = {
    "MI 09 5 voitures": (5, VOITURE),
    "MI 2N Altéo 5 voitures": (5, VOITURE),
    "MI 79 / MI 84 4 voitures": (4, VOITURE),
    "Z 8800 4 voitures": (4, VOITURE),
    "Avanto 5 caisses": (5, CAISSE),
    "Citadis Dualis 4 caisses": (4, CAISSE),
}


def _voitures(*positions, rame=1, passengers=10):
    return [{"rame": rame, "position": p, "passengers": passengers} for p in positions]


def _body(**extra) -> dict:
    body = {
        "client_id": "jeton", "origin_stop_id": "A", "destination_stop_id": "B",
        "origin_name": "Nanterre", "destination_name": "Châtelet", "trip_id": "TRIP1",
        "passengers": 40, "reliability": 70, "snapshot": PHOTO,
    }
    body.update(extra)
    return body


def _post(tmp_path, **extra):
    client = TestClient(create_app(tmp_path))
    return client, client.post("/api/sessions", json=_body(**extra))


def _stocke(tmp_path, **extra) -> dict:
    client, reponse = _post(tmp_path, **extra)
    assert reponse.status_code == 200, reponse.text
    return client.get("/api/sessions").json()[0]


def _refus(tmp_path, **extra) -> str:
    client, reponse = _post(tmp_path, **extra)
    assert reponse.status_code == 422, reponse.text
    assert client.get("/api/sessions").json() == []
    return reponse.json()["detail"]


def test_les_rames_franciliennes_ont_leur_nombre_de_voitures():
    for label, (voitures, mot) in FRANCILIENNES.items():
        entree = formation(label)
        assert entree is not None, label
        assert (entree.voitures, entree.mot) == (voitures, mot), label
    assert formation("mi 2n alteo 5 voitures").label == "MI 2N Altéo 5 voitures"


def test_un_comptage_partiel_avec_materiel_garde_sa_representativite(tmp_path):
    row = _stocke(
        tmp_path, materiel="MI 09 5 voitures", composition="US", perimetre="voiture",
        passengers=20, voitures=_voitures(2, 4), representativite="plus",
    )
    assert row["representativite"] == "plus"
    assert [v["position"] for v in row["voitures"]] == [2, 4]
    assert _materiel_texte(row) == (
        "MI 09 5 voitures · US, compté par voiture · voitures comptées plus fréquentées que les autres"
    )


def test_sans_materiel_le_nombre_de_voitures_donne_le_schema(tmp_path):
    row = _stocke(
        tmp_path, composition="UM2", perimetre="voiture", nb_voitures=5,
        passengers=10, voitures=_voitures(3), representativite="moins",
    )
    assert (row["nb_voitures"], row["representativite"], row["materiel"]) == (5, "moins", None)
    assert _materiel_texte(row) == (
        "rame de 5 voitures · UM2, compté par voiture · voitures comptées moins fréquentées que les autres"
    )


def test_toutes_les_voitures_sans_materiel_n_ont_pas_de_representativite(tmp_path):
    row = _stocke(
        tmp_path, composition="US", perimetre="voiture", nb_voitures=3,
        passengers=30, voitures=_voitures(1, 2, 3),
    )
    assert (row["nb_voitures"], row["representativite"]) == (3, None)


def test_le_csv_telecharge_porte_les_deux_colonnes(tmp_path):
    client, reponse = _post(
        tmp_path, composition="US", perimetre="voiture", nb_voitures=4,
        passengers=10, voitures=_voitures(1), representativite="representative",
    )
    assert reponse.status_code == 200, reponse.text
    lignes = list(csv.DictReader(StringIO(client.get("/api/export.csv").text)))
    assert (lignes[0]["nb_voitures"], lignes[0]["representativite"]) == ("4", "representative")


def test_un_comptage_partiel_sans_representativite_est_refuse(tmp_path):
    assert "représentatives" in _refus(
        tmp_path, materiel="Z 8800 4 voitures", composition="US", perimetre="voiture",
        passengers=10, voitures=_voitures(1),
    )


def test_un_comptage_complet_n_a_pas_de_representativite(tmp_path):
    assert "partiel" in _refus(
        tmp_path, materiel="Z 8800 4 voitures", composition="US", perimetre="voiture",
        passengers=40, voitures=_voitures(1, 2, 3, 4), representativite="plus",
    )


def test_une_representativite_sur_une_rame_entiere_est_refusee(tmp_path):
    assert "partiel" in _refus(tmp_path, composition="US", perimetre="um", representativite="plus")


def test_une_representativite_inventee_est_refusee(tmp_path):
    assert "représentativité" in _refus(
        tmp_path, composition="US", perimetre="voiture", nb_voitures=5,
        passengers=10, voitures=_voitures(1), representativite="bondée",
    )


def test_une_voiture_hors_du_nombre_donne_est_refusee(tmp_path):
    assert "position" in _refus(
        tmp_path, composition="US", perimetre="voiture", nb_voitures=3,
        passengers=10, voitures=_voitures(4), representativite="plus",
    )


def test_un_nombre_de_voitures_hors_borne_est_refuse(tmp_path):
    for valeur in (0, 21, -3, "cinq", True, 2.5):
        assert "nombre de voitures" in _refus(tmp_path, composition="US", perimetre="um", nb_voitures=valeur)


def test_le_materiel_donne_deja_le_nombre(tmp_path):
    assert "matériel" in _refus(
        tmp_path, materiel="Z 8800 4 voitures", composition="US", perimetre="um", nb_voitures=4,
    )
