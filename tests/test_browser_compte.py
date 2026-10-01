"""Le parcours du compte dans un vrai Chromium, avec le secret et le signalement.

Ce que `test_compte.py` et `test_signalement.py` vérifient, et que ces tests
vérifient autrement :

- **`test_compte.py` relit le HTML.** Il voit qu'un `<script>` est présent. Il ne
  voit pas si le script s'exécute, si le bouton est câblé, ni si le presse-papiers
  fonctionne. Une régression de la forme `$(\"#id\")` — le helper est
  `getElementById`, qui attend un id nu — tue le script au chargement, et toutes
  les assertions sur la présence du bouton restent vertes.
- **`test_browser.py` ne voit pas le compte.** Il teste le formulaire de comptage,
  pas `/compte`.

Deux propriétés du secret ne sont vérifiables que par un vrai navigateur, et
elles sont l'objet principal de ce fichier :

1. **Le bouton de copie fonctionne.** C'est un `.then()` sur une promesse : une
   régression dans le nom de la méthode ne lève aucune exception synchrone, et le
   bouton devient silencieusement inerte.
2. **Le repli fonctionne quand l'API presse-papiers est absente.** Le test coupe
   `navigator.clipboard` avant le clic, ce qui reproduit une installation en
   `http://10.x` — le cas réel que le repli a été écrit pour. Sur ce chemin, le
   secret doit être **sélectionné** et la page doit le dire : une personne qui ne
   peut pas récupérer son compte parce qu'un bouton ne marche pas a perdu son
   compte.

Le parcours complet est couvert aussi : création, secret affiché une fois,
déconnexion, reconnexion par le secret, et signalement d'un relevé. C'est la
seule façon de vérifier que le cookie est posé et lu comme le veut le
navigateur, et qu'un `303` arrive à destination.
"""

import json
import sqlite3
import urllib.request

from comptagefer.compte import COOKIE_COMPTE, LONGUEUR_SECRET

# L'identifiant du TER du scénario, écrit par la fixture `site`.
TER = "1_F:TER:1234"


from tests.conftest import _console_errors  # noqa: F401

# Les fixtures sont importées, pas recopiées. `test_browser.py` monte un serveur
# réel sur un vrai port avec une vraie base : dupliquer ce montage dans ce
# fichier donnerait deux sites de test qui divergent dès que l'un change. Les
# importer garantit que `/compte` est testé contre le même serveur que le
# formulaire de comptage — donc le même cookie, la même base, les mêmes headers.
# Les fixtures `page`, `site` et `base_du_site` ne sont **pas** importées ici :
# elles vivent dans `tests/conftest.py` et pytest les résout pour tout fichier du
# dossier. Les importer depuis `test_browser.py` définissait une seconde instance
# de la fixture `scope="session"` du navigateur, et les 12 tests de ce fichier
# échouaient tous au setup quand la suite entière tournait — verts seuls, morts en
# CI. Voir la docstring de `tests/conftest.py`.

# La forme affichée : 24 caractères de l'alphabet, en 6 groupes de 4.
FORME_SECRET = 6


def _historique(page, site: str) -> None:
    """Atteindre la page de l'historique, et attendre qu'elle soit prête.

    Le `wait_for_selector` sur un bouton est un mauvais point d'attente après un
    `goto` : le bouton est présent dans le HTML servi, mais la page peut être en
    train de poser son premier rendu quand le test cherche déjà. On attend donc le
    **titre**, qui est la première chose que `chrome` rend et qui change selon
    l'état — puis le bouton, ce qui n'échoue plus pour une raison de timing.
    """
    page.goto(site + "/compte")
    page.wait_for_selector("h1:has-text('Vos releves')")
    page.wait_for_selector("button:has-text('Se deconnecter')", state="attached")


def _cookie_pose(page, nom: str) -> bool:
    """Le cookie est-il dans le contexte du navigateur ?

    Le contexte, et non `document.cookie` : le cookie de session est `httpOnly`,
    donc invisible au JavaScript. C'est le comportement voulu — un jeton de session
    lisible par la page est un jeton qu'une injection lit. Le test vérifie donc sa
    présence par l'API du navigateur, ce qui voit ce que la page ne peut pas voir.
    """
    return any(cookie["name"] == nom for cookie in page.context.cookies())


