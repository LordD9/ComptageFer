"""Les filtres de la liste, et la vue par paire de gares.

Chaque test porte sur un défaut qu'un défaut réel rendrait invisible
ou Trompeur. Ce sont des tests d'URL, donc ils vérifient deux choses à
la fois : ce que la page dit, et ce que l'adresse permet de retrouver.
"""

import re

from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.filtres import PAR_PAGE

PHOTO = {"precedent": None, "courant": {"trip_id": "T", "status": "SCHEDULED"}, "suivant": None}


def _poster(client, **champs):
    """Enregistre un relevé. Sans `passengers`, c'est un train signalé.

    Un train signalé n'a pas d'effectif : c'est un relevé de l'offre qui
    manque, pas une charge mesurée. Lui en donner un par défaut ferait
    passer la moitié des tests pour une vérification de la gestion du
    `None`.
    """
    corps = {
        "client_id": champs["client_id"],
        "origin_stop_id": champs.get("o", "A"),
        "destination_stop_id": champs.get("d", "B"),
        "origin_name": champs.get("on", "Lyon Part Dieu"),
        "destination_name": champs.get("dn", "Nîmes Pont du Gard"),
        "trip_id": champs.get("trip", "TRIP1"),
        # `reliability` est obligatoire à l'enregistrement : sans lui le
        # POST est refusé en 422 et le relevé n'arrive jamais en base, ce
        # qui ferait échouer tous les tests pour une raison invisible.
        "reliability": champs.get("reliability", 70),
        "snapshot": PHOTO,
    }
    if "passengers" in champs:
        corps["passengers"] = champs["passengers"]
    for cle in ("pseudo",):
        if cle in champs:
            corps[cle] = champs[cle]
    # Un train signalé a sa propre route. `kind` n'y sert à rien :
    # `POST /api/sessions` enregistre un comptage, et c'est
    # `POST /api/missing` qui enregistre une offre manquante.
    if champs.get("kind") == "missing":
        return client.post("/api/missing", json=corps)
    return client.post("/api/sessions", json=corps)


def _corps(page: str) -> str:
    """La page sans son chrome, pour Assertions qui portent sur la liste."""
    return page.split("<main>", 1)[1].split("</main>", 1)[0]


def _lignes(page: str) -> list[str]:
    corps = _corps(page)
    tableau = re.search(r"<tbody>(.*?)</tbody>", corps, re.S)
    if not tableau:
        return []
    return re.findall(r"<tr>(.*?)</tr>", tableau.group(1), re.S)


def test_the_three_filters_narrow_the_list_and_say_what_they_kept(tmp_path):
    client = TestClient(create_app(tmp_path))
    _poster(client, client_id="a", passengers=10, pseudo="alice")
    _poster(client, client_id="b", passengers=20, pseudo="bob", on="Grenoble", dn="Lyon")
    _poster(client, client_id="c", kind="missing", on="Nice", dn="Menton")
    assert len(_lignes(client.get("/comptages").text)) == 3
    assert len(_lignes(client.get("/comptages?mode=signale").text)) == 1
    assert len(_lignes(client.get("/comptages?mode=unique").text)) == 2
    assert len(_lignes(client.get("/comptages?mode=serpent").text)) == 0
    assert "alice" in client.get("/comptages?mode=unique").text


def test_a_filter_that_empties_the_list_says_so_and_offers_the_way_out(tmp_path):
    """Le verrou de la vague 2, en test.

    Une page vide sans explication se lit comme une absence de données.
    Le lecteur conclut qu'il n'y a rien à voir ici, et ne revient pas
    chercher. Les deux moitiés sont donc exigées : le constat, et le lien
    qui enlève le filtre.
    """
    client = TestClient(create_app(tmp_path))
    _poster(client, client_id="a", passengers=10)

    page = client.get("/comptages?depuis=2020-01-01&jusqu=2020-12-31").text

    assert "Aucun comptage ne correspond" in _corps(page)
    # Le filtre actif doit être nommé : « aucun résultat » sans dire sur
    # quoi on a filtré laisse le lecteur deviner entre ses quatre champs.
    assert "du 2020-01-01 au 2020-12-31" in _corps(page)
    assert "Enlever le filtre" in _corps(page)
    # Le lien de sortie doit vraiment mener à la liste entière. Les
    # guillemets sont tolérés des deux côtés : la page écrit des
    # attributs à apostrophes, et un test qui impose les guillemets
    # doubles échouerait sur une question de typographie, pas de
    # comportement.
    sortie = re.search(r"""href=["']([^"']*)["']>Enlever le filtre""", _corps(page))
    assert sortie is not None
    assert len(_lignes(client.get(sortie.group(1).replace("&#x27;", "'")).text)) == 1


