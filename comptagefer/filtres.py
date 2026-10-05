"""Les filtres de la liste de lecture, et la vue par paire de gares.

La vague 1 a rendu la liste lisible ; celle-ci la rend cherchable. Les
trois filtres sont des paramètres d'URL — `?depuis=&jusqu=&mode=&ligne=`
— pour la même raison que le tri l'est : une liste filtrée se partage,
se met en signet et se teste, et il n'y a pas de JavaScript dans une page
dont le JavaScript n'est jamais exécuté par la suite de tests.

Les décisions structurantes sont dans `docs/projet.md`, qui les explique
avec leurs raisons. Les trois qui dictent la forme du code :

**Les filtres sont faits en SQL, en une requête.** Pas en Python après
avoir tout chargé : à 5 000 relevés, la page `/comptages` doit pouvoir
dire « 200 sur 5 000 » sans avoir lu les 5 000 — et le total est compté
par un `COUNT(*)` distinct, pas déduit de la longueur de la liste
rendue. Le tri, lui, reste en Python : il ne coûte rien en mémoire et
il s'applique à des agrégats comme à des lignes.

**La vue par paire est un paramètre de plus, pas une page.** `?vue=paire`
sur la même URL garde le contexte de la liste, et une page de plus
serait un endroit de plus où les données divergent. Le regroupement
donne ce qu'aucune page ne donnait : la charge typique d'un corridor,
avec son effectif moyen, son minimum, son maximum et le nombre de
relevés sur lesquels ces trois chiffres sont calculés.

**Le tri par défaut de la vue par paire est le nombre de relevés
décroissant.** C'est plus honnête sur ce que la base sait : un corridor
en tête avec 40 relevés est mieux documenté qu'un corridor en tête avec
2, et mettre le second devant le premier répondrait à une autre
question — « le corridor le plus chargé », qui mélange ce que la base
sait et ce que la circulation fait.

Une règle tient partout : **un filtre qui vide la liste doit le dire et
proposer de l'enlever.** Une page vide sans explication se lit comme une
absence de données, pas comme un filtre.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Callable
from urllib.parse import urlencode

# Les modes de relevé, dans les mots que la page affiche déjà. Un filtre
# qui parle « count » alors que la colonne affiche « unique » oblige le
# lecteur à traduire ; on prend donc les mots de la colonne.
#
# « signalé » est un relevé d'offre manquante, pas une charge : il peut
# être filtré, mais il n'a pas d'effectif et n'entre dans aucun calcul
# de moyenne.
MODES = {"unique": "count", "serpent": "serpent", "signale": "missing"}

# Combien de relevés par page de liste.
#
# Ce nombre est mesuré, pas choisi. Avec 6 000 relevés synthétiques
# (`client.post` × 6 000 puis `GET /comptages`), la page rendait 2,38 Mo
# de HTML en 227 ms — personne n'ouvre ça, et le poids croît linéairement
# avec la base. À 200 par page, la même base donne 30 pages de 80 ko,
# ce qui tient sur un écran large en tableau et se parcourt au pouce.
#
# Une seule valeur pour les deux lectures, mobile et large : la feuille de
# style décide de la forme, pas du nombre de lignes. Une pagination qui
# diffère selon la largeur ferait qu'un signet partagé ouvrirait un
# contenu différent selon l'écran qui le lit.
PAR_PAGE = 200


@dataclass(frozen=True)
class Filtres:
    """Ce qu'on lit dans l'URL, une fois validé.

    Les champs sont déjà nettoyés : une date illisible ou une ligne
    inconnue ne devient pas une chaîne qui remonte dans la requête. Le
    champ `erreurs` porte ce qui a été écarté et pourquoi, parce que
    l'ignorer en silence ferait croire à un filtre qui n'a pas servi.
    """

    depuis: str = ""
    jusqu: str = ""
    mode: str = ""
    ligne: str = ""
    # Le nom de gare tapé dans le formulaire, mis dans la forme canonique
    # du catalogue. C'est un nom et non un `stop_id` parce que c'est ce que le
    # lecteur sait : « les comptages de Lyon », pas `StopArea:Lyon`. Le champ
    # `ligne` reste lisible dans l'URL — les anciens liens et signets — mais
    # n'est plus proposé dans le formulaire.
    gare: str = ""
    erreurs: tuple[str, ...] = ()

    def vide(self) -> bool:
        return not (self.depuis or self.jusqu or self.mode or self.ligne or self.gare)

    def etiquettes(self) -> list[str]:
        """Les filtres actifs, en français, pour les chips de la page."""
        morceaux = []
        if self.depuis or self.jusqu:
            if self.depuis and self.jusqu:
                morceaux.append(f"du {self.depuis} au {self.jusqu}")
            elif self.depuis:
                morceaux.append(f"depuis le {self.depuis}")
            else:
                morceaux.append(f"jusqu'au {self.jusqu}")
        if self.mode:
            morceaux.append(f"mode {self.mode}")
        if self.ligne:
            morceaux.append(f"ligne {self.ligne}")
        if self.gare:
            morceaux.append(f"gare {self.gare}")
        return morceaux


def _date_iso(valeur: str) -> str:
    """Une date `AAAA-MM-JJ` en borne SQL, ou une chaîne vide.

    On ne compare pas des chaînes : `created_at` est un ISO UTC
    (`2026-09-30T13:04:11+00:00`), donc « le 30 septembre » doit
    devenir `2026-09-30T00:00:00+00:00` et `2026-10-01T00:00:00+00:00`
    pour englober la journée entière. Un `LIKE '2026-09-30%'` ferait la
    même chose en plus court, mais il ne se Serrait pas sur l'index, et
    surtout il se tromperait pour un comptage fait à 1 h du matin heure
    de Paris — donc le 30 septembre à 23 h UTC la veille.
    """
    jour = datetime.strptime(valeur, "%Y-%m-%d")
    return (jour + timedelta(days=1)).replace(tzinfo=timezone.utc).isoformat()


def lire(
    depuis: str,
    jusqu: str,
    mode: str,
    ligne: str,
    gare: str = "",
    *,
    lignes_disponibles: Callable[[str], dict | None],
    gares_disponibles: Callable[[str], str | None] | None = None,
) -> Filtres:
    """Les filtres de l'URL, nettoyés.

    `lignes_disponibles` est injecté plutôt qu'importé : la résolution
    d'une ligne est une lecture du GTFS, donc un accès disque, et cette
    fonction doit rester testable sans base. Elle retourne la ligne
    trouvée ou `None`.

    Une valeur illisible est écartée **et signalée**. Le comportement
    par défaut d'une page web est de dies : un `?tri=bidon` retombe sur
    la date, un `?mode=inconnu` ne doit pas faire plus. Mais l'écarter
    en silence serait pire : le lecteur qui filtre par « laisse-passer »
    verrait la liste entière et croirait que son filtre n'a rien
    donné. On garde donc la raison de l'écart dans `erreurs`, et la page
    l'affiche à côté des filtres.
    """
    erreurs: list[str] = []

    date_debut = depuis.strip()
    if date_debut:
        try:
            datetime.strptime(date_debut, "%Y-%m-%d")
        except ValueError:
            erreurs.append(f"« {escape(date_debut)} » n'est pas une date (AAAA-MM-JJ), filtre ignoré.")
            date_debut = ""
    date_fin = jusqu.strip()
    if date_fin:
        try:
            datetime.strptime(date_fin, "%Y-%m-%d")
        except ValueError:
            erreurs.append(f"« {escape(date_fin)} » n'est pas une date (AAAA-MM-JJ), filtre ignoré.")
            date_fin = ""

    # Un intervalle à l'envers ne veut rien dire : on l'inverse plutôt que
    # de renvoyer zéro résultat, parce qu'un lecteur qui s'est trompé de
    # borne veut ses données, pas une page vide qui ne dit pas pourquoi.
    if date_debut and date_fin and date_debut > date_fin:
        erreurs.append("Intervalle inversé : les deux bornes ont été échangées.")
        date_debut, date_fin = date_fin, date_debut

    mode_propre = mode.strip()
    if mode_propre and mode_propre not in MODES:
        erreurs.append(
            f"« {escape(mode_propre)} » n'est pas un mode de relevé, filtre ignoré."
        )
        mode_propre = ""

    ligne_propre = ligne.strip()
    if ligne_propre:
        trouvee = lignes_disponibles(ligne_propre)
        if trouvee is None:
            erreurs.append(
                f"La ligne « {escape(ligne_propre)} » n'est pas dans le GTFS, filtre ignoré."
            )
            ligne_propre = ""
        else:
            ligne_propre = trouvee["route_id"]

    # La gare est résolue par son **nom**, comme la ligne l'est par son
    # `route_id` : ce que le lecteur tape, et non un identifiant interne.
    # `gares_disponibles` rend le nom canonique du catalogue, ou `None` si
    # aucune gare ne porte ce nom — donc le filtre est écarté **et dit**,
    # comme les autres.
    gare_propre = gare.strip()
    if gare_propre:
        trouvee = gares_disponibles(gare_propre) if gares_disponibles else gare_propre
        if not trouvee:
            erreurs.append(
                f"« {escape(gare_propre)} » n'est pas une gare du catalogue, filtre ignoré."
            )
            gare_propre = ""
        else:
            gare_propre = trouvee

    return Filtres(
        date_debut, date_fin, mode_propre, ligne_propre, gare_propre, tuple(erreurs)
    )


def conditions(
    filtres: Filtres,
    trips: frozenset[str] = frozenset(),
    gares: frozenset[str] = frozenset(),
) -> tuple[str, list]:
    """La clause `WHERE` du filtre, et ses paramètres.

    `trips` est l'ensemble des circulations de la ligne filtrée. Seul
    l'appelant peut le connaître : résoudre une ligne est une lecture
    du GTFS, et ce module ne touche à aucun fichier.

    Les colonnes sont nommées, jamais construites à partir d'une entrée
    navigateur : `?colonne=...` n'existe pas ici. Seules les valeurs sont
    paramétrées, et SQLite s'en charge.

    Le filtre « ligne » passe par `trip_id IN (...)`, comme
    `_saisies_de_ligne` : c'est le seul lien écrit au moment du
    comptage, et il dit la ligne exacte. Un comptage sans `trip_id`
    n'appartient à aucune ligne, donc à aucun résultat filtré — on ne
    lui invente pas une ligne, pas plus qu'on ne lui invente un
    effectif.

    `gares` est la **famille** d'identifiants de la gare filtrée : l'aire
    et ses quais. Le filtre ne s'arrête pas aux extrémités du comptage, il
    prend aussi les gares intermédiaires :

    - `legs` porte les arrêts du serpent, `trajet` le parcours complet figé
      au moment du comptage. Les deux sont du JSON, donc ils sont lus avec
      `json_each` et non avec un `LIKE` sur la colonne — une sous-chaîne
      trouverait un relevé parce que le nom d'une autre gare contient
      l'identifiant cherché. `COALESCE` parce que les deux colonnes sont
      nulles sur la plupart des relevés, et que `json_each(NULL)` n'existe
      pas ;
    - le nom de gare est aussi comparé aux colonnes `origin_name` /
      `destination_name`, qui sont du texte libre saisi au comptage : une
      gare hors catalogue s'y retrouve quand même. C'est aussi ce qui rend
      le filtre utile quand `stops.db` n'a pas été importé — la famille
      d'identifiants est alors vide, les noms suffisent.
    """
    morceaux: list[str] = []
    params: list = []

    if filtres.depuis:
        debut = datetime.strptime(filtres.depuis, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        morceaux.append("created_at >= ?")
        params.append(debut.isoformat())
    if filtres.jusqu:
        morceaux.append("created_at < ?")
        params.append(_date_iso(filtres.jusqu))
    if filtres.mode:
        morceaux.append("kind = ?")
        params.append(MODES[filtres.mode])
    if filtres.ligne:
        if not trips:
            # Aucune circulation pour cette ligne : la clause doit être
            # fausse, pas absente. Une clause absente afficherait la
            # liste entière, et la page semblerait ignorer le filtre.
            morceaux.append("1 = 0")
        else:
            morceaux.append(f"trip_id IN ({','.join('?' for _ in trips)})")
            params.extend(sorted(trips))
    if filtres.gare:
        marques = ",".join("?" for _ in gares) if gares else "NULL"
        # Quatre passages de la famille : les deux extrémités, les arrêts du
        # serpent, ceux du parcours figé. Les paramètres suivent l'ordre des
        # `?` de la chaîne, donc ils sont répétés dans le même ordre.
        # `NULL` comme liste vide n'est pas une astuce : un `IN (NULL)` est
        # toujours faux, donc le reste de la clause — les noms — décide
        # seul, ce qui est le comportement voulu quand le catalogue des
        # gares n'a pas été importé.
        morceaux.append(
            f"""(
    origin_stop_id IN ({marques})
    OR destination_stop_id IN ({marques})
    OR origin_name = ? COLLATE NOCASE
    OR destination_name = ? COLLATE NOCASE
    OR EXISTS (
        SELECT 1 FROM json_each(COALESCE(saisie.legs, '[]'))
        WHERE json_extract(value, '$.stop_id') IN ({marques})
    )
    OR EXISTS (
        SELECT 1 FROM json_each(COALESCE(saisie.trajet, '{{}}'), '$.arrets')
        WHERE json_extract(value, '$.stop_id') IN ({marques})
    )
)"""
        )
        famille = sorted(gares)
        params.extend(famille)
        params.extend(famille)
        params.append(filtres.gare)
        params.append(filtres.gare)
        params.extend(famille)
        params.extend(famille)

    return (" AND ".join(morceaux), params)


@dataclass(frozen=True)
class Paire:
    """Un corridor origine-destination, agrégé.

    Les champs `moyenne`, `minimum` et `maximum` sont `None` quand aucun
    relevé du corridor n'a d'effectif — un corridor de trains signalés
    est réel, il n'a simplement pas de charge mesurée. Écrire 0 les
    confondrait avec un corridor vide de voyageurs, qui est un autre
    défaut, mesuré celui-là.
    """

    origine: str
    destination: str
    releves: int
    avec_effectif: int
    moyenne: float | None
    minimum: int | None
    maximum: int | None
    premier: str = ""
    dernier: str = ""


def _nom(row: dict, cle: str) -> str:
    valeur = (row.get(cle) or "").strip()
    if valeur:
        return valeur
    # Un relevé sans nom de gare n'est pas un relevé sans origine : il
    # en a une, dont on ne connaît que l'identifiant. Écrire la vide
    # ferait lire « → Lyon » comme un départ de nulle part.
    return f"arrêt {row.get(cle + '_stop_id') or 'inconnu'}"


def par_paire(rows: list[dict]) -> list[Paire]:
    """Les comptages regroupés par paire origine-destination.

    Le nom de gare sert de clé, et non `stop_id`. C'est un choix
    discutable, et c'est le bon ici : deux `stop_id` qui portent le même
    nom sont la même gare pour un lecteur, et un corridor affiché en
    deux morceaux sous le même nom serait une absence d'information, pas
    une finesse. Le regroupement se fait donc sur ce que la page
    affiche.

    Un train signalé (`passengers IS NULL`) compte dans `releves` — c'est
    bien un relevé du corridor — mais pas dans les statistiques de charge
    : le compter comme 0 ferait passer « aucune mesure » pour « train
    vide », qui sont deux affirmations opposées.
    """
    groupes: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        cle = (_nom(row, "origin_name"), _nom(row, "destination_name"))
        groupes.setdefault(cle, []).append(row)

    paires = []
    for (origine, destination), members in groupes.items():
        effectifs = [m["passengers"] for m in members if m.get("passengers") is not None]
        dates = sorted(
            m["created_at"]
            for m in members
            if isinstance(m.get("created_at"), str) and m["created_at"]
        )
        paires.append(
            Paire(
                origine=origine,
                destination=destination,
                releves=len(members),
                avec_effectif=len(effectifs),
                moyenne=round(sum(effectifs) / len(effectifs), 1) if effectifs else None,
                minimum=min(effectifs) if effectifs else None,
                maximum=max(effectifs) if effectifs else None,
                premier=dates[0] if dates else "",
                dernier=dates[-1] if dates else "",
            )
        )
    return paires


def trier_paires(paires: list[Paire], tri: str, sens: str) -> list[Paire]:
    """L'ordre de la vue par paire.

    Le défaut est le nombre de relevés décroissant, voir la note de
    module. Les autres clés — moyenne, minimum, maximum — sont là parce
    qu'un lecteur qui cherche « le corridor le plus chargé » doit
    pouvoir le demander explicitement ; la moyenne n'est pas mise par
    défaut parce qu'elle répond à cette autre question.

    Les paires sans effectif vont en dernier dans les deux sens, pour
    la même raison que dans `_trier` : une moyenne absente n'est pas
    « la plus petite », et un `reverse=True` sur une valeur absente la
    remonterait en tête.
    """
    ranges = {
        "releves": lambda p: p.releves,
        "moyenne": lambda p: p.moyenne,
        "minimum": lambda p: p.minimum,
        "maximum": lambda p: p.maximum,
        "trajet": lambda p: f"{p.origine} {p.destination}".lower(),
    }
    cle = tri if tri in ranges else "releves"
    # Même règle que dans `_trier` : sans paramètre, l'ordre est le plus
    # grand d'abord ; un tri demandé sans sens part en croissant, parce
    # qu'un nom commence par A et qu'un effectif du plus petit au plus
    # grand sert à comparer.
    decroissant = sens == "desc" or (not sens and not tri)
    tries = sorted(paires, key=ranges[cle], reverse=decroissant)
    if cle == "releves":
        # Le nombre de relevés est toujours présent : rien à remettre en
        # fin de liste.
        return tries
    sans = [p for p in tries if ranges[cle](p) is None]
    avec = [p for p in tries if ranges[cle](p) is not None]
    return avec + sans


def parametres(filtres: Filtres) -> dict[str, str]:
    """Les filtres actifs, sous la forme d'un dictionnaire d'URL."""
    return {
        "depuis": filtres.depuis,
        "jusqu": filtres.jusqu,
        "mode": filtres.mode,
        "ligne": filtres.ligne,
        "gare": filtres.gare,
    }


def lien(filtres: Filtres, tri: str = "", sens: str = "", **surcharges) -> str:
    """Une URL `/comptages` qui garde les filtres de la page courante.

    Le point est là : `lien` prend l'état courant et le modifie, au lieu
    de reconstruire une URL à partir de rien. Reconstruire est
    exactement comment les filtres se perdent — chaque lien écrit à la
    main oublie le `ligne=` qu'il devait garder, et la page Filtrée
    devient la liste entière sans que rien ne le dise.

    Les surcharges sont le moyen de changer une seule chose : `vue=""`
    retire la vue par paire, `page=3` va à la page 3, `ligne=""` retire
    le filtre ligne. Une surcharge vide retire donc le paramètre, et
    c'est ce qui permet d'écrire le lien « tout enlever » sans le
    reconstruire à la main.
    """
    params = parametres(filtres)
    params["tri"] = tri
    params["sens"] = sens
    for cle, valeur in surcharges.items():
        if valeur:
            params[cle] = str(valeur)
        else:
            params.pop(cle, None)
    retenus = {k: v for k, v in params.items() if v}
    return f"/comptages?{urlencode(retenus)}" if retenus else "/comptages"
