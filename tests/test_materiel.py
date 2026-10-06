"""Le matériel roulant, la composition, la répartition, et ce que l'effectif compte.

Trois colonnes qui n'ont aucun sens séparément : « AGC 3 caisses » ne dit pas
l'échelle, une composition ne dit pas si l'usager a compté une voiture ou la
rame. Ensemble, l'effectif devient comparable d'un train à l'autre — et sans
elles, 180 voyageurs dans une voiture d'une UM3 se confondent avec 180 dans les
trois.

Depuis la phase 10, deux listes s'y ajoutent : la répartition par voiture et la
répartition par rame. Elles **détaillent** `passengers`, elles ne le remplacent
pas — c'est la propriété que la moitié des tests ci-dessous défend.
"""

import csv
import sqlite3
from io import StringIO

from fastapi.testclient import TestClient

from comptagefer.app import COMPOSITIONS, SCHEMA_SAISIE, create_app
from comptagefer.publish import render_csv

PHOTO = {"precedent": None, "courant": {"trip_id": "TRIP1", "status": "SCHEDULED"}, "suivant": None}

AGC3 = "AGC 3 caisses"

NOUVELLES_FORMATIONS = {
    "Regio 2N 6 caisses": (6, "caisse", "Regio 2N (Z 55500, Z 56300)"),
    "Regio 2N 10 caisses": (10, "caisse", "Regio 2N (Z 55500, Z 56000)"),
    "Omneo Premium 8 caisses": (8, "caisse", "Omneo Premium (Z 56700)"),
    "Omneo Premium 10 caisses": (10, "caisse", "Omneo Premium (Z 56600, Z 56800)"),
    "Coradia Liner 6 caisses": (6, "caisse", "Coradia Liner (B 85000)"),
    "RER NG 6 voitures": (6, "voiture", "RER NG (Z 58000)"),
    "RER NG 7 voitures": (7, "voiture", "RER NG (Z 58500)"),
    "Z 5600 4 voitures": (4, "voiture", "Z 5600 (RER C)"),
    "Z 5600 6 voitures": (6, "voiture", "Z 5600 (RER C)"),
    "MI 2N 5 voitures": (5, "voiture", "MI 2N SNCF Eole (Z 22500)"),
}


def _body(**extra) -> dict:
    body = {
        "client_id": "jeton",
        "origin_stop_id": "A",
        "destination_stop_id": "B",
        "origin_name": "Gare du Nord",
        "destination_name": "Gare de Lyon",
        "trip_id": "TRIP1",
        "passengers": 180,
        "reliability": 70,
        "snapshot": PHOTO,
    }
    body.update(extra)
    return body


def _store(tmp_path, **extra) -> dict:
    client = TestClient(create_app(tmp_path))
    stored = client.post("/api/sessions", json=_body(**extra))
    assert stored.status_code == 200, stored.text
    return client.get("/api/sessions").json()[0]


def _refuse(tmp_path, **extra) -> str:
    """Poster et rendre le motif du refus — jamais un booléen seul.

    Un 422 sans son motif prouve qu'on refuse, pas qu'on refuse **pour la bonne
    raison** : les règles de cohérence sont voisines, et un test qui n'attrape
    que le code passerait avec la mauvaise règle en place.
    """
    client = TestClient(create_app(tmp_path))
    refused = client.post("/api/sessions", json=_body(**extra))
    assert refused.status_code == 422, refused.text
    assert client.get("/api/sessions").json() == [], "un refus ne doit rien laisser"
    return refused.json()["detail"]


# --- le matériel, la composition, le périmètre -------------------------------


def test_the_three_come_back_exactly_as_they_went_in(tmp_path):
    row = _store(tmp_path, materiel="Z 20500 4 voitures", composition="UM3", perimetre="voiture")
    assert row["materiel"] == "Z 20500 4 voitures"
    assert row["composition"] == "UM3"
    assert row["perimetre"] == "voiture"


def test_a_count_without_material_stays_possible(tmp_path):
    """La plupart des comptages n'auront pas de matériel.

    Le rendre obligatoire aurait vidé la base : on sait compter, on ne sait pas
    toujours sous quel numéro de série on l'a fait. Le trio reste facultatif,
    c'est sa cohérence qui ne l'est pas.
    """
    row = _store(tmp_path)
    assert row["materiel"] is None
    assert row["composition"] is None
    assert row["perimetre"] is None


