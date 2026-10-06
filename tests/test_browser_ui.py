"""Les défauts d'interface vérifiés **dans un vrai Chromium**.

`tests/test_ui_reports.py` vérifie le HTML : c'est suffisant pour un lien
manquant, un champ vide, un tableau absent. Ce ne l'est pas pour
l'alignement — « le libellé est-il au-dessus de son champ » est une
question de géométrie, pas de balise. Un HTML correct peut se rendre mal,
et c'est exactement ce que le formulaire de `/comptages` faisait : chaque
`label` et chaque `input` étaient des enfants directs de la grille, donc
le navigateur les répartissait dans ses colonnes alternativement.

C'est la différence entre « le code dit ce qu'il veut dire » et « la page
montre ce que le code veut dire » — voir `docs/regles.md` § 3.

Le viewport est ici celui d'un écran large, et **pas** celui de la fixture
`page` de `conftest.py`, qui est un iPhone : c'est le défaut qui n'existe
qu'au-dessus de 48 rem, donc le vérifier en 390 px le ferait passer pour
corrigé.
"""

import itertools
import json
import math
import re

from tests.conftest import _console_errors

# Le TER du scénario GTFS écrit par la fixture `site`. La même constante est
# dans `conftest.py` ; elle est répétée ici plutôt qu'importée parce que
# l'importer d'un module de tests crée une seconde fixture pour un fichier de
# tests (voir le docstring de `conftest.py`).
TER = "1_F:TER:1234"

# Les coordonnées du viewport par défaut de la fixture `page`, qui est un
# iPhone : les défauts d'alignement de ce fichier n'existent qu'au-dessus de
# 48 rem, donc les vérifier en 390 px les ferait passer pour corrigés.
LARGE = {"width": 1280, "height": 900}
ETROIT = {"width": 390, "height": 844}


def _ecrire_releve(page, site: str, jeton: str) -> None:
    """Un relevé écrit **depuis la page**, donc avec le cookie de session.

    Pas un `urllib` depuis le test : une requête faite par le processus de
    test n'a pas le cookie du navigateur, donc le relevé serait enregistré
    sans compte — et `/classement`, qui ne liste que les relevés rattachés à
    un compte, resterait vide. C'est le genre de fixture qui fait échouer un
    test pour une raison qui n'a rien à voir avec la page.

    Passer par le formulaire de saisie transformerait ce fichier en test de
    saisie, qui existe déjà ailleurs. On écrit donc par l'API, mais **depuis
    la page** — et il faut que la page soit sur le site : un `fetch` à URL
    relative sur `about:blank` échoue, ce qui est le genre de détail que le
    message d'erreur rend très bien.
    """
    if not page.url.startswith("http"):
        page.goto(site)
    corps = json.dumps(
        {
            "client_id": jeton,
            "origin_stop_id": "StopPoint:AnnecyA",
            "destination_stop_id": "StopPoint:VienneA",
            "origin_name": "Lyon Part-Dieu",
            "destination_name": "Vienne",
            "trip_id": TER,
            "passengers": 123,
            "reliability": 70,
            "snapshot": {
                "precedent": None,
                "courant": {"trip_id": TER, "status": "SCHEDULED", "delay_seconds": 0},
                "suivant": None,
            },
        }
    )
    page.evaluate(
        """async (corps) => {
            await fetch("/api/sessions", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: corps
            });
        }""",
        corps,
    )


def _boites(page) -> list[tuple[str, float, float]]:
    """Chaque champ du formulaire, avec le bord haut de son libellé et de son champ.

    Renvoie `(identifiant, haut_du_label, haut_du_champ)` en pixels CSS.
    """
    return page.evaluate(
        """() => {
            const formulaire = document.querySelector("form.filtres");
            return [...formulaire.querySelectorAll("label[for]")].map(label => {
                const champ = document.getElementById(label.getAttribute("for"));
                const l = label.getBoundingClientRect();
                const c = champ ? champ.getBoundingClientRect() : null;
                return [label.getAttribute("for"), l.top, c ? c.top : NaN];
            });
        }"""
    )


def test_chaque_libelle_est_bien_au_dessus_de_son_champ(page, site):
    """La géométrie, pas la syntaxe : le libellé surplombe son propre champ.

    Le test échoue sur l'ancienne version pour une raison qu'aucune relecture
    de code ne montre : les `<label>` et les `<input>` étaient tous des
    enfants de la grille à cinq colonnes, donc la grille posait le libellé
    « Depuis le » dans la colonne 1, son champ dans la colonne 2, le libellé
    « Mode » dans la colonne 5, et le champ `mode` dans la colonne 1 de la
    rangée suivante. Les quatre libellés se lisaient à côté de champs
    étrangers.
    """
    page.set_viewport_size(LARGE)
    page.goto(site + "/comptages?mode=serpent&ligne=C13")
    page.wait_for_selector("form.filtres")

    boites = _boites(page)

    assert boites, "le formulaire n'a plus aucun champ étiqueté"
    for identifiant, haut_label, haut_champ in boites:
        assert math.isfinite(haut_champ), (
            f"le libellé « {identifiant} » désigne un champ absent du formulaire"
        )
        assert haut_champ > haut_label, (
            f"le champ « {identifiant} » est au-dessus ou à côté de son libellé "
            f"(libellé à {haut_label:.0f} px, champ à {haut_champ:.0f} px) : "
            "la grille répartit les balises au lieu des champs"
        )


