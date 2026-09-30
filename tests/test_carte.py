"""La carte : ce qu'elle dessine, et ce qu'elle refuse de dessiner.

Les tracés suivent la voie ferrée réelle, via `reseau.py`, et tombent droit
quand le réseau ne relie pas deux arrêts. Ces tests verrouillent le contrat :
deux points placeables au minimum, l'ordre du serpent conservé, le tracé routé distinct
des marqueurs d'arrêt, et l'honnêteté du pied de page sur les
segments droits comme sur les comptages sans coordonnées.
"""

import csv
import math
from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.carte import counted_features, map_page
from comptagefer.offer import import_stop_names

# Coordonnées [lat, lon] de gares réelles, prises dans `gares.merged.geojson`
# (Cerema) puis arrondies comme le fait `counted_features`. Lyon Part-Dieu est à
# 4.859355 et non 4.8557 : à 280 m de la gare, le point de voie le plus proche
# est hors de la borne de 500 m et le tracé resterait droit. C'est un
# Gabarit, pas un hasard.
STOPS = [
    ("StopArea:Lyon", "Lyon Part-Dieu", "", 45.7606, 4.8594),
    ("StopPoint:LyonA", "Lyon Part-Dieu voie A", "StopArea:Lyon", 45.7606, 4.8594),
    ("StopArea:Vienne", "Vienne", "", 45.5212, 4.8742),
    ("StopPoint:VienneA", "Vienne voie 1", "StopArea:Vienne", 45.5212, 4.8742),
    ("StopArea:Valence", "Valence", "", 44.9280, 4.8933),
    ("StopPoint:ValenceA", "Valence voie A", "StopArea:Valence", 44.9280, 4.8933),
    # Une voie sans coordonnées : c'est le cas réel dans le GTFS national, et
    # elle doit hériter de la position de sa gare.
    ("StopPoint:VienneB", "Vienne voie 2", "StopArea:Vienne", "", ""),
    # Une gare sans position du tout : rien à dessiner pour elle.
    ("StopArea:Nulle", "Gare fantôme", "", "", ""),
]


def _stops_db(tmp_path: Path) -> Path:
    csv_path = tmp_path / "stops.txt"
    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"])
        for stop_id, name, parent, lat, lon in STOPS:
            writer.writerow([stop_id, name, lat, lon, "0" if parent else "1", parent])
    database = tmp_path / "stops.db"
    import_stop_names(database, csv_path)
    return database


def _count(**extra) -> dict:
    body = {
        "client_id": "jeton",
        "origin_stop_id": "StopArea:Lyon",
        "destination_stop_id": "StopArea:Valence",
        "origin_name": "Lyon Part-Dieu",
        "destination_name": "Valence",
        "passengers": 40,
        "reliability": 70,
        "pseudo": "railfan",
    }
    body.update(extra)
    return body


def test_a_count_becomes_a_segment_between_its_two_stops(tmp_path):
    stops = _stops_db(tmp_path)
    rows = [
        {
            "client_id": "jeton",
            "kind": "count",
            "origin_stop_id": "StopArea:Lyon",
            "destination_stop_id": "StopArea:Valence",
            "origin_name": "Lyon Part-Dieu",
            "destination_name": "Valence",
            "passengers": 40,
            "pseudo": "railfan",
            "legs": None,
        }
    ]
    features = counted_features(stops, rows)
    assert len(features) == 1
    assert features[0]["points"] == [[45.7606, 4.8594], [44.928, 4.8933]]
    assert features[0]["stops"] == ["Lyon Part-Dieu", "Valence"]


def test_a_count_between_two_stations_follows_the_railway_track(tmp_path):
    """Lyon–Valence est un tracé de voie ferrée, pas une diagonale.

    Le test utilise deux gares qui sont dans le réseau livré, donc le routage a
    toutes les chances de réussir. Si le réseau change et qu'elles n'y sont
    plus, le test le dit — c'est un signal, pas un échec à contourner.
    """
    stops = _stops_db(tmp_path)
    features = counted_features(
        stops,
        [
            {
                "client_id": "jeton",
                "kind": "count",
                "origin_stop_id": "StopArea:Lyon",
                "destination_stop_id": "StopArea:Valence",
                "origin_name": "Lyon Part-Dieu",
                "destination_name": "Valence",
                "passengers": 40,
                "pseudo": None,
                "legs": None,
            }
        ],
    )
    feature = features[0]
    assert feature["droite"] is False, "Lyon–Valence est sur le réseau, le tracé doit le suivre"
    assert len(feature["trace"]) > 2, "un tracé qui ne garde que les deux gares coupe au travers"
    # Le tracé part et arrive sur les gares, pas sur le point de voie le plus
    # proche : c'est la gare qu'on a comptée. La tolérance couvre le crochet
    # d'accroche, qui est de l'ordre de la centaine de mètres.
    assert _trace_passe_par(feature["trace"], feature["points"][0])
    assert _trace_passe_par(feature["trace"], feature["points"][1])


