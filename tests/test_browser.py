"""Parcours du formulaire dans un vrai Chromium.

Le JS de la page est écrit à la main dans une chaîne Python. Les tests pytest
ne l'exécutent pas : une page peut casser au chargement et tous rester verts.
Ces tests ouvrent donc un vrai navigateur et traversent le parcours.

Une régression de la forme ``$("#id")`` (le helper est getElementById, qui
attend un id nu) tue le script au chargement et fait disparaître tous les
handlers déclarés après : ``test_page_loads_without_a_script_error`` est le
filet qui l'attrape.
"""

import csv
import json
import threading
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from comptagefer.app import create_app
from comptagefer.offer import import_stop_names
from comptagefer.timetable import import_timetable

PARIS = ZoneInfo("Europe/Paris")

STOPS = [
    # stop_id, nom, parent (StopArea), lat, lon
    ("StopArea:Lyon", "Lyon Part-Dieu", "", 45.7608, 4.8557),
    ("StopPoint:LyonA", "Lyon Part-Dieu voie A", "StopArea:Lyon", 45.7608, 4.8557),
    ("StopPoint:LyonB", "Lyon Part-Dieu voie B", "StopArea:Lyon", 45.7608, 4.8557),
    ("StopArea:Vienne", "Vienne", "", 45.4481, 4.8789),
    ("StopPoint:VienneA", "Vienne voie 1", "StopArea:Vienne", 45.4481, 4.8789),
    ("StopArea:Valence", "Valence", "", 44.9294, 4.9925),
    ("StopPoint:ValenceA", "Valence voie A", "StopArea:Valence", 44.9294, 4.9925),
]


def _write_stop_csv(path: Path) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"])
        for stop_id, name, parent, lat, lon in STOPS:
            writer.writerow([stop_id, name, lat, lon, "0" if parent else "1", parent])


def _write_gtfs(folder: Path, now: datetime) -> None:
    """Un service TER, un TGV et un IC, autour de maintenant, pour que la
    fenêtre de deux heures les fasse tous apparaître.

    GTFS autorise 25:00 pour un passage après minuit, mais pas 00:25 : un
    départ à 23h50 suivi d'un arrêt à +20 min déborderait. On décale donc le
    scenario pour qu'il tienne dans la journée, et on garde les trois trains
    à moins de deux heures de maintenant.
    """
    trips = folder / "trips.txt"
    times = folder / "stop_times.txt"
    dates = folder / "calendar_dates.txt"

    local = now.astimezone(PARIS)
    day = local.strftime("%Y%m%d")
    # Départ du TER : une heure pile, dans le passé récent, pour que le train
    # soit déjà parti sans que l'arrêt final ne déborde sur le lendemain.
    hour = max(0, min(23, local.hour - 1))
    anchor = local.replace(hour=hour, minute=0, second=0, microsecond=0)

    routes = [
        # trip_id, service, départ, (arrêt, minutes depuis le départ)
        # Le format réel est <prefixe>_<sens>:<marqueur>:<numéro> : kind_of
        # prend le troisième champ, donc il faut "1_F:TER:..." et non
        # "TER:1_F:...".
        ("1_F:TER:1234", "TER1", anchor, [
            ("StopPoint:LyonA", 0), ("StopPoint:VienneA", 52), ("StopPoint:ValenceA", 104)]),
        ("1_F:TGV:5678", "TGV1", anchor - timedelta(minutes=25), [
            ("StopPoint:LyonA", 0), ("StopPoint:ValenceA", 110)]),
        ("1_F:IC:9012", "IC1", anchor + timedelta(minutes=70), [
            ("StopPoint:LyonA", 0), ("StopPoint:VienneA", 40), ("StopPoint:ValenceA", 90)]),
    ]

    with trips.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["trip_id", "service_id"])
        for trip_id, service, _departure, _stops in routes:
            writer.writerow([trip_id, service])

    with times.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["trip_id", "stop_id", "departure_time", "arrival_time"])
        for trip_id, _service, departure, stops in routes:
            for stop_id, offset in stops:
                moment = departure + timedelta(minutes=offset)
                stamp = moment.strftime("%H:%M:%S")
                writer.writerow([trip_id, stop_id, stamp, stamp])

    with dates.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["service_id", "date", "exception_type"])
        for _trip_id, service, _departure, _stops in routes:
            writer.writerow([service, day, "1"])


