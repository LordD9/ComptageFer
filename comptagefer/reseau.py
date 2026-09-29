"""Le réseau ferré, pour tracer un comptage le long de la voie réelle.

`carte.py` relie deux arrêts par un segment droit. C'est lisible, mais faux :
sur une carte de France, un Paris–Marseille traverse des villes qui ne sont
sur aucune ligne. Ce module donne à la carte le chemin réel entre deux points,
calculé par A* sur le graphe que fabrique `tools/build_reseau.py`.

Le graphe est un fichier du dépôt, pas une ressource téléchargée : la carte
n'a pas besoin du réseau pour fonctionner, et l'image n'embarque que des
données. Il est lu au premier tracé puis gardé en mémoire — c'est un cache de
fichier statique, pas un état de session, donc il n'a pas à expirer.

**Source et licence.** Le réseau vient du Cerema, sous Licence Etalab 2.0. Il ne
faut pas le confondre avec le GTFS national, qui vient de la SNCF en Licence
Ouverte 2.0 : deux organismes, deux licences, et le fichier comme la page
`/methode` citent la bonne. Le fichier produit porte `organisme` et `licence`
dans ses en-têtes, pour qu'une donnée détachée de ce dépôt reste attribuable.

Quatre décisions méritent leur pourquoi :

- Le fichier est chargé à la demande. `/` et `/comptages` n'ont aucune raison
  de charger 3 Mo de réseau ; seule la carte le demande.

- Un point s'accroche à un *point de voie*, pas à un nœud. Après contraction
  il ne reste que 2 625 nœuds, espacés de 4 km en moyenne : accrocher une gare
  au nœud le plus proche l'attacherait à une bifurcation, pas à la gare. Les
  arêtes portent leur géométrie, et c'est dessus qu'on accroche.

- Accroché en milieu d'arête, un trajet part vers l'une de ses deux extrémités,
  et ce n'est pas devinable. On tente donc les deux, pour les deux extrémités,
  et on garde le plus court : quatre A* par segment, quelques millisecondes,
  pour un tracé qui part du bon côté.

- Un point trop loin de toute voie est rendu tel quel, jamais accroché au
  réseau. Un arrêt sans voie à moins de 500 m est probablement une donnée
  fausse, et le relier quand même inventerait un trajet. Le tracé direct
  reste, et la page dit pourquoi.

- A* est borné par un budget de nœuds. Sur un graphe non connexe, une
  recherche sans fin ferait tenir la requête ouverte ; ici elle abandonne et
  retombe sur le segment droit, qui est au pire faux mais toujours dessiné.
"""

from __future__ import annotations

import heapq
import json
import math
from itertools import pairwise
from pathlib import Path

DONNEES = Path(__file__).parent / "data" / "reseau.json"

# Sous 46.5° N, un degré de longitude vaut 78 km. On prend cette valeur pour
# tous les calculs de distance, et l'erreur qui en découle — jusqu'à 8 % au
# nord du pays — reste très en dessous de ce qu'on mesure ici.
LATITUDE_REFERENCE = 46.5
METRES_PAR_DEGRE = 111_320.0
KX = METRES_PAR_DEGRE * math.cos(math.radians(LATITUDE_REFERENCE))

# Au-delà, le point n'est pas sur le réseau ferré et on ne le force pas.
DISTANCE_MAX_M = 500.0
# Taille de la grille de proximité, en mètres. Une case doit attraper l'arête
# voisine sans en contenir beaucoup ; à 200 m sur le réseau national, une case
# porte une ou deux arêtes.
CASE_M = 200.0
# Budget de nœuds pour un A*. Le graphe entier n'en compte que 2 625 : passer
# à 5 000 ne peut pas le borner réellement, et c'est voulu. À 2 000, un quart
# des trajets échouait alors que la composante principale couvre
# 93 % du réseau — la borne coupait des trajets qui existent. Elle ne protège
# plus que d'une boucle, ce qui suffit.
BUDGET_NOEUDS = 5_000
# Précision des coordonnées stockées, en décimales. Cinq font ~1 m, et c'est
# aussi l'unité du décodage des polylignes différentielles.
PRECISION = 5

_cache: Reseau | None = None


