"""La carte des comptages.

Elle sert à lire, pas à saisir. Les tracés suivent la voie ferrée réelle
entre les arrêts d'une saisie, calculée par `reseau.py` ; quand le réseau ne
sait pas relier deux arrêts, le segment droit reste, et la page le dit.

Le JS vit ici comme dans `page.py` — une chaîne HTML. C'est pour ça que
`tests/test_browser.py` ouvre `/carte` dans un vrai Chromium : sans lui, une
faute dans ce script ne se voit pas.

Leaflet et les tuiles viennent d'un tiers. C'est un choix assumé : le projet
n'héberge pas de serveur de tuiles, et la carte sert sur le réseau, alors que
la saisie, elle, fonctionne hors ligne. Quand la bibliothèque ne se charge
pas, la page doit le dire et rester lisible — pas laisser un cadre vide.
"""

import json
from html import escape
from pathlib import Path

from comptagefer.affichage import chrome

LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
# Fond de plan. Les tuiles raster officielles d'OpenStreetMap ne demandent
# aucune clé ni compte, contrairement à Géoplateforme et aux services de
# tuiles français qui en réclament un. Le coût, c'est la politique d'usage :
# une petite application comme celle-ci passe, un trafic massif non. Le
# fournisseur est une constante, donc le changer tient en une ligne si un jour
# cette limite devient un problème.
TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_ATTRIBUTION = (
    '&copy; les contributeurs <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
)
TILE_SUBDOMAINS = "abc"
TILE_MAX_ZOOM = 19

# Une couleur par mode de saisie, lisible sur fond clair. Le type de train
# n'entre pas dans la couleur : deux comptages du même segment se superposent,
# et c'est le mode qui distingue une observation d'un profil.
COULEURS = {"count": "#1c1915", "serpent": "#1c6b4f", "missing": "#8a2b1b"}
KIND_FR = {"count": "comptage unique", "serpent": "serpent", "missing": "train signalé"}


def counted_features(stops_database: Path, rows: list[dict]) -> list[dict]:
    """Un tracé par saisie, avec les points qu'on sait placer.

    Une saisie dont on n'a pas les coordonnées est omise, pas dessinée au
    hasard : `map_page` compare les deux longueurs et l'écrit en bas de page.

    Les noms viennent de la base et peuvent contenir « < » ou « & » : ils sont
    échappés ici, une fois, et la page comme le script s'en servent tels quels.
    """
    places = _coordinates(stops_database)
    reseau = _reseau()
    features = []
    for row in rows:
        arretees = _trace(row)
        points = []
        placables = []
        for stop_id, name in arretees:
            place = places.get(stop_id)
            if place is not None:
                points.append([place[1], place[0]])  # [lon, lat] comme le réseau
                # Les deux versions sont gardées : `places` part dans le
                # <script> et doit être échappé, `noms_bruts` part dans la
                # courbe de charge, qui l'échappe de son côté. Échapper deux
                # fois afficherait « R&amp;D » à l'écran.
                placables.append(name or stop_id)
        if len(points) < 2:
            continue
        trace, droite = _trace_reseau(points, reseau)
        kind = row.get("kind") or "count"
        # La charge est calculée en même temps que les points, parce que les
        # deux portent la même liste d'arrêts. Les calculer séparément
        # reviendrait à supposer que tout ce qu'on sait placer est aussi ce
        # qu'on sait nommer sur la courbe, et c'est faux : une gare sans
        # coordonnées a une valeur mais pas de place sur le parcours.
        charge, incomplet = _charge(row, places)
        features.append(
            {
                # client_id est échappé comme les autres : il vient du client et
                # atterrit dans le même <script> que le reste. Un `</script>`
                # dedans fermait la balise, et la suite était exécutée.
                "client_id": escape(str(row["client_id"])),
                "kind": kind,
                "kind_fr": KIND_FR.get(kind, kind),
                "couleur": COULEURS.get(kind, "#5c554b"),
                "passengers": row.get("passengers"),
                "pseudo": escape(str(row.get("pseudo") or "anonyme")),
                "origine": escape(str(row.get("origin_name") or arretees[0][1])),
                "destination": escape(str(row.get("destination_name") or arretees[-1][1])),
                "stops": [escape(str(name or stop_id)) for stop_id, name in arretees],
                # `noms_bruts` sont les arrêts qu'on sait poser, dans le
                # même ordre que `points`. Ce sont eux qui nomment l'axe de
                # la courbe, et ils sont bruts : le script écrit le texte par
                # `textContent`, qui n'interprète rien, donc un nom contenant
                # « & » s'affiche tel quel. Une gare sans coordonnées n'a pas
                # de place sur le parcours, et lui en donner une ferait
                # avancer la charge d'une gare.
                "noms_bruts": [str(nom) for nom in placables],
                # `points` garde la position des arrêts, entiers compris : les
                # marqueurs de gare doivent tomber sur la gare, pas sur le
                # point de voie le plus proche.
                "points": [[round(lat, 5), round(lon, 5)] for lon, lat in points],
                "trace": [[round(lat, 5), round(lon, 5)] for lon, lat in trace],
                "droite": droite,
                # `charge` est la charge à bord, arrêt par arrêt. Elle est
                # calculée en Python, et non lissée en JavaScript : c'est le
                # seul endroit où une absence de données se voit — une
                # descente non relevée arrête la courbe au lieu de la
                # compléter par un zéro. `incomplet_brut` dit où elle
                # s'arrête, et il est brut pour la même raison que
                # `noms_bruts`.
                "charge": charge,
                "incomplet_brut": str(incomplet) if incomplet else "",
            }
        )
    return features


