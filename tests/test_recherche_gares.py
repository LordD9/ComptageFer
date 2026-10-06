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
    PLAFOND_LISTE_COMPLETE,
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
