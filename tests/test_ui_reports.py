"""Les cinq défauts d'interface signalés le 1er octobre 2026.

Chacun de ces tests porte sur un constat fait **à l'écran**, par quelqu'un
qui utilisait le site. Aucun n'aurait pu être trouvé par la relecture du
code : les cinq pages répondaient 200, aucun lien n'était mort, et la suite
était verte. C'est la démonstration que « la suite passe » ne dit rien de
l'interface — voir `docs/regles.md` § 3.

Chaque test dit dans son docstring ce qui n'allait pas, pour qu'on ne puisse
pas défaire la correction sans lire pourquoi.

Le premier test est le seul qui vérifie une **mise en page**, donc il est le
seul qui ait besoin d'un vrai navigateur : les autres vérifient le HTML, et
le HTML suffit parce que le défaut était dans le HTML.

- Les filtres : le libellé d'un champ n'était plus au-dessus de son champ.
- Les cartes : aucun lien vers le relevé, donc rien à cliquer.
- Les routes `/rechercher` et `/ligne` ont été retirées ; les filtres restent dans `/comptages`.
- `/` : le pseudo du compte connecté n'était pas repris dans le formulaire.
- `/classement` : une pile de cartes illisibles les unes par rapport aux
  autres.
"""

import re

from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.offer import import_stop_names

PHOTO = {"precedent": None, "courant": {"trip_id": "T", "status": "SCHEDULED"}, "suivant": None}

# Une gare, son aire et son point : c'est la forme que prend le GTFS national,
# et c'est elle qui fait que `/gare` doit retrouver les comptages faits depuis
# un `StopPoint` alors que le lien vient d'un `StopArea`.
# L'ordre des colonnes est celui de `stops.txt`, lu **par nom d'en-tête** :
# `import_stop_names` n'a pas de position, donc une fixture écrite dans un
# autre ordre met un identifiant dans `stop_lat` et `float()` échoue.
STOPS = [
    ("StopArea:Annecy", "Annecy", "45.9", "6.1", "1", ""),
    ("StopPoint:AnnecyA", "Annecy", "45.9", "6.1", "0", "StopArea:Annecy"),
    ("StopArea:Vienne", "Vienne", "45.48", "4.87", "1", ""),
    ("StopPoint:VienneA", "Vienne", "45.48", "4.87", "0", "StopArea:Vienne"),
]


def _stops(tmp_path):
    """Le catalogue des gares, **dans le répertoire que `create_app` lit**.

    `create_app(tmp_path)` prend `tmp_path` comme répertoire de données :
    le catalogue doit y être écrit, pas dans un `data/` à côté. Les deux
    chemins sont plausibles à l'œil, et le second donne une base vide — donc
    une recherche qui ne trouve rien, et une page gare qui dit « pas dans
    le catalogue » pour une gare qui y est.
    """
    import csv

    chemin = tmp_path / "stops.txt"
    with chemin.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"]
        )
        writer.writerows(STOPS)
    import_stop_names(tmp_path / "stops.db", chemin)


def _poster(client, **champs):
    corps = {
        "client_id": champs["client_id"],
        "origin_stop_id": champs.get("o", "StopPoint:AnnecyA"),
        "destination_stop_id": champs.get("d", "StopPoint:VienneA"),
        "origin_name": champs.get("on", "Annecy"),
        "destination_name": champs.get("dn", "Vienne"),
        "trip_id": champs.get("trip", "1_F:TER:1234"),
        "passengers": champs.get("passengers", 120),
        "reliability": champs.get("reliability", 70),
        "snapshot": PHOTO,
    }
    for cle in ("pseudo", "comment", "composition", "perimetre", "materiel", "standing"):
        if cle in champs:
            corps[cle] = champs[cle]
    if champs.get("kind") == "missing":
        return client.post("/api/missing", json=corps)
    # Le `kind` va dans le corps : `POST /api/sessions` en déduit le genre,
    # et un serpent écrit par cette route sans `kind` deviendrait un comptage
    # unique. C'est ce que vérifie `test_le_releve_cherche_par_la_cle_complète`.
    if champs.get("kind") == "serpent":
        corps["kind"] = "serpent"
    return client.post("/api/sessions", json=corps)