def _charge(row: dict, places: dict | None = None) -> tuple[list[int] | None, str | None]:
    """La charge à bord, arrêt par arrêt, et l'arrêt où elle n'est plus connue.

    Deux formes, parce que les deux modes de saisie ne mesurent pas la même
    chose :

    - **comptage unique** : un nombre, pour tout le trajet. La charge est
      donc constante entre les deux gares — deux points de même valeur. Ce
      n'est pas une interpolation, c'est ce que l'observation veut dire.
    - **serpent** : des montées et des descentes à chaque arrêt, donc une
      charge qui varie. On ne comble rien : une descente non relevée arrête
      la courbe et nomme l'arrêt, plutôt que de tracer une valeur inventée.

    Renvoie `(None, None)` quand il n'y a rien à dessiner : un train signalé
    n'a pas d'effectif, et un serpent sans nombre à bord au départ n'a pas de
    première valeur.

    `places` est le stop_id -> position. Il n'est pas décoratif : sans lui,
    la courbe garderait une valeur par arrêt de la saisie alors que le tracé
    n'en a qu'un par arrêt **posable**. Les deux listes se décaleraient d'une
    gare, et le maximum serait annoncé à la mauvaise station. Les montées et
    descentes d'une gare non posable restent donc dans le calcul de la
    charge — le train est bien passé par là — mais ne reçoivent pas de point.
    """
    kind = row.get("kind") or "count"
    legs = row.get("legs")
    if kind == "serpent" and isinstance(legs, list) and legs:
        valeurs: list[int] = []
        aboard = legs[0].get("onboard")
        if not isinstance(aboard, int):
            return None, None
        # La valeur du premier arrêt n'a pas de point antérieur pour la
        # qualifier : elle n'est notée que si l'arrêt est posable.
        if _posable(legs[0], places):
            valeurs.append(aboard)
        for leg in legs[1:]:
            boarded = leg.get("boarded")
            alighted = leg.get("alighted")
            # Une descente manquante, y compris à l'arrivée — le voyageur ne
            # compte pas sa propre sortie — laisse la charge suivante inconnue.
            # On s'arrête là et on le dit, plutôt que de faire comme si
            # personne n'était descendu.
            if not isinstance(boarded, int) or not isinstance(alighted, int):
                # Aucun point ici. La dernière valeur connue est celle de
                # l'arrêt précédent, et la répéter à cette gare dessinerait
                # un palier — « rien ne s'est passé entre les deux » — alors
                # qu'on vient précisément de dire qu'on n'en sait rien. La
                # courbe s'arrête, et la légende nomme l'arrêt.
                return valeurs, str(leg.get("stop_name") or "")
            aboard = _a_bord(aboard, leg, alighted)
            if _posable(leg, places):
                valeurs.append(aboard)
        return valeurs, None
    passengers = row.get("passengers")
    if isinstance(passengers, int):
        return [passengers, passengers], None
    return None, None


def _posable(leg: dict, places: dict | None) -> bool:
    """Cet arrêt a-t-il une place sur le parcours ?

    Sans table de positions — le cas des tests de la fonction seule — on ne
    filtre rien : mieux vaut une abscisse de trop qu'une courbe amputée.
    """
    return places is None or str(leg.get("stop_id") or "") in places


def _a_bord(aboard: int, leg: dict, alighted: int) -> int:
    """La charge à bord après cet arrêt : ce qui est monté moins ce qui descend.

    Le dernier appelant possible n'arrive pas ici : quand une descente
    manque, la fonction est court-circuitée plus haut, parce que le résultat
    serait de toute façon faux.
    """
    return aboard - alighted + int(leg.get("boarded") or 0)


