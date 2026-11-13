"""Les rames franciliennes, et l'échelle d'un comptage sans matériel.

Deux ajouts. Les formations RER et tram-train d'Île-de-France entrent dans la
liste fermée. Et un usager qui ne reconnaît pas son matériel peut dire combien
de voitures a sa rame, puis compter une seule voiture en disant si elle est
représentative, moins ou plus fréquentée que les autres : sans ces deux
renseignements, 40 voyageurs dans une voiture ne se rapportent à rien.
"""

import csv
from io import StringIO

from fastapi.testclient import TestClient

from comptagefer.app import _edition_values, _materiel_texte, create_app
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
    assert formation("mi 09 5 VOITURES").label == "MI 09 5 voitures"
    assert formation("mi 2n alteo 5 voitures").label == "MI 2N Altéo 5 voitures"


def test_un_mi09_se_compte_voiture_par_voiture(tmp_path):
    voitures = [{"rame": 1, "position": p, "passengers": 8} for p in range(1, 6)]
    client, reponse = _post(
        tmp_path, materiel="MI 09 5 voitures", composition="US", perimetre="voiture", voitures=voitures,
    )
    assert reponse.status_code == 200, reponse.text
    assert client.get("/api/sessions").json()[0]["materiel"] == "MI 09 5 voitures"


def test_une_voiture_seule_sans_materiel_revient_avec_son_echelle(tmp_path):
    client, reponse = _post(
        tmp_path, composition="UM2", perimetre="voiture", nb_voitures=5, representativite="plus",
    )
    assert reponse.status_code == 200, reponse.text
    row = client.get("/api/sessions").json()[0]
    assert (row["nb_voitures"], row["representativite"], row["perimetre"]) == (5, "plus", "voiture")
    assert _materiel_texte(row) == (
        "rame de 5 voitures · UM2, compté sur une voiture · voiture plus fréquentée que les autres voitures"
    )


def test_le_nombre_de_voitures_seul_sur_une_rame_entiere(tmp_path):
    client, reponse = _post(tmp_path, composition="US", perimetre="um", nb_voitures="6")
    assert reponse.status_code == 200, reponse.text
    row = client.get("/api/sessions").json()[0]
    assert (row["nb_voitures"], row["representativite"]) == (6, None)


def test_le_csv_telecharge_porte_les_deux_colonnes(tmp_path):
    client, reponse = _post(
        tmp_path, composition="US", perimetre="voiture", nb_voitures=4, representativite="moins",
    )
    assert reponse.status_code == 200, reponse.text
    lignes = list(csv.DictReader(StringIO(client.get("/api/export.csv").text)))
    assert (lignes[0]["nb_voitures"], lignes[0]["representativite"]) == ("4", "moins")


def test_une_voiture_seule_sans_nombre_de_voitures_est_refusee(tmp_path):
    assert "nombre de voitures" in _refus(tmp_path, composition="US", perimetre="voiture", representativite="representative")


def test_la_representativite_d_une_rame_entiere_est_refusee(tmp_path):
    assert "une seule voiture" in _refus(
        tmp_path, composition="US", perimetre="um", nb_voitures=5, representativite="plus",
    )


def test_une_representativite_inventee_est_refusee(tmp_path):
    assert "représentativité" in _refus(
        tmp_path, composition="US", perimetre="voiture", nb_voitures=5, representativite="bondée",
    )


def test_un_nombre_de_voitures_hors_borne_est_refuse(tmp_path):
    for valeur in (0, 21, -3, "cinq", True, 2.5):
        assert "nombre de voitures" in _refus(tmp_path, composition="US", perimetre="um", nb_voitures=valeur)


def test_le_materiel_donne_deja_le_nombre(tmp_path):
    assert "matériel" in _refus(
        tmp_path, materiel="Z 8800 4 voitures", composition="US", perimetre="um", nb_voitures=4,
    )


def test_le_detail_voiture_par_voiture_n_a_pas_de_representativite(tmp_path):
    voitures = [{"rame": 1, "position": p, "passengers": 10} for p in range(1, 5)]
    assert "une seule voiture" in _refus(
        tmp_path, materiel="Z 8800 4 voitures", composition="US", perimetre="voiture",
        voitures=voitures, representativite="plus",
    )


def test_l_edition_relit_et_valide_l_echelle():
    base = {"composition": "US", "perimetre": "voiture", "passengers": "40", "reliability": "70"}
    values = _edition_values({**base, "nb_voitures": "5", "representativite": "moins"}, "count")
    assert (values["nb_voitures"], values["representativite"]) == (5, "moins")
    vide = _edition_values({**base, "perimetre": "um", "nb_voitures": "", "representativite": ""}, "count")
    assert (vide["nb_voitures"], vide["representativite"]) == (None, None)
