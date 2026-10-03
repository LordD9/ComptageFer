"""Les trains franciliens, importés à côté du national.

Le GTFS national ne contient aucune ligne francilienne — mesuré sur le fichier
du moment, 731 routes, zéro Transilien. Le GTFS de l'Île-de-France Mobilités
n'est pas la SNCF francilienne non plus : sur 2026 routes, 1966 sont du bus. Le
bon fichier est le « transilien-gtfs.zip » de l'OpenData SNCF.

Ces tests vérifient la propriété qui rend l'ajout possible : **les deux jeux
n'ont aucune clé en commun**. Mesuré sur les fichiers réels — 0 sur `trip_id`,
`service_id`, `route_id` comme `stop_id`. Sans cela, importer le second jeu dans
la même base écraserait des trips déjà là, et `INSERT OR REPLACE` le ferait
silencieusement.
"""

import csv
import io
import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import comptagefer.app as app_module
from comptagefer.app import TRANSILIEN_URL, _timetable_has_transilien, ensure_referential
from comptagefer.timetable import TRANSILIEN, kind_of

PARIS = ZoneInfo("Europe/Paris")


def _zip(**fichiers: str) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        for nom, contenu in fichiers.items():
            archive.writestr(nom.replace("__", "."), contenu)
    return tampon.getvalue()


def _csv(entetes: list[str], *lignes: list[str]) -> str:
    """Un fichier GTFS minimal : des en-têtes, puis les lignes.

    Les colonnes de tête sont explicites parce que l'ordre des en-têtes est ce
    qui définit la colonne : un GTFS écrit avec `**colonnes` ferait dépendre la
    lecture de l'ordre d'insertion des clés du dictionnaire, et le test
    passerait pour une raison qui n'a rien à voir avec le code.
    """
    sortie = io.StringIO()
    redacteur = csv.writer(sortie, lineterminator="\n")
    redacteur.writerow(entetes)
    for ligne in lignes:
        redacteur.writerow(ligne)
    return sortie.getvalue()


def _national(depart: datetime) -> bytes:
    jour = depart.astimezone(PARIS).strftime("%Y%m%d")
    heure = depart.astimezone(PARIS).strftime("%H:%M:%S")
    return _zip(
        stops__txt=_csv(["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"],
                        ["StopPoint:OCE1", "Lyon Part-Dieu", "45.76", "4.85", "0", ""]),
        trips__txt=_csv(["trip_id", "service_id", "route_id"], ["1_F:TER:1234", "TER1", "NAT:1"]),
        stop_times__txt=_csv(["trip_id", "stop_id", "departure_time", "arrival_time"],
                             ["1_F:TER:1234", "StopPoint:OCE1", heure, heure]),
        calendar_dates__txt=_csv(["service_id", "date", "exception_type"], ["TER1", jour, "1"]),
        routes__txt=_csv(["route_id", "route_short_name", "route_long_name", "route_type"],
                         ["NAT:1", "C3", "Lyon - Valence", "2"]),
    )


def _transilien(depart: datetime) -> bytes:
    jour = depart.astimezone(PARIS).strftime("%Y%m%d")
    heure = depart.astimezone(PARIS).strftime("%H:%M:%S")
    # Deux gares, pas une : `_pairs` exige `destination.depart_sec >
    # origin.depart_sec`, donc une gare identique en origine et en destination
    # ne donne aucun train. Une seule gare testerait le tri mais pas le chemin.
    return _zip(
        stops__txt=_csv(["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"],
                        ["IDFM:monomodalStopPlace:1", "Nation", "48.84", "2.39", "0", ""],
                        ["IDFM:monomodalStopPlace:2", "Gare de Lyon", "48.84", "2.37", "0", ""]),
        trips__txt=_csv(["trip_id", "service_id", "route_id"],
                        [f"{TRANSILIEN}abc", "TN1", "IDFM:C01727"]),
        stop_times__txt=_csv(["trip_id", "stop_id", "departure_time", "arrival_time"],
                             [f"{TRANSILIEN}abc", "IDFM:monomodalStopPlace:1", heure, heure],
                             [f"{TRANSILIEN}abc", "IDFM:monomodalStopPlace:2", "23:00:00", "23:00:00"]),
        calendar_dates__txt=_csv(["service_id", "date", "exception_type"], ["TN1", jour, "1"]),
        routes__txt=_csv(["route_id", "route_short_name", "route_long_name", "route_type"],
                         ["IDFM:C01727", "A", "RER A", "2"]),
    )