def test_the_material_alone_is_kept(tmp_path):
    """Savoir le type de rame sans connaître sa composition reste utile.

    C'est le seul des trois qui ne crée pas d'ambiguïté sur l'effectif : une
    formation ne prétend rien sur l'échelle. Le refuser ferait perdre une
    information au lieu d'éviter une incohérence.
    """
    row = _store(tmp_path, materiel="Z 6400 4 caisses")
    assert row["materiel"] == "Z 6400 4 caisses"
    assert row["composition"] is None


def test_a_composition_without_a_perimetre_is_refused(tmp_path):
    """« UM3 » seul laisse croire qu'on a compté la rame entière.

    Pour un train francilien, c'est faux deux fois sur trois : la plupart des
    gens montent dans une voiture et ne voient pas les deux autres. Un CSV où
    la moitié des lignes a une composition et pas de périmètre se lit comme une
    absence d'information, alors que c'est une information fausse.
    """
    assert "périmètre" in _refuse(tmp_path, composition="UM3")


def test_a_perimetre_without_a_composition_is_refused(tmp_path):
    """Le périmètre seul n'a pas d'échelle : on ne sait pas ce qu'il compte."""
    assert "ensemble" in _refuse(tmp_path, perimetre="voiture")


def test_a_us_is_counted_as_a_whole_rame(tmp_path):
    """Une US est une rame de plusieurs voitures, pas une voiture.

    On compte toujours la rame entière : « US » et « toute la rame » vont
    ensemble, c'est même le cas le plus courant.
    """
    row = _store(tmp_path, composition="US", perimetre="um")
    assert (row["composition"], row["perimetre"]) == ("US", "um")


def test_a_us_counted_as_one_carriage_is_fine(tmp_path):
    row = _store(tmp_path, materiel="Z 6400 4 caisses", composition="US", perimetre="voiture")
    assert (row["composition"], row["perimetre"]) == ("US", "voiture")


def test_composition_is_case_and_space_insensitive(tmp_path):
    """Le champ est saisi sur un téléphone, dans un train.

    « um3 » et « UM3 » sont la même composition ; refuser les minuscules ferait
    perdre le relevé pour une raison qui n'est pas une information fausse.
    """
    row = _store(tmp_path, composition=" um3 ", perimetre=" Voiture ")
    assert (row["composition"], row["perimetre"]) == ("UM3", "voiture")


def test_an_invented_composition_is_refused(tmp_path):
    """« UM2 ramesihat » dans un champ libre rend le CSV illisible.

    Le lecteur verrait 180 voyageurs et ne saurait pas s'il faut les multiplier
    par deux. Une liste fermée rend la donnée calculable, ou absente.
    """
    assert "UM3" in _refuse(tmp_path, composition="UM2 ramesihat", perimetre="um")


def test_every_composition_has_a_car_count(tmp_path):
    """La table des compositions est la seule chose qui rende l'effectif calculable.

    Si une composition entre sans nombre de voitures, on ne peut plus multiplier
    un effectif par-voiture pour obtenir la charge de la rame. C'est la
    propriété qui justifie la liste fermée.
    """
    assert COMPOSITIONS == {"US": 1, "UM2": 2, "UM3": 3}
    for _composition, voitures in COMPOSITIONS.items():
        assert voitures >= 1


# --- le matériel est une liste fermée ----------------------------------------


def test_the_material_is_case_accent_and_space_insensitive(tmp_path):
    """« agc 3 caisses » et « Régiolis 4 caisses » arrivent du téléphone.

    Le libellé est choisi dans une liste, mais la casse, les accents et les
    espaces multiples y arrivent tout seuls — et un refus pour ça ferait perdre
    un comptage déjà fait, pour une raison qui n'est pas une information fausse.
    La forme **stockée** reste celle de la liste : c'est elle que lit le CSV.
    """
    row = _store(tmp_path, materiel="  agc  3 CAISSES ")
    assert row["materiel"] == AGC3


