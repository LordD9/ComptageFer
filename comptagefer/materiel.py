"""La liste fermée des formations TER, et le mot qui les compte.

Le matériel roulant n'est pas un texte libre. C'est lui qui donne le nombre de
voitures de la rame, donc le schéma sur lequel on compte voiture par voiture :
un libellé inventé rendrait ce schéma incalculable, et l'effectif publié
indéchiffrable. Une liste fermée rend la donnée calculable, ou absente — c'est
la même règle que `COMPOSITIONS` dans `app.py`.

Chaque entrée est une **formation**, pas un type : « AGC 3 caisses » et
« AGC 4 caisses » sont deux entrées, parce qu'elles ne se comptent pas sur le
même nombre de cases. La famille est là pour la recherche et l'auto-complétion,
jamais pour la donnée : elle n'est pas stockée.

Le mot compte aussi. Un AGC se compte par **caisse**, un Regio 2N ou une rame
tractée par **voiture**. C'est le vocabulaire de celui qui compte, et l'écrire
de travers sur le schéma fait douter du reste de l'écran.
"""

from dataclasses import dataclass
from unicodedata import combining, normalize

# Le mot qui désigne une unité élémentaire d'une formation. Deux valeurs
# seulement, parce que le français ferroviaire en a deux ici : les caisses
# d'une automotrice articulée, les voitures d'une rame à éléments distincts.
CAISSE = "caisse"
VOITURE = "voiture"


@dataclass(frozen=True)
class Formation:
    """Une formation TER, telle qu'elle se compte sur le quai.

    `voitures` est le nombre d'unités élémentaires — le nombre de cases du
    schéma — et `mot` dit comment on les appelle. Les deux sont figés : le
    schéma et la validation serveur lisent la même valeur, il n'y a pas de
    second endroit où le nombre de caisses d'un AGC serait écrit.
    """

    label: str
    famille: str
    voitures: int
    mot: str

    @property
    def pluriel(self) -> str:
        return self.mot + "s"


# Les formations courantes du TER français. Le nombre d'unités est celui de la
# version standard de la série ; les variantes qui changent de longueur ont
# leur propre entrée, c'est le principe de la liste.
FORMATIONS: tuple[Formation, ...] = (
    Formation("AGC 3 caisses", "AGC (X 76500, Z 27500, B 81500)", 3, CAISSE),
    Formation("AGC 4 caisses", "AGC (Z 27500, B 81500, B 82500)", 4, CAISSE),
    Formation("Régiolis 3 caisses", "Régiolis (Z 51500, B 83500)", 3, CAISSE),
    Formation("Régiolis 4 caisses", "Régiolis (Z 51500, B 83500)", 4, CAISSE),
    Formation("Régiolis 6 caisses", "Régiolis (Z 51500, B 83500)", 6, CAISSE),
    Formation("Regio 2N 7 voitures", "Regio 2N (Z 55500)", 7, VOITURE),
    Formation("Regio 2N 8 voitures", "Regio 2N (Z 55500)", 8, VOITURE),
    Formation("TER 2N NG 3 caisses", "TER 2N NG (Z 24500)", 3, CAISSE),
    Formation("TER 2N NG 4 voitures", "TER 2N NG (Z 26500)", 4, VOITURE),
    Formation("TER 2N NG 5 voitures", "TER 2N NG (Z 26500)", 5, VOITURE),
    Formation("TER 2N 2 voitures", "TER 2N première génération (Z 23500)", 2, VOITURE),
    Formation("Z TER 3 caisses", "Z TER (Z 21500)", 3, CAISSE),
    Formation("X TER 2 caisses", "X TER (X 72500)", 2, CAISSE),
    Formation("X TER 3 caisses", "X TER (X 72500)", 3, CAISSE),
    Formation("X 73500 1 caisse", "Autorail X 73500 / X 73900", 1, CAISSE),
    Formation("Z 20500 4 voitures", "Z 20500 (Transilien)", 4, VOITURE),
    Formation("Z 20500 5 voitures", "Z 20500 (Transilien)", 5, VOITURE),
    Formation("Z 20900 4 voitures", "Z 20900 (Transilien)", 4, VOITURE),
    Formation("Z 50000 7 voitures", "Francilien Z 50000 (Transilien)", 7, VOITURE),
    Formation("Z 50000 8 voitures", "Francilien Z 50000 (Transilien)", 8, VOITURE),
    Formation("Z 6400 4 caisses", "Z 6400 (Transilien)", 4, CAISSE),
)

PAR_LABELLE = {formation.label: formation for formation in FORMATIONS}


def _clef(texte: str) -> str:
    """Une forme comparable : sans accent, sans casse, sans espaces de trop.

    Le libellé est choisi dans une liste sur un téléphone, dans un train : la
    casse et les espaces multiples y arrivent tout seuls, et refuser « agc 3
    caisses » ferait perdre un comptage pour une raison qui n'est pas une
    information fausse. Les accents sont retirés de la **comparaison**
    seulement — le libellé stocké, lui, garde les siens.
    """
    decompose = normalize("NFKD", texte.strip().casefold())
    sans_accent = "".join(c for c in decompose if not combining(c))
    return " ".join(sans_accent.split())


_CLEFS = {_clef(label): label for label in PAR_LABELLE}


def normaliser(texte: object) -> str | None:
    """Le libellé canonique d'une formation, ou `None` si elle n'y est pas.

    `None` n'est pas une erreur ici : le matériel est facultatif, et un relevé
    sans matériel reste un relevé. C'est l'appelant qui décide si l'absence est
    légitime — voir `_materiel` dans `app.py`.
    """
    if not isinstance(texte, str):
        return None
    return _CLEFS.get(_clef(texte))


def formation(label: object) -> Formation | None:
    """La formation derrière un libellé, normalisé ou déjà canonique."""
    canonique = normaliser(label)
    return PAR_LABELLE.get(canonique) if canonique else None


def libelles() -> tuple[str, ...]:
    """Les libellés, dans l'ordre de la liste, pour le client."""
    return tuple(formation.label for formation in FORMATIONS)