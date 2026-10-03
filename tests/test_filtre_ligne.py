"""Le filtre `?ligne=`, vérifié avec une vraie base d'horaires.

Le plan l'annonçait comme une limite : la clause `trip_id IN (...)` existe
dans `filtres.py`, et la suite
n'avait pas de `timetable.db` — donc `_trips_de_ligne` renvoyait toujours un
ensemble vide, et la clause que tout le monde croyait testée ne l'était pas.

Ces tests montent un horaire minimal : deux lignes, trois circulations. C'est
assez pour que la ligne soit trouvée, que ses `trip_id` soient résolus, et que
la clause filtre pour de vrai. Un test qui passe avec un timetable vide ne
prouve rien : il passe aussi quand le filtre est cassé.

**Vérifié en cassant le filtre volontairement.** Un test qui passe n'est pas
un test qui prouve ; il faut voir qu'il échoue quand la couverture est fausse.
Trois mutations ont été essayées :

- la clause `if filtres.ligne` désactivée → **5 tests sur 7 échouent** ;
- `1 = 0` remplacé par une clause vide → le test de la clause fausse échoue ;
- `params.extend(sorted(trips))` retiré → **5 tests sur 7 échouent**.

Une première tentative de la première mutation n'appliquait rien — l'indentation
de la branche ne correspondait pas au motif. Elle « passait » donc sans rien
casser, et aurait fait conclure à tort que les tests ne détectaient rien. Le
motif est vérifié avant de croire le résultat : c'est le seul moyen de ne pas
se fier à une mutation qui n'a jamais eu lieu.
"""

import csv
import re
from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.filtres import conditions, lire
from comptagefer.timetable import import_timetable

PHOTO = {"precedent": None, "courant": {"trip_id": "T", "status": "SCHEDULED"}, "suivant": None}

