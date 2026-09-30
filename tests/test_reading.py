import re

from fastapi.testclient import TestClient

from comptagefer.app import create_app


def _count(photo: dict) -> dict:
    return {
        "client_id": "jeton",
        "origin_stop_id": "A",
        "destination_stop_id": "B",
        "origin_name": "Lyon Part Dieu",
        "destination_name": "Nîmes Pont du Gard",
        "trip_id": "TRIP1",
        "passengers": 40,
        "reliability": 70,
        "pseudo": "railfan",
        "snapshot": photo,
    }


def test_saved_count_keeps_the_three_trains_without_the_feed(tmp_path):
    client = TestClient(create_app(tmp_path))
    photo = {
        "precedent": {"trip_id": "PREV", "status": "CANCELED"},
        "courant": {"trip_id": "TRIP1", "status": "SCHEDULED", "delay_seconds": 120},
        "suivant": {"trip_id": "NEXT", "status": "SCHEDULED"},
    }

    stored = client.post("/api/sessions", json=_count(photo))
    listed = client.get("/api/sessions")

    assert stored.status_code == 200
    assert listed.status_code == 200
    row = listed.json()[0]
    assert row["snapshot"] == photo
    assert row["pseudo"] == "railfan"
    assert row["origin_name"] == "Lyon Part Dieu"
    assert not (tmp_path / "rt.db").exists()


def test_optional_load_indicators_are_kept_and_bounded(tmp_path):
    client = TestClient(create_app(tmp_path))
    photo = {"precedent": None, "courant": {"trip_id": "TRIP1", "status": "SCHEDULED"}, "suivant": None}
    body = _count(photo)
    body["seats_free"] = 30
    body["imbalance"] = 15

    stored = client.post("/api/sessions", json=body)
    listed = client.get("/api/sessions").json()

    assert stored.status_code == 200
    assert listed[0]["seats_free"] == 30
    assert listed[0]["imbalance"] == 15

    refused = client.post(
        "/api/sessions",
        json={**body, "client_id": "autre", "imbalance": 140},
    )
    assert refused.status_code == 422


def test_export_and_page_show_the_count_and_the_licence(tmp_path):
    client = TestClient(create_app(tmp_path))
    photo = {
        "precedent": {"trip_id": "PREV", "status": "CANCELED"},
        "courant": {"trip_id": "TRIP1", "status": "SCHEDULED"},
        "suivant": None,
    }
    client.post("/api/sessions", json=_count(photo))

    page = client.get("/comptages")
    exported = client.get("/api/export.csv")

    assert page.status_code == 200
    assert "pas une fréquentation officielle" in page.text
    assert "railfan" in page.text
    assert "Lyon Part Dieu" in page.text
    assert "Licence Ouverte 2.0" in exported.text
    assert "railfan" in exported.text
    assert "CANCELED" in exported.text


# --- La lecture sur un écran large ----------------------------------------


def _trois_comptages(tmp_path) -> TestClient:
    """Trois relevés distincts, pour que l'ordre se voie."""
    client = TestClient(create_app(tmp_path))
    photo = {"precedent": None, "courant": {"trip_id": "T", "status": "SCHEDULED"}, "suivant": None}
    for nom, effectif, fiabilite in (
        ("Alpha", 10, 30),
        ("Bravo", 90, 90),
        ("Charlie", 50, 60),
    ):
        client.post(
            "/api/sessions",
            json={
                **_count(photo),
                "client_id": f"jeton-{nom}",
                "origin_name": nom,
                "destination_name": "Arrivée",
                "passengers": effectif,
                "reliability": fiabilite,
            },
        )
    return client


def test_the_newest_count_is_first_without_asking_for_anything(tmp_path):
    """Sans paramètre, le plus récent d'abord.

    L'ordre venait de l'insertion en base : le plus récent se trouvait en
    bas de page, et arriver sur /comptages demandait un défilement complet
    pour voir ce qui venait d'arriver. C'est un défaut de stockage lu comme
    un choix de lecture.
    """
    page = _trois_comptages(tmp_path).get("/comptages").text

    assert page.index("Charlie") < page.index("Bravo") < page.index("Alpha")