def test_an_unknown_material_is_refused(tmp_path):
    """Un matériel hors liste rend le schéma de comptage incalculable.

    C'est lui qui donne le nombre de voitures : accepter « Z » * 200 ferait
    dessiner une rame qui n'existe pas, et le nombre de cases du schéma serait
    inventé. Le champ reste facultatif, donc un matériel inconnu se tait — il
    ne s'écrit pas.
    """
    assert "matériel" in _refuse(tmp_path, materiel="Z" * 200)
    assert "matériel" in _refuse(tmp_path, materiel="TGV Duplex")


def test_an_absent_material_is_not_an_unknown_one(tmp_path):
    """Vide, c'est un choix ; inventé, c'est une erreur. Les deux ne se confondent pas."""
    row = _store(tmp_path, materiel="   ")
    assert row["materiel"] is None


def test_the_ten_french_formations_have_verified_counts_and_families():
    from comptagefer.materiel import FORMATIONS, formation, libelles, normaliser

    actual = {
        item.label: (item.voitures, item.mot, item.famille)
        for item in FORMATIONS
        if item.label in NOUVELLES_FORMATIONS
    }
    assert actual == NOUVELLES_FORMATIONS
    assert len(actual) == 10
    assert {"Regio 2N 7 voitures", "Regio 2N 8 voitures"} <= set(libelles())
    assert len(libelles()) == len(set(libelles()))
    assert normaliser("  regio 2n 6 CAISSES ") == "Regio 2N 6 caisses"
    assert normaliser("coradia liner 6 caisses") == "Coradia Liner 6 caisses"
    for label, (count, word, _family) in NOUVELLES_FORMATIONS.items():
        entree = formation(label)
        assert entree is not None
        assert (entree.voitures, entree.mot) == (count, word)


def test_the_new_six_carriage_regio_2n_is_valid_for_us_and_um2(tmp_path):
    materiel = "Regio 2N 6 caisses"
    us = [{"rame": 1, "position": pos, "passengers": 30} for pos in range(1, 7)]
    um2 = [
        {"rame": rame, "position": pos, "passengers": 15}
        for rame in (1, 2) for pos in range(1, 7)
    ]
    row = _store(tmp_path / "us", materiel=materiel, composition="US", perimetre="voiture", voitures=us)
    assert row["materiel"] == materiel and row["voitures"] == us
    row = _store(tmp_path / "um2", materiel=materiel, composition="UM2", perimetre="voiture", voitures=um2)
    assert row["materiel"] == materiel and row["voitures"] == um2

    assert "toutes les voitures" in _refuse(
        tmp_path / "refus", materiel=materiel, composition="US", perimetre="voiture", passengers=180,
        voitures=us[:-1],
    )


# --- la répartition par voiture ----------------------------------------------


def _voitures_deux_rames() -> list[dict]:
    """Six voitures sur deux rames d'un AGC 3 caisses, 180 au total."""
    return [
        {"rame": 1, "position": 1, "passengers": 40},
        {"rame": 1, "position": 2, "passengers": 30},
        {"rame": 1, "position": 3, "passengers": 20},
        {"rame": 2, "position": 1, "passengers": 30},
        {"rame": 2, "position": 2, "passengers": 30},
        {"rame": 2, "position": 3, "passengers": 30},
    ]


def test_the_car_breakdown_comes_back_and_keeps_the_total(tmp_path):
    """Le détail s'ajoute au total, il ne le remplace pas.

    `passengers` reste l'effectif du comptage : c'est lui que lisent le score,
    la carte et le classement. La répartition dit **où** sont les gens, ce
    qu'aucune de ces trois choses ne sait faire.
    """
    row = _store(
        tmp_path,
        materiel=AGC3,
        composition="UM2",
        perimetre="voiture",
        voitures=_voitures_deux_rames(),
    )
    assert row["passengers"] == 180
    assert row["voitures"] == _voitures_deux_rames()
    assert sum(item["passengers"] for item in row["voitures"]) == row["passengers"]


def test_the_car_breakdown_requires_the_material(tmp_path):
    """Sans matériel, on ne sait pas combien de voitures dessiner.

    Le refus est ici, et pas une liste de positions devinée : inventer le
    nombre de voitures d'une rame inconnue serait fabriquer de la donnée.
    """
    detail = _refuse(
        tmp_path,
        composition="UM2",
        perimetre="voiture",
        voitures=[{"rame": 1, "position": 1, "passengers": 180}],
    )
    assert "matériel" in detail


