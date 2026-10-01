"""Le score d'un compte, calculé à la lecture.

**Ce n'est pas une colonne.** Un score en base devient faux dès que la formule
change, et il faudrait le recalculer — donc le migrer — à chaque réglage. Le plan
demande une « fonction de score unique, paramétrée » : c'est ce fichier.

**Ce que le score récompense**, dans l'ordre du plan : un relevé lisible à
l'échelle de la rame (`um`) avant tout, puis un serpent de charge, puis un
corridor qu'aucun relevé ne portait, puis un corridor qui n'a pas été vu depuis
longtemps. Ce qu'il ne récompense pas : la fidélité déclarée, parce qu'elle est
déclarée par celui qui compte et donc manipulable par construction ; et un relevé
redondant, parce que le même origine-destination par la même personne dans la
même journée est un relevé écrit deux fois.

**Les coefficients ne sont pas calibrés.** Ils sont lisibles, pas mesurés : la
calibration se fait sur la base réelle, avec `tools/calibrer_score.py`, et c'est
elle qui décide de ces nombres. Une distribution inventée ici ferait de
`/classement` une illustration du score plutôt que le score. C'est dit dans
`docs/projet.md` (phase 9) et dans la sortie de l'outil.

Un relevé anonyme n'a pas de compte : il compte dans les données et il n'entre
dans aucun score. Ce n'est pas un relevé raté.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class Coeff:
    """Les poids de la formule, tous les six, sans valeur par défaut cachée.

    Il n'y a pas de défaut : un appelant qui passe un objet a choisi ses six
    nombres. Un score calculé avec des poids oubliés est un score que personne ne
    peut reproduire, et donc personne ne peut calibreer.
    """

    base: float
    """Un relevé rapporté, quel qu'il soit."""

    um: float
    """En plus quand `perimetre` vaut `um` : la charge de la rame entière, donc
    comparable d'un train à l'autre."""

    serpent: float
    """En plus quand le relevé est un serpent de charge : il dit où la charge
    monte et où elle descend, là où un effectif unique ne dit qu'une valeur."""

    corridor_inedit: float
    """En plus quand le corridor n'avait jamais été porté par aucun relevé."""

    corridor_vieux: float
    """En plus quand le corridor existait mais n'avait pas été vu depuis
    `fenetre_jours`."""

    fenetre_jours: int
    """Ce que « pas vu depuis longtemps » veut dire, en jours. Une fenêtre, pas
    un booléen : « pas vu depuis longtemps » n'a pas de sens sans dire ce que
    « longtemps » vaut, et 30 jours est une convention à mesurer."""


# Les poids lus. Distincts là où le plan distingue, égaux là où il ne dit rien.
#
# PAS CALIBRÉS. `tools/calibrer_score.py` produit la distribution sur la base
# réelle, et c'est elle qui doit fixer ces nombres.
PAR_DEFAUT = Coeff(
    base=1,
    um=4,
    serpent=3,
    corridor_inedit=3,
    corridor_vieux=1,
    fenetre_jours=30,
)


def _instant(texte: str | None) -> datetime | None:
    """Un `created_at` en datetime aware, ou `None` si la colonne est illisible.

    `created_at` est un texte, donc rien n'en garantit le format. Un relevé dont
    la date ne se lit pas ne peut pas être daté, donc il ne peut être ni « inédit »
    ni « pas vu depuis » : il vaut son relevé, et rien de plus. Faire planter le
    classement pour une ligne mal formée serait bien pire que l'ignorer.
    """
    if not texte:
        return None
    try:
        moment = datetime.fromisoformat(texte)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


@dataclass
class _Un:
    """Un relevé de `saisie`, réduit à ce que la formule regarde.

    Deux colonnes et rien de plus : `perimetre` et `kind` ont déjà servi à
    calculer le poids, les garder ici ferait deux fois la même décision, et deux
    endroits où elle peut diverger.
    """

    compte: str
    couple: str
    moment: datetime | None
    poids: float
    inedit: float


