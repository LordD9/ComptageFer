"""Le filtre `?gare=` de la liste des comptages.

Il a remplacé le champ « Ligne (route_id) » dans le formulaire : ce que le
lecteur sait écrire n'est pas un `route_id` du GTFS mais un nom de gare. Le
filtre ligne reste lu dans l'URL — les liens partagés et les signets — et son
champ est devenu caché, donc les deux filtres coexistent.

**La gare intermédiaire doit faire apparaître le comptage.** C'est le point
que la page `/gare` traitait déjà et que le filtre devait aussi traiter : un
relevé dont l'origine et la destination sont ailleurs mais dont le train
passe par la gare demandée est un relevé **de** cette gare. Trois colonnes
portent cette information, et il faut les trois :

- `origin_stop_id` / `destination_stop_id` : les extrémités ;
- `legs` : les arrêts du serpent ;
- `trajet` : le parcours complet figé au moment du comptage.

Omettre la troisième est le défaut que ce fichier existe pour empêcher : le
lecteur filtre sur une gare, la liste rend des relevés qui ne la touchent
pas, et il conclut que la gare n'est pas couverte.
"""

import json
import re
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.offer import import_stop_names

PHOTO = {"precedent": None, "courant": {"trip_id": "T", "status": "SCHEDULED"}, "suivant": None}

# Trois gares alignées comme le catalogue de test du site : `Lyon Part-Dieu`
# est une aire avec deux voies, `Valence` une aire, `Nîmes` une aire sans
# quai. Une gare sans famille d'identifiants et une gare avec, c'est ce qui
# prouve que la résolution par nom fonctionne.
LIGNES = [
    ("StopArea:Lyon", "Lyon Part-Dieu", "", 45.7608, 4.8557),
    ("StopPoint:LyonA", "Lyon Part-Dieu voie A", "StopArea:Lyon", 45.7608, 4.8557),
    ("StopPoint:LyonB", "Lyon Part-Dieu voie B", "StopArea:Lyon", 45.7608, 4.8557),
    ("StopArea:Valence", "Valence", "", 44.9294, 4.9925),
    ("StopPoint:ValenceA", "Valence voie A", "StopArea:Valence", 44.9294, 4.9925),
    ("StopArea:Nimes", "Nîmes", "", 43.8364, 4.3605),
]