def test_a_breakdown_that_does_not_sum_to_the_total_is_refused(tmp_path):
    """Le total et le détail ne peuvent pas se contredire dans la même ligne."""
    detail = _refuse(
        tmp_path,
        materiel=AGC3,
        composition="US",
        perimetre="voiture",
        voitures=[
            {"rame": 1, "position": 1, "passengers": 40},
            {"rame": 1, "position": 2, "passengers": 30},
            {"rame": 1, "position": 3, "passengers": 20},
        ],
    )
    assert "total" in detail


def test_a_missing_car_of_a_counted_unit_is_refused(tmp_path):
    """Une voiture vide comptée et une voiture non comptée ne sont pas la même chose.

    Exiger les trois positions oblige à dire « zéro » plutôt que de laisser un
    trou qu'un lecteur du CSV interprétera comme une voiture oubliée.
    """
    detail = _refuse(
        tmp_path,
        materiel=AGC3,
        composition="US",
        perimetre="voiture",
        passengers=70,
        voitures=[
            {"rame": 1, "position": 1, "passengers": 40},
            {"rame": 1, "position": 3, "passengers": 30},
        ],
    )
    assert "toutes les voitures" in detail


def test_a_car_outside_the_formation_is_refused(tmp_path):
    """Un AGC 3 caisses n'a pas de quatrième caisse."""
    detail = _refuse(
        tmp_path,
        materiel=AGC3,
        composition="US",
        perimetre="voiture",
        passengers=70,
        voitures=[
            {"rame": 1, "position": 1, "passengers": 40},
            {"rame": 1, "position": 3, "passengers": 30},
            {"rame": 1, "position": 4, "passengers": 0},
        ],
    )
    assert "position" in detail


def test_the_same_car_twice_is_refused(tmp_path):
    detail = _refuse(
        tmp_path,
        materiel=AGC3,
        composition="US",
        perimetre="voiture",
        passengers=70,
        voitures=[
            {"rame": 1, "position": 1, "passengers": 40},
            {"rame": 1, "position": 1, "passengers": 30},
            {"rame": 1, "position": 3, "passengers": 0},
        ],
    )
    assert "deux fois" in detail


def test_a_car_breakdown_with_the_unit_perimetre_is_refused(tmp_path):
    """Le périmètre dit ce que compte **chaque valeur**, et il doit être d'accord.

    Une répartition par voiture dont le périmètre dit « um » ferait lire chaque
    voiture comme une rame entière — l'erreur d'un facteur trois, silencieuse.
    """
    detail = _refuse(
        tmp_path,
        materiel=AGC3,
        composition="US",
        perimetre="um",
        voitures=[
            {"rame": 1, "position": 1, "passengers": 60},
            {"rame": 1, "position": 2, "passengers": 60},
            {"rame": 1, "position": 3, "passengers": 60},
        ],
    )
    assert "voiture" in detail


# --- la répartition par rame --------------------------------------------------


def test_the_unit_breakdown_carries_the_counted_units(tmp_path):
    """La liste porte la sélection : une UM3 dont on compte deux rames en a deux.

    C'est la donnée, pas une déduction. Sans elle, un comptage en UM3 ne dit pas
    si les 180 voyageurs sont dans une rame ou dans les trois.
    """
    row = _store(
        tmp_path,
        composition="UM3",
        perimetre="um",
        rames=[{"rame": 2, "passengers": 80}, {"rame": 3, "passengers": 100}],
    )
    assert row["passengers"] == 180
    assert row["rames"] == [{"rame": 2, "passengers": 80}, {"rame": 3, "passengers": 100}]
    assert row["voitures"] is None


def test_a_unit_breakdown_without_a_composition_is_refused(tmp_path):
    """Sans composition, il n'y a pas d'échelle : la rame 2 de quoi ?"""
    detail = _refuse(tmp_path, perimetre="um", rames=[{"rame": 1, "passengers": 180}])
    assert "composition" in detail