def _releves(connection: sqlite3.Connection, coeff: Coeff) -> list[_Un]:
    """Tous les relevés rattachés, dans l'ordre où ils sont arrivés.

    L'ordre n'est pas un détail : « inédit » et « pas vu depuis » se définissent
    par rapport aux relevés *antérieurs*, donc il faut le temps et pas seulement la
    date du jour. Le tri est fait en SQL, par `created_at` puis `rowid` : deux
    lignes écrites dans la même seconde ont alors l'ordre réel d'insertion, donc
    la distribution ne change pas d'un import à l'autre. Trié par `client_id`, ce
    serait le compte qui déciderait du point d'inédit.

    La couverture se mesure sur **tous** les relevés de la base, pas sur ceux d'un
    compte : un corridor est inédit pour la base. Sans cela le premier à arriver
    le ferait et le suivant serait puni d'arriver après, ce qui récompense
    l'horaire autant que le travail.
    """
    lignes = connection.execute(
        "SELECT client_id, kind, perimetre, created_at, compte_id, "
        "origin_stop_id, destination_stop_id FROM saisie "
        "WHERE compte_id IS NOT NULL AND kind IN ('count', 'serpent') "
        # `created_at` est à la seconde près : deux relevés postés dans la même
        # seconde ont le même tri, et l'ordre alphabétique de `client_id`
        # déciderait alors du champion du point « inédit ». `rowid` est l'ordre
        # d'insertion, donc l'ordre réel des faits — et sur une égalité de
        # timestamp, c'est le seul ordre qui ne soit pas arbitraire.
        "ORDER BY created_at, rowid"
    ).fetchall()
    vus: dict[str, datetime | None] = {}
    releves = []
    for ligne in lignes:
        couple = f"{ligne['origin_stop_id']}>{ligne['destination_stop_id']}"
        moment = _instant(ligne["created_at"])
        poids = coeff.base
        if ligne["perimetre"] == "um":
            poids += coeff.um
        if ligne["kind"] == "serpent":
            poids += coeff.serpent
        inedit = 0.0
        # `couple in vus` et non `vus.get(couple)`. Un `.get()` confond « jamais
        # vu » et « vu, à une date illisible » : les deux rendent `None`, et le
        # relevé suivant réclamerait alors le point d'inédit sur un corridor
        # déjà couvert. Avec une seule date corrompue quelque part dans la base,
        # le classement couronnerait une couverture inventée — le pire mode de
        # défaillance, parce qu'il ne signale rien.
        if couple not in vus:
            # Jamais porté par aucun relevé : c'est ce que le plan appelle un
            # corridor inédit. Ce n'est pas un « pas vu depuis » déguisé, donc
            # les deux bonus ne s'additionnent pas.
            poids += coeff.corridor_inedit
            inedit = coeff.corridor_inedit
        else:
            precedent = vus[couple]
            # Le couple existe. L'écart ne se mesure que si les deux dates se
            # lisent : une date illisible vaut zéro point, pas le maximum.
            if (
                moment is not None
                and precedent is not None
                and moment - precedent >= timedelta(days=coeff.fenetre_jours)
            ):
                poids += coeff.corridor_vieux
        releves.append(
            _Un(
                compte=ligne["compte_id"],
                couple=couple,
                moment=moment,
                poids=poids,
                inedit=inedit,
            )
        )
        # Le couple est désormais couvert, quelle que soit la lisibilité de sa
        # date : c'est la couverture qui compte, pas le calendrier. Un relevé à
        # date illisible n'efface donc pas la bonne date d'un précédent, il ne
        # fait que ne pas pouvoir la remplacer.
        precedent = vus.get(couple)
        if couple not in vus or (
            moment is not None and (precedent is None or moment > precedent)
        ):
            vus[couple] = moment
    return releves


@dataclass
class Points:
    """Le score d'un compte, décomposé.

    La décomposition est gardée parce que la page doit dire d'où viennent les
    points : un score qu'on ne sait pas expliquer ne se calibre pas. `inedit` est
    la part du total qui vient des corridors qu'aucun relevé ne portait, et c'est
    la mesure que le plan demande pour le premier réglage des coefficients.
    """

    points: float
    releves: int
    paires: int
    inedit: float
    dernier: datetime | None
    pseudo: str