def test_a_bad_filter_is_dropped_and_named_not_silently_ignored(tmp_path):
    """Un filtre illisible est écarté **et dit**.

    L'écarter sans le dire serait pire que de ne pas l'écarter : le
    lecteur qui filtre par « laisse-passer » verrait la liste entière et
    croirait que son filtre n'a rien donné.
    """
    client = TestClient(create_app(tmp_path))
    _poster(client, client_id="a", passengers=10)

    page = client.get("/comptages?depuis=bidon&mode=incroyable&ligne=ZZ9").text

    assert "n'est pas une date" in _corps(page)
    assert "n'est pas un mode" in _corps(page)
    assert "n'est pas dans le GTFS" in _corps(page)
    # Le relevé reste visible : un filtre écarté ne doit pas vider la liste.
    assert len(_lignes(page)) == 1


def test_reversed_dates_are_swapped_rather_than_returning_nothing(tmp_path):
    client = TestClient(create_app(tmp_path))
    _poster(client, client_id="a", passengers=10)

    page = client.get("/comptages?depuis=2026-12-31&jusqu=2026-01-01").text

    assert "Intervalle inversé" in _corps(page)
    assert len(_lignes(page)) == 1, "un intervalle à l'envers veut dire « entre les deux »"


def test_the_sort_links_keep_the_filters(tmp_path):
    """Un lien de tri qui perd le filtre montre la liste entière.

    Le lecteur verrait des relevés qu'il vient d'exclure, et croirait
    que son filtre n'a rien donné. C'est le genre de faute qui ne se voit
    qu'en suivant le lien.
    """
    client = TestClient(create_app(tmp_path))
    _poster(client, client_id="a", passengers=10)
    _poster(client, client_id="b", passengers=20, on="Grenoble", dn="Lyon")

    page = client.get("/comptages?mode=unique").text
    liens = re.findall(r"href='(/comptages\?[^']*)'>(?:Voyageurs|Date|Trajet)", _corps(page))

    assert liens, "le tableau doit proposer des liens de tri"
    for url in liens:
        assert "mode=unique" in url, f"le lien {url} perd le filtre"


def test_the_paired_view_is_the_same_url_with_one_more_parameter(tmp_path):
    """La vue par paire est un paramètre, pas une page.

    Une page de plus serait un endroit de plus où les mêmes données
    peuvent diverger, et le contexte de la liste s'y perdrait.
    """
    client = TestClient(create_app(tmp_path))
    _poster(client, client_id="a", passengers=100, on="Lyon", dn="Chambéry")
    _poster(client, client_id="b", passengers=200, on="Lyon", dn="Chambéry")
    _poster(client, client_id="c", passengers=50, on="Grenoble", dn="Lyon")

    page = client.get("/comptages?vue=paire").text

    assert "Lyon → Chambéry" in _corps(page)
    assert "paires de gares" in _corps(page)
    # Le lien de retour est bien sur la même route. Les guillemets sont
    # tolérés des deux côtés — voir `test_a_filter_that_empties...`.
    retour = re.search(r"""href=["'](/comptages[^"']*)["']>Voir la liste""", _corps(page))
    assert retour is not None
    assert retour.group(1).startswith("/comptages")
    assert "vue=paire" not in retour.group(1)


def test_the_paired_view_groups_and_averages(tmp_path):
    client = TestClient(create_app(tmp_path))
    for i, effectif in enumerate([100, 200, 300]):
        _poster(client, client_id=f"a{i}", passengers=effectif, on="Lyon", dn="Chambéry")
    _poster(client, client_id="b0", passengers=10, on="Nice", dn="Menton")

    lignes = _lignes(client.get("/comptages?vue=paire").text)

    assert len(lignes) == 2
    # La moyenne est la seule des trois statistiques qui demande un
    # calcul : 100, 200, 300 font 200, et un minimum/maximum justes ne
    # prouveraient pas que la moyenne existe.
    assert ">200<" in lignes[0]
    assert "100" in lignes[0] and "300" in lignes[0]
    # Lyon → Chambéry d'abord : c'est le corridor le mieux documenté.
    assert "Lyon → Chambéry" in lignes[0]