# Deux lignes distinctes. Une seule ne prouverait rien : avec une seule ligne
# dans le GTFS, un `trip_id IN (...)` trop large passerait le test.
LIGNES = ["TER_LYON_VALENCE", "TER_VALENCE_NIMES"]


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _hhmm(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def _timetable(data_dir: Path) -> Path:
    """Un horaire minimal mais réel : deux lignes, deux circulations chacune.

    Le cinquième argument est `routes.txt`. Sans lui, `import_timetable`
    crée bien les tables `ligne` et `trip_ligne` mais les laisse vides :
    c'est ce qui faisait échouer ces tests, et c'est exactement le piège que
    la suite ancienne ne pouvait pas voir — elle n'avait pas de base du tout.
    """
    trips = data_dir / "trips.txt"
    times = data_dir / "stop_times.txt"
    days = data_dir / "calendar_dates.txt"
    routes = data_dir / "routes.txt"

    _write(
        routes,
        [
            {"route_id": "TER_LYON_VALENCE", "route_short_name": "Lyon - Valence",
             "route_long_name": "TER Lyon Part-Dieu - Valence", "route_type": "2"},
            {"route_id": "TER_VALENCE_NIMES", "route_short_name": "Valence - Nimes",
             "route_long_name": "TER Valence - Nimes Pont du Gard", "route_type": "2"},
        ],
    )

    lignes = [
        ("TER_LYON_VALENCE", "S1", "LYON_VAL_1"),
        ("TER_LYON_VALENCE", "S1", "LYON_VAL_2"),
        ("TER_VALENCE_NIMES", "S2", "VAL_NIMES_1"),
    ]
    _write(
        trips,
        [
            {"route_id": route, "service_id": service, "trip_id": trip}
            for route, service, trip in lignes
        ],
    )
    _write(
        times,
        [
            {
                "trip_id": trip,
                "arrival_time": _hhmm(8 * 3600 + 600 * position),
                "departure_time": _hhmm(8 * 3600 + 600 * position),
                "stop_id": stop,
                "stop_sequence": str(position),
            }
            for trip in ("LYON_VAL_1", "LYON_VAL_2", "VAL_NIMES_1")
            for position, stop in enumerate(("Lyon", "Valence", "Nimes"), start=1)
        ],
    )
    _write(
        days,
        [
            {"service_id": "S1", "date": "20260925", "exception_type": "1"},
            {"service_id": "S2", "date": "20260925", "exception_type": "1"},
        ],
    )
    database = data_dir / "timetable.db"
    import_timetable(database, trips, times, days, routes)
    return database


def _poster(client, **champs):
    corps = {
        "client_id": champs["client_id"],
        "origin_stop_id": champs.get("o", "StopArea:Lyon"),
        "destination_stop_id": champs.get("d", "StopArea:Valence"),
        "origin_name": champs.get("on", "Lyon Part Dieu"),
        "destination_name": champs.get("dn", "Valence"),
        "trip_id": champs["trip"],
        "reliability": champs.get("reliability", 70),
        "snapshot": PHOTO,
    }
    if "passengers" in champs:
        corps["passengers"] = champs["passengers"]
    if "pseudo" in champs:
        corps["pseudo"] = champs["pseudo"]
    return client.post("/api/sessions", json=corps)


def _site(tmp_path: Path) -> TestClient:
    """Un site avec un horaire et trois relevés répartis sur deux lignes.

    Les pseudos sont ce que le test cherche dans la page : sans eux, deux
    relevés de la même ligne seraient indiscernables et « alice absent »
    ne prouverait rien.
    """
    _timetable(tmp_path)
    client = TestClient(create_app(tmp_path))
    # Deux relevés sur la première ligne, un sur la seconde.
    _poster(client, client_id="a", trip="LYON_VAL_1", passengers=10, pseudo="alice",
            on="Lyon", dn="Valence")
    _poster(client, client_id="b", trip="LYON_VAL_2", passengers=20, pseudo="bob",
            on="Lyon", dn="Valence")
    _poster(client, client_id="c", trip="VAL_NIMES_1", passengers=30, pseudo="carol",
            on="Valence", dn="Nimes")
    return client


def _noms(page: str) -> list[str]:
    return [nom for nom in ("alice", "bob", "carol") if nom in page]


# --- la clause, avec les vraies circulations --------------------------------

def test_the_line_filter_keeps_only_that_line(tmp_path):
    """Le cas nominal : deux lignes, deux relevets, un filtre qui trie.

    Sans base d'horaires, `_trips_de_ligne` renvoie un ensemble vide et la
    clause devient `1 = 0` : la page est vide, et le test passe quand meme
    parce qu'il attend une liste vide. Avec de vraies circulations, la liste
    doit contenir les deux relevets de la ligne demandee, et pas celui de
    l'autre.
    """
    client = _site(tmp_path)

    page = client.get("/comptages?ligne=TER_LYON_VALENCE").text

    assert "alice" in page and "bob" in page, "les deux releves de la ligne doivent rester"
    assert "carol" not in page, "le releve de l'autre ligne ne doit pas rester"


def test_the_other_line_gives_the_other_relevés(tmp_path):
    """Le filtre inverse rend la liste complémentaire.

    C'est ce qui distingue un filtre d'une liste : si les deux lignes
    donnaient la meme chose, la clause ne filtrerait rien et le test
    precedent passerait encore.
    """
    client = _site(tmp_path)

    page = client.get("/comptages?ligne=TER_VALENCE_NIMES").text

    assert "carol" in page
    assert "alice" not in page and "bob" not in page


def test_the_filtered_page_says_which_line_it_kept(tmp_path):
    """Un filtre actif non nomme est un filtre qu'on ne croit pas applique."""
    client = _site(tmp_path)

    page = client.get("/comptages?ligne=TER_LYON_VALENCE").text

    assert "TER_LYON_VALENCE" in page, "la page doit nommer la ligne filtree"


def test_an_unknown_line_is_dropped_and_says_so(tmp_path):
    """Une ligne inconnue est écartée **et dite**.

    Le code choisit de rendre la liste entière plutôt qu'une page vide, comme
    pour les autres filtres illisibles : un lecteur qui s'est trompé de ligne
    veut ses données, pas un néant. Le risque serait donc l'inverse — afficher
    la liste entière sans rien dire, et le lecteur croirait que son filtre
    n'a pas été appliqué. C'est donc le message qui est vérifié ici, avec le
    lien qui enlève le filtre.
    """
    client = _site(tmp_path)

    page = client.get("/comptages?ligne=TER_INEXISTANTE").text

    assert "n'est pas dans le GTFS" in page, "la page doit dire que la ligne est inconnue"
    assert "filtre ignoré" in page, "et qu'il a été ignoré, pas mal appliqué"
    # La liste revient, entière : c'est le choix, et il doit rester visible
    # pour que le lecteur sache qu'il n'a rien filtré.
    assert "alice" in page and "carol" in page, (
        "un filtre écarté rend la liste entière ; c'est le comportement voulu"
    )
    # Le lien d'effacement ne doit pas garder la mauvaise ligne.
    retirer = re.search(r'class=.retirer.[^>]*', page)
    assert retirer is not None, "le lien qui enlève le filtre est obligatoire"
    assert "ligne=TER_INEXISTANTE" not in retirer.group(0)


def test_a_count_without_trip_id_disappears_when_a_line_is_filtered(tmp_path):
    """Un comptage sans `trip_id` n'appartient a aucune ligne.

    On ne lui invente pas une ligne, pas plus qu'on ne lui invente un
    effectif. C'est le même principe que pour le filtre par ligne.
    """
    client = TestClient(create_app(tmp_path))
    _timetable(tmp_path)
    client.post(
        "/api/sessions",
        json={
            "client_id": "sans-trip",
            "origin_stop_id": "StopArea:Lyon",
            "destination_stop_id": "StopArea:Valence",
            "origin_name": "Lyon",
            "destination_name": "Valence",
            "passengers": 12,
            "reliability": 70,
            "snapshot": PHOTO,
        },
    )

    sans = client.get("/comptages").text
    assert "Lyon" in sans, "le releve doit etre visible sans filtre"
    avec = client.get("/comptages?ligne=TER_LYON_VALENCE").text
    assert "Aucun comptage ne correspond" in avec, (
        "un comptage sans trip_id ne peut pas etre rattache a une ligne"
    )


def test_the_clause_uses_one_placeholder_per_trip(tmp_path):
    """Chaque circulation a son `?`, et les valeurs sont parametrees.

    Un `?` de moins que la liste de valeurs lèverait une erreur SQLite ; un
    `?` de plus que la liste prendrait la première valeur pour un nom de
    colonne. Les deux cassent au premier vrai appel, donc le test les
    attrape ici plutot qu'en production.
    """
    client = _site(tmp_path)
    filtres = lire("", "", "", "TER_LYON_VALENCE", lignes_disponibles=lambda _: {"route_id": "L"})
    trips = frozenset({"LYON_VAL_1", "LYON_VAL_2"})

    where, params = conditions(filtres, trips)

    assert where.count("?") == len(trips), f"{where} : un ? par circulation"
    assert sorted(params) == ["LYON_VAL_1", "LYON_VAL_2"], "les valeurs sont les trip_id"


def test_no_trip_means_the_clause_is_false_not_absent(tmp_path):
    """Sans circulation, la clause doit etre fausse, pas absente.

    C'est le cas que la suite ne voyait jamais, parce que `_trips_de_ligne`
    renvoyait toujours vide : la clause `1 = 0` n'etait donc atteignable que
    par accident. Elle reste la plus importante des deux — une clause absente
    afficherait la liste entiere.
    """
    filtres = lire("", "", "", "L", lignes_disponibles=lambda _: {"route_id": "L"})

    where, params = conditions(filtres, frozenset())

    assert where == "1 = 0", f"la clause doit etre fausse : {where}"
    assert params == [], "aucun parametre a passer"