def test_a_unit_index_outside_the_composition_is_refused(tmp_path):
    """Une UM2 n'a pas de rame 3."""
    detail = _refuse(
        tmp_path,
        composition="UM2",
        perimetre="um",
        rames=[{"rame": 1, "passengers": 80}, {"rame": 3, "passengers": 100}],
    )
    assert "hors composition" in detail


def test_the_same_unit_twice_is_refused(tmp_path):
    detail = _refuse(
        tmp_path,
        composition="UM2",
        perimetre="um",
        rames=[{"rame": 1, "passengers": 90}, {"rame": 1, "passengers": 90}],
    )
    assert "deux fois" in detail


def test_a_unit_breakdown_that_does_not_sum_to_the_total_is_refused(tmp_path):
    detail = _refuse(
        tmp_path,
        composition="UM2",
        perimetre="um",
        rames=[{"rame": 1, "passengers": 80}, {"rame": 2, "passengers": 80}],
    )
    assert "total" in detail


def test_a_unit_breakdown_with_the_car_perimetre_is_refused(tmp_path):
    detail = _refuse(
        tmp_path,
        composition="UM2",
        perimetre="voiture",
        rames=[{"rame": 1, "passengers": 90}, {"rame": 2, "passengers": 90}],
    )
    assert "um" in detail


def test_the_two_breakdowns_agree_or_are_refused(tmp_path):
    """Les deux niveaux peuvent coexister, mais alors ils se recoupent.

    La somme des voitures d'une rame **est** son effectif : deux colonnes qui se
    contredisent dans la même ligne ne sont pas deux informations, c'est une
    erreur que personne ne verra.
    """
    voitures = _voitures_deux_rames()
    row = _store(
        tmp_path,
        materiel=AGC3,
        composition="UM2",
        perimetre="voiture",
        voitures=voitures,
        rames=[{"rame": 1, "passengers": 90}, {"rame": 2, "passengers": 90}],
    )
    assert row["rames"] == [{"rame": 1, "passengers": 90}, {"rame": 2, "passengers": 90}]
    assert row["voitures"] == voitures

    detail = _refuse(
        tmp_path / "refus",
        materiel=AGC3,
        composition="UM2",
        perimetre="voiture",
        voitures=voitures,
        rames=[{"rame": 1, "passengers": 90}, {"rame": 2, "passengers": 80}],
    )
    assert "effectif" in detail


def test_a_breakdown_of_another_kind_is_refused(tmp_path):
    """Le détail ne s'applique qu'au comptage.

    Un serpent se lit arrêt par arrêt dans `legs`, et un train signalé n'a pas
    d'effectif du tout : accepter une répartition là ferait deux sources pour
    le même chiffre.
    """
    client = TestClient(create_app(tmp_path))
    refused = client.post(
        "/api/missing",
        json=_body(
            passengers=None,
            reliability=None,
            composition="UM2",
            perimetre="um",
            rames=[{"rame": 1, "passengers": 180}],
        ),
    )
    assert refused.status_code == 200, refused.text
    assert client.get("/api/sessions").json()[0]["rames"] is None


def test_a_string_where_an_integer_is_expected_is_refused(tmp_path):
    """« 180 » n'est pas 180 : une chaîne qui se glisse dans un effectif s'écrit sans bruit."""
    detail = _refuse(
        tmp_path,
        composition="UM2",
        perimetre="um",
        rames=[{"rame": 1, "passengers": "180"}],
    )
    assert "entier" in detail


# --- le CSV ------------------------------------------------------------------


def _lignes_csv(texte: str) -> list[list[str]]:
    """Le CSV, lu avec le module `csv` et non découpé sur les virgules.

    Les deux répartitions sont du JSON, donc pleines de virgules et de
    guillemets : un `split(",")` les coupe en morceaux et fait passer une
    colonne vide pour une colonne remplie. Le fichier est lu par des gens et
    par des robots, donc il se lit avec un lecteur de CSV.
    """
    return list(csv.reader(StringIO(texte)))


def _colonnes(lignes: list[str]) -> tuple[list[str], list[str]]:
    lu = _lignes_csv("\n".join(lignes))
    return lu[1], lu[2]