def test_les_cinq_champs_sont_sur_une_meme_rangee(page, site):
    """Cinq filtres sur une rangée, pas cinq lignes.

    La grille annonce cinq colonnes ; le test lit les positions réelles,
    donc une règle CSS qui dirait le contraire — ou une qui serait ignorée
    par le navigateur — se verrait ici.
    """
    page.set_viewport_size(LARGE)
    page.goto(site + "/comptages")
    page.wait_for_selector("form.filtres")

    largeurs = page.evaluate(
        """() => [...document.querySelectorAll("form.filtres p.champ")]
             .map(p => Math.round(p.getBoundingClientRect().width))"""
    )

    assert len(largeurs) == 5, f"il doit y avoir cinq champs de filtre : {largeurs}"
    # Cinq colonnes de largeur égale : le plus écarté des deux à gauche et
    # le plus à droite doivent se toucher, à la tolérance de rendu près.
    ecart = max(largeurs) - min(largeurs)
    assert ecart <= 2, f"les colonnes ne sont pas égales : {largeurs}"


def test_les_champs_ne_debordent_pas_sur_un_telephone(page, site):
    """Quatre colonnes sur un téléphone, ce serait quatre champs minuscules.

    Le défaut est l'inverse de celui d'un grand écran, donc il se vérifie sur
    la même page : sous 48 rem, la grille doit retomber à une colonne, sinon
    le formulaire de consultation devient illisible sur l'écran qui sert à
    lire.
    """
    page.set_viewport_size(ETROIT)
    page.goto(site + "/comptages")
    page.wait_for_selector("form.filtres")

    largeurs = page.evaluate(
        """() => [...document.querySelectorAll("form.filtres p.champ")]
             .map(p => Math.round(p.getBoundingClientRect().width))"""
    )
    # La largeur **intérieure** du formulaire, pas sa largeur de bordure :
    # le formulaire a un `padding: 0.8rem`, donc ses champs sont plus étroits
    # que lui. Comparer les deux donnerait un échec de 26 px sur un
    # formulaire correct.
    interieur = page.evaluate(
        """() => {
            const f = document.querySelector("form.filtres");
            const style = getComputedStyle(f);
            return Math.round(f.getBoundingClientRect().width
                              - parseFloat(style.paddingLeft)
                              - parseFloat(style.paddingRight));
        }"""
    )

    assert len(set(largeurs)) == 1, f"les champs ne font pas la même largeur : {largeurs}"
    assert max(largeurs) == interieur, (
        f"un champ de filtre déborde du formulaire ({max(largeurs)} px pour "
        f"{interieur} px utiles) : il faut une seule colonne sur un téléphone"
    )


def test_la_carte_d_un_comptage_s_ouvre_au_clic_sur_telephone(page, site):
    """Le lien des cartes, sur l'écran qui affiche les cartes.

    Les deux lectures de `/comptages` ne sont pas le même rendu : les cartes
    sur téléphone, le tableau au-dessus de 48 rem. Un lien présent dans l'une
    seulement est invisible exactement là où on lit — donc les deux sont
    cliqués, chacune dans la largeur où elle s'affiche.
    """
    _ecrire_releve(page, site, "jeton-carte")

    page.set_viewport_size(ETROIT)
    page.goto(site + "/comptages")
    page.wait_for_selector(".cartes article")

    with page.expect_navigation():
        page.locator(".cartes a", has_text="Détail du comptage").first.click()
    page.wait_for_selector("dl.faits")

    assert "client_id=jeton-carte" in page.url
    assert "123 voyageurs" in page.text_content("dl.faits")
    assert page.locator("h1").inner_text().strip() == "Comptage"


def test_le_trajet_du_tableau_s_ouvre_le_releve(page, site):
    """Le lien du tableau, sur l'écran qui affiche le tableau.

    Un `href` correct dans le HTML peut être couvert par un élément au-dessus,
    ou une ligne peut ouvrir autre chose. Seul un clic dit si le geste atteint
    la cible — et le geste, c'est tout ce que le lecteur a.
    """
    _ecrire_releve(page, site, "jeton-tableau")

    page.set_viewport_size(LARGE)
    page.goto(site + "/comptages")
    page.wait_for_selector("table.tableau")

    with page.expect_navigation():
        page.locator("table.tableau tbody tr td a").first.click()
    page.wait_for_selector("dl.faits")

    assert "client_id=jeton-tableau" in page.url
    assert "123 voyageurs" in page.text_content("dl.faits")



