"""Fabrique le graphe de voie ferrée que la carte utilise.

Le tracé ne peut pas être calculé à la volée depuis le GeoJSON brut : il fait
12 Mo, et le simplifier à chaque requête coûterait plus cher que la page
entière. Ce script le transforme une fois en un graphe compact, versionné dans
le dépôt, que `comptagefer/reseau.py` se contente de lire.

    python tools/build_reseau.py Reseau_Final_Traite.geojson

Il ne télécharge rien et n'écrit rien hors du fichier de sortie : la source
vient de l'habitant, et le fichier produit est ce qui est commité. Le relancer
doit donc donner le même fichier, sinon la carte change sans qu'on puisse dire
pourquoi.

**Source et licence.** Les deux GeoJSON viennent du Cerema, sous Licence
Etalab 2.0. Ils ne sont pas dans le dépôt : ils font 12 Mo, et le construire
est une opération manuelle. `comptagefer/data/reseau.json` est le résultat de
ce script, versionné. Le champ `source` du fichier écrit le nom du GeoJSON
d'origine, pour qu'on puisse remonter à la version exacte des données.

Trois choix font la qualité du graphe, et chacun a été mesuré sur le réseau
national plutôt que supposé :

- On ne garde que les lignes `EXPLOITE`. Une ligne fermée ne relie plus deux
  gares, et la faire passer pour une voie disponible serait faux.

- On fusionne les points à 20 m. Les sections d'une même ligne ne partagent
  pas leurs extrémités au mètre près dans la source ; sans fusion, 524
  composantes connexes et presque aucun trajet routable. À 20 m on retombe à
  73 composantes, dont une porte 95,7 % du réseau. Passer à 50 m n'apporte
  presque rien (95,8 %) et risque d'accoller deux voies distinctes : 20 m est
  le bon compromis, pas un chiffre arrondi.

- On contracte les chaînes de sommets de degré 2, en gardant la géométrie
  dans l'arête. 158 410 sommets deviennent 4 154 nœuds, et l'A* passe de
  116 ms à quelques millisecondes. La polyligne de chaque arête est conservée
  telle quelle : le tracé suit toujours la voie réelle, au simplifier près.
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from itertools import pairwise
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
SORTIE = RACINE / "comptagefer" / "data" / "reseau.json"

# Distance de fusion des sommets, en mètres. Voir la note du module.
FUSION_M = 20.0
# Précision des coordonnées conservées, en degrés. Cinq décimales font ~1 m,
# soit deux ordres de grandeur sous la fusion : arrondir plus fin ne
# changerait aucun nœud, arrondir plus grossier en fusionnerait.
PRECISION = 5

# Les distances sont calculées dans un repère local en mètres. Un plateau
# unique est faux sur 10 degrés de longitude : à 51° N un degré de longitude
# vaut 70 km, à 43° N 81 km. On prend la valeur médiane du réseau et l'erreur
# résiduelle, ~8 %, reste très en dessous de la fusion.
LATITUDE_REFERENCE = 46.5
METRES_PAR_DEGRE = 111_320.0
KX = METRES_PAR_DEGRE * math.cos(math.radians(LATITUDE_REFERENCE))


def _lignes_exploitees(source: Path) -> list[list[list[float]]]:
    """Les géométries `EXPLOITE`, MultiLineString déplié en LineString."""
    donnees = json.loads(source.read_text())
    lignes = []
    for feature in donnees["features"]:
        if feature["properties"].get("STATUT_MNE") != "EXPLOITE":
            continue
        geometrie = feature["geometry"]
        if geometrie["type"] == "LineString":
            lignes.append(geometrie["coordinates"])
        else:
            lignes.extend(geometrie["coordinates"])
    return lignes


def _carre(a: list[float], b: list[float]) -> float:
    """La distance au carré, dans le repère local en mètres."""
    return ((a[0] - b[0]) * KX) ** 2 + ((a[1] - b[1]) * METRES_PAR_DEGRE) ** 2


def _graphe_brut(lignes: list[list[list[float]]]) -> tuple[list[list[float]], list[list[tuple[int, float]]]]:
    """Les sommets fusionnés, et leurs arêtes.

    La fusion est gloutonne et dans l'ordre des lignes : le premier sommet
    arrivé fixe la position, les suivants s'y agrègent. C'est déterministe,
    donc deux exécutions donnent le même fichier.
    """
    sommets: list[list[float]] = []
    grille: dict[tuple[int, int], list[int]] = defaultdict(list)
    aretes: list[list[tuple[int, float]]] = []
    limite = FUSION_M * FUSION_M

    def ajoute(point: list[float]) -> int:
        cx = int(point[0] * KX / FUSION_M)
        cy = int(point[1] * METRES_PAR_DEGRE / FUSION_M)
        meilleur, plus_proche = None, limite
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for index in grille.get((cx + dx, cy + dy), ()):
                    carre = _carre(point, sommets[index])
                    if carre <= plus_proche:
                        plus_proche, meilleur = carre, index
        if meilleur is None:
            meilleur = len(sommets)
            sommets.append(point)
            aretes.append([])
            grille[(cx, cy)].append(meilleur)
        return meilleur

    for ligne in lignes:
        suite = [ajoute(point) for point in ligne]
        for avant, apres in pairwise(suite):
            if avant == apres:
                continue
            poids = _carre(sommets[avant], sommets[apres]) ** 0.5
            aretes[avant].append((apres, poids))
            aretes[apres].append((avant, poids))

    # Deux voies parallèles dont les deux extrémités se fondent dans les mêmes
    # sommets produisent la même arête deux fois. laissée telle quelle, elle
    # donne à un sommet un voisinage où le voisin est identique des deux côtés,
    # et la contraction bute dessus. On garde la plus courte des deux, qui est
    # la directe : même visuel, longueur cohérente avec la géométrie tracée.
    for index, voisins in enumerate(aretes):
        retenus: dict[int, float] = {}
        for voisin, poids in voisins:
            if voisin not in retenus or poids < retenus[voisin]:
                retenus[voisin] = poids
        aretes[index] = sorted(retenus.items())
    return sommets, aretes


def _contracte(
    sommets: list[list[float]], aretes: list[list[tuple[int, float]]]
) -> tuple[list[int], dict[tuple[int, int], list[int]]]:
    """Les chaînes de degré 2 en une arête, géométrie comprise.

    Un sommet de degré 2 n'apporte rien à la topologie : il est sur un segment
    d'une ligne, et le seul chemin possible passe par lui. On le retire du
    graphe de routage en gardant tous ses points dans l'arête, ce qui laisse le
    tracé identique.

    Les extrémités de chaîne et les bifurcations, elles, restent des nœuds :
    ce sont les seuls endroits où le chemin peut changer. Les arêtes sont
    rendues en `(nœud contracted, nœud contracted, sommets bruts du chemin)`.
    """
    degrees = [len(voisins) for voisins in aretes]
    reels = [i for i, degree in enumerate(degrees) if degree != 2]
    rang = {brut: index for index, brut in enumerate(reels)}
    contractees: dict[tuple[int, int], list[int]] = {}

    def suit(depart: int, voisin: int) -> list[int]:
        """Les sommets bruts du départ au prochain nœud réel, chaîne comprise."""
        chemin = [depart, voisin]
        precedent, courant = depart, voisin
        while degrees[courant] == 2:
            # Un sommet de degré 2 a exactement deux voisins : celui d'où on
            # vient, et l'autre.
            autres = [v for v, _ in aretes[courant] if v != precedent]
            precedent, courant = courant, autres[0]
            chemin.append(courant)
        return chemin

    for sommet in reels:
        for voisin, _ in aretes[sommet]:
            if degrees[voisin] != 2:
                # Un voisin déjà contracté est un nœud réel : l'arête est directe.
                chemin = [sommet, voisin]
            else:
                chemin = suit(sommet, voisin)
            cle = (rang[sommet], rang[chemin[-1]])
            if cle not in contractees:
                contractees[cle] = chemin
    return reels, contractees


def _encode_trace(points: list[list[float]]) -> list[list[int]]:
    """La polyligne en coordonnées différentielles, en unités de 10^-5 degré.

    Un point est stocké par son écart au précédent, pas en absolu. Sur une voie
    deux points voisins s'écartent de quelques mètres, là où une longitude
    absolue fait six chiffres : le fichier passe de 4,80 à 1,03 Mo compressé à
    géométrie identique. C'est cette économie qui rend la suppression de la
    simplification acceptable — et `reseau._decode_trace` est son exact
    inverse, testé des deux côtés.
    """
    code = []
    px = py = 0
    for lon, lat in points:
        ix, iy = round(lon * 10**PRECISION), round(lat * 10**PRECISION)
        code.append([ix - px, iy - py])
        px, py = ix, iy
    return code


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    source = Path(argv[1])
    lignes = _lignes_exploitees(source)
    sommets, aretes = _graphe_brut(lignes)
    reels, contractees = _contracte(sommets, aretes)

    sortie_sommets = [
        [round(sommets[i][0], PRECISION), round(sommets[i][1], PRECISION)] for i in reels
    ]
    sortie_aretes = []
    for (a, b), chemin in sorted(contractees.items()):
        # La géométrie n'est PAS simplifiée, et c'est un choix mesuré. Douglas-
        # Peucker à 20 m faisait passer l'accrochage des gares de 95 % à 75 % :
        # il efface les points des faisceaux, où la voie se sépare en
        # quelques rails écartés de quelques mètres, et une gare s'y retrouve
        # à plus de 500 m de toute polyligne conservée. La simplifier économise
        # 0,4 Mo… que l'encodage ci-dessous récupère autrement, et sans rien
        # perdre. Voir la note de module.
        trace = [sommets[i] for i in chemin]
        longueur = sum(_carre(u, v) ** 0.5 for u, v in pairwise(trace))
        # La longueur suit le chemin, pas la distance entre les deux bouts : sur
        # une voie en lacets, seule la première mène au bon sommet.
        sortie_aretes.append([a, b, round(longueur, 1), _encode_trace(trace)])

    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(
        json.dumps(
            {
                "source": source.name,
                "organisme": "Cerema",
                "licence": "Licence Etalab 2.0",
                "fusion_m": FUSION_M,
                "sommets": sortie_sommets,
                "aretes": sortie_aretes,
            },
            separators=(",", ":"),
        )
    )
    print(
        f"{len(lignes)} lignes, {len(sommets)} sommets bruts, "
        f"{len(sortie_sommets)} nœuds, {len(sortie_aretes)} arêtes, "
        f"{SORTIE.stat().st_size / 1e6:.1f} Mo"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