def _corps(page: str) -> str:
    return page.split("<main>", 1)[1].split("</main>", 1)[0]


# --- 1. Les filtres : un libellé au-dessus de son champ -------------------------


def test_chaque_champ_de_filtre_est_dans_sa_propre_boite(tmp_path):
    """Les libellés et leurs champs n'étaient plus alignés, sur grand écran.

    Le formulaire est une grille à cinq colonnes, et ses enfants étaient les
    balises : `label`, `input`, `label`, `input`, `label`, `select`,
    `label`, `input`. La grille les répartissait **alternativement** — le
    libellé dans une colonne, son champ dans la suivante, celui d'après dans
    la colonne suivante encore. Résultat, à partir de 48 rem : « Mode »
    surplombait la liste déroulante `ligne`, « Ligne (route_id) » surplombait
    le champ caché, et les deux champs de date n'étaient plus sous leurs
    libellés. Sur téléphone la grille n'a qu'une colonne, donc le défaut ne
    se voyait pas — c'est pour ça qu'il a survécu.

    Le test vérifie la **forme** qu'a rendues le CSS : chaque `label` et le
    `input`/`select` qu'il désigne doivent être dans le même parent. C'est la
    propriété qui tient quel que soit le nombre de colonnes, donc elle ne
    peut pas revenir sans que ce test échoue.
    """
    page = TestClient(create_app(tmp_path)).get("/comptages?mode=serpent&ligne=C13").text
    formulaire = re.search(r"<form class='filtres'.*?</form>", page, re.DOTALL)
    assert formulaire is not None, "le formulaire de filtre a disparu"

    html = formulaire.group(0)
    # Chaque `for` du formulaire doit avoir son champ dans le même parent.
    for champ in re.finditer(r"for='([^']+)'", html):
        identifiant = champ.group(1)
        boite = re.search(
            rf"<p class='champ'>(?:(?!</p>).)*for='{re.escape(identifiant)}'.*?</p>",
            html,
            re.DOTALL,
        )
        assert boite is not None, (
            f"le libellé « {identifiant} » n'est pas dans une boîte avec son champ : "
            "la grille le pose dans une colonne et son champ dans une autre"
        )
        assert f"id='{identifiant}'" in boite.group(0), (
            f"le libellé « {identifiant} » n'a pas son champ sous lui"
        )

    # Et la grille en annonce quatre colonnes, pas cinq : le cinquième
    # enfant est le `input hidden`, qui ne prend pas de place, donc une
    # cinquième colonne vole de la largeur aux quatre champs.
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in page, (
        "la grille des filtres doit annoncer quatre colonnes de largeur égale"
    )
    assert "repeat(4, 1fr) auto" not in page, (
        "l'ancienne grille à cinq colonnes est revenue : les libellés et leurs "
        "champs se remetttront à alterner"
    )


# --- 2. Un comptage se clique et donne son détail -------------------------------


def test_chaque_comptage_de_la_liste_est_cliquable(tmp_path):
    """Aucun lien vers un relevé depuis la liste : rien n'était cliquable.

    `/comptages` affichait un résumé — OD, effectif, auteur, date — et le
    reste du relevé (rame, périmètre, indicateurs, commentaire, arrêts du
    serpent) n'était lisible qu'en téléchargeant le CSV. Sur un téléphone,
    dans un train, ce n'est pas une option : c'est la seule façon de vérifier
    qu'un effectif se lit à l'échelle de la rame et pas à celle d'une
    voiture.

    Le test vérifie les deux lectures — la carte et la ligne du tableau —
    parce qu'elles ne sont pas le même rendu : sur grand écran c'est le
    tableau qui s'affiche, et un lien présent uniquement dans les cartes
    serait invisible exactement là où on lit.
    """
    client = TestClient(create_app(tmp_path))
    _poster(client, client_id="jeton-1")
    page = client.get("/comptages").text

    assert "href='/releve?client_id=jeton-1&amp;kind=count'" in page, (
        "la carte d'un comptage ne mène nulle part : il faut pouvoir l'ouvrir"
    )
    assert "Détail du comptage" in page, (
        "un lien sur le titre seul ne se voit pas sur un téléphone ; "
        "il faut un lien nommé"
    )