@pytest.fixture
def site(tmp_path: Path):
    """L'application réelle, servie sur un vrai port, avec une base complète."""
    data = tmp_path / "data"
    data.mkdir()
    _write_stop_csv(tmp_path / "stops.txt")
    import_stop_names(data / "stops.db", tmp_path / "stops.txt")

    now = datetime.now(PARIS)
    _write_gtfs(tmp_path, now)
    import_timetable(
        data / "timetable.db",
        tmp_path / "trips.txt",
        tmp_path / "stop_times.txt",
        tmp_path / "calendar_dates.txt",
    )

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


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    """Un contexte neuf par test : localStorage et cookies ne fuient pas."""
    context = browser.new_context(viewport={"width": 390, "height": 844})  # iPhone, la cible
    page = context.new_page()
    # Un handler non câblé fait expirer le wait_for_selector en 30 s : on
    # échoue vite et avec un message lisible.
    page.set_default_timeout(5000)
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.errors = errors  # type: ignore[attr-defined]
    yield page
    context.close()


def _console_errors(page) -> list[str]:
    return [e for e in page.errors if e]  # type: ignore[attr-defined]


def _sessions(site: str) -> list[dict]:
    """Ce que la base contient vraiment, lu hors du navigateur."""
    with urllib.request.urlopen(site + "/api/sessions") as response:
        return json.load(response)


def _fill_count(page, value: int) -> None:
    """Remplit le nombre exact comme un utilisateur : on tape dans le champ."""
    field = page.locator("#passengers")
    field.fill("")
    field.type(str(value), delay=5)


def _reach_form(page, site: str) -> None:
    page.goto(site)
    page.fill("#origin-q", "Lyon")
    page.click("#origin-list button:has-text('Lyon Part-Dieu')")
    page.fill("#destination-q", "Valence")
    page.click("#destination-list button:has-text('Valence')")
    page.wait_for_selector("#trains button")
    page.click("#trains button:has-text('TER')")
    page.click("#mode-unique")
    page.wait_for_selector("#form-step:not(.hidden)")


# --- la page doit se charger sans erreur ------------------------------------


def test_page_loads_without_a_script_error(page, site):
    page.goto(site)
    page.wait_for_selector("#origin-q")
    assert _console_errors(page) == [], f"erreur JS au chargement : {_console_errors(page)}"


def test_every_button_of_the_form_is_wired(page, site):
    """Un handler non câblé est invisible en Python : ici on le vérifie."""
    page.goto(site)
    page.wait_for_selector("#origin-q")
    wired = page.evaluate(
        """() => {
            const ids = ['near', 'missing', 'send', 'again', 'change-origin',
                         'change-od', 'change-train', 'change-mode',
                         'mode-unique', 'mode-snake', 'plus1', 'plus5',
                         'plus10', 'minus', 'snake-back'];
            return ids.filter(id => {
                const el = document.getElementById(id);
                return !el || !(el.onclick || el.oninput);
            });
        }"""
    )
    assert wired == [], f"boutons sans handler : {wired}"


def test_changing_step_always_leaves_one_step_visible(page, site):
    """show() listait les étapes à la main et avait oublié le serpent : après
    le choix d'un train, toutes les sections étaient masquées et l'écran
    restait vide. Chaque étape doit pouvoir s'afficher seule."""
    sections = page.evaluate(
        "() => [...document.querySelectorAll('main > section')].map(s => s.id)"
    )
    for step in sections:
        page.evaluate("(id) => show(id)", step)
        visible = page.evaluate(
            "() => [...document.querySelectorAll('main > section')]"
            ".filter(s => !s.classList.contains('hidden')).map(s => s.id)"
        )
        assert visible == [step], f"show({step!r}) affiche {visible} au lieu de [{step!r}]"
    page.goto(site)  # on laisse la page dans son état initial


def test_choosing_a_train_leads_to_the_mode_step(page, site):
    page.goto(site)
    page.fill("#origin-q", "Lyon")
    page.click("#origin-list button:has-text('Lyon Part-Dieu')")
    page.fill("#destination-q", "Valence")
    page.click("#destination-list button:has-text('Valence')")
    page.wait_for_selector("#trains button")
    page.click("#trains button:has-text('TER')")
    page.wait_for_selector("#mode-step:not(.hidden)")


