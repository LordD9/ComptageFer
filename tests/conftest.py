"""Les fixtures que plusieurs fichiers de tests partagent.

Rien d'autre ici. Un `conftest.py` est le seul endroit où pytest résout une fixture
**une fois pour toute la session**, quel que soit le fichier qui l'utilise — et
c'est la propriété qui manquait.

Ce fichier existe parce d'un bug de test qui n'en est pas un. `test_browser.py`
avait une fixture `browser` en `scope="session"`, et `test_browser_compte.py` —
écrit plus tard — faisait `from tests.test_browser import page, site`. Les 69 tests
de `test_browser.py` passaient, les 12 du second fichier échouaient **tous** au
setup, avec la même erreur :

    It looks like you are using Playwright Sync API inside the asyncio loop

La cause : importée dans deux modules, une fixture `scope="session"` définit deux
objets distincts. Pytest identifie une fixture par le module qui la définit, pas
par son nom. Le second `sync_playwright()` s'ouvrait donc alors que la boucle
asyncio du premier tournait encore, et Playwright refuse de s'y ouvrir. Aucun test
n'échouait par lui-même : le fichier entier était mort dans la suite complète et
vert dans l'isolement — le pire mode de défaillance pour un test.

Deux règles, donc, et elles s'appliquent au-delà de Playwright :

- **une fixture partagée va dans `conftest.py`**, pas dans un fichier de tests qu'un
  autre importe ;
- **un test qui passe seul et échoue en suite n'est pas un test flaky**, c'est un
  test qui dépend d'un état partagé. Le `pytest` seul le montre : il lance le
  fichier entier dans le même ordre que la suite.

Le viewport est celui d'un iPhone, parce que c'est la cible du projet : une page
qui déborde sur un téléphone est un défaut, et il ne se voit pas dans le HTML.
"""

import threading
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from comptagefer.app import create_app
from comptagefer.offer import import_stop_names
from comptagefer.timetable import import_timetable

# La fenêtre temporelle du scénario GTFS, en minutes. Lisible ici parce que la
# fixture est le seul endroit qui l'impose : les tests qui écrivent des relevés
# reprennent `1_F:TER:1234` et doivent savoir qu'il existe.
TER = "1_F:TER:1234"
TGV = "1_F:TGV:5678"
IC = "1_F:IC:9012"


@pytest.fixture(scope="session")
def navigateur_partage():
    """Un seul Chromium pour toute la session de tests.

    `scope="session"` : lancer un navigateur par fichier serait lent, et deux
    navigateurs ne se partagent rien. C'est ici, dans un `conftest.py`, que cette
    portée est réellement partagée — voir la docstring du module.
    """
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(navigateur_partage):
    """Un contexte neuf par test : localStorage et cookies ne fuient pas.

    Le viewport est celui d'un iPhone, la cible du projet. Les erreurs de page
    sont collectées dans `page.errors` : un script qui meurt au chargement ne
    lève rien dans les assertions Python, et c'est le filet qui le voit.
    """
    context = navigateur_partage.new_context(viewport={"width": 390, "height": 844})
    une_page = context.new_page()
    # Un handler non câblé fait expirer le `wait_for_selector` en 5 s : on échoue
    # vite et avec un message lisible.
    une_page.set_default_timeout(5000)
    erreurs: list[str] = []
    une_page.on("pageerror", lambda exc: erreurs.append(str(exc)))
    une_page.errors = erreurs  # type: ignore[attr-defined]
    yield une_page
    context.close()


@pytest.fixture
def site(tmp_path: Path):
    """L'application réelle, servie sur un vrai port, avec une base complète.

    Un vrai port et un vrai `uvicorn` plutôt qu'un `TestClient` : les
    `expect_navigation`, les cookies et les redirections `303` ne se comportent pas
    de la même façon en mémoire. Un test qui clique un bouton et attend la page
    d'arrivée ne peut pas le faire sur un client de test.
    """
    data = tmp_path / "data"
    data.mkdir()
    _ecrire_stops(tmp_path, data)
    _ecrire_gtfs(tmp_path, data)

    app = create_app(data, admin_token="jeton-admin-test")
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        threading.Event().wait(0.05)
    assert server.started, "le serveur de test n'a pas démarré"

    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=10)


def _console_errors(page) -> list[str]:
    """Les erreurs que le navigateur a vues sur cette page.

    Le filet des tests de navigateur : un script qui meurt au chargement ne lève
    rien dans les assertions Python — le `<script>` est bien dans la page, il
    n'est juste pas exécuté. Il faut donc lire ce que le navigateur a rapporté.

    Ici et pas dans un module de tests, parce que deux fichiers l'utilisent.
    """
    return [e for e in page.errors if e]  # type: ignore[attr-defined]