def test_la_page_d_un_releve_donne_tout_ce_que_le_csv_donne(tmp_path):
    """La fiche d'un relevé, et pas un résumé de plus.

    Le test compte les faits affichés et vérifie que le commentaire, la
    composition et les indicateurs y sont : ce sont précisément les champs
    qui n'étaient lisibles que dans le fichier. Une fiche qui les perdrait
    serait la carte de la liste avec un autre titre.
    """
    client = TestClient(create_app(tmp_path))
    _poster(
        client,
        client_id="jeton-1",
        passengers=180,
        comment="Voiture 3 pleine, Agent fantôme.",
        composition="UM3",
        perimetre="um",
        materiel="Z 20500",
        standing=40,
    )

    fiche = client.get("/releve?client_id=jeton-1&kind=count")

    assert fiche.status_code == 200
    corps = _corps(fiche.text)
    for attendu in (
        "180 voyageurs",
        "Voiture 3 pleine, Agent fantôme.",
        "UM3",
        "Z 20500",
        "40",
        "Annecy",
        "Vienne",
    ):
        assert attendu in corps, f"la fiche ne dit pas : {attendu!r}"
    # La composition seule ne suffit pas : c'est le couple
    # composition + périmètre qui dit si le chiffre se lit par rame.
    assert "rame entière" in corps or "toute la rame" in corps, (
        "la fiche doit dire ce que l'effectif compte : sans le périmètre, "
        "180 voyageurs dans une voiture et 180 dans les trois sont le même chiffre"
    )


def test_un_releve_qui_n_existe_pas_le_dis_plutot_que_404(tmp_path):
    """L'URL d'un relevé est partageable, donc elle peut arriver après coup.

    Un lien copié dans un ticket, une page d'erreur 404 nue : elle ne dit pas
    d'où elle vient, et elle ressemble à un site cassé.
    """
    page = TestClient(create_app(tmp_path)).get("/releve?client_id=jamais-vu&kind=count")

    assert page.status_code == 200
    assert "n'existe pas" in _corps(page.text)


def test_le_releve_cherche_par_la_cle_complète_pas_par_le_client_id(tmp_path):
    """Un serpent et un comptage unique du même navigateur ont deux fiches.

    La clé primaire est `(client_id, kind)`. Une lecture par `client_id` seul
    rendrait le premier genre trouvé, donc `/releve` sans `kind` afficherait
    un serpent sous l'identité d'un comptage unique — deux nombres différents
    pour le même lien.
    """
    client = TestClient(create_app(tmp_path))
    _poster(client, client_id="partage", passengers=10)
    client.post(
        "/api/sessions",
        json={
            "client_id": "partage",
            "kind": "serpent",
            "origin_stop_id": "StopPoint:AnnecyA",
            "destination_stop_id": "StopPoint:VienneA",
            "origin_name": "Annecy",
            "destination_name": "Vienne",
            "trip_id": "1_F:TER:1234",
            "passengers": 300,
            "reliability": 70,
            "snapshot": PHOTO,
            "legs": [
                {"stop_id": "StopPoint:AnnecyA", "stop_name": "Annecy", "onboard": 300},
                {"stop_id": "StopPoint:VienneA", "stop_name": "Vienne", "boarded": 40, "alighted": 20},
            ],
        },
    )

    unique = _corps(client.get("/releve?client_id=partage&kind=count").text)
    serpent = _corps(client.get("/releve?client_id=partage&kind=serpent").text)

    assert "10 voyageurs" in unique, "la fiche du comptage unique doit dire 10"
    assert "300 voyageurs" in serpent, "la fiche du serpent doit dire 300"
    assert "montées" in serpent, "le serpent doit montrer ses arrêts"