def _reseau():
    """Le réseau, ou None s'il n'a pas pu être lu.

    Absent, la carte reste en segment droit : c'est une dégradation lisible,
    pas une panne. Voir `reseau.reseau`.
    """
    from comptagefer.reseau import reseau as charger

    return charger()


def _trace_reseau(
    points: list[list[float]], reseau
) -> tuple[list[list[float]], bool]:
    """Le tracé le long de la voie, et le dire si c'est resté droit.

    On rend `(trace, droite)`. Un serpent de six arrêts donne cinq segments ;
    chacun est routé séparément, parce qu'un A* de bout en bout passerait par
    les bifurcation et ne reviendrait pas sur les voies empruntées.

    `droite` dit si un seul segment a dû rester droit. C'est ce que la page
    annonce, et elle ne l'annonce que s'il y a eu un segment droit — sinon
    elle dirait une limite du site alors qu'il n'y en a pas eu.
    """
    if reseau is None:
        return list(points), True
    trace: list[list[float]] = [points[0]]
    droite = False
    for avant, apres in zip(points, points[1:]):
        chemin = reseau.chemin(avant, apres)
        if chemin is None:
            droite = True
            trace.append(apres)
        else:
            trace.extend(chemin[1:])
    return trace, droite


def _trace(row: dict) -> list[tuple[str, str]]:
    """Les arrêts de la saisie, dans l'ordre.

    Le serpent a enregistré son parcours : on s'en sert. Sinon on n'a que le
    couple origine-destination, et c'est déjà un segment.
    """
    legs = row.get("legs")
    if isinstance(legs, list) and len(legs) >= 2:
        return [
            (str(leg.get("stop_id") or ""), str(leg.get("stop_name") or ""))
            for leg in legs
            if isinstance(leg, dict) and leg.get("stop_id")
        ]
    return [
        (str(row.get("origin_stop_id") or ""), str(row.get("origin_name") or "")),
        (str(row.get("destination_stop_id") or ""), str(row.get("destination_name") or "")),
    ]


def _coordinates(stops_database: Path) -> dict[str, tuple[float, float]]:
    """stop_id -> (lat, lon) pour tout ce qui a une position.

    Un arrêt enfant sans coordonnée hérite de sa gare parente : c'est le cas
    courant dans le GTFS national, où les voies ont la position de la gare.
    """
    if not Path(stops_database).exists():
        return {}
    from comptagefer.offer import open_stops

    with open_stops(stops_database) as connection:
        direct = {
            row[0]: (row[1], row[2])
            for row in connection.execute(
                "SELECT stop_id, lat, lon FROM stop WHERE lat IS NOT NULL AND lon IS NOT NULL"
            )
        }
        enfants = connection.execute(
            "SELECT stop_id, parent FROM stop WHERE parent IS NOT NULL"
        ).fetchall()
    places = dict(direct)
    for stop_id, parent in enfants:
        # Un arrêt enfant sans coordonnée hérite de sa gare parente : c'est le
        # cas courant dans le GTFS national, où les voies reprennent la
        # position de la gare. Une seule passe suffit, le parent d'un enfant
        # étant toujours une gare et donc un arrêt avec position.
        if stop_id not in places and parent in places:
            places[stop_id] = places[parent]
    return places