@pytest.fixture
def gtfs(monkeypatch):
    """Les deux archives servies à la place du réseau, avec les URL demandées."""
    maintenant = datetime.now(PARIS).replace(hour=max(1, datetime.now(PARIS).hour - 1), minute=0, second=0, microsecond=0)
    demandees: list[str] = []

    def _faux(url: str, timeout: int = 60) -> bytes:
        demandees.append(url)
        if url == TRANSILIEN_URL:
            return _transilien(maintenant)
        return _national(maintenant)

    monkeypatch.setattr(app_module, "_fetch", _faux)
    return demandees


# --- la nature du francilien -------------------------------------------------


def test_a_transilien_trip_is_named_transilien_and_not_train():
    """`kind_of` doit reconnaître le préfixe IDFM, pas le motif national.

    Le garde de `kind_of` cherche `_F:` ou `_R:`, que les trip_id franciliens
    n'ont pas : sans une porte à part, tous les RER s'afficheraient « Train ».
    Et « TN » brut se lirait comme un nom de ligne dans la liste des trains.
    """
    assert kind_of(f"{TRANSILIEN}abc") == "Transilien"


def test_the_national_trips_keep_their_kind():
    """Le préfixe ne doit rien changer au national."""
    assert kind_of("1_F:TER:1234") == "TER"
    assert kind_of("OCESN1F1187_F:OUI:FR:Line::x") == "TGV"
    assert kind_of("1_F:IC:5678") == "Intercités"


def test_the_two_sets_share_no_key(tmp_path, gtfs):
    """La propriété qui rend l'import dans la même base possible.

    Mesurée sur les fichiers réels : 0 clé commune. Si le jour un `trip_id`
    ou un `route_id` se croisait, `INSERT OR REPLACE` écraserait un trip déjà
    là sans rien dire. Ce test verrouille le raisonnement sur une échelle
    réduite ; la mesure réelle est dans la description de la PR.
    """
    ensure_referential(tmp_path)
    with sqlite3.connect(tmp_path / "timetable.db") as connection:
        trips = {row[0] for row in connection.execute("SELECT trip_id FROM circulation")}
    assert "1_F:TER:1234" in trips
    assert f"{TRANSILIEN}abc" in trips, "le francilien a écrasé le national, ou n'est pas là"


def test_the_timetable_keeps_the_francilien_route_names(tmp_path, gtfs):
    """Le RER A reste rattaché à son nom GTFS pour le filtre des comptages."""
    ensure_referential(tmp_path)
    with sqlite3.connect(tmp_path / "timetable.db") as connection:
        ligne = connection.execute(
            "SELECT nom_court, nom_long FROM ligne WHERE route_id = 'IDFM:C01727'"
        ).fetchone()
    assert ligne == ("A", "RER A")


# --- le francilien seul, sur une base déjà remplie ----------------------------