def _scores(connection: sqlite3.Connection, coeff: Coeff) -> dict[str, Points]:
    """Le score de chaque compte, en un passage sur `saisie`.

    Une requête et une boucle, pas une requête par compte : avec quelques milliers
    de relevés, une requête par compte est le N+1 que `docs/regles.md` §4
    interdit.

    La redondance est éliminée par groupe : un même couple par un même compte dans
    la même journée ne rapporte qu'une fois. La clé est (compte, couple, jour) et
    non le relevé, donc le doublon disparaît sans que l'ordre d'insertion décide
    du gagnant — et le groupe garde le **meilleur** de ses relevés, pas le premier
    arrivé : deux relevés du même jour dont un seul est à `um` ne doivent pas se
    départager sur l'ordre d'écriture.
    """
    releves = _releves(connection, coeff)
    pseudos = {
        ligne["id"]: ligne["pseudo"]
        for ligne in connection.execute("SELECT id, pseudo FROM compte")
    }
    groupes: dict[tuple, list[_Un]] = {}
    for releve in releves:
        jour = releve.moment.date().isoformat() if releve.moment else "?"
        groupes.setdefault((releve.compte, releve.couple, jour), []).append(releve)

    points: dict[str, Points] = {}
    for (compte, _couple, _jour), membres in groupes.items():
        meilleur = max(membres, key=lambda r: r.poids)
        entree = points.get(compte)
        if entree is None:
            entree = Points(0.0, 0, 0, 0.0, None, pseudos.get(compte, ""))
            points[compte] = entree
        entree.points += meilleur.poids
        entree.inedit += max(r.inedit for r in membres)
        entree.releves += len(membres)
        entree.paires += 1
        dernier = max((r.moment for r in membres if r.moment is not None), default=None)
        if dernier is not None and (entree.dernier is None or dernier > entree.dernier):
            entree.dernier = dernier

    return points


def _arrondir(valeur: float) -> float:
    """Un score affiché sans décimale inutile.

    Les poids sont entiers dans la configuration prévue, donc le score est entier
    dans le cas nominal ; l'arrondi ne sert qu'à ne pas afficher `3.0000000004`
    si quelqu'un calibre avec un coefficient fractionnaire.
    """
    return round(valeur, 2)


def classement(database: Path, coeff: Coeff = PAR_DEFAUT) -> list[dict]:
    """Les comptes classés, du meilleur au moins bon.

    Un compte sans relevé rattaché n'apparaît pas : il ne couvre rien, donc le
    classer revient à classer des vides. Le filtre est ici et pas dans le SQL —
    un compte vide a bien une ligne dans `compte`, c'est seulement `saisie` qui
    n'a rien à dire de lui.

    L'identifiant n'est jamais rendu, ni en repli du pseudo : `/classement` est
    une page publique, et y écrire l'id d'un compte sans pseudo serait la fuite la
    plus facile du projet — celle qu'on ne verrait qu'en ouvrant la page.
    """
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        tous = _scores(connection, coeff)
    lignes = [p for p in tous.values() if p.releves > 0]
    lignes.sort(key=lambda p: (-p.points, p.pseudo, -p.releves))
    return [
        {
            "pseudo": p.pseudo or "compte sans pseudo",
            "points": _arrondir(p.points),
            "releves": p.releves,
            "paires": p.paires,
            "inedit": _arrondir(p.inedit),
            "dernier": p.dernier.isoformat() if p.dernier else None,
        }
        for p in lignes
    ]


def score_de(database: Path, identifiant: str, coeff: Coeff = PAR_DEFAUT) -> Points:
    """Le score d'un compte précis, pour sa page d'historique.

    Jamais `None`. Un compte sans relevé a un score **zéro**, pas un score
    inconnu : la page doit pouvoir écrire « 0 point » sans teste à faire, et une
    fonction qui renvoie `None` pour « rien » oblige chaque appelant à re-poser la
    question que la base vient de lui répondre. `releves == 0` reste le seul test
    à faire, et il est exact.

    Coût : une passe complète sur `saisie`, comme `/classement`. C'est le même
    calcul, donc la même requête — l'écrire deux fois donnerait deux formules à
    maintenir, et la page `/compte` n'affiche pas le classement.
    """
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        tous = _scores(connection, coeff)
    entree = tous.get(identifiant)
    if entree is None:
        return Points(0.0, 0, 0, 0.0, None, "")
    return Points(
        points=_arrondir(entree.points),
        releves=entree.releves,
        paires=entree.paires,
        inedit=_arrondir(entree.inedit),
        dernier=entree.dernier,
        pseudo=entree.pseudo,
    )
