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
        for stop_id, _name in arretees:
            place = places.get(stop_id)
            if place is not None:
                points.append([place[1], place[0]])  # [lon, lat] comme le réseau
        if len(points) < 2:
            continue
        trace, droite = _trace_reseau(points, reseau)
        kind = row.get("kind") or "count"
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
                # `points` garde la position des arrêts, entiers compris : les
                # marqueurs de gare doivent tomber sur la gare, pas sur le
                # point de voie le plus proche.
                "points": [[round(lat, 5), round(lon, 5)] for lon, lat in points],
                "trace": [[round(lat, 5), round(lon, 5)] for lon, lat in trace],
                "droite": droite,
            }
        )
    return features


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
    """
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Carte — ComptagesFer</title>
<link rel="stylesheet" href="{LEAFLET_CSS}">
<style>
  body {{ margin: 0; font: 18px/1.4 system-ui, sans-serif; background: #f4f1ea; color: #1c1915; }}
  main {{ max-width: 40rem; margin: 0 auto; padding: 1rem 1rem 3rem; }}
  #carte {{ height: 22rem; border-radius: 0.8rem; background: #fff; }}
  .note {{ background: #fff; border-radius: 0.8rem; padding: 0.8rem 1rem; margin: 1rem 0; }}
  a {{ color: #1c1915; }}
  ol {{ padding-left: 1.2rem; }}
  li {{ margin: 0.4rem 0; }}
  .pied {{ font-size: 0.85rem; color: #5c554b; }}
</style>
</head>
<body>
<main>
  <h1>Carte</h1>
  <p>Ce n'est pas une fréquentation officielle. Les partages sont sous Licence Ouverte 2.0.</p>
  <p><a href="/comptages">Voir la liste</a> · <a href="/rechercher">Rechercher</a> · <a href="/">Compter</a> · <a href="/methode">Méthode</a></p>
  {_NOTE}
  {_corps(features)}
  <p id="carte-pied" class="pied">{_pied(total, features)}</p>
</main>
<script src="{LEAFLET_JS}"></script>
<script>
{_script(features)}
</script>
</body>
</html>
"""


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
    for feature in features:
        profil = " → ".join(feature["stops"])
        who = feature["pseudo"] or "anonyme"
        nombre = (
            "sans effectif" if feature["passengers"] is None else f"{feature['passengers']} voyageurs"
        )
        # Les noms sont déjà échappés par counted_features.
        lignes.append(f"<li>{profil} · {nombre} · {feature['kind_fr']} · {who}</li>")
    liste = "<ol>" + "".join(lignes) + "</ol>" if len(lignes) <= 30 else ""
    return "<div id='carte'></div>" + liste


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

function dessine() {
  const carte = L.map("carte", { scrollWheelZoom: false });
  L.tileLayer(TILE_URL, {
    attribution: TILE_ATTRIBUTION,
    subdomains: TILE_SUBDOMAINS,
    maxZoom: TILE_MAX_ZOOM,
  }).addTo(carte);
  const groupe = L.layerGroup().addTo(carte);
  let bornes = null;
  for (const feature of FEATURES) {
    // Le tracé vient du réseau, il a beaucoup plus de points que les arrêts.
    // Les marqueurs, eux, restent sur les arrêts : le tracé ne doit pas
    // décaler la gare de 300 m vers la voie.
    const trace = feature.trace.map((point) => [point[0], point[1]]);
    const points = feature.points.map((point) => [point[0], point[1]]);
    if (trace.length > 1) {
      L.polyline(trace, { color: feature.couleur, weight: 4, opacity: 0.7 }).addTo(groupe);
    }
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

// Sur une carte vide, _corps ne rend pas de #carte : il n'y a rien à
// dessiner, et le script n'a rien à dire non plus.
const conteneur = document.getElementById("carte");

if (!conteneur) {
  // Rien à faire, la page d'invitation à compter suffit.
} else if (typeof L === "undefined") {
  // Pas de bibliothèque, pas de cadre vide : la liste des tracés reste en place.
  conteneur.outerHTML = "<p class='note'>La carte n'a pas pu se charger : la bibliothèque de "
    + "carte vient d'un tiers, et le réseau n'a pas répondu. Les tracés listés ci-dessous, eux, "
    + "sont complets.</p>";
} else {
  dessine();
}
"""


def _script(features: list[dict]) -> str:
    # Chaque valeur passe par json.dumps : l'attribution OpenStreetMap contient
    # des guillemets, et une interpolation brute casserait la chaîne JavaScript.
    return (
        _MAP_SCRIPT.replace("__FEATURES__", json.dumps(features, ensure_ascii=False))
        .replace("__TILE_URL__", json.dumps(TILE_URL))
        .replace("__TILE_ATTRIBUTION__", json.dumps(TILE_ATTRIBUTION))
        .replace("__TILE_SUBDOMAINS__", json.dumps(TILE_SUBDOMAINS))
        .replace("__TILE_MAX_ZOOM__", str(TILE_MAX_ZOOM))
    )
