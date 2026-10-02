"""Les pages de la phase 9 se trouvent par un lien, pas en devinant l'URL.

`/compte` et `/classement` répondaient 200 et n'étaient dans rien : on arrivait
à elles en tapant l'adresse. Une route qui répond n'existe pas pour qui ne la
connaît pas, et le plan le disait dans sa section « Fichiers » avant que ce soit
fait — c'est la forme exacte d'un plan qui décrit plus que le code.

Ce test lit `NAVIGATION` et vérifie trois choses qu'un test de page ne verrait pas :

- les deux pages y sont ;
- chaque entrée pointe vers une route qui existe **et répond 200**, donc pas un
  lien mort ;
- chaque entrée a un libellé qui se distingue des autres, parce que deux liens
  « Compte » et « Comptages » dans le même bandeau sont un défaut d'accessibilité
  avant d'être un défaut de lisibilité.

Il vérifie aussi `aria-current` : la navigation doit dire où l'on est, sinon une
  page longue de sept liens oblige à relire le bandeau pour savoir qu'on l'a
  déjà franchi.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.affichage import NAVIGATION

PAGES_PHASE_9 = ("/compte", "/classement")


def test_les_pages_de_la_phase_9_sont_dans_la_navigation():
    hrefs = {href for href, _ in NAVIGATION}

    for href in PAGES_PHASE_9:
        assert href in hrefs, (
            f"{href} répond mais n'est dans le chrome : on n'y arrive qu'en tapant "
            "l'URL, donc la page n'existe pas pour qui ne la connaît pas"
        )


def test_chaque_lien_de_la_navigation_repond(tmp_path: Path):
    """Un lien mort dans le bandeau est pire qu'un lien absent.

    Le bandeau est sur toutes les pages, donc une entrée cassée ne casse pas une
    page : elle est là partout, et chaque visiteur la voit. Le test demande donc un
    vrai `200` sur chaque entrée, pas une simple présence dans le tuple.
    """
    client = TestClient(create_app(tmp_path))

    for href, texte in NAVIGATION:
        reponse = client.get(href)
        assert reponse.status_code == 200, f"le lien « {texte} » pointe sur {href} : {reponse.status_code}"


def test_les_libelles_de_la_navigation_sont_distincts():
    libelles = [texte for _, texte in NAVIGATION]

    assert len(set(libelles)) == len(libelles), (
        f"deux liens portent le même libellé : {libelles}"
    )


def test_la_page_marque_où_on_est(tmp_path: Path):
    """`aria-current`, sur les pages qui passent par le chrome.

    La racine `/` est exclue, et pour une raison qui vaut d'être écrite : elle
    renvoie `PAGE`, une application d'une seule page écrite à part, avec son
    propre bandeau. Elle n'utilise pas `chrome` — et lui ajouter la navigation
    serait un changement d'interface, pas un oubli de constante. Les autres pages
    passent toutes par `chrome`, donc c'est là que la règle doit tenir.

    Ce qui est vérifié est le rendu, pas la constante : `actif` est un paramètre
    que chaque appelant peut oublier, et une page qui oublie s'affiche sans
    que rien d'autre ne change.
    """
    client = TestClient(create_app(tmp_path))

    for href, _ in NAVIGATION:
        if href == "/":
            continue  # l'application d'une seule page, bandeau compris
        corps = client.get(href).text
        assert f'href="{href}" aria-current="page"' in corps, (
            f"{href} ne se marque pas comme page courante"
        )


def test_chaque_libelle_de_la_navigation_est_affiche(tmp_path: Path):
    """Chaque entrée de `NAVIGATION` est rendue par le chrome.

    Une entrée que personne ne rend reste invisible sans test : c'est une
    constante dont personne ne dépend, et le seul moment où on le verrait est
    celui où quelqu'un cherche où mettre un lien. Le test lit le HTML rendu et
    cherche chaque libellé, donc il attrape une entrée perdue entre la constante
    et le chrome.
    """
    client = TestClient(create_app(tmp_path))

    corps = client.get("/comptages").text

    for _href, texte in NAVIGATION:
        assert texte in corps, f"le libellé « {texte} » n'est rendu nulle part"


def test_la_page_de_comptage_partage_le_menu_complet_et_la_mise_en_page(tmp_path: Path):
    client = TestClient(create_app(tmp_path))
    page = client.get("/").text

    assert page.count('class="site"') == 1
    assert 'href="/" aria-current="page"' in page
    for href, texte in NAVIGATION:
        assert f'href="{href}"' in page, texte
    assert "max-width: 72rem" in page
    assert "pas une fréquentation officielle" not in page