def test_a_count_without_a_value_sorts_last_in_both_directions(tmp_path):
    """Un « train signalé » n'a pas d'effectif : il passe par /api/missing.

    Trié par effectif, il doit tomber en dernier — jamais en premier, où il
    se lirait comme le relevé le plus chargé alors qu'il n'a pas été compté.
    """
    client = TestClient(create_app(tmp_path))
    photo = {"precedent": None, "courant": {"trip_id": "T", "status": "SCHEDULED"}, "suivant": None}
    client.post("/api/sessions", json={**_count(photo), "client_id": "a", "passengers": 12,
                                       "origin_name": "Compté", "destination_name": "Z"})
    client.post("/api/missing", json={"client_id": "b", "origin_stop_id": "A",
                                      "destination_stop_id": "B", "origin_name": "Signale",
                                      "destination_name": "Z", "trip_id": None,
                                      "snapshot": photo})

    for sens in ("asc", "desc"):
        page = client.get(f"/comptages?tri=passengers&sens={sens}").text
        assert page.index("Signale") > page.index("Compté"), (
            f"en tri {sens}, un relevé sans effectif ne doit pas passer devant un relevé compté"
        )


def test_a_missing_train_is_not_labelled_as_a_count(tmp_path):
    """Un train signalé n'est pas un comptage unique.

    Le mode affiché venait de `serpent` ou `unique` selon que le relevé
    était ou non un serpent : un train signalé, qui n'a pas été compté du
    tout, se lisait comme un comptage unique. C'est le genre d'étiquette
    qu'on ne voit plus après l'avoir lue une fois.
    """
    client = TestClient(create_app(tmp_path))
    photo = {"precedent": None, "courant": {"trip_id": "T", "status": "SCHEDULED"}, "suivant": None}
    client.post("/api/missing", json={"client_id": "b", "origin_stop_id": "A",
                                      "destination_stop_id": "B", "origin_name": "Lyon",
                                      "destination_name": "Vienne", "trip_id": None,
                                      "snapshot": photo})

    page = client.get("/comptages").text
    # Le corps de la liste, sans le formulaire de filtre : celui-ci nomme
    # les trois modes dans ses options, donc chercher « unique » dans la
    # page entière testerait la liste déroulante et pas le relevé.
    liste = page.split("<main>", 1)[1].split("<footer", 1)[0]
    releve = liste.split("class='cartes'", 1)[1]

    assert "signalé" in releve
    assert "unique" not in releve, "un train signalé n'est pas un comptage unique"
    # « sans effectif voyageurs » serait un faux pluriel sur une unité absente.
    assert "voyageurs" not in releve, "un relevé sans effectif n'a pas d'unité à écrire"


def test_sorting_is_in_the_url_and_survives_a_bad_one(tmp_path):
    client = _trois_comptages(tmp_path)

    croissant = client.get("/comptages?tri=passengers&sens=asc").text
    assert croissant.index("Alpha") < croissant.index("Charlie") < croissant.index("Bravo")

    decroissant = client.get("/comptages?tri=passengers&sens=desc").text
    assert decroissant.index("Bravo") < decroissant.index("Charlie") < decroissant.index("Alpha")

    # Une faute de frappe sur un paramètre d'URL ne doit pas casser la page :
    # le lien est tapé ou recopié, et une page d'erreur perdrait le lecteur.
    typo = client.get("/comptages?tri=voyageurs&sens=asc")
    assert typo.status_code == 200
    assert "Charlie" in typo.text


def test_the_page_shows_the_date_and_what_was_recorded_with_it(tmp_path):
    """La date, la fiabilité, les indicateurs et le commentaire sont dans la
    base et dans le CSV. Ils n'étaient sur aucune page : la donnée sortait
    sans qu'un lecteur du site puisse la voir."""
    client = TestClient(create_app(tmp_path))
    photo = {"precedent": None, "courant": {"trip_id": "T", "status": "SCHEDULED"}, "suivant": None}
    client.post(
        "/api/sessions",
        json={
            **_count(photo),
            "comment": "Car de substitution, le TER supprimé",
            "seats_free": 30,
            "imbalance": 15,
        },
    )

    page = client.get("/comptages").text

    # La date est formatée en français et en heure de Paris, pas en ISO UTC :
    # deux relevés du même jour n'ont pas la même heure.
    assert re.search(r"\d{2}/\d{2}/\d{4} à \d{2}:\d{2}", page), "la date doit être lisible"
    assert "+00:00" not in page, "l'ISO UTC n'est pas lisible par un humain"
    assert "30 % de places libres" in page
    assert "15 % d'écart de charge" in page
    assert "Car de substitution" in page
    # 70 % de fiabilité ne veut rien dire sans échelle : l'adjectif le dit.
    assert "moyen (70 %)" in page