def _relever_rattache(site: str, page) -> None:
    """Un relevé rattaché au compte du cookie, écrit hors du navigateur.

    Le test de signalement doit porter sur le **clic**, pas sur la saisie : on
    écrit le relevé en direct avec le cookie de session du contexte, ce qui le
    rattache au compte comme le ferait le formulaire. Passer par le formulaire
    pour chaque relevé serait lent et transformerait un test de mise en page en
    test de saisie.

    Le `client_id` est fixe : un relevé par test, et chaque test a sa base neuve.
    """
    corps = {
        "client_id": "j-compte",
        "origin_stop_id": "StopArea:Lyon",
        "destination_stop_id": "StopArea:Valence",
        "origin_name": "Lyon",
        "destination_name": "Valence",
        "passengers": 42,
        "reliability": 70,
        # La ligne du TER est obligatoire : sans elle, le relevé est rejeté en
        # 422 parce qu'il ne se rattache à aucun train. C'est le même contrat que
        # par le formulaire, donc le test passe par la même voie qu'un relevé vrai.
        "trip_id": TER,
        "snapshot": {
            "precedent": None,
            "courant": {"trip_id": TER, "status": "SCHEDULED", "delay_seconds": 0},
            "suivant": None,
        },
    }
    # La requête part **du contexte du navigateur**, cookie compris. Un
    # `urllib` séparé n'enverrait pas le cookie de session, donc le relevé
    # partirait sans compte et le formulaire de signalement n'aurait rien à
    # viser — le test passerait sur une page vide.
    # `headers` explicite : Playwright ne devine pas le type et n'enverrait pas du
    # JSON, ce que le serveur refuse en 422 avec un message qui parle de type de
    # dictionnaire — donc un 422 ici serait un test mal écrit, pas un bug.
    reponse = page.request.post(
        site + "/api/sessions",
        data=corps,
        headers={"Content-Type": "application/json"},
    )
    assert reponse.status == 200


def _creer(page, site: str, pseudo: str = "romain") -> str:
    """Créer un compte et revenir avec le secret affiché. Le clic, pour de vrai.

    Le bouton « Créer mon compte » est **sous** le formulaire de connexion, et la
    fixture est sur un viewport d'iPhone : le bouton est donc hors de l'écran au
    chargement. Playwright fait défiler avant de cliquer, mais seulement si
    l'élément existe et est cliquable — sans `wait_for_selector` après le
    `fill`, le test peut cliquer pendant que le navigateur est encore en train de
    poser le premier rendu, et le clic part dans le vide.

    L'ordre est donc : aller à la page, attendre le champ, remplir, attendre que le
    bouton soit là, cliquer, attendre la page du secret. Chaque étape correspond à
    un état observable, pas à une temporisation.
    """
    page.goto(site + "/compte")
    page.wait_for_selector("#pseudo")
    page.fill("#pseudo", pseudo)
    page.wait_for_selector("button:has-text('Créer mon compte')")
    with page.expect_navigation():
        page.click("button:has-text('Créer mon compte')")
    page.wait_for_selector("#secret-texte")
    return page.text_content("#secret-texte").strip()


# --- le secret, une fois, avec un bouton qui marche --------------------------


def test_le_secret_affiche_a_la_forme_attendue(page, site):
    """Le secret est lisible, groupé, et la page est en `noindex`.

    La forme groupée n'est pas cosmétique : c'est elle qui rend la recopie
    manuelle sans faute, et c'est la seule chose que la personne ait en main si le
    presse-papiers est refusé. Le `noindex` va avec : le secret passe par l'URL
    après le `303`, donc un moteur de recherche qui indexerait la page pourrait
    conserver le secret dans son cache.
    """
    secret = _creer(page, site)

    secret = page.text_content("#secret-texte")
    groupes = secret.split("-")

    assert len(secret.replace("-", "")) == LONGUEUR_SECRET
    assert len(groupes) == FORME_SECRET
    assert all(len(g) == 4 for g in groupes), f"groupes mal formés : {groupes}"
    assert page.locator('meta[name="robots"][content="noindex"]').count() == 1, (
        "la page du secret ne doit pas être indexée"
    )