def _decode_trace(code: list[list[int]]) -> list[list[float]]:
    """La polyligne, des écarts aux coordonnées.

    L'inverse exact de `build_reseau._encode_trace` : un point est l'écart au
    précédent, en unités de 10^-5 degré. Le gain de poids vient de là, et il
    est tel qu'il permet de garder la géométrie entière — voir la note de
    module.
    """
    points = []
    x = y = 0
    for dx, dy in code:
        x += dx
        y += dy
        points.append([x / 10**PRECISION, y / 10**PRECISION])
    return points


class Reseau:
    """Le graphe de voie ferré, lu une fois et partagé."""

    def __init__(self, donnees: dict) -> None:
        self.sommets: list[list[float]] = donnees["sommets"]
        # La géométrie d'une arête, dans les deux sens. C'est elle qui fait que
        # le tracé suit les lacets de la voie : une arête ne porte pas sa
        # longueur seule.
        self.traces: dict[tuple[int, int], list[list[float]]] = {}
        self.longueurs: dict[tuple[int, int], float] = {}
        for depart, arrivee, longueur, code in donnees["aretes"]:
            trace = _decode_trace(code)
            self.traces[(depart, arrivee)] = trace
            self.traces[(arrivee, depart)] = trace[::-1]
            self.longueurs[(depart, arrivee)] = longueur
            self.longueurs[(arrivee, depart)] = longueur
        # Les voisins et leur longueur, pour l'A*.
        self.voisins: dict[int, list[tuple[int, float]]] = {}
        for (depart, arrivee), longueur in self.longueurs.items():
            self.voisins.setdefault(depart, []).append((arrivee, longueur))
        self._grille = self._construit_grille()

    def _construit_grille(self) -> dict[tuple[int, int], set[tuple[int, int]]]:
        """Les arêtes, indexées par case.

        Indexer les nœuds ne marche pas : après contraction il en reste 2 625,
        espacés de 4 km en moyenne, et une case de 200 m en attrape un sur
        deux mille. Les arêtes, elles, font 9,5 km de moyenne et couvrent des
        milliers de cases, donc toute gare en tombe dans une case occupée.
        """
        grille: dict[tuple[int, int], set[tuple[int, int]]] = {}
        for segment, trace in self.traces.items():
            for lon, lat in trace:
                case = (int(lon * KX / CASE_M), int(lat * METRES_PAR_DEGRE / CASE_M))
                grille.setdefault(case, set()).add(segment)
        return grille

    def _accroche(self, point: list[float]) -> tuple[int, int, int, float] | None:
        """L'arête et l'endroit où le point s'y rattache.

        Rend `(départ, arrivée, index, distance)`. L'index est la place du point
        le plus proche dans la polyligne de l'arête, et c'est lui qui permet de
        remonter vers l'une des deux extrémités.
        """
        lon, lat = point
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            # Un point aberrant donnerait `int(inf)`. Hors de cette plage il
            # n'y a pas de réseau : on rend None plutôt qu'une exception.
            return None
        cx, cy = int(lon * KX / CASE_M), int(lat * METRES_PAR_DEGRE / CASE_M)
        meilleur, ecart, depart, arrivee, index = None, float("inf"), 0, 0, -1
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for segment in self._grille.get((cx + dx, cy + dy), ()):
                    trace = self.traces[segment]
                    for position, (x, y) in enumerate(trace):
                        d = math.hypot((x - lon) * KX, (y - lat) * METRES_PAR_DEGRE)
                        if d < ecart:
                            ecart, index, meilleur = d, position, segment
                            depart, arrivee = segment
        if meilleur is None or ecart > DISTANCE_MAX_M:
            return None
        return depart, arrivee, index, ecart

    def _descend(self, point: list[float], accroche: tuple[int, int, int, float], vers_debut: bool) -> list[list[float]]:
        """Du point accroché à l'extrémité de l'arête, en restant sur la voie.

        En `vers_debut` on remonte la polyligne vers son premier point, sinon on
        la descend vers son dernier. Le point d'origine est mis en tête dans
        les deux cas : le marqueur de gare reste exactement sur la gare.
        """
        depart, arrivee, index, _ = accroche
        trace = self.traces[(depart, arrivee)]
        if vers_debut:
            # On ne garde pas le point de voie le plus proche : il est à plus
            # de 500 m de la gare, et le crochet qu'il dessinerait serait visible.
            chemin = [point] + [list(p) for p in reversed(trace[1 : index + 1])]
        else:
            chemin = [point] + [list(p) for p in trace[index + 1 :]]
        return chemin

    def _route(self, depart: int, arrivee: int) -> list[int] | None:
        """A*, ou None si le graphe ne relie pas les deux nœuds.

        L'heuristique est la distance à vol d'oiseau : elle ne surestime
        jamais, donc le premier chemin sorti du tas est bien le plus court, et
        la recherche reste centrée sur la direction de la cible au lieu de
        l'explorer en rond.
        """
        if depart == arrivee:
            return [depart]
        infini = float("inf")
        cout = [infini] * len(self.sommets)
        vu = [infini] * len(self.sommets)
        precedent = [-1] * len(self.sommets)
        lon0, lat0 = self.sommets[depart]
        lon1, lat1 = self.sommets[arrivee]
        initial = math.hypot((lon1 - lon0) * KX, (lat1 - lat0) * METRES_PAR_DEGRE)
        cout[depart] = 0.0
        vu[depart] = initial
        tas = [(initial, depart)]
        explorés = 0
        while tas:
            f, noeud = heapq.heappop(tas)
            if f > vu[noeud]:
                continue
            if noeud == arrivee:
                chemin = [arrivee]
                while chemin[-1] != depart:
                    chemin.append(precedent[chemin[-1]])
                return chemin[::-1]
            explorés += 1
            if explorés > BUDGET_NOEUDS:
                return None
            lon, lat = self.sommets[noeud]
            for voisin, longueur in self.voisins.get(noeud, ()):
                candidat = cout[noeud] + longueur
                if candidat >= cout[voisin]:
                    continue
                cout[voisin] = candidat
                precedent[voisin] = noeud
                vlon, vlat = self.sommets[voisin]
                f_candidat = candidat + math.hypot(
                    (vlon - lon) * KX, (vlat - lat) * METRES_PAR_DEGRE
                )
                if f_candidat < vu[voisin]:
                    vu[voisin] = f_candidat
                    heapq.heappush(tas, (f_candidat, voisin))
        return None

    def chemin(self, point_a: list[float], point_b: list[float]) -> list[list[float]] | None:
        """La voie réelle entre deux points, ou None si on ne peut pas.

        `point_a` et `point_b` sont des `[lon, lat]`. On rend une polyligne
        `[lon, lat]`, les deux points d'origine en tête et en queue.
        """
        accroche_a = self._accroche(point_a)
        accroche_b = self._accroche(point_b)
        if accroche_a is None or accroche_b is None:
            return None
        # Les deux extrémités de chaque arête d'accroche, avec le chemin pour
        # y monter. Quatre combinaisons, dont une bonne.
        options_a = [
            (accroche_a[0], self._descend(point_a, accroche_a, True)),
            (accroche_a[1], self._descend(point_a, accroche_a, False)),
        ]
        options_b = [
            (accroche_b[0], self._descend(point_b, accroche_b, True)),
            (accroche_b[1], self._descend(point_b, accroche_b, False)),
        ]
        meilleure, longueur = None, float("inf")
        for noeud_a, chemin_a in options_a:
            for noeud_b, chemin_b in options_b:
                suite = self._route(noeud_a, noeud_b)
                if suite is None:
                    continue
                if noeud_a == noeud_b and len(suite) == 1:
                    candidat = chemin_a + list(reversed(chemin_b[1:]))
                else:
                    milieu = []
                    for avant, apres in pairwise(suite):
                        milieu.extend(self.traces[(avant, apres)][1:])
                    candidat = chemin_a + milieu + list(reversed(chemin_b[1:]))
                cout = self._longueur(candidat)
                if cout < longueur:
                    meilleure, longueur = candidat, cout
        return meilleure

    def _longueur(self, points: list[list[float]]) -> float:
        return sum(
            math.hypot((b[0] - a[0]) * KX, (b[1] - a[1]) * METRES_PAR_DEGRE)
            for a, b in pairwise(points)
        )


def reseau() -> Reseau | None:
    """Le réseau, lu au premier appel. None si le fichier n'est pas là.

    Un fichier absent doit laisser la carte fonctionner en segment droit :
    c'est une dégradation, pas une panne. `/` et `/comptages` ne doivent pas
    non plus échouer parce qu'un fichier de données manque.
    """
    global _cache
    if _cache is None:
        if not DONNEES.exists():
            return None
        _cache = Reseau(json.loads(DONNEES.read_text()))
    return _cache