def test_the_csv_carries_the_columns(tmp_path):
    """Sans elles dans le CSV, la donnée ne sort jamais de la base.

    C'est le fichier qui part sur data.gouv : une colonne absente du CSV est une
    colonne jamais collectée, quoi qu'elle vaille dans l'interface.
    """
    client = TestClient(create_app(tmp_path))
    client.post(
        "/api/sessions",
        json=_body(materiel=AGC3, composition="UM2", perimetre="voiture", voitures=_voitures_deux_rames()),
    )
    csv = render_csv(client.get("/api/sessions").json())
    header, ligne = _colonnes(csv.splitlines())
    for nom in ("materiel", "composition", "perimetre", "voitures", "rames"):
        assert nom in header, nom
    assert ligne[header.index("materiel")] == AGC3
    assert ligne[header.index("composition")] == "UM2"
    assert ligne[header.index("perimetre")] == "voiture"
    assert '"rame": 1' in ligne[header.index("voitures")], "le détail n'est pas dans le fichier"


def test_the_downloaded_csv_carries_them(tmp_path):
    """Le vrai fichier, pas `render_csv` appelé à la main.

    `/api/export.csv` est ce que l'usager télécharge et ce qui part sur
    data.gouv. Appeler le rendu directement ne prouverait que la moitié : si
    l'endpoint lisait une autre requête que `_list_saisies` — celle qui porte
    les colonnes — le test passerait quand même, et le fichier publié n'aurait
    que des cases vides. C'est le genre d'écart que rien d'autre ne voit.
    """
    client = TestClient(create_app(tmp_path))
    client.post(
        "/api/sessions",
        json=_body(materiel=AGC3, composition="UM2", perimetre="voiture", voitures=_voitures_deux_rames()),
    )
    reponse = client.get("/api/export.csv")
    assert reponse.status_code == 200
    header, ligne = _colonnes(reponse.text.splitlines())
    for nom, attendu in (
        ("materiel", AGC3),
        ("composition", "UM2"),
        ("perimetre", "voiture"),
    ):
        assert nom in header, f"{nom} absent du fichier téléchargé"
        assert ligne[header.index(nom)] == attendu, f"{nom} est vide dans le fichier téléchargé"
    assert '"rame": 2' in ligne[header.index("voitures")]


def test_a_count_without_material_leaves_the_csv_cells_empty(tmp_path):
    """Des cellules vides, pas des zéros ni des « null ».

    Un 0 se lit comme une composition « aucune voiture », ce qui est faux.
    """
    client = TestClient(create_app(tmp_path))
    client.post("/api/sessions", json=_body())
    header, ligne = _colonnes(render_csv(client.get("/api/sessions").json()).splitlines())
    for nom in ("materiel", "composition", "perimetre", "voitures", "rames"):
        assert ligne[header.index(nom)] == ""


# --- la migration ------------------------------------------------------------


def test_an_existing_database_gains_the_columns_and_keeps_its_rows(tmp_path):
    """Une installation qui a déjà compté doit survivre au changement.

    On fabrique une base à l'ancien schéma — sans le matériel, la composition,
    le périmètre, ni les deux répartitions — avec un relevé dedans, puis on
    ouvre l'application dessus. Trois choses à voir : les colonnes apparaissent,
    le relevé d'avant est encore là, et le nouveau chemin écrit dans la base
    migrée. Une migration qui oublie la liste de recopie reconstruit une table
    vide, et l'installation perd ses données sans un mot.
    """
    ajoutees = {"materiel", "composition", "perimetre", "voitures", "rames"}
    database = tmp_path / "app.db"
    anciens = [(nom, type_) for nom, type_ in SCHEMA_SAISIE if nom not in ajoutees]
    connexion = sqlite3.connect(database)
    connexion.execute(
        f"CREATE TABLE saisie ({', '.join(f'{nom} {type_}' for nom, type_ in anciens)}, PRIMARY KEY (client_id))"
    )
    connexion.execute(
        "INSERT INTO saisie (client_id, kind, origin_stop_id, destination_stop_id, passengers, created_at)"
        " VALUES ('avant', 'count', 'A', 'B', 42, '2026-09-01T10:00:00+00:00')"
    )
    connexion.commit()
    connexion.close()

    client = TestClient(create_app(tmp_path))
    colonnes = {row[1] for row in sqlite3.connect(database).execute("PRAGMA table_info(saisie)")}
    assert ajoutees <= colonnes

    lignes = client.get("/api/sessions").json()
    assert len(lignes) == 1, "la migration a vidé la table"
    assert lignes[0]["passengers"] == 42
    assert lignes[0]["materiel"] is None, "un relevé ancien n'a pas de matériel, pas un matériel vide"
    assert lignes[0]["voitures"] is None and lignes[0]["rames"] is None

    # Et le nouveau chemin écrit dans la base migrée, répartitions comprises.
    stocke = client.post(
        "/api/sessions",
        json=_body(materiel=AGC3, composition="US", perimetre="voiture", voitures=[
            {"rame": 1, "position": 1, "passengers": 60},
            {"rame": 1, "position": 2, "passengers": 60},
            {"rame": 1, "position": 3, "passengers": 60},
        ]),
    )
    assert stocke.status_code == 200, stocke.text
    relu = client.get("/api/sessions").json()[1]
    assert relu["composition"] == "US"
    assert relu["materiel"] == AGC3
    assert len(relu["voitures"]) == 3
    assert relu["legs"] is None and relu["trajet"] is None, (
        "les colonnes de la migration de clé ont été perdues en route"
    )