def map_page(features: list[dict], total: int) -> str:
    """La page de la carte.

    `features` est sérialisé dans la page plutôt que relu après coup : le pied
    annonce le nombre de la base entière, et la liste des tracés reste
    lisible même si le navigateur n'a pas pu charger Leaflet.

    Sur un écran large, la carte passe en pleine hauteur et la liste des
    tracés se place à côté, pas en dessous : c'est la seule page du site où
    la surface disponible est ce qui manque, et un cadre de 22 rem au milieu
    d'un écran de 1080 px est la définition d'un espace perdu. Le téléphone
    garde la pile, parce que c'est là qu'on la fait défiler.
    """
    return chrome(
        "Carte",
        f"<p>Ce n'est pas une fréquentation officielle. Les partages sont sous Licence Ouverte 2.0.</p>"
        f"{_NOTE}"
        f"{_corps(features)}"
        f"<p id='carte-pied' class='pied'>{_pied(total, features)}</p>",
        actif="/carte",
        extra_css="""
  #carte { height: 22rem; border-radius: 0.8rem; background: #fff; }
  .note { background: #fff; border-radius: 0.8rem; padding: 0.8rem 1rem; margin: 1rem 0; }
  ol { padding-left: 1.2rem; }
  li { margin: 0.4rem 0; }
  .pied { font-size: 0.85rem; color: var(--gris); }
  /* Le bouton, c'est toute la ligne du `<li>`, pas un titre au milieu d'un
     texte : il porte la sélection et le profil, donc il doit être la cible
     du clic et du clavier. Rien de visible ne change — le fond, la bordure
     et la police reprennent ceux du texte. */
  .ligne { display: block; width: 100%; text-align: left; background: none;
           border: 0; padding: 0; font: inherit; color: inherit; cursor: pointer; }
  .ligne:hover, .ligne:focus-visible { text-decoration: underline; }
  .ligne[aria-pressed="true"] { font-weight: 700; }
  .profil { margin-top: 0.5rem; }
  .profil[hidden] { display: none; }
  .profil svg { width: 100%; height: auto; display: block; }
  .profil .repere { stroke: #e6e0d5; stroke-width: 1; }
  .profil .axe { fill: var(--gris); font-size: 9px; }
  .profil figcaption { color: var(--gris); font-size: 0.8rem; margin-top: 0.3rem; }
  @media (min-width: 48rem) {
    /* La carte prend la hauteur de l'écran et la liste devient un panneau
       latéral. La grille est déclarée ici et pas sur `.corps` : la liste
       disparaît au-delà de 30 tracés, et une colonne vide sur un écran
       large ferait de la place perdue. */
    #carte { height: calc(100vh - 13rem); min-height: 28rem; }
    .traces { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 1fr); gap: 1.2rem;
              align-items: start; }
    .traces ol { max-height: calc(100vh - 16rem); overflow-y: auto; background: #fff;
                 border-radius: 0.8rem; padding: 0.8rem 0.8rem 0.8rem 2rem; }
  }
""",
        extra_head=f'<link rel="stylesheet" href="{LEAFLET_CSS}">',
        extra_script=f'<script src="{LEAFLET_JS}"></script>\n<script>\n{_script(features)}\n</script>',
        mention="",
    )


def _pied(total: int, features: list[dict]) -> str:
    """Le décompte du bas de page, tracés droits compris.

    Le nombre de tracés et le nombre de segments droits sont deux choses
    différentes, et les confondre dirait un comptage perdu alors qu'il est
    dessiné. Un serpent dont un seul des cinq segments est droit reste un
    seul tracé : on compte donc les tracés, et on précise les segments.
    """
    placos = len(features)
    droits = sum(1 for feature in features if feature.get("droite"))
    texte = (
        f"{total} comptage{'s' if total > 1 else ''} au total, "
        f"{placos} sur la carte. "
        "Les autres n'ont pas de coordonnées exploitables, ou pas deux arrêts distincts."
    )
    if droits == 0:
        return texte + " Tous les tracés suivent la voie ferrée."
    if droits == 1:
        return texte + " 1 tracé a un segment que le réseau ne relie pas, et il reste droit."
    return texte + f" {droits} tracés ont un segment que le réseau ne relie pas, et il reste droit."


def _corps(features: list[dict]) -> str:
    if not features:
        return (
            "<div class='note'><p>Aucun comptage à placer pour l'instant.</p>"
            "<p>La carte montre les arrêts comptés. "
            "<a href='/'>Compter un train</a> pour qu'y apparaisse un segment.</p></div>"
        )
    lignes = []
    for index, feature in enumerate(features):
        profil = " → ".join(feature["stops"])
        who = feature["pseudo"] or "anonyme"
        nombre = (
            "sans effectif" if feature["passengers"] is None else f"{feature['passengers']} voyageurs"
        )
        # Les noms sont déjà échappés par counted_features.
        texte = f"{profil} · {nombre} · {feature['kind_fr']} · {who}"
        courbe = _profil(feature, index)
        # Sans courbe, pas de bouton : un train signalé n'a pas d'effectif à
        # dessiner, et un bouton qui n'ouvre rien est une promesse que la
        # page ne tient pas. La ligne reste du texte, lisible comme avant.
        if not courbe:
            lignes.append(f"<li>{texte}</li>")
            continue
        # `data-i` est l'index dans FEATURES, donc le script retrouve le tracé
        # sans réinventer de clé. Un `client_id` conviendrait mal : le même
        # contributeur peut avoir compté plusieurs trains, et c'est le numéro
        # d'ordre de la page qui fait la correspondance.
        bouton = (
            f'<button class="ligne" type="button" data-i="{index}" '
            f'aria-pressed="false" aria-controls="profil-{index}">{texte}</button>'
        )
        lignes.append(f"<li>{bouton}{courbe}</li>")
    liste = "<ol>" + "".join(lignes) + "</ol>" if len(lignes) <= 30 else ""
    # Le conteneur `traces` porte la grille du grand écran. Il entoure la
    # carte comme la liste : sur un téléphone il ne fait rien, sur un large
    # écran il met la liste à côté au lieu de sous le cadre.
    return "<div class='traces'><div id='carte'></div>" + liste + "</div>"


