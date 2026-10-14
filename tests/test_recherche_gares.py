"""La recherche de gares sans égard à la casse ni aux accents, et la paire.

Trois volets, un fichier : la clé de comparaison (`cle_gare`), `/api/stops`
(liste courte, puis complète à partir de 5 caractères), et le filtre `?gare=` /
`?gare2=` de `/comptages` (une paire se lit dans les deux sens).
"""

import csv
import re
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.offer import (
    FUSION_METRES,
    PLAFOND_LISTE_COMPLETE,
    _distance_m,
    _stop_family,
    cle_gare,
    import_stop_names,
    open_stops,
    search_stops,
)

PHOTO = {"precedent": None, "courant": {"trip_id": "T", "status": "SCHEDULED"}, "suivant": None}


def _stops(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    """Des aires `(stop_id, nom)`, sans voie : la recherche ne porte que sur elles."""
    fichier = tmp_path / "stops.txt"
    with fichier.open("w", newline="") as handle:
        ecrit = csv.writer(handle)
        ecrit.writerow(
            ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"]
        )
        for i, (stop_id, nom) in enumerate(rows):
            ecrit.writerow([stop_id, nom, 45 + i / 1000, 4 + i / 1000, 1, ""])
    base = tmp_path / "stops.db"
    import_stop_names(base, fichier)
    return base


def _stops_detailles(tmp_path: Path, rows: list[list[str]]) -> Path:
    fichier = tmp_path / "stops.txt"
    with fichier.open("w", newline="") as handle:
        ecrit = csv.writer(handle)
        ecrit.writerow(
            ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"]
        )
        ecrit.writerows(rows)
    base = tmp_path / "stops.db"
    import_stop_names(base, fichier)
    return base


# --- la clé de comparaison --------------------------------------------------


def test_la_cle_ignore_casse_accents_tirets_et_espaces():
    assert cle_gare("Béziers") == cle_gare("BEZIERS") == cle_gare("beziers") == "beziers"
    assert cle_gare("Saint-Étienne Châteaucreux") == "saint etienne chateaucreux"
    assert cle_gare("  saint   etienne ") == "saint etienne"
    assert cle_gare("L'Isle-sur-la-Sorgue") == cle_gare("l isle sur la sorgue")
    assert cle_gare("Étienne") == cle_gare("etienne") == cle_gare("ÉTIENNE")
    assert cle_gare("Cœur") == "coeur"
    assert cle_gare("") == ""


def test_la_cle_ne_confond_pas_deux_gares_distinctes():
    assert cle_gare("Lyon") != cle_gare("Lyon Part-Dieu")
    assert cle_gare("St Étienne") != cle_gare("Saint-Étienne"), "une abréviation n'est pas une graphie"


def test_la_recherche_trouve_malgre_casse_et_accents(tmp_path):
    base = _stops(
        tmp_path,
        [
            ("A1", "Béziers"),
            ("A2", "Saint-Étienne Châteaucreux"),
            ("A3", "Lyon Part-Dieu"),
            ("A4", "Étienne-le-Haut"),
        ],
    )

    assert [r["name"] for r in search_stops(base, "BEZIERS")] == ["Béziers"]
    assert [r["name"] for r in search_stops(base, "beziers")] == ["Béziers"]
    assert [r["name"] for r in search_stops(base, "Béz")] == ["Béziers"]
    assert [r["name"] for r in search_stops(base, "saint etienne")] == [
        "Saint-Étienne Châteaucreux"
    ]
    assert [r["name"] for r in search_stops(base, "Étienne")] == [
        "Étienne-le-Haut",
        "Saint-Étienne Châteaucreux",
    ]
    assert [r["name"] for r in search_stops(base, "etienne")] == [
        "Étienne-le-Haut",
        "Saint-Étienne Châteaucreux",
    ]


def test_la_recherche_prend_les_jokers_sql_pour_du_texte(tmp_path):
    base = _stops(tmp_path, [("A1", "Lyon"), ("A2", "Vienne")])

    assert search_stops(base, "%%") == []
    assert search_stops(base, "l_") == []


def test_les_aliases_de_gare_du_nord_rejoignent_une_famille(tmp_path):
    base = _stops_detailles(
        tmp_path,
        [
            ["IDFM:71410", "Gare du Nord", "48.88095669783061", "2.355687439691836", "1", ""],
            [
                "IDFM:monomodalStopPlace:462394",
                "Gare du Nord",
                "48.88230685842714",
                "2.356693437528564",
                "0",
                "IDFM:71410",
            ],
            ["IDFM:22149", "Gare du Nord", "48.879510553130615", "2.356768884572096", "0", "IDFM:71410"],
            ["IDFM:463182", "Gare du Nord", "48.87968001223662", "2.357342884924845", "0", "IDFM:71410"],
            ["IDFM:463183", "Gare du Nord", "48.87959170741013", "2.356808788205409", "0", "IDFM:71410"],
            ["IDFM:492715", "Paris Gare du Nord", "48.881638909220364", "2.3578743724447087", "0", "IDFM:71410"],
            ["IDFM:471102", "Paris Gare du Nord", "48.881815983270954", "2.3573540247231692", "0", "IDFM:71410"],
            ["StopArea:OCE87271007", "Paris Gare du Nord", "48.880136", "2.354851", "1", ""],
            [
                "StopPoint:OCETGV INOUI-87271007",
                "Paris Gare du Nord",
                "48.880136",
                "2.354851",
                "0",
                "StopArea:OCE87271007",
            ],
            [
                "StopPoint:OCETrain TER-87271007",
                "Paris Gare du Nord",
                "48.880136",
                "2.354851",
                "0",
                "StopArea:OCE87271007",
            ],
            [
                "StopPoint:OCETrain-87271007",
                "Paris Gare du Nord",
                "48.880136",
                "2.354851",
                "0",
                "StopArea:OCE87271007",
            ],
        ],
    )

    attendu = [{"stop_id": "IDFM:71410", "name": "Gare du Nord"}]
    assert search_stops(base, "Gare du Nord") == attendu
    assert search_stops(base, "Paris Gare du Nord") == attendu
    famille = {
        "IDFM:71410",
        "IDFM:monomodalStopPlace:462394",
        "IDFM:22149",
        "IDFM:463182",
        "IDFM:463183",
        "IDFM:492715",
        "IDFM:471102",
        "StopArea:OCE87271007",
        "StopPoint:OCETGV INOUI-87271007",
        "StopPoint:OCETrain TER-87271007",
        "StopPoint:OCETrain-87271007",
    }
    assert set(_stop_family(base, "IDFM:71410")) == famille
    assert set(_stop_family(base, "StopArea:OCE87271007")) == famille
    assert _stop_family(base, "IDFM:471102") == ["IDFM:471102"]


def test_les_aliases_inter_sources_verifies_hors_nord_rejoignent_une_famille(tmp_path):
    """Les libellés des ZIP SNCF/IDFM pointent vers les mêmes aires proches."""
    familles = [
        (
            "Gare Saint-Lazare",
            [
                ("StopArea:OCE87384008", "Paris Saint-Lazare", "48.87624200", "2.325331000", "StopPoint:OCETrain TER-87384008", "Paris Saint-Lazare"),
                ("IDFM:71370", "Gare Saint-Lazare", "48.875942631522264", "2.325351594334675", "IDFM:472273", "Paris Saint-Lazare"),
            ],
        ),
        (
            "Gare de l'Est",
            [
                ("StopArea:OCE87113001", "Paris Est", "48.87674200", "2.358424000", "StopPoint:OCEICE-87113001", "Paris Est"),
                ("IDFM:71359", "Gare de l'Est", "48.87720933215212", "2.358644807091585", "IDFM:471856", "Paris Est"),
            ],
        ),
        (
            "Champigny-sur-Yonne",
            [
                ("StopArea:OCE87683128", "Champigny", "48.32374200", "3.130572000", "StopPoint:OCETrain TER-87683128", "Champigny"),
                ("IDFM:411462", "Champigny-sur-Yonne", "48.32421062271411", "3.130154840410363", "IDFM:monomodalStopPlace:411461", "Champigny-sur-Yonne"),
            ],
        ),
        (
            "Aéroport CDG (Terminal 2) - TGV",
            [
                ("StopArea:OCE87271494", "Aéroport Charles de Gaulle 2 TGV", "49.00361800", "2.570812000", "StopPoint:OCETGV INOUI-87271494", "Aéroport Charles de Gaulle 2 TGV"),
                ("IDFM:73699", "Aéroport CDG (Terminal 2) - TGV", "49.00395290355643", "2.5699155572682373", "IDFM:9989", "Gare TGV-SNCF"),
            ],
        ),
        (
            "Saint-Quentin en Yvelines - Montigny-le-Bretonneux",
            [
                ("StopArea:OCE87393843", "Saint-Quentin en Yvelines", "48.78657000", "2.045230000", "StopPoint:OCETrain TER-87393843", "Saint-Quentin en Yvelines"),
                ("IDFM:63812", "Saint-Quentin en Yvelines - Montigny-le-Bretonneux", "48.78737457762048", "2.0453376716443157", "IDFM:monomodalStopPlace:461504", "Saint-Quentin en Yvelines - Montigny-le-Bretonneux"),
            ],
        ),
        (
            "Gare Montparnasse",
            [
                ("StopArea:OCE87391003", "Paris Montparnasse Hall 1 - 2", "48.84117200", "2.320514000", "StopPoint:OCETGV INOUI-87391003", "Paris Montparnasse Hall 1 - 2"),
                ("StopArea:OCE87391102", "Paris Montparnasse Vaugirard", "48.84117200", "2.320514000", "StopPoint:OCETGV INOUI-87391102", "Paris Montparnasse Vaugirard"),
                ("IDFM:71139", "Gare Montparnasse", "48.84067243107856", "2.319342991047202", "IDFM:monomodalStopPlace:43238", "Gare Montparnasse"),
            ],
        ),
        (
            "Gare de Lyon",
            [
                ("StopArea:OCE87686006", "Paris Gare de Lyon Hall 1 - 2", "48.84494500", "2.373481000", "StopPoint:OCETGV INOUI-87686006", "Paris Gare de Lyon Hall 1 - 2"),
                ("IDFM:73626", "Gare de Lyon", "48.84456941572047", "2.3751457799393743", "IDFM:471451", "Paris Gare de Lyon"),
            ],
        ),
        (
            "Gare d'Austerlitz",
            [
                ("StopArea:OCE87547000", "Paris Austerlitz", "48.84228500", "2.364891000", "StopPoint:OCEINTERCITES-87547000", "Paris Austerlitz"),
                ("IDFM:71135", "Gare d'Austerlitz", "48.84235421610177", "2.3668334950792076", "IDFM:471473", "Paris Austerlitz"),
            ],
        ),
    ]
    rows = []
    attendues = []
    for requete, aires in familles:
        aires_ids = set()
        ids = set()
        for aire_id, nom, lat, lon, enfant_id, enfant_nom in aires:
            aires_ids.add(aire_id)
            ids.update((aire_id, enfant_id))
            rows.extend(
                [
                    [aire_id, nom, lat, lon, "1", ""],
                    [enfant_id, enfant_nom, lat, lon, "0", aire_id],
                ]
            )
        attendues.append((requete, aires_ids, ids))

    base = _stops_detailles(tmp_path, rows)
    for requete, aires_ids, ids in attendues:
        trouvees = search_stops(base, requete, complete=True)
        assert len(trouvees) == 1, f"{requete!r} devrait désigner une seule gare: {trouvees}"
        for stop_id in aires_ids:
            assert ids <= set(_stop_family(base, stop_id)), f"famille incomplète pour {stop_id}"


def test_alias_champigny_yonne_ne_fusionne_pas_le_champigny_du_val_de_marne(tmp_path):
    base = _stops_detailles(
        tmp_path,
        [
            ["StopArea:OCE87683128", "Champigny", "48.32374200", "3.130572000", "1", ""],
            ["StopPoint:OCECar TER-87683128", "Champigny", "48.32374200", "3.130572000", "0", "StopArea:OCE87683128"],
            ["StopPoint:OCETrain TER-87683128", "Champigny", "48.32374200", "3.130572000", "0", "StopArea:OCE87683128"],
            ["IDFM:411462", "Champigny-sur-Yonne", "48.32421062271411", "3.130154840410363", "1", ""],
            ["IDFM:monomodalStopPlace:411461", "Champigny-sur-Yonne", "48.32421062271411", "3.130154840410363", "0", "IDFM:411462"],
            ["IDFM:73042", "Champigny", "48.8070899464093", "2.510913169046467", "1", ""],
            ["IDFM:monomodalStopPlace:58270", "Champigny", "48.80682309456137", "2.510538043646919", "0", "IDFM:73042"],
            ["IDFM:473963", "Champigny", "48.80688714445374", "2.510547435439302", "0", "IDFM:73042"],
            ["IDFM:473962", "Champigny", "48.80680592477889", "2.510480117118819", "0", "IDFM:73042"],
        ],
    )

    yonne = search_stops(base, "Champigny-sur-Yonne", complete=True)
    assert len(yonne) == 1
    famille_yonne = set(_stop_family(base, "IDFM:411462"))
    assert yonne[0]["stop_id"] in famille_yonne
    assert {"StopArea:OCE87683128", "IDFM:411462"} <= famille_yonne
    assert "IDFM:73042" not in famille_yonne

    generique = search_stops(base, "Champigny", complete=True)
    assert len(generique) == 2
    assert all(row["name"] == "Champigny" for row in generique)
    assert "IDFM:73042" in {row["stop_id"] for row in generique}
    assert any(row["stop_id"] in famille_yonne for row in generique)


def test_les_voisins_distincts_ne_sont_pas_reconcilies_par_proximite_seule(tmp_path):
    base = _stops_detailles(
        tmp_path,
        [
            ["StopArea:OCE87384008", "Paris Saint-Lazare", "48.87624200", "2.325331000", "1", ""],
            ["IDFM:71370", "Gare Saint-Lazare", "48.875942631522264", "2.325351594334675", "1", ""],
            ["IDFM:73688", "Haussmann Saint-Lazare", "48.87499886199355", "2.3286461783787527", "1", ""],
            ["StopArea:OCE87547000", "Paris Austerlitz", "48.84228500", "2.364891000", "1", ""],
            ["IDFM:71135", "Gare d'Austerlitz", "48.84235421610177", "2.3668334950792076", "1", ""],
            ["StopArea:OCE87737429", "Paris-Austerlitz Routiere", "48.84153600", "2.367945000", "1", ""],
            ["StopArea:OCE87393702", "Massy TGV", "48.72575800", "2.261254000", "1", ""],
            ["IDFM:63244", "Massy - Palaiseau", "48.7244580528689", "2.258823567644015", "1", ""],
            ["IDFM:71264", "Châtelet", "48.85835374843885", "2.348491031653374", "1", ""],
            ["IDFM:474151", "Châtelet - Les Halles", "48.86174503191322", "2.34697651043533", "1", ""],
        ],
    )

    assert _distance_m(48.876242, 2.325331, 48.87499886199355, 2.3286461783787527) < FUSION_METRES
    assert _distance_m(48.84235421610177, 2.3668334950792076, 48.841536, 2.367945) < FUSION_METRES
    assert _distance_m(48.725758, 2.261254, 48.7244580528689, 2.258823567644015) < FUSION_METRES
    paris_saint_lazare = set(_stop_family(base, "StopArea:OCE87384008"))
    austerlitz = set(_stop_family(base, "IDFM:71135"))
    massy_tgv = set(_stop_family(base, "StopArea:OCE87393702"))
    massy_palaiseau = set(_stop_family(base, "IDFM:63244"))
    chatelet = set(_stop_family(base, "IDFM:71264"))
    chatelet_les_halles = set(_stop_family(base, "IDFM:474151"))
    assert "IDFM:73688" not in paris_saint_lazare
    assert "StopArea:OCE87737429" not in austerlitz
    assert massy_tgv.isdisjoint(massy_palaiseau)
    assert chatelet.isdisjoint(chatelet_les_halles)


def test_la_variante_idfm_du_point_trouve_son_aire_parente(tmp_path):
    base = _stops_detailles(
        tmp_path,
        [
            ["IDFM:71410", "Gare du Nord", "48.88095669783061", "2.355687439691836", "1", ""],
            [
                "IDFM:492715",
                "Paris Gare du Nord",
                "48.881638909220364",
                "2.3578743724447087",
                "0",
                "IDFM:71410",
            ],
        ],
    )

    assert search_stops(base, "Paris Gare du Nord") == [
        {"stop_id": "IDFM:71410", "name": "Gare du Nord"}
    ]
    assert search_stops(base, "Paris-Gare du Nord") == [
        {"stop_id": "IDFM:71410", "name": "Gare du Nord"}
    ]


def test_les_aires_reelles_de_chatelet_et_leurs_quais_restent_distincts(tmp_path):
    base = _stops_detailles(
        tmp_path,
        [
            ["IDFM:71264", "Châtelet", "48.85835374843885", "2.348491031653374", "1", ""],
            ["IDFM:22364", "Châtelet", "48.85693592038867", "2.3482429871100323", "0", "IDFM:71264"],
            ["IDFM:474151", "Châtelet - Les Halles", "48.86174503191322", "2.34697651043533", "1", ""],
            [
                "IDFM:monomodalStopPlace:45102",
                "Châtelet - Les Halles",
                "48.86174503191322",
                "2.34697651043533",
                "0",
                "IDFM:474151",
            ],
            ["IDFM:471719", "Châtelet Les Halles", "48.86179993968754", "2.346820432438438", "0", "IDFM:474151"],
        ],
    )

    distance = _distance_m(
        48.85835374843885,
        2.348491031653374,
        48.86174503191322,
        2.34697651043533,
    )
    assert distance < FUSION_METRES
    assert search_stops(base, "Châtelet", complete=True) == [
        {"stop_id": "IDFM:71264", "name": "Châtelet"},
        {"stop_id": "IDFM:474151", "name": "Châtelet - Les Halles"},
    ]
    assert search_stops(base, "Chatelet Les Halles", complete=True) == [
        {"stop_id": "IDFM:474151", "name": "Châtelet - Les Halles"}
    ]
    assert set(_stop_family(base, "IDFM:474151")) == {
        "IDFM:474151",
        "IDFM:monomodalStopPlace:45102",
        "IDFM:471719",
    }
    assert set(_stop_family(base, "IDFM:71264")) == {"IDFM:71264", "IDFM:22364"}
    assert _stop_family(base, "IDFM:471719") == ["IDFM:471719"]


def test_les_aliases_lointains_et_les_gares_voisines_restent_distincts(tmp_path):
    base = _stops_detailles(
        tmp_path,
        [
            ["IDFM:71410", "Gare du Nord", "48.88095669783061", "2.355687439691836", "1", ""],
            ["IDFM:monomodalStopPlace:462394", "Gare du Nord", "48.88230685842714", "2.356693437528564", "0", "IDFM:71410"],
            ["StopArea:OCE87271007", "Paris Gare du Nord", "48.880136", "2.354851", "1", ""],
            ["StopPoint:OCETrain-87271007", "Paris Gare du Nord", "48.880136", "2.354851", "0", "StopArea:OCE87271007"],
            ["StopArea:GARE_NORD_DISTANTE", "Gare du Nord", "48.9", "2.7", "1", ""],
            ["StopPoint:GARE_NORD_DISTANTE", "Gare du Nord", "48.9", "2.7", "0", "StopArea:GARE_NORD_DISTANTE"],
            ["StopArea:LA_CHAPELLE", "La Chapelle", "48.883", "2.3565", "1", ""],
            ["StopPoint:LA_CHAPELLE", "La Chapelle", "48.883", "2.3565", "0", "StopArea:LA_CHAPELLE"],
        ],
    )

    found = search_stops(base, "gare du nord", complete=True)
    assert len(found) == 2
    assert "StopArea:GARE_NORD_DISTANTE" in {row["stop_id"] for row in found}
    assert len({row["stop_id"] for row in found} & {"IDFM:71410", "StopArea:OCE87271007"}) == 1
    famille_proche = set(_stop_family(base, "IDFM:71410"))
    assert {"IDFM:71410", "StopArea:OCE87271007"} <= famille_proche
    assert "StopArea:GARE_NORD_DISTANTE" not in famille_proche
    assert "StopArea:LA_CHAPELLE" not in famille_proche
    assert set(_stop_family(base, "StopArea:GARE_NORD_DISTANTE")) == {
        "StopArea:GARE_NORD_DISTANTE",
        "StopPoint:GARE_NORD_DISTANTE",
    }


def test_une_base_existante_recoit_ses_cles_a_louverture(tmp_path):
    """Une base d'avant `name_key` n'a pas à être réimportée, et la migration est stable."""
    base = tmp_path / "stops.db"
    with sqlite3.connect(base) as ancienne:
        ancienne.execute(
            "CREATE TABLE stop (stop_id TEXT PRIMARY KEY, name TEXT NOT NULL, lat REAL,"
            " lon REAL, parent TEXT, is_area INTEGER NOT NULL)"
        )
        ancienne.execute("INSERT INTO stop VALUES ('A1', 'Béziers', 43.3, 3.2, NULL, 1)")

    assert [r["name"] for r in search_stops(base, "beziers")] == ["Béziers"]
    with open_stops(base) as connection:  # une seconde ouverture ne casse rien
        assert connection.execute("SELECT name_key FROM stop").fetchall() == [("beziers",)]


# --- /api/stops --------------------------------------------------------------


def _client_de_nombreuses_gares(tmp_path: Path, n: int = 30) -> TestClient:
    data = tmp_path / "data"
    data.mkdir()
    lignes = [(f"A{i}", f"Saint-Étienne {i:03d}") for i in range(n)]
    lignes += [(f"B{i}", f"Sainte-Foy {i}") for i in range(3)]
    fichier = tmp_path / "stops.txt"
    with fichier.open("w", newline="") as handle:
        ecrit = csv.writer(handle)
        ecrit.writerow(
            ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"]
        )
        for i, (stop_id, nom) in enumerate(lignes):
            ecrit.writerow([stop_id, nom, 45 + i / 100, 4 + i / 100, 1, ""])
    import_stop_names(data / "stops.db", fichier)
    return TestClient(create_app(data))


def test_api_stops_limite_a_huit_sous_cinq_caracteres_meme_avec_tout(tmp_path):
    client = _client_de_nombreuses_gares(tmp_path)

    assert len(client.get("/api/stops", params={"q": "sa"}).json()) == 8
    # « sain » = 4 caractères : `tout` est sans effet.
    assert len(client.get("/api/stops", params={"q": "sain", "tout": 1}).json()) == 8


def test_api_stops_donne_toute_la_liste_a_partir_de_cinq_caracteres(tmp_path):
    client = _client_de_nombreuses_gares(tmp_path)

    court = client.get("/api/stops", params={"q": "saint"}).json()
    complet = client.get("/api/stops", params={"q": "saint", "tout": 1}).json()
    accents = client.get("/api/stops", params={"q": "ÉTIENNE", "tout": 1}).json()

    assert len(court) == 8, "sans `tout`, la liste reste courte"
    assert len(complet) == 33, f"30 Saint-Étienne + 3 Sainte-Foy : {len(complet)}"
    assert len(accents) == 30
    assert [g["name"] for g in complet] == sorted(g["name"] for g in complet)


def test_api_stops_plafonne_la_liste_complete(tmp_path):
    n = PLAFOND_LISTE_COMPLETE + 40
    client = _client_de_nombreuses_gares(tmp_path, n)

    assert len(client.get("/api/stops", params={"q": "saint", "tout": 1}).json()) == (
        PLAFOND_LISTE_COMPLETE
    )


def test_api_stops_trouve_sans_accent_ni_casse(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _stops(data.parent, [("A1", "Béziers")])
    (data.parent / "stops.db").rename(data / "stops.db")
    client = TestClient(create_app(data))

    assert [g["name"] for g in client.get("/api/stops", params={"q": "BEZIERS"}).json()] == [
        "Béziers"
    ]


# --- le filtre par paire -----------------------------------------------------

LIGNES = [
    ("StopArea:Lyon", "Lyon Part-Dieu", "", 45.7608, 4.8557),
    ("StopPoint:LyonA", "Lyon Part-Dieu voie A", "StopArea:Lyon", 45.7608, 4.8557),
    ("StopArea:Valence", "Valence", "", 44.9294, 4.9925),
    ("StopArea:Nimes", "Nîmes", "", 43.8364, 4.3605),
]


def _site(tmp_path: Path) -> TestClient:
    """Cinq relevés : A→B, B→A, A→C, C→A, et un serpent qui traverse A puis B."""
    data = tmp_path / "data"
    data.mkdir()
    fichier = tmp_path / "stops.txt"
    with fichier.open("w", newline="") as handle:
        handle.write("stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n")
        for stop_id, nom, parent, lat, lon in LIGNES:
            handle.write(f"{stop_id},{nom},{lat},{lon},{'0' if parent else '1'},{parent}\n")
    import_stop_names(data / "stops.db", fichier)
    client = TestClient(create_app(data))

    def poster(client_id, o, d, on, dn, legs=None):
        corps = {
            "client_id": client_id,
            "origin_stop_id": o,
            "destination_stop_id": d,
            "origin_name": on,
            "destination_name": dn,
            "reliability": 70,
            "snapshot": PHOTO,
            "pseudo": client_id,
        }
        if legs:
            corps.update(kind="serpent", legs=legs)
        else:
            corps["passengers"] = 10
        assert client.post("/api/sessions", json=corps).status_code < 300

    poster("aller", "StopArea:Lyon", "StopArea:Valence", "Lyon Part-Dieu", "Valence")
    poster("retour", "StopArea:Valence", "StopArea:Lyon", "Valence", "Lyon Part-Dieu")
    poster("lyon-nimes", "StopArea:Lyon", "StopArea:Nimes", "Lyon Part-Dieu", "Nîmes")
    poster("nimes-lyon", "StopArea:Nimes", "StopArea:Lyon", "Nîmes", "Lyon Part-Dieu")
    poster(
        "serpent", "StopArea:Nimes", "StopArea:Valence", "Nîmes", "Valence",
        legs=[
            {"stop_id": "StopArea:Nimes", "onboard": 20},
            {"stop_id": "StopArea:Lyon", "boarded": 1, "alighted": 0},
            {"stop_id": "StopArea:Valence", "boarded": 0, "alighted": 1},
        ],
    )
    return client


def _pseudos(page: str) -> set[str]:
    """Les pseudos des relevés de la liste (un par carte, `cartes` seulement)."""
    cartes = page.split("<div class='cartes'>", 1)[-1].split("</table>")[0]
    return {p for p in ("aller", "retour", "lyon-nimes", "nimes-lyon", "serpent") if p in cartes}


def test_une_paire_garde_les_deux_sens(tmp_path):
    client = _site(tmp_path)

    page = client.get("/comptages", params={"gare": "Lyon Part-Dieu", "gare2": "Valence"}).text

    assert _pseudos(page) == {"aller", "retour"}, "ni Nîmes, ni le serpent qui traverse A puis B"


def test_la_paire_est_symetrique_et_insensible_aux_accents(tmp_path):
    client = _site(tmp_path)

    a = client.get("/comptages", params={"gare": "nimes", "gare2": "LYON PART DIEU"}).text
    b = client.get("/comptages", params={"gare": "Lyon Part-Dieu", "gare2": "Nîmes"}).text

    assert _pseudos(a) == _pseudos(b) == {"lyon-nimes", "nimes-lyon"}


def test_une_seule_gare_garde_le_comportement_actuel(tmp_path):
    client = _site(tmp_path)

    page = client.get("/comptages", params={"gare": "Valence"}).text

    # Extrémités, mais aussi l'arrêt intermédiaire d'un serpent (A puis B).
    assert _pseudos(page) == {"aller", "retour", "serpent"}
    assert _pseudos(client.get("/comptages", params={"gare": "beziers"}).text) == _pseudos(
        client.get("/comptages").text
    ), "une gare inconnue est ignorée et dite, comme avant"


def test_la_seconde_gare_seule_filtre_comme_une_gare(tmp_path):
    client = _site(tmp_path)

    page = client.get("/comptages", params={"gare2": "Valence"}).text

    assert _pseudos(page) == {"aller", "retour", "serpent"}


def test_une_gare_inconnue_dans_la_paire_est_dite(tmp_path):
    client = _site(tmp_path)

    page = client.get("/comptages", params={"gare": "Valence", "gare2": "Atlantide"}).text

    assert "Atlantide" in page and "filtre ignoré" in page
    # La paire est tombée : reste le filtre simple sur Valence.
    assert _pseudos(page) == {"aller", "retour", "serpent"}


def test_deux_fois_la_meme_gare_ne_font_pas_une_paire(tmp_path):
    client = _site(tmp_path)

    page = client.get("/comptages", params={"gare": "Valence", "gare2": "VALENCE"}).text

    assert _pseudos(page) == {"aller", "retour", "serpent"}
    assert "même" in page


def test_la_paire_est_un_chip_et_dans_les_liens(tmp_path):
    client = _site(tmp_path)
    params = {"gare": "Lyon Part-Dieu", "gare2": "Valence", "tri": "trajet", "sens": "asc"}

    page = client.get("/comptages", params=params).text

    assert "gares Lyon Part-Dieu ⇄ Valence" in page
    bascule = re.search(r"href='([^']*vue=paire[^']*)'", page)
    assert bascule is not None
    assert "gare2=Valence" in bascule.group(1) and "gare=Lyon+Part-Dieu" in bascule.group(1)
    paire = client.get(bascule.group(1).replace("&amp;", "&")).text
    assert "gares Lyon Part-Dieu ⇄ Valence" in paire
    assert "Voir la liste des relevés" in paire
    retour = re.search(r"href='([^']*)'>Voir la liste des relevés", paire).group(1)
    assert "gare2=Valence" in retour, "la vue par paire ne perd pas la seconde gare"


def test_le_formulaire_garde_les_deux_gares_et_tout_enlever_les_retire(tmp_path):
    client = _site(tmp_path)

    page = client.get(
        "/comptages", params={"gare": "Lyon Part-Dieu", "gare2": "Valence", "mode": "unique"}
    ).text

    assert "name='gare2'" in page and "value='Valence'" in page
    assert "et la gare" in page
    sortie = re.search(r"class='retirer' href='([^']*)'", page).group(1)
    assert "gare" not in sortie and "mode" not in sortie, f"« Tout enlever » garde un filtre : {sortie}"


def test_la_page_vide_enleve_aussi_la_paire(tmp_path):
    client = _site(tmp_path)

    page = client.get(
        "/comptages", params={"gare": "Valence", "gare2": "Nîmes", "mode": "signale"}
    ).text

    assert "Aucun comptage ne correspond" in page
    sortie = re.search(r"href='([^']*)'>Enlever le filtre", page).group(1)
    assert "gare" not in sortie


def test_la_pagination_garde_la_paire(tmp_path):
    from comptagefer.filtres import Filtres, lien

    filtres = Filtres(gare="Lyon Part-Dieu", gare2="Valence")

    adresse = lien(filtres, "", "", vue="paire", page=2)

    assert "gare2=Valence" in adresse and "page=2" in adresse
