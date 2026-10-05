"""Parcours du formulaire dans un vrai Chromium.

Le JS de la page est écrit à la main dans une chaîne Python. Les tests pytest
ne l'exécutent pas : une page peut casser au chargement et tous rester verts.
Ces tests ouvrent donc un vrai navigateur et traversent le parcours.

Une régression de la forme ``$("#id")`` (le helper est getElementById, qui
attend un id nu) tue le script au chargement et fait disparaître tous les
handlers déclarés après : ``test_page_loads_without_a_script_error`` est le
filet qui l'attrape.
"""

import json
import urllib.request

# Les fixtures `site`, `page` et le navigateur sont dans `tests/conftest.py`.
#
# Elles étaient ici, et `test_browser_compte.py` les importait — ce qui ne marche
# pas pour une fixture `scope="session"` : pytest identifie une fixture par le
# module qui la définit, donc l'import créait une **seconde** instance du
# navigateur. Le second `sync_playwright()` s'ouvrait pendant que la boucle asyncio
# du premier tournait encore, et Playwright refuse (« Sync API inside the asyncio
# loop »). Résultat : les 69 tests de ce fichier passaient, et les 12 de l'autre
# échouaient tous au setup — verts seuls, morts en suite complète. Un `conftest.py`
# est résolu une fois pour toute la session, quel que soit le fichier.
#
# `_write_stop_csv`, `_write_gtfs` et la constante `STOPS` suivent le même chemin :
# le scénario GTFS est construit une fois, dans la fixture.

from tests.conftest import _console_errors  # noqa: F401

# L'identifiant du TER du scénario, écrit par la fixture `site`.
TER = "1_F:TER:1234"


def _sessions(site: str) -> list[dict]:
    """Ce que la base contient vraiment, lu hors du navigateur."""
    with urllib.request.urlopen(site + "/api/sessions") as response:
        return json.load(response)


def _poster_releve(site: str, jeton: str, nom: str, effectif: int) -> None:
    """Un relevé écrit hors du navigateur.

    Passer par le formulaire pour chaque relevé afin d'en préparer la
    lecture à l'écran serait lent, et transformerait un test de mise en
    page en test de saisie. On écrit donc directement, et on ne teste ici
    que ce qui se voit.
    """
    _ecrire_releve(site, jeton, nom, "Vienne", effectif, "TER")


