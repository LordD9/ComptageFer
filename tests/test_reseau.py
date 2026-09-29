"""Le réseau ferré : ce qu'il sait relier, et ce qu'il avoue ne pas savoir.

Ces tests verrouillent le contrat de `reseau.py` :

- un trajet entre deux gares du réseau suit la voie, il ne coupe pas au
  travers du pays ;
- un point hors réseau ne se fait pas accrocher à la voie la plus proche,
  même si elle est à quelques centaines de mètres ;
- un trajet impossible rend `None` plutôt qu'une polyligne inventée, et c'est
  ce `None` qui fait que la carte garde son segment droit et le dit.

Le réseau est une donnée versionnée dans le dépôt. Ces tests ne le
reconstruisent pas : ils le lisent. `tools/build_reseau.py` le fabrique, et il
faut le relancer quand les données Cerema changent. Une propriété du fichier
livré est donc testée ici, pas la fabrication : c'est le script de fabrication
qui porte ses propres tests.
"""

import json
import math
from itertools import pairwise

from comptagefer import reseau as module
from comptagefer.reseau import BUDGET_NOEUDS, DISTANCE_MAX_M, Reseau

# Deux gares bien connues du réseau principal, et le trajet qu'elles ont.
# Coordonnées en [lon, lat], prises dans `gares.merged.geojson` (Cerema) : les
# écrire de mémoire fait inverser la longitude, et le test échoue alors pour
# une raison qui n'a rien à voir avec le code.
BORDEAUX = [-0.556697, 44.825873]  # Saint-Jean
NANTES = [-1.542357, 47.216148]
# Un point en pleine campagne, loin de toute voie.
RURAL = [3.1, 46.9]


def test_the_shipped_network_is_readable_and_usable():
    """Sans ce fichier, la carte retombe en segment droit.

    C'est une dégradation acceptable, mais elle ne doit pas être l'état
    normal du dépôt : le fichier est versionné avec le code.
    """
    r = module.reseau()
    assert r is not None, "comptagefer/data/reseau.json est absent ou illisible"
    assert len(r.sommets) > 1000, "le réseau livré ne ressemble pas au réseau national"
    assert r.traces, "le réseau n'a aucune arête"


def test_the_route_between_two_stations_follows_the_track():
    """Un Bordeaux–Nantes passe par le sud-ouest, pas en ligne droite.

    Une droite Bordeaux–Nantes couperait les Landes, ce qui se verrait
    immédiatement. Le test compare donc la longueur routée à la distance à
    vol d'oiseau : sur ce trajet, elle est de l'ordre de 1,3.
    """
    r = module.reseau()
    assert r is not None
    trace = r.chemin(BORDEAUX, NANTES)
    assert trace is not None, "le réseau devrait relier Bordeaux à Nantes"
    assert len(trace) > 2, "un routage qui ne passe que par ses extrémités ne suit pas la voie"
    routée = _longueur(trace)
    directe = _distance(BORDEAUX, NANTES)
    assert routée > directe, "un trajet ferroviaire ne peut pas être plus court que la droite"
    assert routée < directe * 1.6, f"trajet de {routée / 1000:.0f} km pour {directe / 1000:.0f} km"


def test_a_point_far_from_any_track_is_not_snapped_to_it():
    """Un point en campagne n'est pas rattaché à la voie la plus proche.

    Sans cette borne, la carte tirerait un segment depuis une gare de l'Ter
    jusqu'à un champ où l'on a posé un point, ce qui serait un faux tracé.
    """
    r = module.reseau()
    assert r is not None
    assert r._accroche(RURAL) is None
    assert r.chemin(RURAL, BORDEAUX) is None


def test_what_snaps_is_always_within_the_limit():
    """Ce qui s'accroche est à moins de la borne, par construction."""
    r = module.reseau()
    assert r is not None
    accroche = r._accroche(BORDEAUX)
    assert accroche is not None, "une gare du réseau doit s'accrocher"
    _, _, _, distance = accroche
    assert distance <= DISTANCE_MAX_M


def test_an_unreachable_pair_returns_none_rather_than_a_made_up_path():
    """Pas de chemin veut dire None, pas une polyligne au hasard.

    C'est ce contrat qui permet à la carte de distinguer un tracé qui suit la
    voie d'un segment droit, et de l'écrire en bas de page.
    """
    r = module.reseau()
    assert r is not None
    # Un point hors d'Europe n'a aucune voie, il ne rend donc pas de chemin.
    assert r.chemin([0.0, 0.0], BORDEAUX) is None


def test_the_route_starts_and_ends_on_the_two_stations():
    """Le tracé passe par les deux gares, à leur position exacte.

    L'accroche au réseau rapproche le point d'une voie, mais le tracé doit
    quand même revenir à la gare : c'est elle qu'on a comptée.
    """
    r = module.reseau()
    assert r is not None
    trace = r.chemin(BORDEAUX, NANTES)
    assert trace is not None
    assert _proche(trace[0], BORDEAUX, 2000.0), "le tracé ne part pas de la gare"
    assert _proche(trace[-1], NANTES, 2000.0), "le tracé n'arrive pas à la gare"