def test_a_legacy_material_with_markup_does_not_execute(tmp_path):
    """Une ligne ancienne peut porter n'importe quoi dans `materiel`.

    Le formulaire d'édition propose la liste fermée, mais il doit aussi garder
    sélectionnable la valeur d'avant — et cette valeur vient de la base, donc
    d'un texte qu'on n'a pas écrit. Un `<option value="<script>">` s'exécute.
    """
    from comptagefer.app import _edition_page

    page = _edition_page({"kind": "count", "client_id": "jeton", "passengers": 10, "reliability": 70,
                          "materiel": '"><script>alert(1)</script>'})
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_the_reading_page_shows_the_breakdown(tmp_path):
    """Le détail doit se relire sans télécharger le CSV.

    Sur un téléphone, dans un train, le fichier n'est pas sous la main au moment
    où la question se pose — et c'est la seule façon de vérifier qu'un effectif
    se lit à l'échelle de la rame.
    """
    client = TestClient(create_app(tmp_path))
    client.post(
        "/api/sessions",
        json=_body(materiel=AGC3, composition="UM2", perimetre="voiture", voitures=_voitures_deux_rames()),
    )
    fiche = client.get("/releve?client_id=jeton&kind=count")
    assert fiche.status_code == 200
    assert "Répartition" in fiche.text
    assert "Rame 1 : 40, 30, 20 — 90 voyageurs sur 3 caisses" in fiche.text
    assert "Rame 2 : 30, 30, 30 — 90 voyageurs sur 3 caisses" in fiche.text
    assert "compté par caisse" in fiche.text


def test_the_reading_page_says_the_unit_breakdown(tmp_path):
    """Sans matériel, le mot retombe sur « voitures » — et le détail reste lisible."""
    client = TestClient(create_app(tmp_path))
    client.post(
        "/api/sessions",
        json=_body(composition="UM2", perimetre="um", rames=[{"rame": 1, "passengers": 80}, {"rame": 2, "passengers": 100}]),
    )
    fiche = client.get("/releve?client_id=jeton&kind=count")
    assert "Rame 1 : 80 voyageurs" in fiche.text
    assert "Rame 2 : 100 voyageurs" in fiche.text


def test_a_legacy_material_stays_editable(tmp_path):
    """Un relevé d'avant, au matériel libre, reste modifiable.

    Le formulaire d'édition propose la liste fermée, mais une ligne écrite quand
    le matériel était un texte libre porte encore « Z 23500 ». La refuser ferait
    perdre le relevé au moment même où on le corrige — le chemin d'édition est
    donc tolérant, et le chemin de saisie, lui, ne l'est pas.
    """
    from comptagefer.app import _edition_values

    values = _edition_values(
        {"materiel": "Z 23500", "composition": "UM2", "perimetre": "um", "passengers": "180", "reliability": "70"},
        "count",
    )
    assert values["materiel"] == "Z 23500"