def test_the_markers_stay_on_the_stops_and_the_track_takes_the_detour(tmp_path):
    """Marqueurs et tracé sont deux listes, pour deux raisons.

    Le tracé est sur la voie, les marqueurs sur les arrêts. Les confondre
    décalerait chaque gare de plusieurs centaines de mètres vers le rail le
    plus proche, ce qui se voit dès qu'on zoome sur une gare.
    """
    stops = _stops_db(tmp_path)
    feature = counted_features(
        stops,
        [
            {
                "client_id": "serpent",
                "kind": "serpent",
                "origin_stop_id": "StopArea:Lyon",
                "destination_stop_id": "StopArea:Valence",
                "passengers": 10,
                "legs": [
                    {"stop_id": "StopPoint:LyonA", "stop_name": "Lyon Part-Dieu"},
                    {"stop_id": "StopPoint:VienneA", "stop_name": "Vienne"},
                    {"stop_id": "StopPoint:ValenceA", "stop_name": "Valence"},
                ],
            }
        ],
    )[0]
    assert len(feature["points"]) == 3, "trois arrêts, trois marqueurs"
    assert len(feature["trace"]) > 3, "le tracé doit avoir plus de points que les arrêts"
    for marqueur in feature["points"]:
        assert _trace_passe_par(feature["trace"], marqueur), (
            "chaque marqueur doit avoir sa gare sur le tracé"
        )


def test_a_snake_routes_each_leg_and_still_passes_through_every_stop(tmp_path):
    """Un serpent de trois arrêts donne deux segments routés, pas deux droites.

    Et le tracé passe par les trois gares : c'est un parcours de voyageur,
    pas un bond de la première à la dernière. On compare avec une tolérance
    de 200 m, pas à l'identique : le tracé s'accroche à la voie la plus proche
    de la gare, qui est à quelques dizaines de mètres, et une comparaison
    exacte dirait à tort que le serpent ne passe pas par son arrêt du milieu.
    """
    stops = _stops_db(tmp_path)
    feature = counted_features(
        stops,
        [
            {
                "client_id": "serpent",
                "kind": "serpent",
                "origin_stop_id": "StopArea:Lyon",
                "destination_stop_id": "StopArea:Valence",
                "passengers": 10,
                "legs": [
                    {"stop_id": "StopPoint:LyonA", "stop_name": "Lyon Part-Dieu"},
                    {"stop_id": "StopPoint:VienneA", "stop_name": "Vienne"},
                    {"stop_id": "StopPoint:ValenceA", "stop_name": "Valence"},
                ],
            }
        ],
    )[0]
    assert feature["droite"] is False
    assert len(feature["trace"]) >= 3
    assert _trace_passe_par(feature["trace"], feature["points"][1]), (
        "le tracé doit passer près de Vienne"
    )


def _trace_passe_par(trace: list[list[float]], arret: list[float], tolerance_m: float = 200.0):
    for point in trace:
        d = math.hypot(
            (point[1] - arret[1]) * 111320.0 * math.cos(math.radians(arret[0])),
            (point[0] - arret[0]) * 111320.0,
        )
        if d <= tolerance_m:
            return True
    return False


