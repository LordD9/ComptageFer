"""Le matériel roulant, la composition, et ce que l'effectif compte.

Trois colonnes qui n'ont aucun sens séparément : « Z 20500 » ne dit pas
l'échelle, une composition ne dit pas si l'usager a compté une voiture ou la
rame. Ensemble, l'effectif devient comparable d'un train à l'autre — et sans
elles, 180 voyageurs dans une voiture d'une UM3 se confondent avec 180 dans les
trois.
"""

from fastapi.testclient import TestClient

from comptagefer.app import COMPOSITIONS, create_app
from comptagefer.publish import render_csv

PHOTO = {"precedent": None, "courant": {"trip_id": "TRIP1", "status": "SCHEDULED"}, "suivant": None}


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


def test_the_three_come_back_exactly_as_they_went_in(tmp_path):
    row = _store(tmp_path, materiel="Z 20500", composition="UM3", perimetre="voiture")
    assert row["materiel"] == "Z 20500"
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

    C'est le seul des trois qui ne crée pas d'ambiguïté sur l'effectif : un
    « Z 20500 » ne prétend rien sur l'échelle. Le refuser ferait perdre une
    information au lieu d'éviter une incohérence.
    """
    row = _store(tmp_path, materiel="Z 6400")
    assert row["materiel"] == "Z 6400"
    assert row["composition"] is None


def test_a_composition_without_a_perimetre_is_refused(tmp_path):
    """« UM3 » seul laisse croire qu'on a compté la rame entière.

    Pour un train francilien, c'est faux deux fois sur trois : la plupart des
    gens montent dans une voiture et ne voient pas les deux autres. Un CSV où
    la moitié des lignes a une composition et pas de périmètre se lit comme une
    absence d'information, alors que c'est une information fausse.
    """
    client = TestClient(create_app(tmp_path))
    refused = client.post("/api/sessions", json=_body(composition="UM3"))
    assert refused.status_code == 422
    assert "périmètre" in refused.json()["detail"]
    assert client.get("/api/sessions").json() == [], "un refus ne doit rien laisser"


def test_a_perimetre_without_a_composition_is_refused(tmp_path):
    """Le périmètre seul n'a pas d'échelle : on ne sait pas ce qu'il compte."""
    client = TestClient(create_app(tmp_path))
    refused = client.post("/api/sessions", json=_body(perimetre="voiture"))
    assert refused.status_code == 422
    assert "ensemble" in refused.json()["detail"]


def test_the_whole_unit_of_a_us_is_its_single_carriage(tmp_path):
    """Une US, c'est une voiture : « toute la rame » n'y a pas de sens.

    L'accepter rendrait l'effectif indéfini — 180 voyageurs pour combien de
    voitures ? Un seul, par construction. On le refuse plutôt que de le
    normaliser en silence : une correction invisible dans le CSV est une donnée
    fausse que personne ne verra.
    """
    client = TestClient(create_app(tmp_path))
    refused = client.post("/api/sessions", json=_body(composition="US", perimetre="um"))
    assert refused.status_code == 422
    assert "US" in refused.json()["detail"]


def test_a_us_counted_as_one_carriage_is_fine(tmp_path):
    row = _store(tmp_path, materiel="Z 6400", composition="US", perimetre="voiture")
    assert (row["composition"], row["perimetre"]) == ("US", "voiture")


def test_composition_is_case_and_space_insensitive(tmp_path):
    """Le champ est saisi sur un téléphone, dans un train.

    « um3 » et « UM3 » sont la même composition ; refuser le minuscules ferait
    perdre le relevé pour une raison qui n'est pas une information fausse.
    """
    row = _store(tmp_path, composition=" um3 ", perimetre=" Voiture ")
    assert (row["composition"], row["perimetre"]) == ("UM3", "voiture")


def test_an_invented_composition_is_refused(tmp_path):
    """« UM2 ramesihat » dans un champ libre rend le CSV illisible.

    Le lecteur verrait 180 voyageurs et ne saurait pas s'il faut les multiplier
    par deux. Une liste fermée rend la donnée calculable, ou absente.
    """
    client = TestClient(create_app(tmp_path))
    refused = client.post("/api/sessions", json=_body(composition="UM2 ramesihat", perimetre="um"))
    assert refused.status_code == 422
    assert "UM3" in refused.json()["detail"]


def test_every_composition_has_a_car_count(tmp_path):
    """La table des compositions est la seule chose qui rende l'effectif calculable.

    Si une composition entre sans nombre de voitures, on ne peut plus multiplier
    un effectif par-voiture pour obtenir la charge de la rame. C'est la
    propriété qui justifie la liste fermée.
    """
    assert COMPOSITIONS == {"US": 1, "UM2": 2, "UM3": 3}
    for _composition, voitures in COMPOSITIONS.items():
        assert voitures >= 1