def test_le_classement_aligne_les_nombres_en_colonne(page, site):
    """Un classement se compare : les nombres doivent être à la même hauteur.

    Le tableau aligne les colonnes par construction, donc le test mesure
    plutôt que de relire du HTML : c'est la seule façon de dire que la page
    **montre** un classement et pas qu'elle en contient un.

    Le compte se crée **par le navigateur**, comme une personne le fait : il
    n'y a pas de raccourci vers la base du serveur de test, et un compte
    créé par un `POST` direct ne prouverait pas que la page le lit.
    """
    page.set_viewport_size(LARGE)
    page.goto(site + "/compte")
    page.fill("#pseudo", "romain")
    page.click("form[action='/compte/creer'] button")
    page.wait_for_selector("#secret-texte")

    _ecrire_releve(page, site, "jeton-classement")

    page.goto(site + "/classement")
    page.wait_for_selector("table.palmares")

    colonnes = page.evaluate(
        """() => [...document.querySelectorAll("table.palmares thead th")]
             .map(th => Math.round(th.getBoundingClientRect().left))"""
    )

    assert len(colonnes) == 7, f"le classement doit avoir sept colonnes : {colonnes}"
    for avant, apres in itertools.pairwise(colonnes):
        assert apres > avant, f"deux colonnes se superposent : {colonnes}"

    # La barre du premier va jusqu'au bout de sa cellule : une barre à moitié
    # pleine pour le premier, c'est un graphique qui ne dit plus rien.
    barre, jauge = page.evaluate(
        """() => {
            const remplissage = document.querySelector(".tableau.palmares .remplissage");
            return [remplissage.getBoundingClientRect().width,
                    remplissage.parentElement.getBoundingClientRect().width];
        }"""
    )
    assert abs(barre - jauge) < 2, (
        f"la barre du premier ne remplit pas sa jauge ({barre:.0f} / {jauge:.0f})"
    )


def test_le_classement_a_une_lecture_telephone_qui_ne_deborde_pas(page, site):
    """Sur un téléphone, le classement se lit en cartes — et il ne déborde pas.

    Un tableau à sept colonnes ne tient pas en 390 px : il débordait de
    145 px, ce que `tests/test_browser_compte.py` mesurait déjà. Le tableau
    est donc retiré du rendu sous 48 rem au profit des cartes — les mêmes
    nombres, dans une boîte qui se lit en descendant.

    Le test ne vérifie pas qu'un tableau existe, il vérifie que **le
    classement est visible** et qu'il tient dans l'écran : c'est la seule
    question que se pose la personne qui l'ouvre.
    """
    page.set_viewport_size(ETROIT)
    page.goto(site + "/compte")
    page.fill("#pseudo", "camille")
    page.click("form[action='/compte/creer'] button")
    page.wait_for_selector("#secret-texte")
    _ecrire_releve(page, site, "jeton-mobile")

    page.goto(site + "/classement")
    page.wait_for_selector(".palmares-cartes article")

    # La carte est visible, et elle porte les nombres qu'on compare.
    carte = page.locator(".palmares-cartes article").first
    assert carte.is_visible(), "la lecture téléphone du classement est invisible"
    # Le score, pas l'effectif : 4 points pour un relevé à 123 voyageurs, et
    # écrire « 123 » ici confondrait les deux mesures — c'est exactement la
    # confusion que le dénominateur existe pour empêcher.
    assert re.search(r"\d+\s*\n?\s*points? sur", carte.inner_text()), (
        f"la carte ne montre pas le score et son dénominateur : {carte.inner_text()!r}"
    )
    assert "camille" in carte.inner_text(), "la carte ne nomme pas le compte"
    assert "1 paire de gares couvertes" in carte.inner_text(), (
        "la carte doit garder la couverture : c'est ce que distingue deux "
        "comptes qui ont le même nombre de relevés"
    )

    # Et rien ne déborde : c'est le défaut exact que le tableau à sept colonnes
    # produisait en 390 px.
    debordement = page.evaluate(
        """() => Math.round(document.documentElement.scrollWidth
                             - document.documentElement.clientWidth)"""
    )
    assert debordement <= 1, (
        f"la page du classement déborde de {debordement} px sur un téléphone"
    )


def test_le_pseudo_du_compte_arrive_dans_le_formulaire_de_saisie(page, site):
    """Le pré-remplissage, dans un vrai navigateur.

    Le test Python vérifie que la valeur est dans le HTML. Celui-ci vérifie
    que le champ visible est celui qui porte la valeur — qu'aucun autre
    `input` ne l'a prise, et que le formulaire de saisie n'a pas été remplacé
    au passage.
    """
    page.set_viewport_size(LARGE)
    page.goto(site + "/compte")
    page.fill("#pseudo", "camille")
    page.click("form[action='/compte/creer'] button")
    page.wait_for_selector("#secret-texte")

    page.goto(site)
    # Le champ est dans un `<details>` replié, donc il n'est pas **visible** :
    # `wait_for_selector` exigerait un clic sur le panneau, qui n'a rien à
    # voir avec ce que le test vérifie. On attend l'attachement et on lit la
    # valeur — c'est bien le champ de la saisie qui la porte, qu'on l'ait
    # ouvert ou non.
    page.wait_for_selector("#pseudo", state="attached")

    assert page.input_value("#pseudo") == "camille", (
        "le pseudo du compte connecté n'est pas dans le champ de la saisie"
    )
    assert _console_errors(page) == [], f"erreur JS : {_console_errors(page)}"