def _profil(feature: dict, index: int) -> str:
    """Le conteneur vide de la courbe, ou rien.

    Le tracé est dessiné par le script au clic, pas écrit ici. La
    raison est mesurée : à 30 relevés, des SVG dans le HTML faisaient 43 %
    de la page — 27 ko pour un graphique qu'aucun lecteur ne voit tant
    qu'il n'a pas cliqué. Les données, elles, sont déjà dans le `<script>`
    (`charge`, `noms_bruts`) : le serveur ne les économisait pas, il les
    dupliquait.

    Ce qu'on y perd, et qui est réel : pytest ne voit plus la géométrie.
    Elle est donc vérifiée dans un vrai Chromium, sur le DOM, comme la
    synchronisation liste/carte.

    Sans effectif — un train signalé, un serpent sans nombre à bord au
    départ — il n'y a pas de courbe à dessiner. La ligne reste dans la
    liste et le bouton n'est pas rendu : un bouton qui ne fait rien est
    une promesse que la page ne tient pas.
    """
    if not feature.get("charge"):
        return ""
    return f'<figure class="profil" id="profil-{index}" hidden></figure>'


# La limite du segment droit est une propriété du réseau de données, pas un
# état de la carte : elle est écrite même quand la carte est vide, et aussi
# quand tous les tracés ont suivi la voie, puisque certains comptages restent
# droits — un arrêt hors réseau, une ligne fermée.
_NOTE = (
    "<p class='note'>Les tracés suivent la voie ferrée réelle quand le réseau national "
    "la connaît (données Cerema, Licence Etalab 2.0). Quand il ne la relie pas, le segment "
    "reste droit. Et la carte ne montre pas une fréquentation&nbsp;: des relevés de "
    "contributeurs.</p>"
)