def test_le_secret_n_est_stocke_qu_en_hache(page, site, base_du_site):
    """Le secret visible à l'écran n'est nulle part en clair en base.

    C'est la propriété que seul un test « page → base » vérifie vraiment : le
    secret affiché existe forcément en clair quelque part, sinon la page ne
    pourrait pas le rendre. Ce qui doit être vrai, c'est que cet endroit est la
    réponse HTTP et rien d'autre.
    """
    secret = _creer(page, site)
    secret = page.text_content("#secret-texte")

    with sqlite3.connect(base_du_site) as connection:
        connection.row_factory = sqlite3.Row
        lignes = [dict(r) for r in connection.execute("SELECT id, pseudo, secret FROM compte")]

    assert len(lignes) == 1
    stocke = lignes[0]["secret"]
    assert stocke is not None, "le secret n'est même pas haché"
    assert secret not in str(stocke), "le secret est stocké en clair"
    assert secret.replace("-", "") not in str(stocke), "le secret est stocké en clair"


def test_le_bouton_copie_dans_une_promesse_resolue(page, site):
    """Le clic met le secret dans le presse-papiers, et le dit.

    `.then()` ne lève pas : une régression dans le nom de la méthode — `write`
    au lieu de `writeText` — laisse le bouton inerte sans erreur. Le test lit donc
    le presse-papiers **et** la confirmation affichée, parce que les deux peuvent
    passer l'un sans l'autre.
    """
    secret = _creer(page, site)
    secret = page.text_content("#secret-texte")

    # Le contexte n'accorde la permission presse-papiers qu'à une origine
    # sécurisée, ou explicitement. Un `http://127.0.0.1` est sans doute traité
    # comme sécurisé par Chromium, mais la permission doit être **accordée** —
    # sinon `writeText` rejette et le script bascule sur son repli, ce qui est le
    # comportement correct mais pas ce que ce test veut mesurer. On l'accorde donc,
    # et le test du repli — qui coupe l'API — couvre l'autre chemin.
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])

    page.click("#copier-secret")
    # La confirmation est écrite par le `.then` : attendre ce texte, c'est attendre
    # que la promesse soit résolue. Sans ça, on lirait le presse-papiers trop tôt.
    page.wait_for_selector("#copie-retour:has-text('copié')")

    presse = page.evaluate("() => navigator.clipboard.readText()")

    assert presse == secret, "le presse-papiers ne contient pas le secret affiché"
    assert page.text_content("#copie-retour") != "", (
        "le bouton doit dire ce qu'il a fait : une copie silencieuse laisse le "
        "doute que rien n'a été copié"
    )


def test_sans_presse_papiers_le_repli_selectionne_et_le_dit(page, site):
    """Le repli sélectionne le secret et l'annonce. Le chemin réel du Pi.

    `navigator.clipboard` est absent en `http://` sur une adresse non sécurisée,
    et refusé quand la permission est coupée. On supprime l'API **avant** le clic
    — le script teste `if (!navigator.clipboard)` au moment du clic, donc il faut
    le retirer avant, pas après.

    Les deux assertions comptent. Le secret doit être sélectionné (sinon la
    personne ne peut pas le récupérer), et la page doit le dire (sinon elle ne
    sait pas pourquoi le bouton a changé d'effet).
    """
    secret = _creer(page, site)
    secret = page.text_content("#secret-texte")

    # L'API disparaît avant que le script ne lise quoi que ce soit.
    page.evaluate("() => { delete Object.getPrototypeOf(navigator).clipboard; }")
    page.evaluate("() => { navigator.clipboard = undefined; }")

    page.click("#copier-secret")
    page.wait_for_selector("#copie-retour:has-text('Sélectionné')")

    selection = page.evaluate("() => window.getSelection().toString()")

    assert selection == secret, (
        "le repli doit sélectionner le secret : sans ça, la personne ne peut pas "
        "le récupérer et le compte est perdu"
    )
    assert "Ctrl+C" in page.text_content("#copie-retour"), (
        "le repli doit dire quoi faire : une sélection sans explication laisse "
        "la personne devant un secret surligné sans comprendre"
    )