def test_le_client_id_ne_peut_pas_s_echapper_de_la_page_d_un_releve(tmp_path):
    """Un `client_id` est choisi par le navigateur, donc c'est une donnée hostile.

    Il part dans une URL — donc dans un attribut `href` de la page publique
    — et il revient dans l'URL de la fiche. Un `client_id` contenant une
    double quote fermerait l'attribut et injecterait du HTML sur `/comptages`,
    la page la plus consultée. Règle 1 de `docs/regles.md`, sans exception
    pour « un champ qui n'a pas de nom lisible ».

    Le test vérifie l'attribut, pas la page entière : la fiche rend un lien
    « retour », donc la chaîne cherchée apparaît légitimement, encodée. Ce
    qui ne doit pas exister, c'est un `<script>` réellement inséré.
    """
    client = TestClient(create_app(tmp_path))
    _poster(client, client_id='"><script>alert(1)</script>')

    liste = client.get("/comptages").text

    assert "<script>alert(1)</script>" not in liste, (
        "le client_id s'est échappé de l'attribut href : la liste est une page "
        "publique et le jeton vient du navigateur"
    )
    assert "%3Cscript%3E" in liste, (
        "le client_id doit être encodé dans l'URL : c'est ce qui empêche la "
        "sortie de l'attribut"
    )


def test_la_page_d_une_gare_donne_ses_comptages(tmp_path):
    """Le clic sur une gare mène à ses comptages, pas à une page vide.

    Le lien est construit sur le `stop_id`, donc c'est aussi un test de
    l'encodage : un `:` non encodé dans une URL de query string passe chez
    un client et casse chez un autre.
    """
    _stops(tmp_path)
    client = TestClient(create_app(tmp_path))
    _poster(client, client_id="jeton-1", passengers=88)

    page = client.get("/gare", params={"stop": "StopArea:Annecy"})

    assert page.status_code == 200
    corps = _corps(page.text)
    assert "Annecy" in corps
    assert "88 voyageurs" in corps, "la gare doit montrer les comptages qui la touchent"


def test_la_page_d_une_gare_retrouve_un_comptage_fait_depuis_un_quai(tmp_path):
    """Le même quai, deux identifiants : la gare doit les réunir.

    Le lien de la recherche vient d'un `StopArea`, le comptage a été saisi
    depuis un `StopPoint`. Une page qui ne chercherait que l'identifiant
    exact afficherait « aucun comptage » pour une gare qui en a un — et le
    lecteur conclurait que le site a perdu la donnée.
    """
    _stops(tmp_path)
    client = TestClient(create_app(tmp_path))
    _poster(
        client,
        client_id="jeton-1",
        o="StopPoint:AnnecyA",
        d="StopPoint:VienneA",
        passengers=88,
    )

    par_aire = client.get("/gare", params={"stop": "StopArea:Annecy"})
    par_quai = client.get("/gare", params={"stop": "StopPoint:AnnecyA"})

    assert "88 voyageurs" in _corps(par_aire.text), (
        "l'aire de gare doit retrouver les comptages faits depuis un de ses points"
    )
    assert "88 voyageurs" in _corps(par_quai.text), (
        "un point de la gare doit ouvrir la même page que son aire"
    )