def _catalogue(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    fichier = tmp_path / "stops.txt"
    with fichier.open("w", newline="") as handle:
        handle.write("stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n")
        for stop_id, nom, parent, lat, lon in LIGNES:
            handle.write(
                f"{stop_id},{nom},{lat},{lon},{'0' if parent else '1'},{parent}\n"
            )
    import_stop_names(data / "stops.db", fichier)
    return data


def _poster(client, **champs):
    corps = {
        "client_id": champs["client_id"],
        "origin_stop_id": champs.get("o", "StopArea:Lyon"),
        "destination_stop_id": champs.get("d", "StopArea:Valence"),
        "origin_name": champs.get("on", "Lyon Part-Dieu"),
        "destination_name": champs.get("dn", "Valence"),
        "reliability": 70,
        "snapshot": PHOTO,
    }
    if "legs" in champs:
        # Le genre suit les `legs` : `_save_saisie` ignore `legs` sur un
        # relevé de genre « count », donc un serpent posté sans `kind` se
        # retrouverait sans arrêts — et le test du filtre gare passerait
        # pour une raison fausse.
        corps["kind"] = "serpent"
        corps["legs"] = champs["legs"]
    elif "passengers" in champs:
        corps["passengers"] = champs["passengers"]
    corps["pseudo"] = champs.get("pseudo", "")
    return client.post("/api/sessions", json=corps)


def _trajet(stops: list[tuple[str, int]]) -> str:
    return json.dumps(
        {
            "trip_id": "T",
            "arrets": [
                {"stop_id": stop, "name": stop.split(":")[-1], "depart_sec": seconde}
                for stop, seconde in stops
            ],
        },
        ensure_ascii=False,
    )


def _figer(client: TestClient, data: Path, client_id: str, arrets: list[tuple[str, int]]) -> None:
    """Écrit le parcours figé d'un relevé, comme le fait le comptage.

    Le faire par SQL et non en composant un GTFS est un choix de ce fichier :
    le gel du trajet est déjà couvert ailleurs, et ici il s'agit de placer un
    état de base — un relevé dont le train passe par une gare donnée — pas de
    retester `_freeze_trajet`.
    """
    with sqlite3.connect(data / "app.db") as connection:
        connection.execute(
            "UPDATE saisie SET trajet = ? WHERE client_id = ?",
            (_trajet(arrets), client_id),
        )


def _site(tmp_path: Path) -> TestClient:
    """Un site avec quatre relevés, un par moyen de toucher la gare.

    - `alice` : Lyon est son origine ;
    - `bob` : la gare est **intermédiaire** du serpent, pas une extrémité ;
    - `carol` : la gare est intermédiaire du parcours figé, sans serpent ;
    - `dave` : ne la touche pas du tout, et sert de témoin.

    Sans `dave`, un filtre cassé qui rendrait la liste entière passerait les
    trois autres.
    """
    data = _catalogue(tmp_path)
    client = TestClient(create_app(data))
    _poster(client, client_id="alice", pseudo="alice", passengers=10,
            o="StopArea:Lyon", d="StopArea:Valence", on="Lyon Part-Dieu", dn="Valence")
    _poster(client, client_id="bob", pseudo="bob", passengers=20,
            o="StopArea:Valence", d="StopArea:Nimes", on="Valence", dn="Nîmes",
            legs=[
                {"stop_id": "StopArea:Valence", "onboard": 20},
                {"stop_id": "StopArea:Lyon", "boarded": 1, "alighted": 0},
                {"stop_id": "StopArea:Nimes", "boarded": 0, "alighted": 1},
            ])
    _poster(client, client_id="carol", pseudo="carol", passengers=30,
            o="StopArea:Valence", d="StopArea:Nimes", on="Valence", dn="Nîmes")
    _figer(client, data, "carol", [
        ("StopArea:Valence", 8 * 3600),
        ("StopArea:Lyon", 9 * 3600),
        ("StopArea:Nimes", 10 * 3600),
    ])
    _poster(client, client_id="dave", pseudo="dave", passengers=40,
            o="StopArea:Valence", d="StopArea:Valence", on="Valence", dn="Valence")
    return client


def _vrais(page: str) -> list[str]:
    return [nom for nom in ("alice", "bob", "carol", "dave") if nom in page]


# --- la gare en extrémité, en intermédiaire, et dans le parcours -------------


def test_the_station_filter_keeps_a_count_whose_leg_stops_there(tmp_path):
    """La gare demandée est un arrêt du serpent, pas une extrémité.

    C'est le cas que `/gare` couvrait déjà et que le filtre devait couvrir
    aussi : le relevé `bob` part de Valence et arrive à Nîmes, et son train
    passe par Lyon.
    """
    client = _site(tmp_path)

    page = client.get("/comptages?gare=Lyon+Part-Dieu").text

    assert "bob" in page, (
        "un relevé dont le serpent passe par la gare filtrée doit rester : "
        "c'est un relevé de cette gare, pas d'une autre"
    )


def test_the_station_filter_keeps_a_count_whose_frozen_route_passes_there(tmp_path):
    """La gare demandée est dans le parcours figé, sans serpent.

    Le serpent est facultatif : un comptage unique garde le trajet complet du
    train (`trajet`) sans avoir de `legs`. Filtrer sur les seuls `legs`
    laisserait ces relevés hors de la liste, et la gare semblerait moins
    couverte qu'elle ne l'est.
    """
    client = _site(tmp_path)

    page = client.get("/comptages?gare=Lyon+Part-Dieu").text

    assert "carol" in page, (
        "un relevé dont le parcours figé passe par la gare filtrée doit rester"
    )


def test_the_station_filter_removes_the_counts_that_do_not_touch_it(tmp_path):
    """Le filtre retire ce qui ne touche pas la gare, et le dit."""
    client = _site(tmp_path)

    page = client.get("/comptages?gare=Lyon+Part-Dieu").text

    assert "dave" not in page, (
        "un relevé Valence → Valence ne touche pas Lyon : le garder prouverait "
        "que le filtre n'a rien fait"
    )
    assert "alice" in page, "l'origine Lyon doit rester"


def test_a_station_resolves_through_its_stop_points_not_its_area(tmp_path):
    """Le nom trouvé sur un quai doit remonter à toute la gare.

    C'est le piège `_saisies_de_gare` documente déjà : le même quai peut
    s'appeler `StopArea:Lyon` dans une offre et `StopPoint:LyonA` dans une
    autre. Un comptage fait depuis le quai B ne doit pas disparaître quand on
    filtre sur l'aire.
    """
    _catalogue(tmp_path)
    client = TestClient(create_app(tmp_path / "data"))
    _poster(client, client_id="quai", pseudo="quai", passengers=12,
            o="StopPoint:LyonB", d="StopArea:Valence",
            on="Lyon Part-Dieu voie B", dn="Valence")

    page = client.get("/comptages?gare=Lyon+Part-Dieu").text

    assert "quai" in page, (
        "un comptage fait depuis un quai de la gare filtrée doit rester : "
        "la famille d'identifiants n'est pas la seule aire"
    )


# --- ce que le filtre dit quand il ne peut pas filtrer ----------------------


def test_an_unknown_station_is_dropped_and_said(tmp_path):
    """Une gare hors catalogue : le filtre est écarté, et la page le dit.

    Le silence serait pire : le lecteur verrait la liste entière et
    croirait que le filtre n'a rien donné.
    """
    client = _site(tmp_path)

    page = client.get("/comptages?gare=Pas-une-gare").text

    assert "n'est pas une gare du catalogue" in page, (
        "un filtre écarté doit dire pourquoi, à côté des autres filtres"
    )
    assert "gare=Pas-une-gare" not in page, (
        "un filtre écarté ne doit pas rester dans les liens de la page"
    )
    assert _vrais(page) == ["alice", "bob", "carol", "dave"], (
        "un filtre écarté laisse la liste entière, et le lecteur doit le voir"
    )


def test_a_partial_station_name_does_not_filter_a_different_one(tmp_path):
    """« Lyon » ne doit pas ramener « Lyon Part-Dieu ».

    Un `LIKE '%Lyon%'` ramènerait la gare sans que le liseurl'ait demandé.
    Le nom demandé doit exister tel quel ; sinon le filtre est écarté et dit.
    """
    client = _site(tmp_path)

    page = client.get("/comptages?gare=Lyon").text

    assert "n'est pas une gare du catalogue" in page, (
        "« Lyon » n'est pas le nom d'une gare du catalogue : le filtre doit "
        "être écarté plutôt que d'élargi"
    )
    assert _vrais(page) == ["alice", "bob", "carol", "dave"]


# --- le formulaire ----------------------------------------------------------


def test_the_form_offers_a_station_and_no_longer_a_line_field(tmp_path):
    """Le champ du formulaire est « Gare », plus « Ligne (route_id) ».

    Le champ ligne a disparu **du formulaire**, pas de la logique : `?ligne=`
    reste lu, affiché en chip, et rendu par un champ caché pour qu'un
    rechargement du formulaire ne le retire pas en silence.
    """
    client = _site(tmp_path)

    page = re.search(
        r"<form class='filtres'.*?</form>",
        client.get("/comptages").text,
        re.DOTALL,
    )
    assert page is not None, "le formulaire de filtre a disparu"
    html = page.group(0)

    assert "for='gare'" in html, "le formulaire ne propose plus de filtre par gare"
    assert "name='gare'" in html
    assert "Ligne (route_id)" not in html, (
        "le libellé « Ligne (route_id) » est toujours proposé : le lecteur "
        "devrait avoir à connaître un identifiant du GTFS"
    )
    assert "name='ligne'" not in html, (
        "le champ ligne ne doit plus être un champ visible du formulaire"
    )


def test_a_carried_line_filter_survives_the_form(tmp_path):
    """Un lien `?ligne=` filtre encore, et le formulaire le rend.

    Sans le champ caché, le premier « Filtrer » de la page effacerait le
    filtre ligne — un filtre qu'on ne voit plus mais qui s'applique. C'est le
    pire des deux mondes : la liste change sans que rien ne l'explique.

    Le test porte sur `_filtres_html` et non sur une URL : il faut une ligne
    **résolue** pour que le filtre soit actif, donc un GTFS complet, et ce
    qu'on vérifie ici est le rendu du formulaire — pas la résolution, déjà
    couverte par `test_filtre_ligne.py`.
    """
    from comptagefer.app import _filtres_html
    from comptagefer.filtres import Filtres

    html = _filtres_html(Filtres(ligne="C13"), "")

    assert "name='ligne'" in html, (
        "un filtre ligne appliqué doit être rendu au formulaire, sinon le "
        "premier clic sur « Filtrer » le retire en silence"
    )
    assert "C13" in html, "la valeur du filtre doit être rendue, pas seulement le champ"


def test_the_date_filter_does_not_say_jusqu_au_le(tmp_path):
    """« Jusqu'au le » : deux articles pour un mot.

    Le champ existe déjà et il est correct ; c'est le libellé qui mentait sur
    la grammaire. Le test est sur le texte rendu, pas sur la constante : une
    correction faite puis annulée laisserait la constante propre.
    """
    client = _site(tmp_path)

    page = client.get("/comptages").text

    assert "Jusqu’au le" not in page, "le libellé de la borne haute est fautif"
    assert "Jusqu’au</label>" in page, "le libellé corrigé doit être présent"


# --- le parcours dans le détail --------------------------------------------


def test_the_count_page_lists_the_stations_of_the_frozen_route(tmp_path):
    """Le détail d'un comptage montre les gares du parcours, comme le CSV.

    La donnée était déjà dans la colonne `trajet` de l'export, donc
    lisible — mais seulement par qui télécharge le fichier. Sur un téléphone,
    dans un train, ce n'est pas une option.
    """
    data = _catalogue(tmp_path)
    client = TestClient(create_app(data))
    _poster(client, client_id="course", pseudo="zoe", passengers=44,
            o="StopArea:Valence", d="StopArea:Nimes", on="Valence", dn="Nîmes")
    _figer(client, data, "course", [
        ("StopArea:Valence", 8 * 3600),
        ("StopArea:Lyon", 9 * 3600),
        ("StopArea:Nimes", 10 * 3600),
    ])

    page = client.get("/releve?client_id=course&kind=count").text

    assert "Gares du parcours" in page, (
        "le détail ne dit pas par où le train est passé"
    )
    # Les gares sont rendues dans l'ordre, avec leur heure : c'est l'ordre du
    # train qui est l'information, pas une collection.
    gares = re.findall(r"<li>([^<]+)<span class='heure'>([^<]+)</span></li>", page)
    assert gares, f"aucune gare avec son heure dans le détail : {page[-2000:]}"
    noms = [nom.strip() for nom, _ in gares]
    assert noms == ["Valence", "Lyon", "Nimes"], f"ordre du parcours faux : {noms}"
    assert [heure.strip() for _, heure in gares] == ["08:00", "09:00", "10:00"]


def test_a_count_without_a_frozen_route_shows_no_station_list(tmp_path):
    """Pas de parcours figé, pas de liste : une liste vide dirait « sans arrêt ».

    Un train signalé manquant n'a pas de `trip_id`, donc pas de trajet. La
    page ne doit pas afficher un titre suivi de rien, qui se lit comme un
    train qui ne dessert nulle part.
    """
    _catalogue(tmp_path)
    client = TestClient(create_app(tmp_path / "data"))
    _poster(client, client_id="sans-trajet", pseudo="zoe", passengers=5,
            o="StopArea:Lyon", d="StopArea:Valence", on="Lyon Part-Dieu", dn="Valence")

    page = client.get("/releve?client_id=sans-trajet&kind=count").text

    assert "Gares du parcours" not in page, (
        "un relevé sans parcours figé ne doit pas afficher de liste de gares"
    )


def test_a_past_midnight_stop_shows_its_clock_time_not_the_gtfs_one(tmp_path):
    """25:30 dans le GTFS, 1:30 à l'écran.

    Le GTFS compte les secondes depuis midi et autorise 25:00 pour un
    passage après minuit. Affiché tel quel, le lecteur lit une heure
    impossible et conclut à une saisie fausse — alors que la donnée est
    bonne.
    """
    data = _catalogue(tmp_path)
    client = TestClient(create_app(data))
    _poster(client, client_id="nuit", pseudo="zoe", passengers=3,
            o="StopArea:Valence", d="StopArea:Nimes", on="Valence", dn="Nîmes")
    # Un passage à 23:30 puis à 01:30 le lendemain, écrit comme le fait le
    # GTFS : les secondes depuis midi, donc 25:30 pour le second.
    _figer(client, data, "nuit", [
        ("StopArea:Valence", 23 * 3600 + 1800),
        ("StopArea:Nimes", 25 * 3600 + 1800),
    ])

    page = client.get("/releve?client_id=nuit&kind=count").text

    assert "23:30" in page, "l'heure du soir doit rester lisible"
    assert "01:30" in page, (
        "un passage après minuit doit être affiché 01:30, pas 25:30 : le "
        "lecteur lirait une heure impossible"
    )