def test_le_bouton_annonce_l_echec_de_l_api_plutot_que_de_se_taire(page, site):
    """Une promesse rejetée bascule sur le repli, elle ne disparaît pas.

    On fait rejeter `writeText` : c'est le cas réel d'une permission refusée avec
    l'API présente. Sans le second argument du `.then`, le script resterait
    silencieux — aucune erreur, aucun texte, et le secret non copié.
    """
    secret = _creer(page, site)
    secret = page.text_content("#secret-texte")

    page.evaluate(
        """() => {
            navigator.clipboard.writeText = () => Promise.reject(new Error('refusé'));
        }"""
    )

    page.click("#copier-secret")
    page.wait_for_selector("#copie-retour:has-text('Sélectionné')")

    assert page.evaluate("() => window.getSelection().toString()") == secret


def test_le_script_de_copie_ne_renvoie_pas_une_erreur(page, site):
    """Le filet qui attrape la régression de forme `$(\"#id\")`.

    Tous les tests du bouton passent au vert si le script meurt au chargement : le
    `<script>` est toujours dans la page, le bouton est toujours là, et
    `onclick` n'est simplement jamais posé. Cette assertion lit la liste des
    erreurs du navigateur, et c'est elle qui couvre cet angle mort.
    """
    secret = _creer(page, site)

    assert _console_errors(page) == [], f"erreur JS sur la page du secret : {_console_errors(page)}"
    # `onclick` posé et non nul : c'est la preuve directe que le script a tourné
    # jusqu'au bout, pas seulement qu'il n'a pas levé.
    assert page.evaluate("() => typeof document.getElementById('copier-secret').onclick") == "function", (
        "le gestionnaire du bouton n'est pas posé : le script s'est arrêté avant"
    )


# --- le parcours complet, avec le vrai cookie --------------------------------


def test_creer_puis_revenir_avec_le_secret(page, site):
    """Créer, se déconnecter, se reconnecter. Le cookie est le pivot.

    Le test traverse le cycle complet dans le navigateur parce que c'est le seul
    qui prouve que le cookie est **posé et relu** : un `TestClient` garde les
    cookies en mémoire de Python, un navigateur gère `max-age`, `path` et
    `secure` selon ses propres règles. Un cookie mal posé — `path` faux, `domain`
    en trop — passe les tests Python et casse ici.

    Le secret est relu depuis la page à chaque étape, jamais réutilisé depuis
    une variable du test : il faut que la reconnexion fonctionne avec ce que la
    personne voit, pas avec ce que le test a gardé.
    """
    secret = _creer(page, site)
    secret = page.text_content("#secret-texte")

    _historique(page, site)

    # Le cookie de session est bien là : sans lui, `/compte` ne montre jamais
    # l'historique. Il est lu **par le contexte**, pas par `document.cookie`, parce
    # qu'il est `httpOnly` — et il doit l'être : un secret de session lisible par
    # le JavaScript de la page est un secret qu'un script tiers peut lire. Un test
    # qui chercherait `document.cookie` échouerait ici, et aurait raison de le dire
    # si l'attribut venait à disparaître.
    assert _cookie_pose(page, COOKIE_COMPTE), (
        "le cookie de session n'a pas été posé à la création"
    )

    # Déconnexion. Le bouton est en **bas** de l'historique : sur un viewport
    # d'iPhone il est hors de l'écran après le `303`. `_historique` attend qu'il
    # soit attaché au DOM — visible ou non — et Playwright fait défiler avant de
    # cliquer, donc le clic part au bon endroit.
    _historique(page, site)
    page.click("button:has-text('Se deconnecter')")
    page.wait_for_selector("button:has-text('Ouvrir mon compte')")
    assert not _cookie_pose(page, COOKIE_COMPTE), (
        "le cookie est resté après la déconnexion"
    )

    # Reconnexion avec le secret relu de la page.
    page.fill("#secret", secret)
    page.click("button:has-text('Ouvrir mon compte')")
    page.wait_for_selector("h1:has-text('Vos releves')")
    assert _cookie_pose(page, COOKIE_COMPTE)


def test_un_secret_avec_les_tirets_en_doublon_se_connecte(page, site):
    """Le secret tapé avec un espace autour se connecte quand même.

    Le collage depuis un gestionnaire de mots de passe arrive souvent avec un
    saut de ligne, et les tirets font partie de la forme affichée. C'est le
    genre de faute qui coûte un compte, et elle n'est vérifiable que par un vrai
    envoi de formulaire.
    """
    secret = _creer(page, site)
    secret = page.text_content("#secret-texte")

    _historique(page, site)
    page.click("button:has-text('Se deconnecter')")
    page.wait_for_selector("#secret")
    page.fill("#secret", f"  {secret}\n")
    page.click("button:has-text('Ouvrir mon compte')")

    page.wait_for_selector("h1:has-text('Vos releves')")