def test_un_serpent_est_un_comptage_de_la_gare_qu_il_traverse(tmp_path):
    """Un relevé qui passe par la gare la concerne, même sans y commencer.

    Exclure les arrêts intermédiaires ferait dire à la page gare qu'elle
    n'est pas couverte alors qu'un relevé l'a parcourue : c'est exactement
    l'écart que le reste du site s'efforce de ne pas laisser.
    """
    _stops(tmp_path)
    client = TestClient(create_app(tmp_path))
    client.post(
        "/api/sessions",
        json={
            "client_id": "serpent-1",
            "kind": "serpent",
            "origin_stop_id": "AUTRE",
            "destination_stop_id": "ENCORE_AUTRE",
            "origin_name": "Bordeaux",
            "destination_name": "Toulouse",
            "trip_id": "1_F:TER:9",
            "passengers": 300,
            "reliability": 70,
            "snapshot": PHOTO,
            "legs": [
                {"stop_id": "StopPoint:AnnecyA", "stop_name": "Annecy", "onboard": 300},
                {"stop_id": "StopPoint:VienneA", "stop_name": "Vienne", "boarded": 10, "alighted": 5},
            ],
        },
    )

    page = client.get("/gare", params={"stop": "StopArea:Annecy"})

    assert "300 voyageurs" in _corps(page.text), (
        "un serpent qui traverse la gare est un relevé de cette gare"
    )


def test_une_gare_hors_catalogue_le_dis_plutot_que_d_inventer_un_nom(tmp_path):
    """Un `stop_id` inconnu ne donne pas une page au nom vide.

    La page prend son nom dans le catalogue des gares, jamais dans l'URL : un
    nom passé en paramètre serait une donnée client affichée sans être
    échappée, et pourrait nommer une gare qui n'existe pas.
    """
    _stops(tmp_path)
    client = TestClient(create_app(tmp_path))

    page = client.get("/gare", params={"stop": "StopArea:Inconnue"})

    assert page.status_code == 200
    assert "pas dans le catalogue" in _corps(page.text)



def test_la_page_gare_sans_identifiant_demande_une_gare(tmp_path):
    """Sans identifiant de gare, la page ne doit pas inventer de résultat."""
    _stops(tmp_path)
    client = TestClient(create_app(tmp_path))

    page = client.get("/gare")

    assert page.status_code == 200
    assert "Quelle gare" in _corps(page.text)


# --- 4. Le pseudo du compte se remplit tout seul -------------------------------


def test_le_pseudo_du_compte_connecte_est_dans_le_formulaire(tmp_path):
    """Une personne connectée retapait son pseudo à chaque comptage.

    Le champ existe, il est dans un `<details>` replié, et il est vide : on
    ouvrait le panneau, on écrivait son pseudo, on le refermait, pour faire
    ce que la session sait déjà. C'est le genre d'oubli qui fait qu'on
    arrête de signer ses relevés — donc que le classement, qui ne compte que
    les relevés rattachés à un compte, s'arrête de remplir.

    Le test passe par la vraie création de compte, donc par le cookie : il
    vérifie le chemin, pas une fonction qui prend un pseudo en paramètre.
    """
    client = TestClient(create_app(tmp_path))
    cree = client.post("/compte/creer", data={"pseudo": "romain"}, follow_redirects=True)
    assert cree.status_code == 200

    page = client.get("/")

    assert 'value="romain"' in page.text, (
        "le champ pseudo du comptage unique doit être rempli par la session"
    )


def test_sans_compte_le_champ_pseudo_reste_vide(tmp_path):
    """Le cas le plus fréquent ne doit rien coûter.

    Sans session, la page est rendue telle quelle et le champ est vide :
    pré-remplir « pseudo du dernier comptage » mettrait le nom de quelqu'un
    d'autre dans le relevé du suivant.
    """
    page = TestClient(create_app(tmp_path)).get("/").text

    assert 'id="pseudo"' in page
    assert 'id="pseudo" type="text" maxlength="40" autocomplete="nickname" value=' not in page


def test_le_pseudo_du_compte_ne_peut_pas_casser_la_page_de_saisie(tmp_path):
    """Un pseudo est une donnée de la base, et il part dans un attribut HTML.

    Règle 1 de `docs/regles.md` : une double quote dans le pseudo fermerait
    l'attribut `value` et injecterait du HTML sur **la page la plus visitée
    du site**, celle de la saisie. Le pseudo est échappé avant d'être mis
    dans la page.
    """
    client = TestClient(create_app(tmp_path))
    client.post("/compte/creer", data={"pseudo": '"><script>alert(1)</script>'})

    page = client.get("/").text

    assert "<script>alert(1)</script>" not in page.split("<script", 1)[1].split(">", 1)[1][:200]
    assert "&quot;" in page or "&#x27;" in page or "&lt;" in page, (
        "le pseudo doit être échappé avant d'entrer dans l'attribut"
    )