def _ecrire_releve(
    site: str,
    jeton: str,
    origine: str,
    destination: str,
    effectif: int | None,
    trip_id: str = "TER",
) -> None:
    """Un relevé dont on choisit la paire et la ligne.

    Séparé de `_poster_releve` parce que la vue par paire a besoin de
    corridors distincts et que le filtre ligne a besoin d'un `trip_id`
    connu : un seul helper ne peut pas servir les deux sans paramètres
    que l'autre n'a aucun sens à prendre.
    """
    photo = {
        "precedent": None,
        "courant": {"trip_id": trip_id, "status": "SCHEDULED", "delay_seconds": 0},
        "suivant": None,
    }
    corps = {
        "client_id": jeton,
        "origin_stop_id": f"StopArea:{origine}",
        "destination_stop_id": f"StopArea:{destination}",
        "origin_name": origine,
        "destination_name": destination,
        "trip_id": trip_id,
        "reliability": 70,
        "pseudo": "railfan",
        "snapshot": photo,
    }
    # Sans `passengers`, c'est un train signalé : un relevé de l'offre
    # qui manque, pas une charge mesurée.
    if effectif is not None:
        corps["passengers"] = effectif
    route = "/api/sessions" if effectif is not None else "/api/missing"
    requete = urllib.request.Request(
        site + route,
        data=json.dumps(corps).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(requete) as response:
        assert response.status == 200


def _fill_count(page, value: int) -> None:
    """Remplit le nombre exact comme un utilisateur : on tape dans le champ."""
    field = page.locator("#passengers")
    field.fill("")
    field.type(str(value), delay=5)


def _bulle(page) -> str | None:
    """Ce que la dernière bulle affiche, ou None si l'appui n'a rien montré.

    On lit la dernière bulle encore présente : les précédentes sont parties
    par leur animation, et c'est le dernier geste qui intéresse l'usager.
    """
    bulles = page.locator(".bulle")
    if bulles.count() == 0:
        return None
    return bulles.last.text_content()


def _geler(page) -> None:
    """Allonge la vie des bulles pour une assertion.

    La bulle vit 900 ms. Sur une machine lente — un runner CI, un téléphone
    chargé — un `click` suivi d'une lecture peut dépasser ce délai, et le test
    échouerait sur une animation que rien ne contrôle. On passe la durée à une
    minute : la lecture est alors déterministe, et `test_une_bulle_quitte_finalement_l_ecran`
    vérifie séparément que la bulle s'en va bien, sans cette aide.
    """
    page.evaluate("() => document.head.appendChild(Object.assign(document.createElement('style'),"
                  " {textContent: '.bulle { animation-duration: 60000ms !important; }'}))")


def _reach_form(page, site: str) -> None:
    page.goto(site)
    page.fill("#origin-q", "Lyon")
    page.click("#origin-list button:has-text('Lyon Part-Dieu')")
    page.fill("#destination-q", "Valence")
    page.click("#destination-list button:has-text('Valence')")
    page.wait_for_selector("#trains button")
    page.click("#trains button:has-text('TER')")
    page.click("#mode-unique")
    page.wait_for_selector("#form-step:not(.hidden)")


# --- la page doit se charger sans erreur ------------------------------------


def test_page_loads_without_a_script_error(page, site):
    page.goto(site)
    page.wait_for_selector("#origin-q")
    assert _console_errors(page) == [], f"erreur JS au chargement : {_console_errors(page)}"


def test_every_button_of_the_form_is_wired(page, site):
    """Un handler non câblé est invisible en Python : ici on le vérifie."""
    page.goto(site)
    page.wait_for_selector("#origin-q")
    wired = page.evaluate(
        """() => {
            const ids = ['near', 'missing', 'send', 'again', 'change-origin',
                         'change-od', 'change-train', 'change-mode',
                         'mode-unique', 'mode-snake', 'plus1', 'plus5',
                         'plus10', 'minus', 'snake-back'];
            return ids.filter(id => {
                const el = document.getElementById(id);
                return !el || !(el.onclick || el.oninput);
            });
        }"""
    )
    assert wired == [], f"boutons sans handler : {wired}"


def test_changing_step_always_leaves_one_step_visible(page, site):
    """show() listait les étapes à la main et avait oublié le serpent : après
    le choix d'un train, toutes les sections étaient masquées et l'écran
    restait vide. Chaque étape doit pouvoir s'afficher seule."""
    sections = page.evaluate(
        "() => [...document.querySelectorAll('main > section')].map(s => s.id)"
    )
    for step in sections:
        page.evaluate("(id) => show(id)", step)
        visible = page.evaluate(
            "() => [...document.querySelectorAll('main > section')]"
            ".filter(s => !s.classList.contains('hidden')).map(s => s.id)"
        )
        assert visible == [step], f"show({step!r}) affiche {visible} au lieu de [{step!r}]"
    page.goto(site)  # on laisse la page dans son état initial


def test_choosing_a_train_leads_to_the_mode_step(page, site):
    page.goto(site)
    page.fill("#origin-q", "Lyon")
    page.click("#origin-list button:has-text('Lyon Part-Dieu')")
    page.fill("#destination-q", "Valence")
    page.click("#destination-list button:has-text('Valence')")
    page.wait_for_selector("#trains button")
    page.click("#trains button:has-text('TER')")
    page.wait_for_selector("#mode-step:not(.hidden)")


def test_exact_number_field_is_visible_and_labelled(page, site):
    """Le champ ne doit pas être masqué : un label invisible et un focus
    impossible cassent la saisie directe et l'accessibilité.

    Il vit dans #form-step, donc il faut choisir un train avant de juger.
    """
    _reach_form(page, site)
    field = page.locator("#passengers")
    assert field.is_visible(), "le champ du nombre exact est masqué"
    assert page.locator('label[for="passengers"]').count() == 1
    assert page.locator('label[for="passengers"]').is_visible()
    # Saisissable au clavier, et il prend le focus quand on ouvre le formulaire.
    field.focus()
    assert page.evaluate("() => document.activeElement.id") == "passengers"


# --- le compteur ------------------------------------------------------------


def test_counter_buttons_add_up(page, site):
    _reach_form(page, site)
    assert page.text_content("#count-display") == "0"
    for _ in range(2):
        page.click("#plus10")
    page.click("#plus5")
    page.click("#plus1")
    page.click("#minus")
    assert page.text_content("#count-display") == "25"
    assert page.input_value("#passengers") == "25"


def test_counter_never_goes_below_zero(page, site):
    _reach_form(page, site)
    for _ in range(3):
        page.click("#minus")
    assert page.text_content("#count-display") == "0"
    assert page.input_value("#passengers") == "0"


def test_typed_number_overrides_the_counter(page, site):
    _reach_form(page, site)
    page.click("#plus10")
    _fill_count(page, 37)
    assert page.text_content("#count-display") == "37"


def test_counter_is_reset_for_each_train(page, site):
    _reach_form(page, site)
    page.click("#plus10")
    page.click("#plus10")
    assert page.text_content("#count-display") == "20"
    page.click("#change-train")
    page.wait_for_selector("#train-step:not(.hidden)")
    page.click("#trains button:has-text('TER')")
    page.click("#mode-unique")
    assert page.text_content("#count-display") == "0", "le compte de la squeezation précédente reste"


# --- le retour visuel de l'appui --------------------------------------------


def test_every_press_shows_the_delta_it_actually_applied(page, site):
    """Un appui qui ne se voit pas est un appui que l'usager recommence.

    Les retours utilisateur disaient « je ne sais pas si ça a pris » : le total
    change, mais entre deux appuis rapprochés — le geste réel — rien ne montre
    que le second est passé. Une bulle monte du bouton pressé avec le delta
    exact, ce qui répond au cas du doigt qui glisse et ne change rien.
    """
    _reach_form(page, site)
    _geler(page)
    page.click("#plus10")
    assert _bulle(page) == "+10"
    assert page.text_content("#count-display") == "10"
    page.click("#plus5")
    assert _bulle(page) == "+5"
    assert page.text_content("#count-display") == "15"
    page.click("#plus1")
    assert _bulle(page) == "+1"
    assert page.text_content("#count-display") == "16"
    page.click("#minus")
    assert _bulle(page) == "−1", "le retour doit se distinguer de l'aller en couleur"
    assert page.text_content("#count-display") == "15"
    assert _console_errors(page) == [], f"erreur JS : {_console_errors(page)}"


def test_a_press_that_changes_nothing_claims_nothing(page, site):
    """Un −1 sur un compte à zéro ne montre pas « −1 ».

    Le compteur ne descend jamais sous zéro. Si la bulle annonçait quand même
    −1, l'usager verrait un geste qu'il n'a pas eu, et chercherait pourquoi le
    total ne baisse pas.
    """
    _reach_form(page, site)
    _geler(page)
    page.click("#minus")
    assert page.text_content("#count-display") == "0"
    assert _bulle(page) is None, "un appui sans effet ne doit rien annoncer"


def test_two_quick_presses_leave_two_bulles(page, site):
    """Le cas d'usage est le double appui rapproché : c'est là que ça compte.

    Une bulle unique réutilisée masquerait la moitié des gestes. Le compteur est
    borné pour que des appuis très répétés n'empilent pas des centaines de
    nœuds dans le DOM pendant un comptage.
    """
    _reach_form(page, site)
    _geler(page)
    page.click("#plus10")
    page.click("#plus10")
    assert page.locator(".bulle").count() == 2
    assert page.text_content("#count-display") == "20"
    for _ in range(30):
        page.click("#plus10")
    assert page.text_content("#count-display") == "320"
    assert page.locator(".bulle").count() <= 8, "les bulles s'empilent sans borne"
    assert _console_errors(page) == [], f"erreur JS : {_console_errors(page)}"


def test_une_bulle_quitte_finalement_l_ecran(page, site):
    """Une bulle qui ne s'en va pas reste pour le comptage suivant.

    Sans filet sur `animationend`, une animation jamais démarrée — onglet en
    arrière-plan, motion réduit — laisserait le marqueur à l'écran pour toujours.
    """
    _reach_form(page, site)
    page.click("#plus10")
    assert page.locator(".bulle").count() == 1
    page.wait_for_selector(".bulle", state="detached")
    assert page.locator(".bulle").count() == 0


def test_the_bubble_is_not_read_out_twice(page, site):
    """#count-display porte déjà aria-live et annonce le total.

    Une bulle lisible ajouterait « +10 » puis « 10 » à chaque appui : le même
    geste annoncé deux fois, une annonce par appui au lieu d'une par résultat.
    """
    _reach_form(page, site)
    _geler(page)
    assert page.locator("#count-display").get_attribute("aria-live") == "polite"
    page.click("#plus10")
    assert page.locator(".bulle").get_attribute("aria-hidden") == "true"


def test_the_bubble_does_not_swallow_the_next_press(page, site):
    """`pointer-events: none` n'est pas décoratif.

    La bulle naît sous le doigt qui vient de presser, en plein sur le bouton
    voisin. Sans ça, elle intercepte le second appui et le compte s'arrête : le
    symptôme ressemble exactement à celui qu'on corrige.
    """
    _reach_form(page, site)
    _geler(page)
    page.click("#plus5")
    assert page.locator(".bulle").count() == 1
    page.click("#plus5")
    assert page.text_content("#count-display") == "10", "la bulle a mangé le second appui"


def test_the_bulb_is_positioned_over_the_button_that_was_pressed(page, site):
    """Une bulle qui sort toujours du même endroit ne relie pas le geste à l'effet.

    Le repère doit être le bouton pressé : c'est le seul endroit que l'usager
    regarde quand il ne sait pas si son doigt a porté.
    """
    _reach_form(page, site)
    _geler(page)
    page.click("#plus10")
    centre_bulle = page.locator(".bulle").bounding_box()
    bouton = page.locator("#plus10").bounding_box()
    assert abs((centre_bulle["x"] + centre_bulle["width"] / 2) - (bouton["x"] + bouton["width"] / 2)) < 4
    assert centre_bulle["y"] + centre_bulle["height"] / 2 <= bouton["y"] + bouton["height"] / 2

    page.click("#plus1")
    assert _bulle(page) == "+1"
    autre = page.locator(".bulle").last.bounding_box()
    assert abs((autre["x"] + autre["width"] / 2) - (bouton["x"] + bouton["width"] / 2)) > 4


def test_reduced_motion_keeps_the_feedback(page, site):
    """Couper l'animation ne doit pas couper le retour.

    `prefers-reduced-motion` dit à l'interface de moins bouger, pas de dire
    moins. Sans ce filet, un usager qui a cette préférence se retrouve avec le
    problème d'origine.
    """
    _reach_form(page, site)
    _geler(page)
    page.emulate_media(reduced_motion="reduce")
    page.click("#plus10")
    assert _bulle(page) == "+10", "le retour visuel disparaît avec l'animation"
    assert page.text_content("#count-display") == "10"
    assert _console_errors(page) == [], f"erreur JS : {_console_errors(page)}"


# --- l'envoi réel -----------------------------------------------------------


def test_the_method_page_is_readable_and_honest(page, site):
    """/methode est la page qui dit ce que les chiffres ne sont pas. Le plan
    la rend obligatoire avant toute estimation, donc elle doit exister, se
    lire, et dire les trois choses : pas une fréquentation officielle, pas
    de chiffre annuel sans méthode, quelle licence."""
    response = page.goto(site + "/methode")
    assert response.status == 200, f"/methode répond {response.status}"
    text = page.text_content("body")

    assert page.title() == "Méthode — ComptagesFer"
    # Ce que la page doit continuer à dire. La section « Ce que ces chiffres ne
    # sont pas » a été retirée sur demande (NonorFer, #23 : « elles sont
    # superflues »), donc ni le dénominateur ni le seuil ne sont plus exigés
    # ici. En revanche la réserve de fiabilité, elle, reste : c'est la ligne
    # qui est sous le titre de toutes les pages, et elle tient en une phrase.
    for attendu in (
        "Licence Ouverte 2.0",
        "GPL-3.0",
        "Aucune coordonnée GPS n'est stockée",
        "Pas de compte",
    ):
        assert attendu in text, f"la méthode ne dit pas : {attendu!r}"

    # La méthode doit aussi dire comment on compte. On compare au texte rendu
    # (text_content retire les balises) et aux espaces normalisés. Issues #23
    # (rédaction) et #25 (OD du trajet compté, pas celui de la ligne).
    aplati = " ".join(text.split())
    for attendu in (
        "Bienvenue sur ComptagesFer",
        "Comment ça marche",
        "l'origine et la destination du trajet compté",
        "et non pas l'origine-destination de la ligne",
        "les deux gares encadrantes le comptage",
        "gare de début du comptage et la gare de fin",
        "gare terminus",
        "gare d'origine",
        "indicateurs, pseudo, commentaire",
        "COREST",
    ):
        assert attendu in aplati, f"la méthode ne dit pas : {attendu!r}"

    # Les réserves doivent être visibles, pas cachées dans un attribut.
    # Le compte de h2 a disparu comme garde : la méthode a été raccourcie deux
    # fois sur demande (#23), et un nombre de sections ne dit rien de ce qui
    # doit rester. Ce sont les titres eux-mêmes qu'on exige, au niveau où ils
    # se trouvent. On compare des fragments sans apostrophe :
    # `has-text('D'où…')` n'est pas un sélecteur CSS valide, Playwright le
    # refuse.
    assert page.is_visible("main")
    titres = page.locator("main h2, main h3").all_text_contents()
    titres = [" ".join(t.split()) for t in titres]
    for titre in (
        "Comment ça marche",
        "D'où viennent les données",
        "Vos données",
        "Licences",
    ):
        assert titres.count(titre) == 1, f"section absente ou dupliquée : {titre!r} dans {titres}"


def test_the_list_switches_between_cards_and_table(page, site):
    """Une seule lecture à l'écran, jamais les deux.

    Le tableau est écrit dans la même page que les cartes parce qu'elles
    sont la même liste. Ce qui les sépare, c'est la feuille de style, et
    elle n'est jamais exécutée par pytest : sans ce test, une media query
    cassée laisserait la page afficher ses sept relevés deux fois sur un
    écran large, sans qu'aucune suite ne le remarque.

    Les deux lectures sont donc vérifiées sur un vrai moteur, dans les
    deux sens, et le téléphone est vérifié aussi : c'est la cible, et une
    correction pour le grand écran ne doit pas la casser.
    """
    _poster_releve(site, "bascule-1", "Lyon", 40)
    _poster_releve(site, "bascule-2", "Vienne", 90)

    def visible(page, selecteur: str) -> bool:
        return page.locator(selecteur).first.is_visible()

    # Téléphone : les cartes, et rien d'autre.
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(site + "/comptages")
    assert visible(page, ".cartes"), "les cartes sont la lecture du téléphone"
    assert not visible(page, "table.tableau"), "le tableau n'a pas sa place sur un téléphone"

    # Grand écran : le tableau, et rien d'autre.
    page.set_viewport_size({"width": 1500, "height": 1000})
    page.goto(site + "/comptages")
    assert visible(page, "table.tableau"), "le grand écran lit en tableau"
    assert not visible(page, ".cartes"), (
        "les cartes ne doivent pas rester au-dessus du tableau : "
        "le lecteur verrait chaque relevé deux fois"
    )
    assert page.locator("table.tableau tbody tr").count() == 2

    # Et l'inverse du tri par colonne, réel, sur le même moteur.
    valeurs_avant = page.locator("table.tableau tbody tr td:nth-child(3)").all_text_contents()
    page.click("table.tableau thead th:nth-child(3) a")
    page.wait_for_selector("table.tableau tbody tr")
    assert "tri=passengers" in page.url, "le tri doit être une URL, pas un état navigateur"
    valeurs_apres = page.locator("table.tableau tbody tr td:nth-child(3)").all_text_contents()
    assert valeurs_avant != valeurs_apres, "cliquer sur la colonne doit changer l'ordre"


def test_the_filters_work_on_a_real_form_and_stay_in_the_url(page, site):
    """Filtrer se fait par le formulaire, et l'URL porte le résultat.

    Le formulaire est la seule interface : sans JavaScript, un `<form
    method='get'>` est la page entière, et il faut le vérifier sur un vrai
    moteur — un `name` mal orthographié produit une URL sans paramètre, la
    liste se recharge entière, et aucun test de contenu ne voit la
    différence.
    """
    _ecrire_releve(site, "f-1", "Lyon", "Chambéry", 40)
    _ecrire_releve(site, "f-2", "Grenoble", "Lyon", 90)

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(site + "/comptages")
    avant = page.locator(".cartes article").count()
    assert avant == 2, avant

    # On filtre par mode, comme un lecteur qui veut les seuls serpents.
    page.select_option("#mode", "serpent")
    page.click("form.filtres button[type='submit']")
    page.wait_for_selector("form.filtres")

    assert "mode=serpent" in page.url, "le filtre doit être dans l'URL, pas dans un état"
    assert page.locator(".cartes article").count() == 0
    # Le verrou : une page vide doit dire pourquoi et offrir la sortie.
    vide = page.locator(".vide-filtre")
    assert vide.is_visible(), "un filtre à vide doit l'expliquer, pas laisser une page nue"
    assert "Enlever le filtre" in vide.text_content()

    # Le lien de sortie ramène à la liste entière.
    vide.locator("a").first.click()
    page.wait_for_selector("form.filtres")
    assert page.locator(".cartes article").count() == 2, "enlever le filtre doit tout ramener"


def test_a_bad_filter_is_named_on_the_page_not_silently_dropped(page, site):
    """Une date illisible se lit dans la page.

    C'est le défaut que pytest ne voit pas : le paramètre est écarté, la
    liste s'affiche entière, et le lecteur croit que son filtre a
    fonctionné. Le message doit être là, et la liste doit rester pleine —
    écarter un filtre ne doit pas vider la liste.
    """
    _ecrire_releve(site, "b-1", "Lyon", "Chambéry", 40)

    page.goto(site + "/comptages?depuis=bidon")

    erreurs = page.locator("ul.erreurs")
    assert erreurs.count() == 1, "le motif de l'écart doit être affiché"
    assert "pas une date" in erreurs.text_content()
    assert page.locator(".cartes article").count() == 1, "un filtre écarté ne vide pas la liste"


def test_the_paired_view_is_readable_on_a_phone(page, site):
    """La vue par paire ne s'efface pas au changement d'écran.

    Elle a la classe `tableau`, et `.tableau` est retirée du rendu sous
    48 rem pour que la liste ne se lise pas deux fois. Sans une règle
    propre, la vue par paire disparaissait sur un téléphone — la page
    répondait 200, le test de contenu passait, et un vrai lecteur
    n'avait rien. C'est exactement la classe de défaut que la vague 1 a
    déjà payée une fois.
    """
    _ecrire_releve(site, "p-1", "Lyon", "Chambéry", 100)
    _ecrire_releve(site, "p-2", "Lyon", "Chambéry", 200)
    _ecrire_releve(site, "p-3", "Nice", "Menton", 50)

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(site + "/comptages?vue=paire")
    table = page.locator("table.paires")
    assert table.is_visible(), "la vue par paire doit rester lisible sur un téléphone"
    assert table.locator("tbody tr").count() == 2

    # Et la liste, elle, garde son basculement : la paire n'a pas dû
    # casser le comportement de la vague 1.
    assert not page.locator("div.cartes").is_visible()

    page.set_viewport_size({"width": 1500, "height": 1000})
    page.goto(site + "/comptages")
    assert page.locator("table.tableau").first.is_visible()
    assert not page.locator("div.cartes").is_visible()


def test_the_paired_view_sorts_by_number_of_counts_and_says_why(page, site):
    """Le tri par défaut met en tête le corridor le mieux documenté.

    Sur un vrai moteur, parce que l'en-tête est un lien : c'est le clic
    qui doit rester dans l'URL, et le second clic doit inverser. Le
    corridor le « moins chargé » a deux relevés, le « plus chargé » en a
    un — le mettre en tête par effectif moyen répondrait à une autre
    question.
    """
    _ecrire_releve(site, "s-1", "Nice", "Menton", 1)
    _ecrire_releve(site, "s-2", "Nice", "Menton", 1)
    _ecrire_releve(site, "s-3", "Lyon", "Chambéry", 900)

    page.set_viewport_size({"width": 1500, "height": 1000})
    page.goto(site + "/comptages?vue=paire")
    lignes = page.locator("table.paires tbody tr")
    assert lignes.count() == 2
    assert "Nice" in lignes.nth(0).text_content(), "le corridor le mieux documenté en tête"

    # Le tri par en-tête est une URL, et le second clic inverse.
    page.click("table.paires thead th:nth-child(1) a")
    page.wait_for_selector("table.paires tbody tr")
    assert "tri=trajet" in page.url, "le tri de la vue par paire doit être une URL"
    avant = lignes.nth(0).text_content()
    page.click("table.paires thead th:nth-child(1) a")
    page.wait_for_selector("table.paires tbody tr")
    assert "sens=desc" in page.url
    assert lignes.nth(0).text_content() != avant, "un second clic doit inverser le tri"


def test_the_filters_survive_a_sort(page, site):
    """Trier après avoir filtré ne doit pas retirer le filtre.

    C'est la faute la plus facile à introduire et la plus discrète : le
    lien de tri construit son URL à partir de rien, la liste entière
    revient, et le lecteur voit les relevés qu'il venait d'exclure. Le
    test la suit réellement, par le clic.
    """
    _ecrire_releve(site, "k-1", "Lyon", "Chambéry", 40)
    _ecrire_releve(site, "k-2", "Grenoble", "Lyon", 90)

    page.set_viewport_size({"width": 1500, "height": 1000})
    page.goto(site + "/comptages?mode=unique")
    assert page.locator("table.tableau tbody tr").count() == 2

    page.click("table.tableau thead th:nth-child(3) a")
    page.wait_for_selector("table.tableau tbody tr")

    assert "mode=unique" in page.url, "le lien de tri a perdu le filtre"
    assert page.locator("table.tableau tbody tr").count() == 2


def test_the_header_navigates_from_every_reading_page(page, site):
    """Un en-tête commun doit être un en-tête commun.

    Cinq pages, cinq fois le même bandeau : c'est la seule chose qui permet
    à un lecteur de savoir qu'il est ailleurs dans le site. Si une page
    garde son ancien paragraphe de liens, elle perd le repère sans qu'aucun
    test de contenu ne s'en aperçoive.
    """
    for chemin in ("/comptages", "/carte", "/methode"):
        page.goto(site + chemin)
        entete = page.locator("header.site")
        assert entete.count() == 1, f"{chemin} n'a pas l'en-tête commun"
        # Chaque destination est là. `/` apparaît deux fois, et c'est
        # voulu : la marque du site et le lien « Compter » mènent au même
        # formulaire, et on ne retire pas le nom du site de sa propre page.
        for cible in ("/comptages", "/carte", "/methode"):
            assert entete.locator(f'a[href="{cible}"]').count() == 1, (
                f"{chemin} : le lien {cible} manque dans l'en-tête, ou y est en double"
            )
        assert entete.locator('a[href="/"]').count() == 2, chemin
        assert entete.is_visible(), chemin


def test_the_reading_page_links_to_the_method(page, site):
    """Le lien doit exister sur la page où se lisent les chiffres, sinon la
    méthode reste une page que personne ne visite."""
    page.goto(site + "/comptages")
    link = page.locator('a[href="/methode"]')
    assert link.count() == 1, "la page des comptages doit renvoyer vers la méthode"
    assert link.is_visible()
    link.click()
    page.wait_for_selector("h1:has-text('Méthode')")


def test_an_implausible_count_is_flagged_but_still_saved(page, site):
    """Le seuil de plausibilité signale, il ne bloque pas.

    Le plan dit « effectif au-dessus d'un plafond : signalé, pas bloqué ».
    Il n'y a pas de plafond par type de train parce qu'aucune source ne
    donne la capacité du matériel : ni le GTFS national, ni GTFS-RT.
    """
    _reach_form(page, site)
    page.fill("#passengers", "1400")
    page.dispatch_event("#passengers", "input")

    warning = page.locator("#plausibilite")
    assert warning.is_visible(), "un effectif de 1400 doit déclencher l'avertissement"
    texte = warning.text_content()
    assert "1400" in texte
    assert "erreur de frappe" in texte
    # Surtout : pas de blocage. Le message invite explicitement à envoyer,
    # et l'envoi passe.
    assert "quand même" in texte
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")

    rows = _sessions(site)
    assert [r["passengers"] for r in rows] == [1400], "la saisie doit être enregistrée"


def test_the_warning_appears_with_the_buttons_too(page, site):
    """Le compteur +10 doit déclencher l'avertissement, pas seulement la
    saisie directe : c'est le chemin le plus utilisé."""
    _reach_form(page, site)
    for _ in range(4):
        page.click("#plus10")
    assert page.text_content("#count-display") == "40"
    assert page.locator("#plausibilite").is_hidden(), "40 personnes, aucun avertissement"

    page.fill("#passengers", "1500")
    page.dispatch_event("#passengers", "input")
    assert page.locator("#plausibilite").is_visible()

    # Repasser sous le seuil doit masquer l'avertissement.
    page.fill("#passengers", "30")
    page.dispatch_event("#passengers", "input")
    assert page.locator("#plausibilite").is_hidden()


def test_the_warning_does_not_survive_a_new_train(page, site):
    """Changer de train remet le compteur à zéro : l'avertissement doit
    disparaître aussi, sinon il traîne sur l'écran suivant."""
    _reach_form(page, site)
    page.fill("#passengers", "1400")
    page.dispatch_event("#passengers", "input")
    assert page.locator("#plausibilite").is_visible()

    page.click("#change-train")
    page.wait_for_selector("#train-step:not(.hidden)")
    page.click("#trains .train >> nth=0")
    page.wait_for_selector("#mode-step:not(.hidden)")
    page.click("#mode-unique")
    page.wait_for_selector("#form-step:not(.hidden)")

    assert page.text_content("#count-display") == "0"
    assert page.locator("#plausibilite").is_hidden(), "l'avertissement doit partir"


def test_a_count_reaches_the_database(page, site):
    _reach_form(page, site)
    page.click("#plus10")
    page.click("#plus5")
    page.click("#plus10")
    page.fill("#reliability", "75")
    # Le pseudo est dans un <details> replié : on l'ouvre comme un utilisateur.
    page.click("#form-step summary")
    page.fill("#pseudo", "testeur")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")

    rows = _sessions(site)
    assert len(rows) == 1
    assert rows[0]["passengers"] == 25, f"la base contient {rows[0]['passengers']}, pas 25"
    assert rows[0]["reliability"] == 75
    assert rows[0]["pseudo"] == "testeur"
    assert rows[0]["origin_name"] == "Lyon Part-Dieu"
    assert rows[0]["destination_name"] == "Valence"


def test_the_count_appears_on_the_reading_page(page, site):
    _reach_form(page, site)
    page.click("#plus10")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")
    page.goto(site + "/comptages")
    assert page.text_content("body").count("Lyon Part-Dieu") >= 1
    assert "10" in page.text_content("body")


# --- le matériel roulant et la composition ------------------------------------


def _open_materiel(page) -> None:
    """Ouvre le second <details> de #form-step, comme un utilisateur."""
    page.click("#form-step details:nth-of-type(2) summary")


def test_the_material_reaches_the_database(page, site):
    """La partie avancée n'est utile que si ce qu'on y met arrive à la base."""
    _reach_form(page, site)
    page.click("#plus10")
    _open_materiel(page)
    page.fill("#materiel", "Z 20500")
    page.select_option("#composition", "UM3")
    page.select_option("#perimetre", "voiture")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")

    row = _sessions(site)[0]
    assert row["materiel"] == "Z 20500"
    assert row["composition"] == "UM3"
    assert row["perimetre"] == "voiture"


def test_the_advanced_block_is_reachable_by_its_label(page, site):
    """Un <details> sans nom n'est pas une partie avancée, c'est un tiroir."""
    _reach_form(page, site)
    resume = page.locator("#form-step details:nth-of-type(2) summary").text_content()
    assert "matériel" in resume.lower()
    assert "composition" in resume.lower()


def test_a_composition_without_a_perimetre_is_caught_before_sending(page, site):
    """L'avertissement vient avant l'envoi, pas en 422 après.

    Le comptage est fait dans le train : l'usager est encore là pour corriger.
    Un refus serveur arrive après coup, quand il est déjà remonté dans son
    siège.
    """
    _reach_form(page, site)
    page.click("#plus10")
    _open_materiel(page)
    page.select_option("#composition", "UM3")
    assert page.locator("#materiel-note").is_visible()
    page.click("#send")
    page.wait_for_selector("#error:not(:empty)")
    assert _sessions(site) == [], "le comptage est parti alors qu'il était incohérent"
    assert page.locator("#form-step:not(.hidden)").count() == 1, "on a quitté le formulaire"


def test_the_whole_unit_of_a_us_is_refused_before_sending(page, site):
    """Une US est une voiture : « toute la rame » n'y a pas de sens.

    Le laisser passer produirait 180 voyageurs pour une rame d'un nombre de
    voitures indéfini.
    """
    _reach_form(page, site)
    page.click("#plus10")
    _open_materiel(page)
    page.select_option("#composition", "US")
    page.select_option("#perimetre", "um")
    assert page.locator("#materiel-note").is_visible()
    page.click("#send")
    page.wait_for_selector("#error:not(:empty)")
    assert _sessions(site) == []


def test_the_note_disappears_once_the_choice_is_coherent(page, site):
    """Un avertissement qui reste après correction fait douter de l'envoi."""
    _reach_form(page, site)
    _open_materiel(page)
    page.select_option("#composition", "UM3")
    assert page.locator("#materiel-note").is_visible()
    page.select_option("#perimetre", "um")
    assert page.locator("#materiel-note").is_hidden()


def test_the_material_stays_optional_on_the_way(page, site):
    """Ne pas savoir sous quel numéro on a compté ne doit pas bloquer l'envoi."""
    _reach_form(page, site)
    page.click("#plus10")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")
    row = _sessions(site)[0]
    assert row["passengers"] == 10
    assert row["composition"] is None and row["perimetre"] is None


def test_the_material_is_visible_on_the_reading_page(page, site):
    """Une donnée qu'on ne peut relire nulle part ne sera pas exploitée."""
    _reach_form(page, site)
    page.click("#plus10")
    _open_materiel(page)
    page.fill("#materiel", "Z 20500")
    page.select_option("#composition", "UM3")
    page.select_option("#perimetre", "voiture")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")
    page.goto(site + "/comptages")
    corps = page.text_content("body")
    assert "Z 20500" in corps
    assert "UM3" in corps
    assert "une voiture" in corps


def test_a_count_without_material_shows_no_empty_line(page, site):
    """Une ligne vide se lit comme une information manquante.

    Ce serait un relevé sans matériel — un choix de l'usager — présenté comme
    une donnée perdue.
    """
    _reach_form(page, site)
    page.click("#plus10")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")
    page.goto(site + "/comptages")
    assert "UM" not in page.text_content("body")


# --- hors ligne -------------------------------------------------------------


def test_a_count_survives_the_tunnel_and_flushes_on_reconnect(page, site):
    """Le cas d'usage principal : un quai, pas de réseau, on compte quand même."""
    _reach_form(page, site)
    page.click("#plus10")
    page.click("#plus5")

    # On coupe le réseau au niveau du navigateur, pas du serveur.
    page.route("**/api/sessions", lambda route: route.abort())
    page.click("#send")
    page.wait_for_timeout(300)

    queued = page.evaluate("() => JSON.parse(localStorage.getItem('comptagefer-queue') || '[]')")
    assert len(queued) == 1, f"le compte n'est pas dans la file : {queued}"
    assert queued[0]["passengers"] == 15
    assert page.is_visible("#done-step") is False, "l'app se croit avoir envoyé le compte"

    page.unroute("**/api/sessions")
    page.evaluate("() => window.dispatchEvent(new Event('online'))")
    page.wait_for_function(
        "() => JSON.parse(localStorage.getItem('comptagefer-queue') || '[]').length === 0",
        timeout=5000,
    )

    rows = _sessions(site)
    assert [row["passengers"] for row in rows] == [15], f"la base contient {rows}"


def test_a_rejected_count_is_not_kept_forever(page, site):
    """Une erreur de validation est un tunnel : on ne garde pas le compte."""
    _reach_form(page, site)
    page.route(
        "**/api/sessions",
        lambda route: route.fulfill(status=422, content_type="application/json", body='{"detail":"x"}'),
    )
    page.click("#plus10")
    page.click("#send")
    page.wait_for_timeout(300)
    queued = page.evaluate("() => JSON.parse(localStorage.getItem('comptagefer-queue') || '[]')")
    assert queued == [], f"un 422 ne doit pas rester en file : {queued}"


# --- le serpent -------------------------------------------------------------


def _reach_snake(page, site: str) -> None:
    """Aller jusqu'à l'écran du serpent : train choisi, puis mode serpent.

    Depuis #form-step, « Changer » du train renvoie sur #train-step, et le
    choix d'un train affiche #mode-step. Il n'y a pas de retour direct vers
    #mode-step depuis le formulaire.
    """
    _reach_form(page, site)
    page.click("#change-train")
    page.wait_for_selector("#train-step:not(.hidden)")
    page.click("#trains button:has-text('TER')")
    page.wait_for_selector("#mode-step:not(.hidden)")
    page.click("#mode-snake")
    page.wait_for_selector("#snake-step:not(.hidden)")
    # startSnake est async : les arrêts arrivent après #snake-step visible.
    page.wait_for_function(
        "() => !document.getElementById('snake-title').textContent.includes('Chargement')"
    )


def test_the_load_snake_is_reachable(page, site):
    """Le serpent avait perdu son handler : le bouton ne faisait rien."""
    _reach_snake(page, site)
    assert page.is_visible("#snake-next")
    assert "Lyon" in page.text_content("#snake-title")


# --- la carte ---------------------------------------------------------------
#
# Leaflet et les tuiles viennent d'un tiers. La suite ne doit pas dépendre du
# réseau du CI : on sert une fausse bibliothèque qui enregistre ce que la page
# lui demande de dessiner, et on coupe les tuiles. Ce qui est testé, c'est la
# page — qu'elle appelle Leaflet avec les bons points, dans le bon ordre, et
# qu'elle survive à l'absence de Leaflet.

FAKE_LEAFLET = """
window.L = {};
const dessines = { segments: [], sections: [], points: [], vues: [], styles: [], carte: null };
window.dessines = dessines;
function latLng(lat, lon) { return { lat: lat, lon: lon }; }
latLng.extend = function (autre) { return { extend: function () { return autre; } }; };
window.L.map = function (id) {
  dessines.vues.push(id);
  const carte = {
    setView: function () {},
    fitBounds: function (bornes) { dessines.bornes = bornes; carte.zoomSurBornes = true; },
    invalidateSize: function () {},
    addTo: function () { return null; },
    on: function (nom, fn) { (carte.handlers = carte.handlers || {})[nom] = fn; },
  };
  dessines.carte = carte;
  return carte;
};
window.L.latLng = latLng;
// `extend` renvoie un objet qui rend la main, et qui doit donc porter `pad`
// comme le neuf : c'est la chaîne que Leaflet renvoie en vrai. Sans lui, la
// sélection d'un tracé échouerait sur `pad is not a function` et le test
// vérifierait une erreur de script au lieu du comportement.
window.L.latLngBounds = function (un, deux) {
  const bornes = { _bornes: [un, deux], pad: function () { return this; } };
  bornes.extend = function () { return bornes; };
  return bornes;
};
window.L.layerGroup = function () { return { addTo: function () { return null; } }; };
window.L.tileLayer = function (url) { return { url: url, addTo: function () { return null; } }; };
window.L.polyline = function (points, options) {
  const trace = { points: points, options: options };
  // Les couches du réseau réel sont différentes des tracés comptés : garder
  // les deux catégories séparées pour que les assertions historiques portent
  // sur les overlays métier, sans rendre invisibles les sections de voie.
  if (options && options.className === "section-carte") {
    dessines.sections.push(trace);
  } else {
    dessines.segments.push(trace);
  }
  // `addTo` rend la main sur l'objet lui-même, comme Leaflet : la page range
  // ce qu'elle reçoit pour pouvoir le surligner plus tard. Renvoyer `null`
  // ici ferait échouer la synchronisation en silence, et le test vérifierait
  // l'absence d'erreur plutôt que l'absence d'effet.
  return {
    options: options,
    setStyle: function (extra) {
      Object.assign(this.options, extra);
      dessines.styles.push(Object.assign({}, extra));
    },
    addTo: function () { return this; },
  };
};
window.L.circleMarker = function (point, options) {
  dessines.points.push({ point: point, options: options });
  return { bindPopup: function () { return this; }, addTo: function () { return null; } };
};
"""


def _carte_sans_leaflet(page, site: str) -> None:
    """Ouvre /carte avec Leaflet coupé : c'est le cas du réseau mort."""
    page.route("**/leaflet.js", lambda route: route.abort())
    page.goto(site + "/carte")


def test_the_map_page_loads_without_a_script_error(page, site):
    page.route("**/leaflet.js", lambda route: route.fulfill(status=200, body=FAKE_LEAFLET))
    page.route("**/*.png", lambda route: route.abort())
    page.goto(site + "/carte")
    page.wait_for_selector("body")
    assert _console_errors(page) == [], f"erreur JS sur la carte : {_console_errors(page)}"


def test_a_count_is_drawn_as_a_segment_between_its_two_stops(page, site):
    """Le segment doit suivre les coordonnées réelles des gares, pas un ordre
    de saisie : Lyon Part-Dieu est au nord de Valence, pas l'inverse."""
    _reach_form(page, site)
    page.click("#plus10")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")

    page.route("**/leaflet.js", lambda route: route.fulfill(status=200, body=FAKE_LEAFLET))
    page.goto(site + "/carte")
    page.wait_for_function("() => window.dessines && window.dessines.segments.length === 1")

    dessines = page.evaluate("() => window.dessines")
    assert len(dessines["segments"]) == 1
    points = dessines["segments"][0]["points"]
    assert points[0][0] > points[-1][0], "le tracé doit commencer à Lyon, au nord"
    assert 45.7 < points[0][0] < 45.8
    assert 44.9 < points[-1][0] < 45.0
    assert 2 == len(dessines["points"]), "un disque par arrêt compté"


def test_the_snake_draws_every_stop_it_recorded(page, site):
    """Un serpent a un parcours : le dessiner comme un couple
    origine-destination effacerait l'arrêt intermédiaire qui fait le compte."""
    _reach_snake(page, site)
    page.fill("#snake-onboard", "40")
    page.click("#snake-next")
    # Les descentes sont obligatoires hors dernier arrêt : c'est ce qui permet
    # de garder le nombre portes fermées au lieu d'inventer un compte à chaque
    # arrêt, donc le test les saisit comme un utilisateur.
    page.fill("#snake-boarded", "3")
    page.fill("#snake-alighted", "1")
    page.click("#snake-next")
    page.fill("#snake-boarded", "0")
    page.click("#snake-next")
    page.wait_for_selector("#done-step:not(.hidden)")

    page.route("**/leaflet.js", lambda route: route.fulfill(status=200, body=FAKE_LEAFLET))
    page.goto(site + "/carte")
    page.wait_for_function("() => window.dessines && window.dessines.segments.length === 1")

    dessines = page.evaluate("() => window.dessines")
    assert len(dessines["segments"][0]["points"]) == 3, "Lyon, Vienne, Valence"
    assert 3 == len(dessines["points"])


def test_the_map_page_explains_itself_when_leaflet_is_missing(page, site):
    """Pas de bibliothèque, pas de cadre vide : la liste des tracés reste là."""
    _carte_sans_leaflet(page, site)
    page.wait_for_selector("body")
    assert _console_errors(page) == [], f"erreur JS sans Leaflet : {_console_errors(page)}"
    assert "carte n'a pas pu se charger" in page.text_content("body")
    assert "suivent la voie ferrée réelle" in page.text_content("body")


def test_the_map_page_has_no_horizontal_overflow(page, site):
    page.route("**/leaflet.js", lambda route: route.fulfill(status=200, body=FAKE_LEAFLET))
    page.goto(site + "/carte")
    page.wait_for_selector("body")
    debord = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert debord <= 0, f"la page déborde de {debord}px"


# --- la liste et la carte se suivent ----------------------------------------
#
# La vague 3. Ces tests sont sur un vrai moteur parce que c'est la seule façon
# d'exercer un `mouseenter`, un `aria-pressed` et un `hidden` : la page
# répond 200 et le HTML est correct même quand aucun des trois ne fait rien.

def _deux_comptages(site: str) -> None:
    """Deux corridors distincts, pour que la synchronisation ait deux cibles.

    Un seul tracé ne permettrait pas de vérifier que le survol éteint *les
    autres* : c'est l'erreur classique d'une synchronisation qui allume tout.
    """
    _ecrire_releve(site, "synchro-1", "Lyon", "Vienne", 40)
    _ecrire_releve(site, "synchro-2", "Vienne", "Valence", 90)


# --- la courbe de charge ----------------------------------------------------
#
# Elle est tracée par le script au clic, donc invisible pour pytest : c'est
# ici qu'elle se vérifie, sur le DOM d'un vrai moteur. Le JS écrit le texte
# par `textContent`, qui n'interprète rien — un nom de gare contenant
# « <script> » s'affiche, il ne s'exécute pas, et le test le vérifie.

def _serpent(site: str, jeton: str, a_bord: int, boarded: int, alighted: int) -> None:
    """Un serpent Lyon → Vienne → Valence dont la fin est incomplète.

    La dernière descente est omise, comme quand le voyageur ne compte pas sa
    propre sortie : c'est le cas que la courbe doit arrêter plutôt que
    prolonger par un palier.
    """
    corps = {
        "client_id": jeton,
        "kind": "serpent",
        "origin_stop_id": "StopArea:Lyon",
        "destination_stop_id": "StopArea:Valence",
        "origin_name": "Lyon",
        "destination_name": "Valence",
        "trip_id": "TER",
        "passengers": a_bord,
        "reliability": 70,
        "legs": [
            {"stop_id": "StopPoint:LyonA", "stop_name": "Lyon Part-Dieu", "onboard": a_bord},
            {"stop_id": "StopPoint:VienneA", "stop_name": "Vienne", "boarded": boarded, "alighted": alighted},
            {"stop_id": "StopPoint:ValenceA", "stop_name": "Valence", "boarded": 0},
        ],
    }
    requete = urllib.request.Request(
        site + "/api/sessions",
        data=json.dumps(corps).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(requete) as response:
        assert response.status == 200


def _ouvre_la_courbe(page, site: str, index: int = 0) -> None:
    page.route("**/leaflet.js", lambda route: route.fulfill(status=200, body=FAKE_LEAFLET))
    page.goto(site + "/carte")
    page.wait_for_selector(f".ligne[data-i='{index}']")
    page.click(f".ligne[data-i='{index}']")
    page.wait_for_selector(f"#profil-{index} svg")


def _points(page, index: int = 0) -> list[tuple[int, int]]:
    brut = page.get_attribute(f"#profil-{index} polyline", "points")
    return [(int(p.split(",")[0]), int(p.split(",")[1])) for p in brut.split()]


def test_the_curve_is_drawn_when_a_line_is_clicked(page, site):
    _ecrire_releve(site, "courbe-1", "Lyon", "Vienne", 40)
    _ouvre_la_courbe(page, site)

    assert page.locator("#profil-0 svg").count() == 1
    assert page.locator("#profil-0 circle").count() == 2, "un disque par arrêt"
    assert _console_errors(page) == [], f"erreur JS : {_console_errors(page)}"


def test_the_curve_starts_at_zero_and_says_its_maximum(page, site):
    """L'axe part de zéro, et le maximum est nommé.

    Ces deux vérifications vivaient en Python tant que le tracé était écrit
    par le serveur. Elles sont le prix du passage en JavaScript, et elles
    doivent rester : un graphique sans maximum nommé est une forme.
    """
    _serpent(site, "courbe-2", 40, 3, 1)
    _ouvre_la_courbe(page, site)

    points = _points(page)
    assert len(points) == 2, f"la courbe s'arrête à Vienne : Valence n'a pas de descente, {points}"
    # L'axe du SVG descend : plus y est grand, plus la valeur est basse. La
    # charge monte de 40 à 42, donc l'ordonnée doit *décroître*.
    assert points[0][1] > points[1][1], f"la charge monte, l'ordonnée doit baisser : {points}"
    # Le repère du bas porte 0, et celui du haut le plafond arrondi à 50.
    reperes = page.locator("#profil-0 text.axe").all_text_contents()
    assert "0" in reperes, f"l'axe ne part pas de zéro : {reperes}"
    assert "50" in reperes, f"le plafond doit être arrondi au pas de 10 : {reperes}"
    legende = page.text_content("#profil-0 figcaption")
    assert "Maximum 42 voyageurs, à Vienne." in legende


def test_the_curve_says_where_the_count_stops(page, site):
    """Une descente non relevée arrête la courbe, et le dit.

    Prolonger jusqu'à la dernière gare dessinerait un palier, « rien ne
    s'est passé », alors qu'on vient précisément de dire qu'on n'en sait rien.
    """
    _serpent(site, "courbe-3", 40, 3, 1)
    _ouvre_la_courbe(page, site)

    legende = page.text_content("#profil-0 figcaption")
    assert "arrête à Valence" in legende, f"l'arrêt du compte n'est pas dit : {legende}"
    desc = page.text_content("#profil-0 desc")
    assert "Le compte s'arrête à Valence" in desc, "le lecteur d'écran n'est pas informé non plus"


def test_the_curve_is_described_for_a_screen_reader(page, site):
    """Un graphique sans alternative textuelle n'est pas une image, c'est un trou."""
    _serpent(site, "courbe-4", 40, 3, 1)
    _ouvre_la_courbe(page, site)

    assert page.get_attribute("#profil-0 svg", "role") == "img"
    desc = page.text_content("#profil-0 desc")
    # Les mêmes nombres que le dessin, pas un résumé.
    assert "Lyon Part-Dieu 40" in desc, desc
    assert "Vienne 42" in desc, desc


def test_a_stop_name_cannot_inject_markup_into_the_curve(page, site):
    """Les noms viennent de la base : ils sont écrits, jamais interprétés.

    Le script construit le texte par `textContent`, qui n'interprète rien.
    C'est la raison d'être du `noms_bruts` : un nom échappé, réinterprété,
    afficherait « &amp; » à l'écran.
    """
    photo = {
        "precedent": None,
        "courant": {"trip_id": "TER", "status": "SCHEDULED", "delay_seconds": 0},
        "suivant": None,
    }
    # Trois arrêts, tous connus de la fixture : un serpent dont une gare est
    # inconnue serait écarté de la carte, et il n'y aurait rien à cliquer.
    corps = {
        "client_id": "injec-1",
        "kind": "serpent",
        "origin_stop_id": "StopArea:Lyon",
        "destination_stop_id": "StopArea:Valence",
        "origin_name": "Lyon",
        "destination_name": "Valence",
        "trip_id": "TER",
        "passengers": 40,
        "reliability": 70,
        "snapshot": photo,
        "legs": [
            {"stop_id": "StopPoint:LyonA", "stop_name": "<script>alert(1)</script>", "onboard": 40},
            {"stop_id": "StopPoint:VienneA", "stop_name": "Vienne & Cie", "boarded": 3, "alighted": 1},
            {"stop_id": "StopPoint:ValenceA", "stop_name": "Valence", "boarded": 0, "alighted": 2},
        ],
    }
    requete = urllib.request.Request(
        site + "/api/sessions",
        data=json.dumps(corps).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(requete) as response:
        assert response.status == 200
    page.route("**/leaflet.js", lambda route: route.fulfill(status=200, body=FAKE_LEAFLET))
    page.goto(site + "/carte")
    page.click(".ligne[data-i='0']")
    page.wait_for_selector("#profil-0 svg")

    # Le nom est affiché tel quel, pas exécuté, et « & Cie » n'est pas
    # devenu « &amp; Cie » — donc le nom voyage brut, et c'est `textContent`
    # qui garantit qu'il ne s'interprète pas.
    assert page.locator("#profil-0 script").count() == 0
    labels = page.locator("#profil-0 text.axe").all_text_contents()
    assert "<script>alert(1)</script>" in labels, f"le nom doit s'afficher tel quel : {labels}"
    # Seules la première et la dernière gare sont nommées : dix noms sur 320
    # pixels seraient illisibles. Le `&` de Vienne n'est donc pas étiqueté
    # ici — c'est le <desc> complet qui le porte.
    assert "Vienne & Cie" not in labels, (
        f"seules les gares du bout sont nommées, sinon l'axe est illisible : {labels}"
    )
    desc = page.text_content("#profil-0 desc")
    assert "Vienne & Cie 42" in desc, f"le nom complet doit rester lisible : {desc}"
    assert "&amp;" not in desc, "un nom échappé afficherait « &amp; » à l'écran"
    assert _console_errors(page) == [], f"erreur JS : {_console_errors(page)}"


def test_hovering_a_line_lights_up_its_track(page, site):
    _deux_comptages(site)
    page.route("**/leaflet.js", lambda route: route.fulfill(status=200, body=FAKE_LEAFLET))
    page.route("**/*.png", lambda route: route.abort())
    page.goto(site + "/carte")
    page.wait_for_function("() => window.dessines && window.dessines.segments.length === 2")
    assert _console_errors(page) == [], f"erreur JS : {_console_errors(page)}"

    page.hover(".ligne[data-i='1']")
    allumes = page.evaluate("() => window.dessines.segments[1].options")
    eteintes = page.evaluate("() => window.dessines.segments[0].options")
    assert allumes["weight"] > 4, "le tracé survolé doit s'épaissir"
    assert allumes["opacity"] == 1
    assert eteintes["opacity"] < 1, (
        "les autres tracés doivent s'effacer : c'est ce qui dit lequel est survolé"
    )


def test_leaving_the_line_puts_the_tracks_back(page, site):
    """Le survol est temporaire. Un tracé qui reste allumé ment sur la suite."""
    _deux_comptages(site)
    page.route("**/leaflet.js", lambda route: route.fulfill(status=200, body=FAKE_LEAFLET))
    page.goto(site + "/carte")
    page.wait_for_function("() => window.dessines && window.dessines.segments.length === 2")

    page.hover(".ligne[data-i='0']")
    assert page.evaluate("() => window.dessines.segments[0].options.weight") > 4
    # La souris part vers le titre de la page, hors de la liste.
    page.hover("h1")
    revenue = page.evaluate("() => window.dessines.segments[0].options.weight")
    assert revenue == 4, f"le tracé doit revenir à son poids normal, il est à {revenue}"


def test_clicking_a_line_opens_its_charge_curve(page, site):
    _reach_form(page, site)
    page.click("#plus10")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")

    page.route("**/leaflet.js", lambda route: route.fulfill(status=200, body=FAKE_LEAFLET))
    page.goto(site + "/carte")
    page.wait_for_selector(".ligne")

    courbe = page.locator("figure.profil").first
    assert not courbe.is_visible(), "aucune courbe ne s'ouvre toute seule"
    page.click(".ligne[data-i='0']")
    assert courbe.is_visible(), "le clic doit ouvrir la courbe de charge"
    assert page.locator(".ligne[data-i='0']").get_attribute("aria-pressed") == "true"
    # La courbe est bien celle du relevé choisi, pas un cadre vide.
    assert courbe.locator("svg").count() == 1


def test_clicking_a_second_line_closes_the_first(page, site):
    _deux_comptages(site)
    page.route("**/leaflet.js", lambda route: route.fulfill(status=200, body=FAKE_LEAFLET))
    page.goto(site + "/carte")
    page.wait_for_selector(".ligne")

    page.click(".ligne[data-i='0']")
    assert page.locator("figure.profil").first.is_visible()
    page.click(".ligne[data-i='1']")
    visibles = page.locator("figure.profil:visible").count()
    assert visibles == 1, (
        f"deux courbes ouvertes à la fois ({visibles}) : la comparaison a son propre mode"
    )


def test_clicking_the_same_line_again_closes_it(page, site):
    """Un bouton qui ne se referme pas oblige à recharger la page."""
    _deux_comptages(site)
    page.route("**/leaflet.js", lambda route: route.fulfill(status=200, body=FAKE_LEAFLET))
    page.goto(site + "/carte")
    page.wait_for_selector(".ligne")

    page.click(".ligne[data-i='0']")
    assert page.locator("figure.profil:visible").count() == 1
    page.click(".ligne[data-i='0']")
    assert page.locator("figure.profil:visible").count() == 0
    assert page.locator(".ligne[data-i='0']").get_attribute("aria-pressed") == "false"


def test_the_keyboard_reaches_the_curve_too(page, site):
    """Le clavier doit obtenir ce que la souris obtient.

    Un bouton qui ne s'ouvre qu'au clic de souris est un bouton inaccessible,
    et le test ne le verrait pas : il faudrait tryser au clavier pour s'en
    apercevoir.
    """
    _deux_comptages(site)
    page.route("**/leaflet.js", lambda route: route.fulfill(status=200, body=FAKE_LEAFLET))
    page.goto(site + "/carte")
    page.wait_for_selector(".ligne")

    page.focus(".ligne[data-i='0']")
    page.keyboard.press("Enter")
    assert page.locator("figure.profil:visible").count() == 1, (
        "Entrée doit ouvrir la courbe : un bouton ne doit pas être une souris à lui seul"
    )


def test_the_list_still_works_without_leaflet(page, site):
    """Sans la bibliothèque, la liste reste utilisable : survol et courbe.

    Ce que la carte apporte — le déplacement au clic — disparaît, mais le
    reste ne doit pas. La page qui répond 200 avec une liste morte est
    exactement le défaut que les vagues 1 et 2 ont appris à traquer.
    """
    _deux_comptages(site)
    _carte_sans_leaflet(page, site)
    page.wait_for_selector("body")
    assert _console_errors(page) == [], f"erreur JS sans Leaflet : {_console_errors(page)}"
    assert "carte n'a pas pu se charger" in page.text_content("body")

    page.click(".ligne[data-i='0']")
    assert page.locator("figure.profil:visible").count() == 1, (
        "la courbe de charge ne dépend pas de Leaflet : elle est dans la page"
    )


def test_the_charge_curve_survives_a_phone_layout(page, site):
    """Sur un téléphone, la courbe doit être lisible et pas débordante.

    C'est le défaut déjà rencontré deux fois dans cette PR : une vue retirée
    sous 48 rem, une page qui répond 200 et n'affiche rien. Ici la courbe est
    dans le flux, donc le risque est le débordement.
    """
    _deux_comptages(site)
    page.set_viewport_size({"width": 390, "height": 844})
    page.route("**/leaflet.js", lambda route: route.fulfill(status=200, body=FAKE_LEAFLET))
    page.goto(site + "/carte")
    page.wait_for_selector(".ligne")
    page.click(".ligne[data-i='0']")

    courbe = page.locator("figure.profil:visible").first
    assert courbe.is_visible(), "la courbe doit exister sur un téléphone aussi"
    largeur = courbe.locator("svg").first.bounding_box()
    assert largeur["width"] <= 390, f"la courbe déborde : {largeur['width']}px sur un écran de 390"


def test_a_reported_train_stays_plain_text_on_the_map(page, site):
    """Pas d'effectif, pas de bouton : la ligne reste du texte lisible.

    Vérifié dans un vrai moteur, et pas seulement en pytest, parce qu'un
    `<button>` sans gestionnaire est visible et cliquable : le lecteur
    cliquerait et rien ne se passerait.
    """
    _ecrire_releve(site, "sans-effectif", "Lyon", "Vienne", None)
    page.route("**/leaflet.js", lambda route: route.fulfill(status=200, body=FAKE_LEAFLET))
    page.goto(site + "/carte")
    page.wait_for_selector("body")
    assert _console_errors(page) == []
    assert page.locator(".ligne").count() == 0, "un train signalé n'a pas de courbe, donc pas de bouton"
    assert "sans effectif" in page.text_content("body"), "mais il reste dans la liste, lisible"


# --- la reprise du serpent après fermeture d'onglet -------------------------


def test_a_snake_survives_closing_the_tab(page, site):
    """C'est le point que la file hors ligne ne couvrait pas : elle ne transporte
    que ce qui doit partir vers le serveur, pas une saisie en cours."""
    _reach_snake(page, site)
    page.fill("#snake-onboard", "40")
    page.click("#snake-next")
    page.fill("#snake-boarded", "3")
    page.fill("#snake-alighted", "1")
    page.click("#snake-next")

    # On simule la fermeture : ce qui compte, c'est ce que le navigateur a
    # gardé, pas le JavaScript qui tourne encore.
    page.reload()

    assert page.is_visible("#snake-step:not(.hidden)"), "la reprise doit s'ouvrir d'elle-même"
    assert "Valence" in page.text_content("#snake-title"), "on doit repartir au bon arrêt"
    assert "Reprise" in page.text_content("#snake-resume")


def test_a_resumed_snake_keeps_the_counts_already_given(page, site):
    """Reprendre au bon arrêt mais perdre les montées déjà comptées refait le
    travail du voyageur et fausse le profil."""
    _reach_snake(page, site)
    page.fill("#snake-onboard", "40")
    page.click("#snake-next")
    page.fill("#snake-boarded", "3")
    page.fill("#snake-alighted", "1")
    page.click("#snake-next")
    page.fill("#snake-boarded", "0")
    page.click("#snake-next")
    page.wait_for_selector("#done-step:not(.hidden)")

    rows = _sessions(site)
    assert rows[0]["passengers"] == 40, "le total doit rester celui des portes fermées"
    assert rows[0]["legs"][1]["boarded"] == 3
    assert rows[0]["legs"][1]["alighted"] == 1


def test_a_finished_snake_is_not_offered_again(page, site):
    """Reproposer un serpent déjà envoyé ferait compter le même trajet deux fois."""
    _reach_snake(page, site)
    page.fill("#snake-onboard", "40")
    page.click("#snake-next")
    page.fill("#snake-boarded", "3")
    page.fill("#snake-alighted", "1")
    page.click("#snake-next")
    page.fill("#snake-boarded", "0")
    page.click("#snake-next")
    page.wait_for_selector("#done-step:not(.hidden)")

    page.reload()
    assert not page.is_visible("#snake-step:not(.hidden)")
    assert page.is_visible("#origin-step:not(.hidden)")


def test_a_snake_can_be_dropped(page, site):
    """Sans ça, un serpent à moitié fait bloque le téléphone : on ne peut plus
    repartir de zéro sans effacer le stockage à la main."""
    _reach_snake(page, site)
    page.fill("#snake-onboard", "40")
    page.click("#snake-next")
    page.reload()

    assert page.is_visible("#snake-drop")
    page.click("#snake-drop")
    page.reload()

    assert not page.is_visible("#snake-step:not(.hidden)")
    assert page.is_visible("#origin-step:not(.hidden)")


def test_the_drop_button_is_hidden_when_there_is_nothing_to_drop(page, site):
    _reach_snake(page, site)
    assert not page.is_visible("#snake-drop"), "on n'offre pas de jeter un serpent qu'on n'a pas"


def test_an_empty_snake_is_not_stored(page, site):
    """Un serpent vide proposerait « reprendre » pour rien."""
    _reach_snake(page, site)
    page.reload()
    assert not page.is_visible("#snake-step:not(.hidden)")
    assert not page.is_visible("#snake-drop")


def test_the_snake_walks_the_stops_of_the_chosen_trip(page, site):
    _reach_snake(page, site)

    assert "Lyon" in page.text_content("#snake-title")
    page.fill("#snake-onboard", "40")
    page.click("#snake-next")

    assert "Vienne" in page.text_content("#snake-title")
    page.fill("#snake-boarded", "3")
    page.fill("#snake-alighted", "1")
    page.click("#snake-next")

    assert "Valence" in page.text_content("#snake-title")
    page.fill("#snake-boarded", "0")
    page.click("#snake-next")
    page.wait_for_selector("#done-step:not(.hidden)")

    rows = _sessions(site)
    assert len(rows) == 1
    assert rows[0]["kind"] == "serpent"
    assert rows[0]["passengers"] == 40, "le nombre portes fermées doit rester le compte unique"
    assert [leg["stop_name"] for leg in rows[0]["legs"]] == [
        "Lyon Part-Dieu",
        "Vienne",
        "Valence",
    ], f"arrêts enregistrés : {rows[0]['legs']}"
    assert rows[0]["legs"][1]["boarded"] == 3
    assert rows[0]["legs"][2]["alighted"] is None, "une descente non comptée reste nulle, pas 0"


def test_the_snake_also_takes_a_pseudo_and_a_comment(page, site):
    """/methode promet un commentaire dans les deux cas. Si le serpent n'en
    accepte pas, la promesse est fausse pour la moitié des saisies : train
    précédent supprimé, car de substitution, forte charge inexpliquée."""
    _reach_snake(page, site)
    page.fill("#snake-onboard", "40")
    page.click("#snake-next")
    page.fill("#snake-boarded", "3")
    page.fill("#snake-alighted", "1")
    page.click("#snake-next")
    assert "Valence" in page.text_content("#snake-title")
    # Au dernier arrêt, les descentes restent facultatives, pas les montées.
    page.fill("#snake-boarded", "0")

    # Le commentaire du serpent est dans un <details> replié, comme celui du
    # comptage unique : on l'ouvre comme un utilisateur.
    page.click("#snake-fields details >> nth=1 >> summary")
    page.fill("#snake-pseudo", "testeur")
    page.fill("#snake-comment", "car de substitution")
    page.click("#snake-next")
    page.wait_for_selector("#done-step:not(.hidden)")

    rows = _sessions(site)
    assert rows[0]["pseudo"] == "testeur", f"pseudo enregistré : {rows[0]['pseudo']!r}"
    assert rows[0]["comment"] == "car de substitution"
