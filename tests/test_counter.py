import re
from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer.app import create_app

# Le script de la page est du JavaScript écrit à la main dans une chaîne Python.
# Aucun test Python ne l'exécute : un $("#id") laissé tel quel fait échouer tout le
# script au chargement, et les autres tests restent verts. Ces tests bloquent
# la régression sur la forme du code, pas sur le comportement navigateur.


def _page(tmp_path) -> str:
    return TestClient(create_app(tmp_path)).get("/").text


def test_dollar_helper_never_receives_a_hash(tmp_path: Path) -> None:
    # $( est un alias de getElementById : il attend un id nu, jamais "#id".
    assert re.search(r'const \$ = \(id\) => document\.getElementById', _page(tmp_path))
    found = re.findall(r'\$\("#', _page(tmp_path))
    assert found == [], f"$( est getElementById, un id avec # renvoie null : {found}"


def test_counter_buttons_and_display_exist(tmp_path: Path) -> None:
    page = _page(tmp_path)
    for element in ("plus1", "plus5", "plus10", "minus", "count-display"):
        assert f'id="{element}"' in page, element
    assert "function updateCount" in page


def test_exact_number_stays_reachable_and_labelled(tmp_path: Path) -> None:
    # Le compteur ne remplace pas la saisie directe : le champ reste focusable,
    # sinon compter 37 voyageurs oblige à cliquer 37 fois et le label pointe
    # dans le vide.
    page = _page(tmp_path)
    found_input = re.search(r'<input id="passengers"[^>]*>', page)
    assert found_input, "le champ passengers doit exister"
    assert 'class="hidden"' not in found_input.group(0)
    assert 'for="passengers"' in page


def test_the_feedback_bubble_cannot_swallow_a_press(tmp_path: Path) -> None:
    # La bulle naît sous le doigt, en plein sur le bouton voisin. Si elle capte
    # le pointeur, le second appui ne part plus et le compte s'arrête — le
    # symptôme ressemble exactement à celui que la bulle corrige.
    page = _page(tmp_path)
    style = re.search(r"\.bulle \{(.*?)\}", page, re.S)
    assert style, "le style de la bulle doit exister"
    assert "pointer-events: none" in style.group(1)


def test_the_bubble_is_removed_even_without_an_animation(tmp_path: Path) -> None:
    # `animationend` ne se déclenche pas si l'animation n'a jamais démarré : onglet
    # en arrière-plan, motion réduit. Sans filet en temps, la bulle reste à
    # l'écran pour tout le reste du comptage.
    page = _page(tmp_path)
    assert re.search(r"animationend", page), "il faut retirer la bulle sur animationend"
    assert re.search(r"setTimeout\(\(\) => marqueur\.remove\(\)", page), (
        "il faut un filet en temps, l'animation peut ne pas démarrer"
    )
    assert "prefers-reduced-motion" in page, (
        "couper l'animation ne doit pas couper le retour visuel"
    )