def test_exact_number_field_is_visible_and_labelled(page, site):
    """Le champ ne doit pas être masqué : un label invisible et un focus
    impossible cassent la saisie directe et l'accessibilité.

    Il vit dans #form-step, donc il faut choisir un train avant de juger.
    """
    _reach_form(page, site)
    field = page.locator("#passengers")
    assert field.is_visible(), "le champ du nombre exact est masqué"
    assert page.locator('label[for="passengers"]').count() == 1
    assert page.locator('label[for="passengers"]').is_visible()
    # Saisissable au clavier, et il prend le focus quand on ouvre le formulaire.
    field.focus()
    assert page.evaluate("() => document.activeElement.id") == "passengers"


# --- le compteur ------------------------------------------------------------


def test_counter_buttons_add_up(page, site):
    _reach_form(page, site)
    assert page.text_content("#count-display") == "0"
    for _ in range(2):
        page.click("#plus10")
    page.click("#plus5")
    page.click("#plus1")
    page.click("#minus")
    assert page.text_content("#count-display") == "25"
    assert page.input_value("#passengers") == "25"


def test_counter_never_goes_below_zero(page, site):
    _reach_form(page, site)
    for _ in range(3):
        page.click("#minus")
    assert page.text_content("#count-display") == "0"
    assert page.input_value("#passengers") == "0"


def test_typed_number_overrides_the_counter(page, site):
    _reach_form(page, site)
    page.click("#plus10")
    _fill_count(page, 37)
    assert page.text_content("#count-display") == "37"


def test_counter_is_reset_for_each_train(page, site):
    _reach_form(page, site)
    page.click("#plus10")
    page.click("#plus10")
    assert page.text_content("#count-display") == "20"
    page.click("#change-train")
    page.wait_for_selector("#train-step:not(.hidden)")
    page.click("#trains button:has-text('TER')")
    page.click("#mode-unique")
    assert page.text_content("#count-display") == "0", "le compte de la squeezation précédente reste"


# --- l'envoi réel -----------------------------------------------------------


def test_the_method_page_is_readable_and_honest(page, site):
    """/methode est la page qui dit ce que les chiffres ne sont pas. Le plan
    la rend obligatoire avant toute estimation, donc elle doit exister, se
    lire, et dire les trois choses : pas une fréquentation officielle, pas
    de chiffre annuel sans méthode, quelle licence."""
    response = page.goto(site + "/methode")
    assert response.status == 200, f"/methode répond {response.status}"
    text = page.text_content("body")

    assert page.title() == "Méthode — ComptageFer"
    for attendu in (
        "Ce n'est pas une fréquentation officielle",
        "Pas de chiffre sans dénominateur",
        "Licence Ouverte 2.0",
        "GPL-3.0",
        "Aucune coordonnée GPS n'est stockée",
        "Pas de compte",
    ):
        assert attendu in text, f"la méthode ne dit pas : {attendu!r}"

    # Les réserves doivent être visibles, pas cachées dans un attribut.
    assert page.is_visible("main")
    assert page.locator("h2").count() >= 6, "la méthode doit être structurée en sections"


def test_the_reading_page_links_to_the_method(page, site):
    """Le lien doit exister sur la page où se lisent les chiffres, sinon la
    méthode reste une page que personne ne visite."""
    page.goto(site + "/comptages")
    link = page.locator('a[href="/methode"]')
    assert link.count() == 1, "la page des comptages doit renvoyer vers la méthode"
    assert link.is_visible()
    link.click()
    page.wait_for_selector("h1:has-text('Méthode')")


def test_a_count_reaches_the_database(page, site):
    _reach_form(page, site)
    page.click("#plus10")
    page.click("#plus5")
    page.click("#plus10")
    page.fill("#reliability", "75")
    # Le pseudo est dans un <details> replié : on l'ouvre comme un utilisateur.
    page.click("#form-step summary")
    page.fill("#pseudo", "testeur")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")

    rows = _sessions(site)
    assert len(rows) == 1
    assert rows[0]["passengers"] == 25, f"la base contient {rows[0]['passengers']}, pas 25"
    assert rows[0]["reliability"] == 75
    assert rows[0]["pseudo"] == "testeur"
    assert rows[0]["origin_name"] == "Lyon Part-Dieu"
    assert rows[0]["destination_name"] == "Valence"