def test_the_paired_view_default_sort_is_by_number_of_counts(tmp_path):
    """Nombre de relevés décroissant, pas effectif moyen.

    Un corridor en tête avec 40 relevés est mieux documenté qu'un
    corridor en tête avec 2. Le mettre devant répondrait à une autre
    question — « le corridor le plus chargé » — qui mélange ce que la
    base sait et ce que la circulation fait.
    """
    client = TestClient(create_app(tmp_path))
    # Deux relevés, petits : le corridor le « moins chargé ».
    for i in (0, 1):
        _poster(client, client_id=f"petit{i}", passengers=1, on="Nice", dn="Menton")
    # Un relevé, énorme : le corridor le « plus chargé ».
    _poster(client, client_id="gros", passengers=900, on="Lyon", dn="Chambéry")

    lignes = _lignes(client.get("/comptages?vue=paire").text)

    assert "Nice → Menton" in lignes[0]
    assert "Lyon → Chambéry" in lignes[1]


def test_a_corridor_of_missing_trains_is_not_a_corridor_of_empty_trains(tmp_path):
    """« Aucune mesure » et « train vide » sont deux affirmations opposées.

    Compter un train signalé comme effectif 0 ferait passer un
    corridor qu'on n'a pas mesuré pour un corridor où personne ne
    voyage.
    """
    client = TestClient(create_app(tmp_path))
    for i in range(3):
        _poster(client, client_id=f"m{i}", kind="missing", on="Lyon", dn="Chambéry")

    page = client.get("/comptages?vue=paire").text
    lignes = _lignes(page)

    assert "3 sans effectif" in lignes[0]
    # Pas de 0 dans la colonne moyenne : 0 serait un relevé de charge.
    assert "<span class=\"vide\">—</span>" in lignes[0]
    assert ">0<" not in lignes[0]


def test_the_paired_view_respects_the_filters(tmp_path):
    """Filtrer puis regarder les corridors : c'est la question à poser.

    La vue par paire regroupe ce qui a été filtré, pas la base entière.
    Un corridor dont tous les relevés sont des trains signalés disparaît
    sous `?mode=unique` : il n'a plus rien à faire dans la vue par paire,
    puisqu'elle ne montre que des statistiques de charge.
    """
    client = TestClient(create_app(tmp_path))
    _poster(client, client_id="a", passengers=100, on="Lyon", dn="Chambéry")
    _poster(client, client_id="b", passengers=200, on="Lyon", dn="Chambéry")
    _poster(client, client_id="c", passengers=50, on="Nice", dn="Menton")
    _poster(client, client_id="d", kind="missing", on="Bordeaux", dn="Toulouse")

    lignes = _lignes(client.get("/comptages?vue=paire&mode=unique").text)

    assert "Lyon → Chambéry" in lignes[0]
    assert "Nice → Menton" in lignes[1]
    assert "Bordeaux → Toulouse" not in "".join(lignes), "le corridor écarté ne doit plus être là"


def test_the_list_is_cut_into_pages_and_says_how_many_match(tmp_path):
    """La page doit dire qu'elle est une tranche.

    Sans le total, 200 relevés affichés se prennent pour les 200 seuls
    existants. C'est exactement la faute que la vague 2 corrige.
    """
    client = TestClient(create_app(tmp_path))
    for i in range(PAR_PAGE + 5):
        _poster(client, client_id=f"c{i}", passengers=i)

    page = client.get("/comptages").text

    assert f"{PAR_PAGE} sur {PAR_PAGE + 5}" in _corps(page)
    assert "Page 1 sur 2" in _corps(page)
    assert len(_lignes(page)) == PAR_PAGE
    assert len(_lignes(client.get("/comptages?page=2").text)) == 5


def test_a_page_beyond_the_last_one_shows_the_last_one(tmp_path):
    """Un signet pris avant l'ajout de relevés ne doit pas devenir muet.

    L'URL dit « page 99 » ; la page doit dire « page 2 sur 2 » et
    afficher la dernière tranche, pas une page vide qui se lit comme une
    absence de données.
    """
    client = TestClient(create_app(tmp_path))
    for i in range(PAR_PAGE + 5):
        _poster(client, client_id=f"c{i}", passengers=i)

    page = client.get("/comptages?page=99").text

    assert "Page 2 sur 2" in _corps(page)
    assert len(_lignes(page)) == 5


def test_under_the_threshold_there_is_no_pagination(tmp_path):
    """Un sélecteur de page avec un seul choix se lit comme cassé.

    Il dirait « il y a un choix à faire » alors qu'il n'y en a pas.
    """
    client = TestClient(create_app(tmp_path))
    _poster(client, client_id="a", passengers=10)

    page = client.get("/comptages").text

    assert "pagination" not in _corps(page)
    assert "Page 1 sur" not in _corps(page)