def test_a_round_trip_does_not_take_the_long_way_round():
    """Un A* mal réglé peut trouver un retour plus court qu'un aller.

    Il longerait alors la ligne par l'autre côté du pays, et la carte
    dessinerait un voyageur passé par Valence. L'écart toléré est de 0,1 % :
    à cette échelle il vient des crochets d'accroche aux gares, qui diffèrent
    selon le côté par lequel on entre sur la voie — pas d'un détour.
    """
    r = module.reseau()
    assert r is not None
    aller = r.chemin(BORDEAUX, NANTES)
    retour = r.chemin(NANTES, BORDEAUX)
    assert aller is not None and retour is not None
    aller_longueur, retour_longueur = _longueur(aller), _longueur(retour)
    ecart = abs(aller_longueur - retour_longueur) / aller_longueur
    assert ecart < 0.001, (
        f"l'aller fait {aller_longueur / 1000:.0f} km, le retour "
        f"{retour_longueur / 1000:.0f} km : le routage n'est pas symétrique"
    )


def test_the_budget_cannot_cut_a_path_that_exists():
    """La borne ne doit pas faire échouer un trajet réel.

    Le graphe fait 2 625 nœuds et le budget 5 000 : la borne protège d'une
    boucle, elle ne borne pas le graphe. Si elle tombait sous la taille du
    réseau, un quart des trajets échouerait alors qu'ils existent.
    """
    r = module.reseau()
    assert r is not None
    assert BUDGET_NOEUDS > len(r.sommets)


def _delta(points) -> list[list[int]]:
    from comptagefer.reseau import PRECISION

    code = []
    x = y = 0
    for lon, lat in points:
        ix, iy = round(lon * 10**PRECISION), round(lat * 10**PRECISION)
        code.append([ix - x, iy - y])
        x, y = ix, iy
    return code


def test_a_hand_made_graph_routes_around_the_corner():
    """Le type se teste sans dépendre du fichier de 2 Mo.

    Trois points en angle droit : le routage doit passer par le coude, pas par
    la diagonale entre les deux extrémités.
    """
    coin = [[0.0, 0.0], [0.1, 0.0], [0.1, 0.1]]
    r = Reseau(
        {
            "sommets": coin,
            "aretes": [
                [0, 1, 7020.0, _delta(coin[:2])],
                [1, 2, 7020.0, _delta(coin[1:])],
            ],
        }
    )
    trace = r.chemin([0.0, 0.0], [0.1, 0.1])
    assert trace is not None
    # Trois points : les deux bouts et le coude. Une diagonale n'en aurait
    # que deux, et le test verrait la carte inventer un raccourci.
    assert len(trace) == 3
    assert abs(trace[1][0] - 0.1) < 1e-4 and abs(trace[1][1]) < 1e-4


def test_a_hand_made_graph_returns_none_when_it_cannot_connect():
    """Deux morceaux de réseau sans pont entre eux ne donnent aucun chemin.

    C'est le contrat qui permet à la carte de dire « segment droit » au lieu
    d'inventer un trajet. Sans pont, il n'y a pas de trajet.
    """
    r = Reseau(
        {
            "sommets": [[0.0, 0.0], [0.1, 0.0], [9.0, 9.0], [9.1, 9.0]],
            "aretes": [
                [0, 1, 7020.0, _delta([[0.0, 0.0], [0.1, 0.0]])],
                [2, 3, 7020.0, _delta([[9.0, 9.0], [9.1, 9.0]])],
            ],
        }
    )
    assert r.chemin([0.0, 0.0], [9.1, 9.0]) is None


def test_the_module_falls_back_to_none_when_the_file_is_absent(tmp_path, monkeypatch):
    """Sans fichier réseau, la carte doit pouvoir fonctionner en segment droit.

    Ce n'est pas un cas théorique : une installation à jour du code mais pas
    des données ne doit pas casser la page.
    """
    monkeypatch.setattr(module, "DONNEES", tmp_path / "absent.json")
    monkeypatch.setattr(module, "_cache", None)
    assert module.reseau() is None


def test_the_module_reads_the_file_once_and_shares_it(tmp_path, monkeypatch):
    """Deux appels rendent le même objet, pas deux lectures de 2 Mo."""
    fichier = tmp_path / "reseau.json"
    fichier.write_text(json.dumps({"sommets": [[0.0, 0.0]], "aretes": []}))
    monkeypatch.setattr(module, "DONNEES", fichier)
    monkeypatch.setattr(module, "_cache", None)
    assert module.reseau() is module.reseau()


def _distance(a: list[float], b: list[float]) -> float:
    """La distance à vol d'oiseau, en mètres, au même repère que le module."""
    return math.hypot(
        (a[0] - b[0]) * 111320.0 * math.cos(math.radians(a[1])),
        (a[1] - b[1]) * 111320.0,
    )


def _longueur(points: list[list[float]]) -> float:
    return sum(_distance(a, b) for a, b in pairwise(points))


def _proche(point: list[float], autre: list[float], tolerance_m: float) -> bool:
    return _distance(point, autre) <= tolerance_m