def test_an_existing_keeps_its_national_trips(tmp_path, gtfs):
    """Le cas normal d'une installation qui tourne déjà.

    Une base avant cette PR a ses lignes nationales et pas les franciliennes.
    L'import francilien se fait alors **sans** réimporter le national. Repartir
    d'un fichier neuf construirait un `timetable.importing` sans les lignes
    existantes, qui le remplacerait ensuite : les 60 000 trips TER
    disparaîtraient au démarrage suivant, sans qu'aucune page ne le dise.

    Une ligne de chaque jeu, et on vérifie que les deux sont là après.
    """
    ensure_referential(tmp_path)
    assert _timetable_has_transilien(tmp_path / "timetable.db")

    # On repart de l'état « avant la PR » : base nationale, pas de francilien.
    import shutil

    shutil.copyfile(tmp_path / "timetable.db", tmp_path / "garde.db")
    with sqlite3.connect(tmp_path / "timetable.db") as connection:
        connection.execute("DELETE FROM circulation WHERE trip_id LIKE 'IDFM:TN:SNCF:%'")
        connection.execute("DELETE FROM ligne WHERE route_id LIKE 'IDFM:%'")
        connection.execute("DELETE FROM trip_ligne WHERE route_id LIKE 'IDFM:%'")
    assert not _timetable_has_transilien(tmp_path / "timetable.db")

    ensure_referential(tmp_path)

    with sqlite3.connect(tmp_path / "timetable.db") as connection:
        trips = {row[0] for row in connection.execute("SELECT trip_id FROM circulation")}
        lignes = {row[0] for row in connection.execute("SELECT route_id FROM ligne")}
    assert "1_F:TER:1234" in trips, "le national a disparu au passage du francilien"
    assert f"{TRANSILIEN}abc" in trips
    assert {"NAT:1", "IDFM:C01727"} <= lignes


def test_the_second_call_does_not_fetch_anything(tmp_path, gtfs):
    """Une fois les deux jeux là, on ne retélécharge rien.

    Sans ce test, une détection défaillante rejouerait un import de 10 Mo à
    chaque démarrage de conteneur, et un airain de 130 Mo sur la carte SD.
    """
    ensure_referential(tmp_path)
    assert len(gtfs) == 2, f"un premier import doit demander les deux archives, pas {gtfs}"
    ensure_referential(tmp_path)
    assert len(gtfs) == 2, f"le deuxième appel a retéléchargé : {gtfs}"


def test_the_francilien_stops_reach_the_stop_database(tmp_path, gtfs):
    """Sans les gares franciliennes, on ne peut ni choisir Paris, ni se géolocaliser."""
    ensure_referential(tmp_path)
    with sqlite3.connect(tmp_path / "stops.db") as connection:
        noms = {row[0] for row in connection.execute("SELECT name FROM stop")}
    assert "Nation" in noms, "les gares franciliennes manquent, le comptage RER est impossible"
    assert "Lyon Part-Dieu" in noms, "les gares nationales ont été écrasées"


def test_no_leftover_folder(tmp_path, gtfs):
    """Les archives décompressées font 80 Mo ; les laisser serait un défaut."""
    ensure_referential(tmp_path)
    assert not (tmp_path / "import").exists()
    assert not (tmp_path / "import-transilien").exists()
    assert not (tmp_path / "timetable.importing").exists()


def test_an_archive_without_a_file_does_not_stop_the_import(tmp_path, gtfs):
    """Un GTFS n'est pas obligé d'avoir le même contenu que son voisin.

    Le GTFS francilien n'a pas de `transfers.txt`, le national si. On prend ce
    qui est là, on n'exige pas une liste — sinon le jour où SNCF ajoute ou
    retire un fichier, l'import échoue au démarrage sans que rien ne le dise.
    """
    import comptagefer.app as module

    maintenant = datetime.now(PARIS)
    sans_routes = _zip(
        stops__txt=_csv(["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"],
                        ["IDFM:1", "Nation", "48.84", "2.39", "0", ""]),
        trips__txt=_csv(["trip_id", "service_id", "route_id"],
                        [f"{TRANSILIEN}zz", "TN1", "IDFM:C1"]),
        stop_times__txt=_csv(["trip_id", "stop_id", "departure_time", "arrival_time"],
                             [f"{TRANSILIEN}zz", "IDFM:1", "08:00:00", "08:00:00"]),
        calendar_dates__txt=_csv(["service_id", "date", "exception_type"],
                                 ["TN1", maintenant.strftime("%Y%m%d"), "1"]),
    )

    def _faux(url: str, timeout: int = 60) -> bytes:
        if url == TRANSILIEN_URL:
            return sans_routes
        return _national(maintenant)

    monkey = getattr(module, "_fetch")
    module._fetch = _faux
    try:
        ensure_referential(tmp_path)
    finally:
        module._fetch = monkey
    with sqlite3.connect(tmp_path / "timetable.db") as connection:
        trips = {row[0] for row in connection.execute("SELECT trip_id FROM circulation")}
    assert f"{TRANSILIEN}zz" in trips, "routes.txt manquant a fait tomber l'import"
    assert monkey is module._fetch