def test_the_pagination_keeps_the_filters_and_the_view(tmp_path):
    client = TestClient(create_app(tmp_path))
    for i in range(PAR_PAGE + 5):
        _poster(client, client_id=f"c{i}", passengers=i, on="Lyon", dn="Chambéry")

    page = client.get("/comptages?mode=unique").text
    # Guillemets tolérés des deux côtés — voir `test_a_filter_that_empties...`.
    suivant = re.search(r"""href=["']([^"']*)["']\s+rel=["']next["']""", page)

    assert suivant is not None
    assert "mode=unique" in suivant.group(1)
    assert "page=2" in suivant.group(1)


def test_the_filter_form_keeps_the_paired_view(tmp_path):
    """Filtrer ne doit pas faire basculer la vue sans le dire.

    Le champ caché `vue` est là pour ça : sans lui, filtrer depuis la vue
    par paire ramène à la liste, et le lecteur ne comprend pas pourquoi
    l'écran a changé de forme.
    """
    client = TestClient(create_app(tmp_path))
    _poster(client, client_id="a", passengers=10)

    page = client.get("/comptages?vue=paire").text

    assert "<input type='hidden' name='vue' value='paire'>" in page


def test_the_paired_view_is_cut_into_pages_too(tmp_path):
    """La vue par paire se pagine, et la mesure l'impose.

    J'avais écrit l'inverse dans un commentaire et dans la doc : « il y
    a au plus autant de paires que de relevés, donc la vue est plus
    légère ». La mesure l'a refuté — sur 6 000 relevés, la vue par paire
    rendait 0,60 Mo contre 0,09 Mo pour une page de liste. Le nombre de
    paires croît avec la base au même rythme que le nombre de relevés, et
    chaque ligne y porte deux colonnes de plus.

    Ce test existe pour que cette pagination ne soit pas retirée un jour
    comme une redondance.
    """
    client = TestClient(create_app(tmp_path))
    # Chaque relevé a sa propre paire : c'est le pire cas, celui où
    # « autant de paires que de relevés » se réalise.
    for i in range(PAR_PAGE + 5):
        _poster(
            client,
            client_id=f"u{i}",
            passengers=i,
            on=f"Origine {i}",
            dn=f"Destination {i}",
        )

    page = client.get("/comptages?vue=paire").text

    assert f"{PAR_PAGE} sur {PAR_PAGE + 5}" in _corps(page)
    assert "Page 1 sur 2" in _corps(page)
    assert len(_lignes(page)) == PAR_PAGE
    assert len(_lignes(client.get("/comptages?vue=paire&page=2").text)) == 5

    # Le lien de page doit rester dans la vue par paire : sans `vue=paire`
    # dans l'URL, le lecteur clique sur « page suivante » et retombe sur
    # la liste, qui est une autre page pour le même sujet.
    suivant = re.search(r"""href=["']([^"']*)["']\s+rel=["']next["']""", page)
    assert suivant is not None
    assert "vue=paire" in suivant.group(1), "le lien de page perd la vue"


def test_the_line_filter_uses_the_trip_and_not_the_station_pair(tmp_path):
    """Le filtre ligne passe par le trip, pas par le corridor.

    Deux lignes se partagent souvent le même corridor : un filtre par
    paire de gares afficherait les comptages de l'autre ligne. On ne
    trie pas par ce qui se trouve être juste.
    """
    client = TestClient(create_app(tmp_path))
    _poster(client, client_id="a", passengers=10, trip="TRIP-A")
    _poster(client, client_id="b", passengers=20, trip="TRIP-B")

    # Sans timetable, aucune ligne n'est résoluble : le filtre doit le
    # dire plutôt que de rendre la liste entière en silence.
    page = client.get("/comptages?ligne=C13").text
    assert "n'est pas dans le GTFS" in _corps(page)
    assert len(_lignes(page)) == 2, "un filtre non résolu ne doit pas vider la liste"


def test_a_broken_tri_or_page_parameter_does_not_break_the_page(tmp_path):
    """Une URL reçoit des fautes de frappe.

    Une page d'erreur pour `tri=voyageurs` mal orthographié est une page
    perdue, et une 500 pour `page=beaucoup` l'est aussi.
    """
    client = TestClient(create_app(tmp_path))
    _poster(client, client_id="a", passengers=10)

    for url in ("/comptages?tri=bidon&sens=zzz", "/comptages?vue=inconnu", "/comptages?page=beaucoup"):
        reponse = client.get(url)
        assert reponse.status_code == 200, url
        assert "Comptages" in reponse.text