def test_a_count_between_two_stops_the_network_cannot_link_stays_straight_and_says_so(tmp_path):
    """Pas de chemin réseau : le segment droit, et la page le dit.

    C'est le contrat honnête du bas de page. Un tracé droit doit être annoncé,
    sinon le lecteur prend la carte pour une carte qui suit la voie.
    """
    stops = _stops_db(tmp_path)
    features = counted_features(
        stops,
        [
            {
                "client_id": "jeton",
                "kind": "count",
                "origin_stop_id": "StopArea:Nulle",
                "destination_stop_id": "StopArea:Lyon",
                "origin_name": "Quelque part",
                "destination_name": "Lyon Part-Dieu",
                "passengers": 1,
                "pseudo": None,
                "legs": None,
            }
        ],
    )
    # Nulle n'a pas de coordonnées : le comptage n'est pas dessiné du tout.
    assert features == []

    # Un point hors réseau mais avec des coordonnées, lui, est dessiné droit.
    import sqlite3

    connection = sqlite3.connect(stops)
    connection.execute(
        "INSERT INTO stop (stop_id, name, lat, lon, parent, is_area) VALUES (?,?,?,?,?,?)",
        ("StopArea:Campagne", "Gare de campagne", 46.9, 3.1, "", 1),
    )
    connection.commit()
    connection.close()
    features = counted_features(
        stops,
        [
            {
                "client_id": "jeton",
                "kind": "count",
                "origin_stop_id": "StopArea:Campagne",
                "destination_stop_id": "StopArea:Lyon",
                "origin_name": "Gare de campagne",
                "destination_name": "Lyon Part-Dieu",
                "passengers": 1,
                "pseudo": None,
                "legs": None,
            }
        ],
    )
    assert len(features) == 1
    assert features[0]["droite"] is True, "un point hors réseau doit rester un segment droit"
    assert features[0]["trace"] == features[0]["points"]
    assert "reste droit" in map_page(features, 1)


def test_the_footer_counts_traces_and_says_when_nothing_is_straight(tmp_path):
    """Zéro segment droit, zéro phrase sur le segment droit.

    Le bas de page annonce ce qui s'est passé, pas ce qui pourrait arriver. Sur
    une carte où tous les tracés suivent la voie, annoncer « 0 tracé a un
    segment droit » serait une limite du site là où il n'y en a pas eu.
    """
    stops = _stops_db(tmp_path)
    features = counted_features(
        stops,
        [
            {
                "client_id": "jeton",
                "kind": "count",
                "origin_stop_id": "StopArea:Lyon",
                "destination_stop_id": "StopArea:Valence",
                "passengers": 40,
                "pseudo": None,
                "legs": None,
            }
        ],
    )
    assert features[0]["droite"] is False
    pied = _pied_de(map_page(features, 1))
    assert "suivent la voie ferrée" in pied
    assert "reste droit" not in pied


def _pied_de(page: str) -> str:
    """Le paragraphe de décompte, seul.

    La note du haut de page parle aussi de segment droit, puisqu'elle décrit
    la règle en général. Chercher la phrase dans toute la page confondrait les
    deux : le test doit porter sur ce qui s'est passé, pas sur ce qui est
    possible.
    """
    debut = page.index("carte-pied")
    return page[debut : page.index("</p>", debut)]


def test_the_snake_draws_every_stop_it_recorded_in_order(tmp_path):
    """Un serpent a un parcours, pas un segment : le tracer comme un couple
    origine-destination effacerait exactement ce qui a été compté."""
    stops = _stops_db(tmp_path)
    rows = [
        {
            "client_id": "serpent",
            "kind": "serpent",
            "origin_stop_id": "StopArea:Lyon",
            "destination_stop_id": "StopArea:Valence",
            "origin_name": "Lyon Part-Dieu",
            "destination_name": "Valence",
            "passengers": 40,
            "pseudo": None,
            "legs": [
                {"stop_id": "StopPoint:LyonA", "stop_name": "Lyon Part-Dieu", "onboard": 40},
                {"stop_id": "StopPoint:VienneA", "stop_name": "Vienne", "boarded": 3},
                {"stop_id": "StopPoint:ValenceA", "stop_name": "Valence", "boarded": 0},
            ],
        }
    ]
    feature = counted_features(stops, rows)[0]
    assert feature["stops"] == ["Lyon Part-Dieu", "Vienne", "Valence"]
    assert len(feature["points"]) == 3
    assert feature["points"][1] == [45.5212, 4.8742]