def test_the_material_is_truncated_not_rejected(tmp_path):
    """40 caractères suffisent pour « Z 22500 - UM3 -_resource 4 ».

    Refuser un champ trop long ferait perdre un comptage déjà fait ; le tronquer
    garde l'essentiel et ne casse rien.
    """
    row = _store(tmp_path, materiel="Z" * 200)
    assert len(row["materiel"]) == 40


def test_the_csv_carries_the_three_columns(tmp_path):
    """Sans elles dans le CSV, la donnée ne sort jamais de la base.

    C'est le fichier qui part sur data.gouv : une colonne absente du CSV est une
    colonne jamais collectée, quoi qu'elle vaille dans l'interface.
    """
    client = TestClient(create_app(tmp_path))
    client.post(
        "/api/sessions",
        json=_body(materiel="Z 20500", composition="UM3", perimetre="voiture"),
    )
    csv = render_csv(client.get("/api/sessions").json())
    header, ligne = csv.splitlines()[1], csv.splitlines()[2]
    colonnes = header.split(",")
    for nom in ("materiel", "composition", "perimetre"):
        assert nom in colonnes, nom
    rang = colonnes.index("materiel")
    valeurs = ligne.split(",")
    assert valeurs[rang] == "Z 20500"
    assert valeurs[colonnes.index("composition")] == "UM3"
    assert valeurs[colonnes.index("perimetre")] == "voiture"


def test_the_downloaded_csv_carries_them(tmp_path):
    """Le vrai fichier, pas `render_csv` appelé à la main.

    `/api/export.csv` est ce que l'usager télécharge et ce qui part sur
    data.gouv. Appeler le rendu directement ne prouverait que la moitié : si
    l'endpoint lisait une autre requête que `_list_saisies` — celle qui porte
    les trois colonnes — le test passerait quand même, et le fichier publié
    n'aurait que des cases vides. C'est le genre d'écart que rien d'autre ne
    voit.
    """
    client = TestClient(create_app(tmp_path))
    client.post(
        "/api/sessions",
        json=_body(materiel="Z 20500", composition="UM3", perimetre="voiture"),
    )
    reponse = client.get("/api/export.csv")
    assert reponse.status_code == 200
    lignes = reponse.text.splitlines()
    colonnes = lignes[1].split(",")
    valeurs = lignes[2].split(",")
    for nom, attendu in (
        ("materiel", "Z 20500"),
        ("composition", "UM3"),
        ("perimetre", "voiture"),
    ):
        assert nom in colonnes, f"{nom} absent du fichier téléchargé"
        assert valeurs[colonnes.index(nom)] == attendu, f"{nom} est vide dans le fichier téléchargé"


def test_a_count_without_material_leaves_the_csv_cells_empty(tmp_path):
    """Des cellules vides, pas des zéros ni des « null ».

    Un 0 se lit comme une composition « aucune voiture », ce qui est faux.
    """
    client = TestClient(create_app(tmp_path))
    client.post("/api/sessions", json=_body())
    ligne = render_csv(client.get("/api/sessions").json()).splitlines()[2].split(",")
    colonnes = render_csv(client.get("/api/sessions").json()).splitlines()[1].split(",")
    for nom in ("materiel", "composition", "perimetre"):
        assert ligne[colonnes.index(nom)] == ""


def test_an_existing_database_gains_the_columns_and_keeps_its_rows(tmp_path):
    """Une installation qui a déjà compté doit survivre au changement.

    On fabrique une base à l'ancien schéma — pas les trois colonnes — avec un
    relevé dedans, puis on ouvre l'application dessus. Deux choses à voir : les
    colonnes apparaissent, et le relevé d'avant est encore là. Une migration qui
    oublie la liste de recopie reconstruit une table vide, et l'installation
    perd ses données sans un mot.
    """
    import sqlite3

    from comptagefer.app import SCHEMA_SAISIE

    database = tmp_path / "app.db"
    anciens = [(nom, type_) for nom, type_ in SCHEMA_SAISIE if nom not in {"materiel", "composition", "perimetre"}]
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
    assert {"materiel", "composition", "perimetre"} <= colonnes

    lignes = client.get("/api/sessions").json()
    assert len(lignes) == 1, "la migration a vidé la table"
    assert lignes[0]["passengers"] == 42
    assert lignes[0]["materiel"] is None, "un relevé ancien n'a pas de matériel, pas un matériel vide"

    # Et le nouveau chemin écrit dans la base migrée.
    stocke = client.post("/api/sessions", json=_body(materiel="Z 20500", composition="UM3", perimetre="um"))
    assert stocke.status_code == 200, stocke.text
    relu = client.get("/api/sessions").json()[1]
    assert relu["composition"] == "UM3"
    assert relu["legs"] is None and relu["trajet"] is None, (
        "les colonnes de la migration de clé ont été perdues en route"
    )