# Le script suit <script src=...> : il tourne donc après Leaflet. Une
# constante Python plutôt qu'une interpolation, parce que les données sont du
# JSON et qu'un guillemet y casserait la chaîne.
_MAP_SCRIPT = """
// Les trois valeurs sont du JSON, guillemets compris : on ne les requote pas.
const FEATURES = __FEATURES__;
const TILE_URL = __TILE_URL__;
const TILE_ATTRIBUTION = __TILE_ATTRIBUTION__;
const TILE_SUBDOMAINS = __TILE_SUBDOMAINS__;
const TILE_MAX_ZOOM = __TILE_MAX_ZOOM__;
// Les tracés dessinés, par index de FEATURES : c'est ce qui permet à la
// liste d'allumer celui qu'elle affiche. Déclaré ici et pas dans `dessine`
// parce que la liste est câblée avant que la carte soit peinte.
//
// Le nom est préfixé parce qu'une page classique partage la portée globale
// avec les scripts qu'elle charge, et qu'un identifiant générique comme
// `dessines` entre en collision avec le moindre nom de ce genre — ce qui tue
// le script au chargement, donc toute la page, sans lever la moindre erreur
// visible dans le HTML.
let TRACES_DESSINES = [];
let CARTE_COURANTE = null;

// La courbe de charge. Le tracé est fait ici, au clic, et non écrit dans le
// HTML : à 30 relevés il pesait 27 ko, soit 43 % d'une page dont personne
// ne voit les graphiques avant d'en ouvrir un. Les valeurs, elles, sont
// déjà arrivées avec FEATURES.
//
// On ne fabrique pas de nœud : `innerHTML` n'est pas utilisé ici parce
// qu'un nom de gare contient déjà son `<` échappé, et le réinterpréter
// serait une seconde chance d'injection.
const SVG_NS = "http://www.w3.org/2000/svg";
const PROFIL_LARGEUR = 320;
const PROFIL_HAUTEUR = 120;
const PROFIL_MARGE_G = 30;
const PROFIL_MARGE_D = 8;
const PROFIL_MARGE_H = 10;
const PROFIL_MARGE_B = 22;

function noeud(balise, attributs, texte) {
  const element = document.createElementNS(SVG_NS, balise);
  for (const nom of Object.keys(attributs)) element.setAttribute(nom, attributs[nom]);
  if (texte !== undefined) element.textContent = texte;
  return element;
}

function plafond(maximum) {
  // Un plafond arrondi au pas de 10 : afficher « 43 voyageurs » au sommet
  // d'une boîte est exact et illisible, parce que le lecteur compare des
  // hauteurs et non des chiffres. Le minimum est 10, sinon un train vide
  // donnerait une division par la hauteur d'un trait.
  if (maximum <= 10) return 10;
  return Math.ceil(maximum / 10) * 10;
}

function dessineProfil(figure, feature) {
  const charge = feature.charge || [];
  if (!charge.length) return;
  const gares = (feature.noms_bruts || []).slice(0, charge.length);
  const haut = PROFIL_HAUTEUR - PROFIL_MARGE_H - PROFIL_MARGE_B;
  const large = PROFIL_LARGEUR - PROFIL_MARGE_G - PROFIL_MARGE_D;
  const max = plafond(Math.max.apply(null, charge));
  const abscisse = function (rang) {
    // Un seul point tombe au milieu : il n'a ni début ni fin, et le
    // dessiner aux bords inventerait un parcours.
    if (charge.length === 1) return PROFIL_MARGE_G + Math.floor(large / 2);
    return Math.round(PROFIL_MARGE_G + rang * large / (charge.length - 1));
  };
  // L'axe descend : plus y est grand, plus la charge est basse. L'axe part
  // de zéro, sinon un voyageur ressemble à dix.
  const ordonnee = function (valeur) {
    return Math.round(PROFIL_MARGE_H + haut - valeur / max * haut);
  };

  const svg = noeud("svg", {
    viewBox: "0 0 " + PROFIL_LARGEUR + " " + PROFIL_HAUTEUR,
    role: "img",
    "aria-labelledby": "profil-titre-" + figure.id,
  });
  svg.appendChild(noeud("title", { id: "profil-titre-" + figure.id },
    "Charge à bord, voyageurs par arrêt"));
  // Le <desc> porte les mêmes nombres que le dessin : c'est ce qu'un
  // lecteur d'écran entend à la place de la courbe.
  let desc = "Charge à bord, voyageurs par arrêt : ";
  charge.forEach(function (valeur, rang) {
    desc += (rang ? ", " : "") + gares[rang] + " " + valeur;
  });
  desc += ".";
  if (feature.incomplet_brut) {
    desc += " Le compte s'arrête à " + feature.incomplet_brut + " : la suite n'a pas été relevée.";
  }
  svg.appendChild(noeud("desc", {}, desc));

  for (const valeur of [0, max]) {
    svg.appendChild(noeud("line", {
      x1: PROFIL_MARGE_G, y1: ordonnee(valeur),
      x2: PROFIL_LARGEUR - PROFIL_MARGE_D, y2: ordonnee(valeur), class: "repere",
    }));
    svg.appendChild(noeud("text", {
      x: PROFIL_MARGE_G - 4, y: ordonnee(valeur) + 4,
      class: "axe", "text-anchor": "end",
    }, String(valeur)));
  }

  const points = charge.map(function (valeur, rang) {
    return abscisse(rang) + "," + ordonnee(valeur);
  }).join(" ");
  svg.appendChild(noeud("polyline", {
    points: points, fill: "none", stroke: feature.couleur, "stroke-width": "2",
  }));
  charge.forEach(function (valeur, rang) {
    svg.appendChild(noeud("circle", {
      cx: abscisse(rang), cy: ordonnee(valeur), r: "2.5", fill: feature.couleur,
    }));
  });
  // Seules la première et la dernière gare sont nommées : dix noms sur
  // 320 pixels sont illisibles, et l'ordre du parcours se lit déjà dans la
  // liste à côté.
  if (gares.length) {
    svg.appendChild(noeud("text", {
      x: abscisse(0), y: PROFIL_HAUTEUR - 6, class: "axe", "text-anchor": "start",
    }, gares[0]));
  }
  if (charge.length > 1 && gares.length > 1) {
    svg.appendChild(noeud("text", {
      x: abscisse(charge.length - 1), y: PROFIL_HAUTEUR - 6, class: "axe", "text-anchor": "end",
    }, gares[gares.length - 1]));
  }

  // La légende est la partie qui engage le projet : un graphique sans son
  // maximum, ni sa gare, est une forme.
  const sommet = Math.max.apply(null, charge);
  const rangSommet = charge.indexOf(sommet);
  const ou = gares[rangSommet] || "";
  const mot = sommet === 1 ? "voyageur" : "voyageurs";
  let legende = "Maximum " + sommet + " " + mot + (ou ? ", à " + ou + "." : ".");
  if (feature.incomplet_brut) {
    legende += " La courbe s'arrête à " + feature.incomplet_brut
      + " : les descentes suivantes n'ont pas été relevées.";
  }

  figure.textContent = "";
  figure.appendChild(svg);
  const caption = document.createElement("figcaption");
  caption.textContent = legende;
  figure.appendChild(caption);
}

function dessine() {
  const carte = L.map("carte", { scrollWheelZoom: false });
  L.tileLayer(TILE_URL, {
    attribution: TILE_ATTRIBUTION,
    subdomains: TILE_SUBDOMAINS,
    maxZoom: TILE_MAX_ZOOM,
  }).addTo(carte);
  const groupe = L.layerGroup().addTo(carte);
  let bornes = null;
  // Les tracés dessinés, par index de FEATURES : c'est ce qui permet à la
  // liste d'allumer celui qu'elle affiche. Sans ce tableau, le survol de la
  // liste n'aurait rien à allumer.
  TRACES_DESSINES = [];
  CARTE_COURANTE = carte;
  for (const feature of FEATURES) {
    // Le tracé vient du réseau, il a beaucoup plus de points que les arrêts.
    // Les marqueurs, eux, restent sur les arrêts : le tracé ne doit pas
    // décaler la gare de 300 m vers la voie.
    const trace = feature.trace.map((point) => [point[0], point[1]]);
    const points = feature.points.map((point) => [point[0], point[1]]);
    let ligne = null;
    if (trace.length > 1) {
      ligne = L.polyline(trace, { color: feature.couleur, weight: 4, opacity: 0.7 }).addTo(groupe);
    }
    TRACES_DESSINES.push({ ligne: ligne, points: points, couleur: feature.couleur });
    for (let index = 0; index < points.length; index += 1) {
      const nombre = feature.passengers === null
        ? "signale, sans effectif"
        : feature.passengers + " voyageurs";
      const contenu = "<strong>" + feature.origine + " &rarr; " + feature.destination
        + "</strong><br>" + nombre + " &middot; " + feature.kind_fr + "<br>" + feature.pseudo
        + (feature.stops.length > 1 ? "<br>" + feature.stops[index] : "");
      L.circleMarker(points[index], {
        radius: 5 + Math.min(7, Math.sqrt(feature.passengers || 0)),
        color: feature.couleur,
        fillColor: feature.couleur,
        fillOpacity: 0.6,
        weight: 1,
      })
        .bindPopup(contenu)
        .addTo(groupe);
      const coin = L.latLng(points[index][0], points[index][1]);
      bornes = bornes ? bornes.extend(coin) : L.latLngBounds(coin, coin);
    }
  }
  if (bornes) {
    carte.fitBounds(bornes.pad(0.2), { maxZoom: 11 });
  } else {
    carte.setView([46.6, 2.4], 6);
  }
  // Le conteneur vient d'être peint : Leaflet doit reprendre ses dimensions.
  carte.invalidateSize();
}

// --- la liste et la carte se suivent ----------------------------------------
//
// Deux gestes, deux portées. Le survol allume le tracé : c'est reversible,
// c'est gratuit, et c'est ce qui relie la ligne qu'on lit au trait qu'on
// voit. Le clic sélectionne : ça déplace la carte et ouvre la courbe de
// charge, donc ça ne se fait pas au survol, qui passerait dix fois sur la
// même ligne en descendant la liste.

function allume(index) {
  TRACES_DESSINES.forEach(function (dessin, rang) {
    if (!dessin.ligne || !dessin.ligne.setStyle) return;
    if (rang === index) {
      dessin.ligne.setStyle({ weight: 8, opacity: 1 });
    } else {
      dessin.ligne.setStyle({ weight: 4, opacity: 0.25 });
    }
  });
}

function rendTout() {
  allume(-1);
}

function selectionne(bouton) {
  const index = Number(bouton.dataset.i);
  const deja = bouton.getAttribute("aria-pressed") === "true";
  // Un seul tracé sélectionné à la fois : deux courbes ouvertes, c'est une
  // comparaison, et la comparaison a son propre mode (la vue par paire).
  document.querySelectorAll(".ligne").forEach(function (autre) {
    const courbe = document.getElementById(autre.getAttribute("aria-controls"));
    autre.setAttribute("aria-pressed", "false");
    if (courbe) courbe.hidden = true;
  });
  if (deja) {
    rendTout();
    return;
  }
  bouton.setAttribute("aria-pressed", "true");
  const profil = document.getElementById(bouton.getAttribute("aria-controls"));
  if (profil) {
    // Le tracé est fait ici, au clic, pas écrit dans le HTML : il y serait
    // payé à chaque chargement, alors qu'un seul est visible à la fois.
    dessineProfil(profil, FEATURES[index]);
    profil.hidden = false;
  }
  allume(index);
  // La carte suit la sélection : sans cela, cliquer une ligne ne change
  // qu'une liste, et la moitié gauche de l'écran reste sur le même endroit.
  const dessin = TRACES_DESSINES[index];
  if (dessin && dessin.points.length > 1 && CARTE_COURANTE) {
    let bornes = null;
    dessin.points.forEach(function (point) {
      const coin = L.latLng(point[0], point[1]);
      bornes = bornes ? bornes.extend(coin) : L.latLngBounds(coin, coin);
    });
    CARTE_COURANTE.fitBounds(bornes.pad(0.2), { maxZoom: 11 });
  }
}

function cableLaListe() {
  const boutons = document.querySelectorAll(".ligne");
  for (const bouton of boutons) {
    // `mouseenter`/`mouseleave` plutôt que `mouseover` : en passant d'une
    // ligne à l'autre par-dessus un enfant du bouton, `mouseover` se
    // redéclencherait et le tracé clignoterait.
    bouton.addEventListener("mouseenter", function () { allume(Number(bouton.dataset.i)); });
    bouton.addEventListener("mouseleave", rendTout);
    // Le focus clavier doit produire le même effet que le survol : sinon la
    // liste reste grise au clavier alors qu'elle s'allume à la souris.
    bouton.addEventListener("focus", function () { allume(Number(bouton.dataset.i)); });
    bouton.addEventListener("blur", rendTout);
    bouton.addEventListener("click", function () { selectionne(bouton); });
  }
  // Un clic dans le vide referme la sélection. Sans ça, après avoir choisi un
  // tracé, il n'y a plus aucun geste pour revenir à la vue d'ensemble : il
  // faudrait cliquer sur le même bouton, ce qu'on ne devine pas.
  if (CARTE_COURANTE) {
    CARTE_COURANTE.on("click", function () {
      document.querySelectorAll(".ligne").forEach(function (autre) {
        const courbe = document.getElementById(autre.getAttribute("aria-controls"));
        autre.setAttribute("aria-pressed", "false");
        if (courbe) courbe.hidden = true;
      });
      rendTout();
    });
  }
}

// Sur une carte vide, _corps ne rend pas de #carte : il n'y a rien à
// dessiner, et le script n'a rien à dire non plus.
const conteneur = document.getElementById("carte");

if (!conteneur) {
  // Rien à faire, la page d'invitation à compter suffit.
} else if (typeof L === "undefined") {
  // Pas de bibliothèque, pas de cadre vide : la liste des tracés reste en place.
  // Elle reste câblée : le survol et la courbe de charge n'ont rien à voir
  // avec Leaflet, et la seule chose qui manque sans la bibliothèque est le
  // déplacement de la carte au clic.
  cableLaListe();
  conteneur.outerHTML = "<p class='note'>La carte n'a pas pu se charger : la bibliothèque de "
    + "carte vient d'un tiers, et le réseau n'a pas répondu. Les tracés listés ci-dessous, eux, "
    + "sont complets.</p>";
} else {
  dessine();
  cableLaListe();
}
"""


def _script(features: list[dict]) -> str:
    # Chaque valeur passe par json.dumps : l'attribution OpenStreetMap contient
    # des guillemets, et une interpolation brute casserait la chaîne JavaScript.
    #
    # `</` est échappé en plus : le JSON est écrit dans un <script>, et un nom
    # de gare contenant « </script> » le fermerait. Le script mourrait au
    # chargement — toute la page, sans la moindre erreur visible dans le
    # HTML. C'est un caractère Unicode, donc invisible lui aussi, ce qui en
    # fait un défaut qui ne se voit qu'à l'écran.
    return (
        _MAP_SCRIPT.replace(
            "__FEATURES__", json.dumps(features, ensure_ascii=False).replace("</", "<\\/")
        )
        .replace("__TILE_URL__", json.dumps(TILE_URL))
        .replace("__TILE_ATTRIBUTION__", json.dumps(TILE_ATTRIBUTION))
        .replace("__TILE_SUBDOMAINS__", json.dumps(TILE_SUBDOMAINS))
        .replace("__TILE_MAX_ZOOM__", str(TILE_MAX_ZOOM))
    )