def test_a_stop_point_without_coordinates_inherits_its_station(tmp_path):
    stops = _stops_db(tmp_path)
    rows = [
        {
            "client_id": "serpent",
            "kind": "serpent",
            "origin_stop_id": "StopPoint:LyonA",
            "destination_stop_id": "StopPoint:ValenceA",
            "passengers": 10,
            "legs": [
                {"stop_id": "StopPoint:LyonA", "stop_name": "Lyon Part-Dieu"},
                {"stop_id": "StopPoint:VienneB", "stop_name": "Vienne voie 2"},
                {"stop_id": "StopPoint:ValenceA", "stop_name": "Valence"},
            ],
        }
    ]
    feature = counted_features(stops, rows)[0]
    assert feature["points"][1] == [45.5212, 4.8742], "la voie sans position prend celle de sa gare"


def test_a_count_without_two_placeable_stops_is_left_out(tmp_path):
    """Mieux vaut une carte honnête qu'un point posé au hasard sur la France."""
    stops = _stops_db(tmp_path)
    rows = [
        {
            "client_id": "fantome",
            "kind": "count",
            "origin_stop_id": "StopArea:Lyon",
            "destination_stop_id": "StopArea:Nulle",
            "origin_name": "Lyon Part-Dieu",
            "destination_name": "Gare fantôme",
            "passengers": 5,
            "legs": None,
        }
    ]
    assert counted_features(stops, rows) == []


def test_the_page_says_how_many_counts_it_could_not_place(tmp_path):
    _stops_db(tmp_path)
    client = TestClient(create_app(tmp_path))
    client.post("/api/sessions", json=_count())
    client.post(
        "/api/sessions",
        json=_count(
            client_id="fantome",
            destination_stop_id="StopArea:Nulle",
            destination_name="Gare fantôme",
        ),
    )

    page = client.get("/carte")
    assert page.status_code == 200
    assert page.text.count("2 comptages au total") == 1
    assert "1 sur la carte" in page.text
    assert "Lyon Part-Dieu → Valence" in page.text


def test_the_map_page_links_back_to_the_list_and_the_method(tmp_path):
    _stops_db(tmp_path)
    page = TestClient(create_app(tmp_path)).get("/carte")
    assert 'href="/comptages"' in page.text
    assert 'href="/methode"' in page.text
    assert "pas une fréquentation officielle" in page.text
    # Le réseau ferré est une limite de la donnée, pas un détail : elle est écrite.
    assert "suivent la voie ferrée réelle" in page.text


def test_an_empty_map_invites_to_count_instead_of_showing_an_empty_frame(tmp_path):
    _stops_db(tmp_path)
    page = TestClient(create_app(tmp_path)).get("/carte")
    assert "Aucun comptage à placer" in page.text
    assert 'href="/"' in page.text
    assert "0 comptage au total" in page.text


def test_a_stop_name_cannot_inject_html_into_the_page(tmp_path):
    _stops_db(tmp_path)
    client = TestClient(create_app(tmp_path))
    client.post("/api/sessions", json=_count(pseudo="<img src=x onerror=alert(1)>"))

    page = client.get("/carte")
    assert "<img src=x" not in page.text
    assert "&lt;img src=x" in page.text


def test_the_list_line_is_a_button_that_reveals_the_charge_curve(tmp_path):
    """La vague 3 : la liste commande la carte, et elle porte la courbe.

    Le bouton est l'élément testé, pas un `<li>` décoratif : c'est lui qui
    porte `aria-controls`, donc c'est lui qui doit exister. Une liste qui ne
    serait que du texte passerait tous les tests de contenu et ne
    synchroniserait rien.
    """
    stops = _stops_db(tmp_path)
    features = counted_features(
        stops,
        [{
            "client_id": "jeton",
            "kind": "count",
            "origin_stop_id": "StopArea:Lyon",
            "destination_stop_id": "StopArea:Valence",
            "passengers": 40,
            "pseudo": None,
            "legs": None,
        }],
    )
    page = map_page(features, 1)
    assert '<button class="ligne"' in page
    assert 'aria-controls="profil-0"' in page
    # La courbe est dans la page, pas seulement dans le script : c'est le
    # serveur qui la dessine, pour qu'elle soit relue par pytest.
    assert "<polyline" in page
    assert "<figure" in page and "Maximum 40 voyageurs" in page


