"""La charge à bord, et la courbe qu'elle donne.

Ces tests portent sur du SVG écrit par le serveur. C'est le seul moyen que la
courbe soit relue : dans du JavaScript, elle ne serait vérifiée que par un
navigateur, et une courbe qui s'affiche faux ne casse rien.

Les cas qui comptent :

- un **comptage unique** donne une charge constante sur tout son OD, parce
  que c'est ce qu'un comptage unique veut dire — un nombre pour le trajet ;
- une **descente non relevée** arrête la courbe et nomme l'arrêt, au lieu de
  laisser une valeur inventée jusqu'au bout ;
- la courbe **part de zéro**, parce qu'un axe tronqué double les écarts ;
- un nom de gare ne peut pas injecter de balise, comme partout ailleurs.
"""

from comptagefer.carte import _charge
from comptagefer.profil import LARGEUR, profil_svg


def _serpent(*legs) -> dict:
    return {
        "client_id": "jeton",
        "kind": "serpent",
        "origin_stop_id": "A",
        "destination_stop_id": "D",
        "passengers": 10,
        "legs": list(legs),
    }


def test_a_single_count_is_flat_over_the_whole_pair():
    """Un comptage unique mesure un nombre, pour tout le trajet.

    Ce n'est pas une droite tracée entre deux points par hasard : c'est ce
    que l'observation veut dire. Une courbe qui monterait de zéro à la
    valeur inventerait une montée que personne n'a comptée.
    """
    charge, incomplet = _charge(
        {"kind": "count", "passengers": 40, "legs": None}
    )
    assert charge == [40, 40]
    assert incomplet is None


def test_a_snake_accumulates_boardings_and_alightings():
    charge, incomplet = _charge(
        _serpent(
            {"stop_id": "A", "stop_name": "Lyon", "onboard": 40},
            {"stop_id": "B", "stop_name": "Vienne", "boarded": 3, "alighted": 1},
            {"stop_id": "C", "stop_name": "Valence", "boarded": 0, "alighted": 9},
        )
    )
    # 40, puis 40 - 1 + 3, puis 42 - 9 + 0.
    assert charge == [40, 42, 33]
    assert incomplet is None


def test_a_missing_alighting_stops_the_curve_and_names_the_stop():
    """Le voyageur ne compte pas sa propre sortie : la fin est inconnue.

    Le piège serait de prolonger la courbe jusqu'à la dernière gare
    comme si tout le monde était descendu au milieu. Le test verrouille que
    la courbe s'arrête ET que la page le dit.
    """
    charge, incomplet = _charge(
        _serpent(
            {"stop_id": "A", "stop_name": "Lyon", "onboard": 40},
            {"stop_id": "B", "stop_name": "Vienne", "boarded": 3, "alighted": 1},
            {"stop_id": "C", "stop_name": "Valence", "boarded": 0},
        )
    )
    assert charge == [40, 42], "la courbe ne doit pas continuer après une descente inconnue"
    assert incomplet == "Valence"


def test_a_snake_without_a_starting_number_draws_nothing():
    charge, incomplet = _charge(
        _serpent({"stop_id": "A", "stop_name": "Lyon"}, {"stop_id": "B", "boarded": 1, "alighted": 0})
    )
    assert charge is None, "sans nombre à bord au départ, il n'y a pas de première valeur"
    assert incomplet is None


def test_a_reported_train_has_no_curve_at_all():
    charge, _ = _charge({"kind": "missing", "passengers": None, "legs": None})
    assert charge is None, "un train signalé n'a pas d'effectif à dessiner"


def test_the_curve_starts_at_zero():
    """L'axe vertical part de zéro, ou un passager ressemble à dix.

    Une hauteur de 43 voyageurs dessinée dans une boîte qui démarre au maximum
    de 43 double tous les écarts.

    L'axe du SVG descend : plus y est grand, plus la valeur est basse. La
    charge décroît ici de 40 à 33, donc l'ordonnée doit *croître* de la
    première à la dernière gare — s'il en était autrement, l'axe serait
    inversé et une montée se lirait comme une descente.
    """
    svg = profil_svg([40, 42, 33], ["Lyon", "Vienne", "Valence"], "#000")
    points = svg.split('<polyline points="')[1].split('"')[0].split()
    assert len(points) == 3, "trois arrêts, trois points sur la courbe"
    premier = int(points[0].split(",")[1])
    dernier = int(points[-1].split(",")[1])
    assert premier < dernier, (
        f"40 voyageurs ({premier}) puis 33 ({dernier}) : l'ordonnée doit monter quand la charge baisse"
    )
    # Le sommet de la courbe ne touche pas le bord haut de la boîte : le
    # plafond est arrondi au pas de 10, donc 42 se dessine sous un plafond de
    # 50 et le haut du dessin porte de la marge.
    sommet = min(int(p.split(",")[1]) for p in points)
    assert sommet > 10, f"le sommet touche le bord haut de la boîte (y={sommet})"
    # Et l'axe part bien de zéro : un repère est tracé sur toute la hauteur.
    assert ">0</text>" in svg, "le bas de l'axe doit porter 0"


def test_an_empty_peak_does_not_divide_by_zero():
    """Un train vide n'a pas de courbe cassée : il a une courbe plate."""
    svg = profil_svg([0, 0], ["Lyon", "Valence"], "#000")
    assert svg, "un serpent vide reste dessinable"
    assert '<polyline points="' in svg


def test_a_single_point_is_drawn_in_the_middle():
    svg = profil_svg([40], ["Lyon"], "#000")
    assert '<circle' in svg
    assert "Lyon" in svg


def test_the_curve_describes_itself_for_a_screen_reader():
    svg = profil_svg([40, 42], ["Lyon", "Vienne"], "#000", incomplet="Valence")
    assert 'role="img"' in svg, "un graphique sans rôle n'est pas une image, c'est un trou"
    assert "<title>" in svg and "<desc>" in svg
    # Le desc porte les mêmes nombres que la courbe : c'est ce qu'un lecteur
    # d'écran entend à la place du dessin.
    assert "Lyon 40" in svg and "Vienne 42" in svg
    assert "Valence" in svg


def test_a_stop_name_cannot_inject_markup_into_the_curve():
    """Les noms viennent de la base : ils sont échappés, comme partout ailleurs.

    Un nom de gare contenant `</svg>` ou `<script>` fermerait la balise et
    la suite deviendrait exécutable. Le test compte les balises fermantes,
    parce que c'est la fermeture qui rend l'injection exécutable.
    """
    svg = profil_svg(
        [40, 42],
        ["Lyon </svg><script>alert(1)</script>", "Vienne"],
        "#000",
    )
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
    assert svg.count("</svg>") == svg.count("<svg")