def test_a_column_header_links_to_the_other_direction(tmp_path):
    """Recliquer sur la colonne active inverse le sens.

    Le lien doit viser l'état opposé à celui qu'on regarde. Un lien vers
    l'état courant ressemble à un lien mort, et celui qui reclique pour
    « l'inverser » ne voit rien se passer : c'est exactement ce qu'un test
    qui vérifie seulement le premier clic laisse passer.
    """
    client = _trois_comptages(tmp_path)

    asc = client.get("/comptages?tri=passengers&sens=asc").text
    assert "tri=passengers&amp;sens=desc" in asc or "tri=passengers&sens=desc" in asc
    assert asc.index("Alpha") < asc.index("Bravo")

    desc = client.get("/comptages?tri=passengers&sens=desc").text
    assert "tri=passengers&amp;sens=asc" in desc or "tri=passengers&sens=asc" in desc
    assert desc.index("Bravo") < desc.index("Alpha")


def test_a_table_column_shows_which_direction_it_is_sorted(tmp_path):
    """La colonne triée porte une flèche, sinon rien ne dit dans quel sens
    elle est triée : « 28 47 61 » se lit pareil dans les deux."""
    page = _trois_comptages(tmp_path).get("/comptages?tri=passengers&sens=desc").text

    entete = re.search(r"<th><a href='/comptages\?tri=passengers[^<]*</a></th>", page)
    assert entete, "l'en-tête de la colonne triée doit rester un lien"
    assert "▾" in entete.group(0), "le tri décroissant se marque par une flèche descendante"

    ascendante = _trois_comptages(tmp_path).get("/comptages?tri=passengers&sens=asc").text
    entete_asc = re.search(r"<th><a href='/comptages\?tri=passengers[^<]*</a></th>", ascendante)
    assert entete_asc, "l'en-tête doit rester un lien dans les deux sens"
    assert "▴" in entete_asc.group(0)


def test_every_reading_page_shares_one_header_and_one_navigation(tmp_path):
    """Le chrome est écrit une fois, et chaque page s'y conforme.

    Sept pages recopiant chacune leur mise en page divergeaient déjà avant
    ce travail ; le test verrouille l'inverse, parce qu'un chrome commun
    qui cesse de l'être ne se voit pas à l'œil sur cinq pages.
    """
    client = TestClient(create_app(tmp_path))
    for chemin in ("/comptages", "/carte", "/rechercher", "/methode", "/ligne?ligne=R-TER-1"):
        page = client.get(chemin)
        assert page.status_code == 200, chemin
        assert page.text.count('class="barre"') == 2, f"{chemin} n'a pas l'en-tête ni le pied communs"
        assert "<header class=\"site\">" in page.text, chemin
        assert page.text.count('href="/carte"') == 1, f"{chemin} duplique la navigation"
        assert "max-width: 32rem" in page.text, chemin


def test_the_wide_layout_is_in_the_stylesheet_not_in_a_second_page(tmp_path):
    """Le tableau de lecture et l'ouverture à 72 rem vivent dans la même
    page que les cartes.

    Deux URL pour les mêmes données, ce serait deux endroits où elles
    divergent — et la largeur double versionnerait avec les chiffres.
    """
    page = _trois_comptages(tmp_path).get("/comptages").text

    assert "min-width: 48rem" in page, "la bascule vers le grand écran est une media query"
    assert "max-width: 72rem" in page
    assert "class='tableau'" in page
    # Le tableau est la même liste que les cartes, pas une autre : les trois
    # relevés y figurent aussi.
    for nom in ("Alpha", "Bravo", "Charlie"):
        assert page.count(nom) >= 2, f"{nom} n'est que dans une des deux vues"