def test_a_reported_train_gets_no_curve(tmp_path):
    """Pas d'effectif, pas de courbe — et pas de bouton qui ne fait rien."""
    stops = _stops_db(tmp_path)
    features = counted_features(
        stops,
        [{
            "client_id": "jeton",
            "kind": "missing",
            "origin_stop_id": "StopArea:Lyon",
            "destination_stop_id": "StopArea:Valence",
            "passengers": None,
            "pseudo": None,
            "legs": None,
        }],
    )
    page = map_page(features, 1)
    assert "<polyline" not in page
    assert "<button" not in page, "un bouton sans courbe est une promesse que la page ne tient pas"


def test_an_incomplete_snake_is_told_where_its_curve_stops(tmp_path):
    """La fin d'un serpent sans descente relevée est dite, pas devinée."""
    stops = _stops_db(tmp_path)
    features = counted_features(
        stops,
        [{
            "client_id": "serpent",
            "kind": "serpent",
            "origin_stop_id": "StopArea:Lyon",
            "destination_stop_id": "StopArea:Valence",
            "passengers": 40,
            "pseudo": None,
            "legs": [
                {"stop_id": "StopPoint:LyonA", "stop_name": "Lyon Part-Dieu", "onboard": 40},
                {"stop_id": "StopPoint:VienneA", "stop_name": "Vienne", "boarded": 3, "alighted": 1},
                {"stop_id": "StopPoint:ValenceA", "stop_name": "Valence", "boarded": 0},
            ],
        }],
    )
    assert features[0]["charge"] == [40, 42], "la courbe ne continue pas après une descente inconnue"
    page = map_page(features, 1)
    # Le fragment est sans apostrophe : `escape()` la transforme en `&#x27;`
    # dans la page, et une assertion qui chercherait « s'arrête » échouerait
    # sur un rendu parfaitement correct.
    assert "arrête à Valence" in page, "le lecteur doit savoir où le compte s'arrête"


def test_the_curve_is_named_after_the_places_it_could_plot(tmp_path):
    """Une gare sans coordonnées n'a pas de place sur le parcours.

    Le cas est réel : une voie du GTFS national sans position hérite de celle
    de sa gare, mais un serpent peut contenir une gare qu'on ne sait pas poser.
    Donner une abscisse à cette gare ferait avancer la charge d'un cran sans
    que le train se soit déplacé.
    """
    stops = _stops_db(tmp_path)
    features = counted_features(
        stops,
        [{
            "client_id": "serpent",
            "kind": "serpent",
            "origin_stop_id": "StopArea:Lyon",
            "destination_stop_id": "StopArea:Valence",
            "passengers": 10,
            "pseudo": None,
            "legs": [
                {"stop_id": "StopPoint:LyonA", "stop_name": "Lyon Part-Dieu", "onboard": 10},
                {"stop_id": "StopArea:Nulle", "stop_name": "Gare fantôme", "boarded": 5, "alighted": 0},
                {"stop_id": "StopPoint:ValenceA", "stop_name": "Valence", "boarded": 0, "alighted": 4},
            ],
        }],
    )
    # La gare fantôme est écartée de la courbe comme elle l'est du tracé :
    # elle a bien été parcourue, mais elle n'a pas de place sur le graphique.
    # Donc deux abscisses pour trois arrêts comptés — et les montées de la
    # gare fantôme restent dans le calcul.
    assert features[0]["noms_bruts"] == ["Lyon Part-Dieu", "Valence"]
    assert features[0]["charge"] == [10, 11], (
        "10 à Lyon, puis 10 + 5 à la gare fantôme puis - 4 à Valence : "
        "la gare fantôme est dans le calcul mais pas dans le graphique"
    )


def test_a_client_id_cannot_break_out_of_the_page_script(tmp_path):
    """Le `client_id` était le seul champ non échappé, et il atterrit dans le
    même `<script>` que le reste. Un `</script>` dedans fermait la balise et la
    suite était exécutée par le navigateur. C'est le seul champ qui venait du
    client sans passer par `escape()`."""
    _stops_db(tmp_path)
    client = TestClient(create_app(tmp_path))
    client.post(
        "/api/sessions",
        json=_count(client_id="</script><script>alert('injecté')</script>"),
    )

    page = client.get("/carte")
    assert "</script><script>alert" not in page.text
    assert "&lt;/script&gt;&lt;script&gt;alert" in page.text
    # Le nombre de balises fermantes ne doit pas avoir augmenté non plus : c'est
    # la fermeture de balise qui rend l'injection exécutable, pas le texte.
    assert page.text.count("</script>") == page.text.count("<script")