def test_the_count_appears_on_the_reading_page(page, site):
    _reach_form(page, site)
    page.click("#plus10")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")
    page.goto(site + "/comptages")
    assert page.text_content("body").count("Lyon Part-Dieu") >= 1
    assert "10" in page.text_content("body")


# --- hors ligne -------------------------------------------------------------


def test_a_count_survives_the_tunnel_and_flushes_on_reconnect(page, site):
    """Le cas d'usage principal : un quai, pas de réseau, on compte quand même."""
    _reach_form(page, site)
    page.click("#plus10")
    page.click("#plus5")

    # On coupe le réseau au niveau du navigateur, pas du serveur.
    page.route("**/api/sessions", lambda route: route.abort())
    page.click("#send")
    page.wait_for_timeout(300)

    queued = page.evaluate("() => JSON.parse(localStorage.getItem('comptagefer-queue') || '[]')")
    assert len(queued) == 1, f"le compte n'est pas dans la file : {queued}"
    assert queued[0]["passengers"] == 15
    assert page.is_visible("#done-step") is False, "l'app se croit avoir envoyé le compte"

    page.unroute("**/api/sessions")
    page.evaluate("() => window.dispatchEvent(new Event('online'))")
    page.wait_for_function(
        "() => JSON.parse(localStorage.getItem('comptagefer-queue') || '[]').length === 0",
        timeout=5000,
    )

    rows = _sessions(site)
    assert [row["passengers"] for row in rows] == [15], f"la base contient {rows}"


def test_a_rejected_count_is_not_kept_forever(page, site):
    """Une erreur de validation est un tunnel : on ne garde pas le compte."""
    _reach_form(page, site)
    page.route(
        "**/api/sessions",
        lambda route: route.fulfill(status=422, content_type="application/json", body='{"detail":"x"}'),
    )
    page.click("#plus10")
    page.click("#send")
    page.wait_for_timeout(300)
    queued = page.evaluate("() => JSON.parse(localStorage.getItem('comptagefer-queue') || '[]')")
    assert queued == [], f"un 422 ne doit pas rester en file : {queued}"


# --- le serpent -------------------------------------------------------------


def _reach_snake(page, site: str) -> None:
    """Aller jusqu'à l'écran du serpent : train choisi, puis mode serpent.

    Depuis #form-step, « Changer » du train renvoie sur #train-step, et le
    choix d'un train affiche #mode-step. Il n'y a pas de retour direct vers
    #mode-step depuis le formulaire.
    """
    _reach_form(page, site)
    page.click("#change-train")
    page.wait_for_selector("#train-step:not(.hidden)")
    page.click("#trains button:has-text('TER')")
    page.wait_for_selector("#mode-step:not(.hidden)")
    page.click("#mode-snake")
    page.wait_for_selector("#snake-step:not(.hidden)")
    # startSnake est async : les arrêts arrivent après #snake-step visible.
    page.wait_for_function(
        "() => !document.getElementById('snake-title').textContent.includes('Chargement')"
    )


def test_the_load_snake_is_reachable(page, site):
    """Le serpent avait perdu son handler : le bouton ne faisait rien."""
    _reach_snake(page, site)
    assert page.is_visible("#snake-next")
    assert "Lyon" in page.text_content("#snake-title")


def test_the_snake_walks_the_stops_of_the_chosen_trip(page, site):
    _reach_snake(page, site)

    assert "Lyon" in page.text_content("#snake-title")
    page.fill("#snake-onboard", "40")
    page.click("#snake-next")

    assert "Vienne" in page.text_content("#snake-title")
    page.fill("#snake-boarded", "3")
    page.fill("#snake-alighted", "1")
    page.click("#snake-next")

    assert "Valence" in page.text_content("#snake-title")
    page.fill("#snake-boarded", "0")
    page.click("#snake-next")
    page.wait_for_selector("#done-step:not(.hidden)")

    rows = _sessions(site)
    assert len(rows) == 1
    assert rows[0]["kind"] == "serpent"
    assert rows[0]["passengers"] == 40, "le nombre portes fermées doit rester le compte unique"
    assert [leg["stop_name"] for leg in rows[0]["legs"]] == [
        "Lyon Part-Dieu",
        "Vienne",
        "Valence",
    ], f"arrêts enregistrés : {rows[0]['legs']}"
    assert rows[0]["legs"][1]["boarded"] == 3
    assert rows[0]["legs"][2]["alighted"] is None, "une descente non comptée reste nulle, pas 0"