@pytest.fixture
def base_du_site(tmp_path: Path):
    """La base `app.db` du site de test, pour relire ce que la page ne montre pas.

    `create_app(data)` crée `app.db` dans le dossier qu'on lui donne, donc le
    chemin est celui de la fixture `site`. Elle sert à une seule propriété : « le
    secret affiché à l'écran n'est stocké en clair nulle part ». Cette affirmation
    ne se vérifie pas depuis la page — la page **doit** rendre le secret, c'est son
    rôle — il faut donc relire la base.
    """
    return tmp_path / "data" / "app.db"


def _ecrire_stops(tmp_path: Path, data: Path) -> None:
    """Trois gares sur une ligne, assez pour deux couples distincts.

    `location_type` à 1 pour le `StopArea` (la gare) et 0 pour les `StopPoint`
    (les voies) : c'est ce qui distingue une gare d'une voie dans le GTFS, et un
    couple affiché comme « Lyon Part-Dieu → Vienne » doit venir des `StopArea`.
    """
    lignes = [
        ("StopArea:Lyon", "Lyon Part-Dieu", "", 45.7608, 4.8557),
        ("StopPoint:LyonA", "Lyon Part-Dieu voie A", "StopArea:Lyon", 45.7608, 4.8557),
        ("StopPoint:LyonB", "Lyon Part-Dieu voie B", "StopArea:Lyon", 45.7608, 4.8557),
        ("StopArea:Vienne", "Vienne", "", 45.4481, 4.8789),
        ("StopPoint:VienneA", "Vienne voie 1", "StopArea:Vienne", 45.4481, 4.8789),
        ("StopArea:Valence", "Valence", "", 44.9294, 4.9925),
        ("StopPoint:ValenceA", "Valence voie A", "StopArea:Valence", 44.9294, 4.9925),
    ]
    fichier = tmp_path / "stops.txt"
    with fichier.open("w", newline="") as handle:
        handle.write("stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n")
        for stop_id, nom, parent, lat, lon in lignes:
            handle.write(
                f"{stop_id},{nom},{lat},{lon},{'0' if parent else '1'},{parent}\n"
            )
    import_stop_names(data / "stops.db", fichier)


def _ecrire_gtfs(tmp_path: Path, data: Path) -> None:
    """Un TER, un TGV et un IC autour de maintenant, pour la fenêtre de deux heures.

    GTFS autorise 25:00 pour un passage après minuit, mais pas 00:25 : un départ à
    23h50 suivi d'un arrêt à +20 min déborderait. On décale donc le scénario pour
    qu'il tienne dans la journée, et on garde les trois trains à moins de deux
    heures de maintenant.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    paris = ZoneInfo("Europe/Paris")
    now = datetime.now(paris)

    trips = tmp_path / "trips.txt"
    times = tmp_path / "stop_times.txt"
    dates = tmp_path / "calendar_dates.txt"

    local = now.astimezone(paris)
    jour = local.strftime("%Y%m%d")
    # Départ du TER : une heure pile, dans le passé récent, pour que le train soit
    # déjà parti sans que l'arrêt final ne déborde sur le lendemain.
    heure = max(0, min(23, local.hour - 1))
    anchor = local.replace(hour=heure, minute=0, second=0, microsecond=0)

    # trip_id, service, départ, (arrêt, minutes depuis le départ).
    # Le format réel est `<prefixe>_<sens>:<marqueur>:<numéro>` : `kind_of` prend
    # le troisième champ, donc il faut "1_F:TER:..." et non "TER:1_F:...".
    lignes = [
        (TER, "TER1", anchor, [
            ("StopPoint:LyonA", 0), ("StopPoint:VienneA", 52), ("StopPoint:ValenceA", 104)]),
        (TGV, "TGV1", anchor - timedelta(minutes=25), [
            ("StopPoint:LyonA", 0), ("StopPoint:ValenceA", 110)]),
        (IC, "IC1", anchor + timedelta(minutes=70), [
            ("StopPoint:LyonA", 0), ("StopPoint:VienneA", 40), ("StopPoint:ValenceA", 90)]),
    ]

    with trips.open("w", newline="") as handle:
        handle.write("trip_id,service_id\n")
        for trip_id, service, _depart, _stops in lignes:
            handle.write(f"{trip_id},{service}\n")

    with times.open("w", newline="") as handle:
        handle.write("trip_id,stop_id,departure_time,arrival_time\n")
        for trip_id, _service, depart, stops in lignes:
            for stop_id, decalage in stops:
                moment = (depart + timedelta(minutes=decalage)).strftime("%H:%M:%S")
                handle.write(f"{trip_id},{stop_id},{moment},{moment}\n")

    with dates.open("w", newline="") as handle:
        handle.write("service_id,date,exception_type\n")
        for _trip_id, service, _depart, _stops in lignes:
            handle.write(f"{service},{jour},1\n")

    import_timetable(
        data / "timetable.db",
        trips,
        times,
        dates,
    )