def test_un_compte_qui_ferme_sa_session_ne_laisse_plus_de_pseudo(tmp_path):
    """Se déconnecter vide aussi le champ.

    Le cookie est retiré par la route ; si le pseudo restait pré-rempli, la
    personne compterait ensuite sous le nom du compte qu'elle vient de
    fermer — et, sur un téléphone partagé, sous le nom de quelqu'un d'autre.
    """
    client = TestClient(create_app(tmp_path))
    client.post("/compte/creer", data={"pseudo": "romain"})
    client.post("/compte/deconnecter")

    page = client.get("/").text

    assert 'value="romain"' not in page


# --- 5. Le classement se compare -----------------------------------------------


def test_le_classement_est_un_tableau_et_non_une_pile_de_cartes(tmp_path):
    """Le classement était illisible, et c'était une question de forme.

    Une pile de cartes : chaque personne dans sa boîte, avec son nom, son
    score et sa phrase. Deux comptes côte à côte ne se comparent pas — il faut
    aligner les nombres verticalement, ce qu'aucune carte ne permet. Et
    comme le nom était un `<h2>`, la page lisait « un compte, un autre
    compte » sans jamais dire lequel est premier : l'ordre de tri existait,
    il n'était pas écrit.

    Le test vérifie les trois propriétés qui rendent un tableau lisible :
    une colonne par grandeur, un rang affiché, et un repère visuel de
    comparaison.
    """
    database = tmp_path / "app.db"
    client = TestClient(create_app(tmp_path))
    from comptagefer import compte as compte_module

    for nom, nombre in (("romain", 3), ("camille", 1)):
        identifiant, _secret = compte_module.creer_compte(tmp_path / "app.db", nom)
        for i in range(nombre):
            client.post(
                "/api/sessions",
                json={
                    "client_id": f"{nom}-{i}",
                    "origin_stop_id": f"A{i}",
                    "destination_stop_id": "B",
                    "origin_name": f"Gare {i}",
                    "destination_name": "Terminus",
                    "trip_id": "T",
                    "passengers": 100,
                    "reliability": 70,
                    "snapshot": PHOTO,
                },
            )
            with __import__("sqlite3").connect(database) as connection:
                connection.execute(
                    "UPDATE saisie SET compte_id = ? WHERE client_id = ?",
                    (identifiant, f"{nom}-{i}"),
                )

    page = client.get("/classement").text
    corps = _corps(page)

    assert "<table class='tableau palmares'>" in corps, (
        "un classement se compare en tableau, pas en pile de cartes"
    )
    assert "<article class=\"carte\">" not in corps, "l'ancienne pile de cartes est encore là"
    for colonne in ("Rang", "Points", "Relevés", "Paires de gares"):
        assert colonne in corps, f"la colonne « {colonne} » manque : sans elle, rien à comparer"
    assert "class='remplissage'" in corps, (
        "il faut une barre : deux scores alignés ne donnent pas l'écart d'un coup d'œil"
    )
    # Le rang est écrit, pas seulement déduit de l'ordre des lignes.
    rangs = re.findall(r"<td class='nombre rang'>(\d+)</td>", corps)
    assert rangs == ["1", "2"], f"les rangs doivent être visibles et croissants : {rangs}"