# --- ce que les pages promettront --------------------------------------------


def test_the_train_page_says_the_francilien_has_no_realtime(tmp_path):
    """Une page qui promet un retard qui n'arrive jamais est pire qu'une page muette.

    Le francilien n'a pas de flux GTFS-RT : sans le dire, un RER en retard de
    vingt minutes s'affiche « programmé » et l'usager conclude que son train est
    à l'heure. Le guide le dit dans les deux endroits où l'état du train est
    annoncé.
    """
    from fastapi.testclient import TestClient

    from comptagefer.app import create_app

    client = TestClient(create_app(tmp_path))
    accueil = client.get("/").text
    assert "franciliens" in accueil, "la liste des trains ne mentionne pas le francilien"
    assert "programmé" in accueil, "la page ne dit plus ce qu'est un état « programmé »"

    methode = client.get("/methode").text
    assert "pas de flux temps réel" in methode, "/methode ne dit pas que le francilien n'a pas de temps réel"
    assert "Transilien" in methode, "/methode ne dit pas d'où viennent les horaires franciliens"


def test_the_method_page_still_lists_the_national_sources(tmp_path):
    """Ajouter le francilien ne doit pas faire disparaître le reste.

    La liste des sources est la seule endroit qui dise ce que l'on peut
    attendre comme donnée. Perdre une ligne en ajoutant une autre la rendrait
    fausse.
    """
    from fastapi.testclient import TestClient

    from comptagefer.app import create_app

    methode = TestClient(create_app(tmp_path)).get("/methode").text
    for attendu in ("Trip Updates", "Service Alerts", "SIRI", "Cerema"):
        assert attendu in methode, f"la source {attendu} a disparu de /methode"


def test_the_stop_window_keeps_both_families(tmp_path, gtfs):
    """Deux gares franciliennes doivent donner un train francilien.

    C'est le chemin complet de l'usager : il choisit une gare, une autre, et la
    fenêtre de deux heures doit ramener ce qui passe. Un trip francilien
    importé mais invisible à `_pairs` ne servirait à rien — et il le serait
    facilement, la table `passage` étant indexée sur `(stop_id, depart_sec)`.
    """
    from comptagefer.timetable import listed_trips

    ensure_referential(tmp_path)
    maintenant = datetime.now(PARIS)
    trips = listed_trips(
        tmp_path / "timetable.db",
        tmp_path / "rt.db",
        "IDFM:monomodalStopPlace:1",
        "IDFM:monomodalStopPlace:2",
        maintenant,
        stops_database=tmp_path / "stops.db",
    )
    ids = [item["trip_id"] for item in trips]
    assert f"{TRANSILIEN}abc" in ids, f"le trip francilien ne sort pas de la fenêtre : {ids}"
    kinds = {item["trip_id"]: item["kind"] for item in trips}
    assert kinds[f"{TRANSILIEN}abc"] == "Transilien", (
        f"le francilien est classé « {kinds[f'{TRANSILIEN}abc']} » dans la liste des trains"
    )