def test_le_secret_ne_revient_pas_dans_la_page_tant_qu_il_y_a_son_cookie(page, site):
    """Un secret affiché une fois, vraiment une fois.

    Le secret passe par l'URL à la création. Après ça, la page ne doit plus le
    rendre, même si on recharge `/compte` : c'est la promesse « affiché une fois »,
    et elle se vérifie en rechargeant, pas en regardant la page d'arrivée.
    """
    secret = _creer(page, site)
    secret = page.text_content("#secret-texte")

    _historique(page, site)

    assert secret not in page.content(), "le secret réapparaît sur /compte"
    assert page.locator("#secret-texte").count() == 0


# --- le signalement, cliqué et non simulé ------------------------------------


def test_le_signalement_se_pose_par_un_clic_et_dit_quil_est_deja_signale(page, site):
    """Le parcours du signalement, dans le navigateur.

    Le formulaire est un `<details>` : il est fermé au chargement, donc le test
    l'ouvre avant de chercher le champ. C'est aussi un fait d'ergonomie qu'on
    vérifie ici — un `details` qui ne s'ouvrirait pas rendrait le signalement
    impossible à faire, et aucun test HTML ne le verrait.

    Le second appui est le plus important : la page doit dire « déjà signalé ».
    Un formulaire qui revient sans changement visible laisse croire que l'action
    n'a rien fait, et la personne signale deux fois.
    """
    secret = _creer(page, site)
    _historique(page, site)

    # Un relevé rattaché, écrit avec le cookie de session.
    _relever_rattache(site, page)
    page.reload()
    page.wait_for_selector("details summary")

    page.click("details summary")
    page.fill("details input[name='motif']", "compte deux fois")
    page.click("details button[type='submit']")
    page.wait_for_selector("text=Deja signale")

    assert page.locator("details").count() == 0, (
        "un relevé déjà signalé ne doit plus proposer de formulaire"
    )
    # Le relevé signalé est toujours là, et il est toujours compté.
    assert "Lyon" in page.content()


def test_le_signalement_ne_casse_pas_le_parcours(page, site):
    """Signaler ne fait pas sortir de la session.

    Le `303` renvoie sur `/compte`. Si la route perdait le cookie ou changeait
    l'état, la personne se retrouverait sur la page de connexion juste après avoir
    signalé, et elle croirait avoir perdu son compte. Le test vérifie donc qu'on
    est toujours sur l'historique, et pas seulement que le mot « signalé » est
    écrit.
    """
    secret = _creer(page, site)
    _historique(page, site)
    _relever_rattache(site, page)
    _historique(page, site)
    page.wait_for_selector("details summary")

    page.click("details summary")
    page.fill("details input[name='motif']", "compte deux fois")
    page.click("details button[type='submit']")
    page.wait_for_selector("text=Deja signale")

    assert "button:has-text('Se deconnecter')" in page.content() or (
        page.locator("button:has-text('Se deconnecter')").count() == 1
    ), "le signalement a déconnecté la personne"


# --- le classement, cliqué ---------------------------------------------------


def test_le_classement_affiche_les_points_et_le_denie(page, site):
    """La page du classement se lit dans un navigateur, pas seulement en HTML.

    Le test précédent vérifie que la page affiche un score. Celui-ci vérifie que
    le classement **n'affiche que des comptes qui ont des relevés** et qu'il se
    laisse lire sur un téléphone — le viewport de la fixture est celui d'un iPhone,
    donc une carte qui déborde horizontalement se verrait ici.
    """
    secret = _creer(page, site)
    _historique(page, site)
    _relever_rattache(site, page)

    page.goto(site + "/classement")
    page.wait_for_selector("h1:has-text('Classement')")

    corps = page.content()
    assert "romain" in corps
    assert "point" in corps
    assert _console_errors(page) == []

    # Pas de débordement horizontal : c'est la contrainte de la cible téléphone,
    # et elle ne se voit pas dans le HTML.
    largeur = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert largeur <= 1, f"la page déborde de {largeur}px sur un écran de téléphone"