def test_le_classement_garde_le_dominateur_de_son_score(tmp_path):
    """Un score sans « sur N relevés » se lit comme un nombre de voyageurs.

    C'est une règle du plan (§9), et elle a failli partir avec les cartes :
    le tableau affichait les points dans une colonne et les relevés dans une
    autre, ce qui informe sans rattacher le nombre à ce qu'il mesure. La
    phrase « sur N relevés » est donc dans la cellule du score.
    """
    client = TestClient(create_app(tmp_path))
    from comptagefer import compte as compte_module

    identifiant, _secret = compte_module.creer_compte(tmp_path / "app.db", "romain")
    client.post(
        "/api/sessions",
        json={
            "client_id": "jeton-1",
            "origin_stop_id": "A",
            "destination_stop_id": "B",
            "origin_name": "Lyon",
            "destination_name": "Vienne",
            "trip_id": "T",
            "passengers": 100,
            "reliability": 70,
            "snapshot": PHOTO,
        },
    )
    import sqlite3

    with sqlite3.connect(tmp_path / "app.db") as connection:
        connection.execute("UPDATE saisie SET compte_id = ? WHERE client_id = ?", (identifiant, "jeton-1"))

    corps = _corps(client.get("/classement").text)

    assert re.search(r"sur 1 relevé", corps), (
        "le score a un dénominateur : « 5 points sur 1 relevé », jamais « 5 points » seul"
    )


def test_les_comptes_a_egalite_partagent_leur_rang(tmp_path):
    """Deux comptes au même score sont ex aequo, pas 1 et 2.

    Écrire 1, 2, 3 pour trois comptes à 12 points inventerait une
    hiérarchie que le score ne dit pas — c'est la même faute que le statut
    qui mentait dans le plan.
    """
    client = TestClient(create_app(tmp_path))
    import sqlite3

    from comptagefer import compte as compte_module

    for nom in ("romain", "camille"):
        identifiant, _secret = compte_module.creer_compte(tmp_path / "app.db", nom)
        client.post(
            "/api/sessions",
            json={
                "client_id": f"{nom}-1",
                "origin_stop_id": f"A-{nom}",
                "destination_stop_id": "B",
                "origin_name": f"Gare {nom}",
                "destination_name": "Terminus",
                "trip_id": "T",
                "passengers": 100,
                "reliability": 70,
                "snapshot": PHOTO,
            },
        )
        with sqlite3.connect(tmp_path / "app.db") as connection:
            connection.execute(
                "UPDATE saisie SET compte_id = ? WHERE client_id = ?",
                (identifiant, f"{nom}-1"),
            )

    corps = _corps(client.get("/classement").text)
    rangs = re.findall(r"<td class='nombre rang'>(\d+)</td>", corps)

    assert rangs == ["1", "1"], (
        f"deux comptes au même score partagent le rang 1, pas 1 et 2 : {rangs}"
    )


def test_le_classement_ne_perd_pas_sa_regle_sur_un_ecran_etroit(tmp_path):
    """Le tableau du classement reste visible sur téléphone.

    `.tableau` est retiré du rendu sous 48 rem, ce qui est la règle de la
    liste des relevés : là, les cartes font la lecture. Ici il n'y a pas de
    lecture de repli, donc la retraction laisserait une page muette sur le
    seul écran qui sert à lire un classement dans un train.
    """
    client = TestClient(create_app(tmp_path))
    from comptagefer import compte as compte_module

    identifiant, _secret = compte_module.creer_compte(tmp_path / "app.db", "romain")
    client.post(
        "/api/sessions",
        json={
            "client_id": "jeton-1",
            "origin_stop_id": "A",
            "destination_stop_id": "B",
            "origin_name": "Lyon",
            "destination_name": "Vienne",
            "trip_id": "T",
            "passengers": 100,
            "reliability": 70,
            "snapshot": PHOTO,
        },
    )
    import sqlite3

    with sqlite3.connect(tmp_path / "app.db") as connection:
        connection.execute("UPDATE saisie SET compte_id = ? WHERE client_id = ?", (identifiant, "jeton-1"))

    page = client.get("/classement").text

    assert ".tableau.palmares { display: table;" in page, (
        "le tableau du classement doit rester visible sur téléphone, "
        "sinon la page y est vide"
    )
    assert "Ce que le score récompense" in page, (
        "la règle du score reste sur la page : un classement sans règle est "
        "une page de Vanity"
    )
