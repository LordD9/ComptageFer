import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
import sqlite3
import threading
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse

from comptagefer.carte import counted_features, map_page as carte_page
from comptagefer.offer import nearest_stops, search_stops, trips_serving
from comptagefer.timetable import (
    find_line,
    line_stops,
    listed_trips,
    search_lines,
    stops_between,
    trip_stops_all,
)
from comptagefer.page import PAGE
from comptagefer.publish import (
    Publication,
    config_from_env,
    render_csv,
)
from comptagefer.rt import (
    ALERTS_URL,
    SIRI_URL,
    TU_URL,
    cache_status,
    poll_once,
)

STOPS_URL = "https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip"

# Les trains franciliens vivent dans un autre GTFS, pas dans le national.
#
# Le GTFS national est le « Export_OpenData_SNCF_GTFS_NewTripId » : ses 731
# routes ne contiennent aucune ligne francilienne, mesuré sur le fichier du
# moment. Ce n'est pas un oubli de l'appli, c'est le périmètre du jeu.
#
# Le GTFS « IDFM-gtfs.zip » de l'Île-de-France Mobilités n'est pas la SNCF
# francilienne non plus : sur 2026 routes, 1966 sont en `route_type` 3, donc
# du bus — RATP, Keolis, lesCars. Le RER y est noyé. Le bon fichier est le
# « transilien-gtfs.zip » de l'OpenData SNCF : 38 routes, RER A/B/D/N, ligne U,
# et les TER qui desservent la région.
#
# Les deux jeux n'ont **aucune** clé en commun — 0 sur `trip_id`, `service_id`,
# `route_id` comme sur `stop_id`, mesuré sur les fichiers du moment. C'est ce qui
# permet de les importer dans la même base sans qu'un trip_id national soit
# écrasé par un francilien, ou l'inverse.
TRANSILIEN_URL = "https://eu.ftp.opendatasoft.com/sncf/gtfs/transilien-gtfs.zip"

# Les fichiers lus dans chaque archive. Un GTFS n'est pas obligé d'avoir le
# même contenu que son voisin ; on prend ce qui est là plutôt que d'exiger.
GTFS_FILES = ("stops.txt", "trips.txt", "stop_times.txt", "calendar_dates.txt", "routes.txt")

# Une session d'administration tient une heure de travail, pas plus. Le
# dictionnaire qui les porte est en mémoire : sans expiration il ne redescend
# jamais, et une session volée resterait valable jusqu'au redémarrage.
SESSION_SECONDS = 3600


def _prune_sessions(sessions: dict[str, float]) -> None:
    """Oublie les sessions d'administrateur dépassées.

    Appelé à chaque connexion, donc le dictionnaire reste à la taille des
    sessions réellement ouvertes plutôt que de celle de toutes les sessions
    jamais ouvertes.
    """
    limite = time.time() - SESSION_SECONDS
    for jeton in [j for j, ouverte in sessions.items() if ouverte < limite]:
        sessions.pop(jeton, None)


# Les colonnes de `saisie`, en un seul endroit, avec leur type et leur
# contrainte. La création de table et la migration qui reconstruit la table
# pour la clé composite s'en servent toutes les deux : les dupliquer
# stockpillerait la réponse, et c'est ainsi que `trajet` s'est perdue — la
# migration la recopiait sans l'avoir dans sa liste.
SCHEMA_SAISIE = (
    ("client_id", "TEXT NOT NULL"),
    ("kind", "TEXT NOT NULL"),
    ("origin_stop_id", "TEXT NOT NULL"),
    ("destination_stop_id", "TEXT NOT NULL"),
    ("origin_name", "TEXT"),
    ("destination_name", "TEXT"),
    ("trip_id", "TEXT"),
    ("passengers", "INTEGER"),
    ("reliability", "INTEGER"),
    ("pseudo", "TEXT"),
    ("comment", "TEXT"),
    ("standing", "INTEGER"),
    ("seats_free", "INTEGER"),
    ("imbalance", "INTEGER"),
    ("materiel", "TEXT"),
    ("composition", "TEXT"),
    ("perimetre", "TEXT"),
    ("snapshot", "TEXT"),
    ("created_at", "TEXT NOT NULL"),
    ("legs", "TEXT"),
    ("trajet", "TEXT"),
)

# Ce que « composition » veut dire, et pourquoi c'est une liste fermée.
#
# Une US est une seule voiture ; une UM2, deux ; une UM3, trois. Ce n'est pas
# une convention de saisie : c'est ce qui décide si un effectif se lit par
# voiture ou par rame. Un champ libre accepterait « UM2 (ramesihat) » et
# « deux voitures », et le lecteur du CSV ne saurait plus dire si 180 voyageurs
# se multiplient par 2. Une liste fermée rend la donnée calculable, ou absente.
COMPOSITIONS = {"US": 1, "UM2": 2, "UM3": 3}

# Ce que l'effectif de la saisie compte : une voiture, ou la rame entière.
# Un train francilien peut être une UM3, et celui qui compte peut être monté
# dans une seule voiture. Sans cette distinction, 180 voyageurs dans une voiture
# d'une UM3 et 180 dans les trois sont le même relevé — et c'est précisément la
# différence qui donne la charge à l'échelle de la rame.
PERIMETRES = {"voiture", "um"}


def _clef_par_genre(connection: sqlite3.Connection) -> None:
    """Passe la clé primaire de `saisie` de `client_id` à `(client_id, kind)`.

    Un même navigateur peut signaler un train manquant et compter un train
    réel : les deux lignes sont légitimes et le jeton les identifie toutes les
    deux. Avec `client_id` seul en clé, la seconde ne pouvait pas s'écrire.

    SQLite ne sait pas changer une clé primaire : on recrée la table et on
    recopie. Les bases existantes ne peuvent pas contenir deux lignes de même
    `client_id` — l'ancien code rejetait le doublon avant d'écrire — donc la
    recopie ne peut pas buter sur une collision.
    """
    colonnes = {row[1]: row for row in connection.execute("PRAGMA table_info(saisie)")}
    if "kind" not in colonnes or "legs" not in colonnes:
        return
    if colonnes["client_id"][5] == 0:
        return  # déjà composite : rien à faire
    noms = ", ".join(nom for nom, _ in SCHEMA_SAISIE if nom in colonnes)
    connection.execute("ALTER TABLE saisie RENAME TO saisie_ancienne_clef")
    connection.execute(
        f"CREATE TABLE saisie ({', '.join(f'{nom} {type_}' for nom, type_ in SCHEMA_SAISIE)}, "
        "PRIMARY KEY (client_id, kind))"
    )
    connection.execute(
        f"INSERT OR IGNORE INTO saisie ({noms}) SELECT {noms} FROM saisie_ancienne_clef"
    )
    connection.execute("DROP TABLE saisie_ancienne_clef")


def create_app(
    data_dir: Path,
    admin_token: str | None = None,
    publication: Publication | None = None,
) -> FastAPI:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    database = data_dir / "app.db"
    if admin_token is None:
        admin_token = os.environ.get("ADMIN_TOKEN", "")
    if publication is None:
        publication = Publication(
            config_from_env(),
            data_dir / "publish.json",
            lambda: render_csv(_list_saisies(database)),
        )
    admin_sessions: dict[str, float] = {}
    with sqlite3.connect(database) as connection:
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS saisie ("
            f"{', '.join(f'{nom} {type_}' for nom, type_ in SCHEMA_SAISIE)})"
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(saisie)")}
        if "legs" not in columns:
            connection.execute("ALTER TABLE saisie ADD COLUMN legs TEXT")
        if "trajet" not in columns:
            connection.execute("ALTER TABLE saisie ADD COLUMN trajet TEXT")
        # Trois colonnes du même changement, ajoutées dans le même `if` : une
        # base qui en a une mais pas les autres ne peut pas exister par un
        # chemin normal, et scinder le test donnerait l'illusion que chacune
        # est indépendante alors qu'elles ne le sont pas. La liste de recopie
        # n'a pas besoin d'être touchée : `_clef_par_genre` la tire de
        # `SCHEMA_SAISIE` par nom, et c'est exactement pour ça qu'elle existe.
        if not {"materiel", "composition", "perimetre"} <= columns:
            for nom in ("materiel", "composition", "perimetre"):
                connection.execute(f"ALTER TABLE saisie ADD COLUMN {nom} TEXT")
        _clef_par_genre(connection)

    timetable = data_dir / "timetable.db"
    stops_database = data_dir / "stops.db"

    app = FastAPI(title="ComptagesFer")
    # Exposée pour que l'exploitation puisse lire l'état sans passer par la
    # route, et pour qu'un test injecte un opener sans dupliquer le câblage.
    app.state.publication = publication

    @app.get("/health")
    def health() -> dict[str, str]:
        with sqlite3.connect(database) as connection:
            connection.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/api/rt")
    def rt_status() -> dict[str, int | str | None]:
        return cache_status(data_dir / "rt.db")

    @app.get("/offline.js")
    def offline_js() -> PlainTextResponse:
        script = Path(__file__).parent / "offline.js"
        return PlainTextResponse(script.read_text(), media_type="text/javascript")

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return PAGE

    @app.get("/api/stops")
    def stops(q: str = "") -> list[dict]:
        return search_stops(data_dir / "stops.db", q)

    @app.get("/api/stops/nearest")
    def nearby(lat: float, lon: float) -> list[dict]:
        return nearest_stops(data_dir / "stops.db", lat, lon)

    @app.get("/api/trips")
    def trips(from_: str = Query(alias="from"), to: str = Query(), at: str = Query()) -> list[dict]:
        try:
            parsed = datetime.fromisoformat(at.replace("Z", "+00:00"))
        except ValueError as ex:
            # Un paramètre client malformé est une 422, pas une 500 : personne
            # n'a besoin de la traceback pour comprendre « pas-une-date ».
            raise HTTPException(
                status_code=422, detail="`at` n'est pas une date ISO 8601"
            ) from ex
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if (data_dir / "timetable.db").exists():
            return listed_trips(
                data_dir / "timetable.db",
                data_dir / "rt.db",
                from_,
                to,
                parsed,
                stops_database=data_dir / "stops.db",
            )
        return trips_serving(data_dir / "rt.db", from_, to, parsed, stops_database=data_dir / "stops.db")

    @app.get("/api/trip-stops")
    def trip_stops(
        trip: str,
        from_: str = Query(alias="from"),
        to: str = Query(...),
    ) -> list[dict]:
        if not timetable.exists():
            return []
        return stops_between(
            timetable,
            trip,
            from_,
            to,
            stops_database=data_dir / "stops.db",
        )

    @app.get("/api/sessions")
    def list_sessions() -> list[dict]:
        return _list_saisies(database)

    @app.get("/comptages", response_class=HTMLResponse)
    def comptages() -> str:
        return _reading_page(_list_saisies(database))

    @app.get("/carte", response_class=HTMLResponse)
    def carte() -> str:
        rows = _list_saisies(database)
        # `total` est le nombre de relevés collectés, `placos` le nombre de
        # traits dessinés : l'écart entre les deux est l'information utile, il
        # ne faut donc pas les confondre. Mais le décompte ignorait les trains
        # signalés, qui sont eux dessinés — la page pouvait alors annoncer
        # « 0 comptage au total, 1 sur la carte ».
        return carte_page(counted_features(data_dir / "stops.db", rows), len(rows))

    @app.get("/api/lignes", response_class=PlainTextResponse)
    def api_lignes(q: str = Query("", max_length=80)) -> PlainTextResponse:
        """Les lignes dont le nom court ou le nom long contient la recherche.

        La clé est le `route_id`, jamais le nom court : le GTFS national
        attribue le même « C13 » à six lignes différentes, donc une URL
        construite sur le nom court ouvrirait une page au hasard.
        """
        return PlainTextResponse(
            json.dumps(search_lines(timetable, q), ensure_ascii=False),
            media_type="application/json",
        )

    @app.get("/ligne", response_class=HTMLResponse)
    def ligne(ligne_id: str = Query("", alias="ligne", max_length=120)) -> str:
        """Les comptages d'une ligne, ou une invitation à en faire un.

        Une ligne sans comptage n'est pas une page vide : la liste des arrêts
        est déjà là, et c'est exactement ce qu'il faut pour partir compter.
        """
        identifiant = ligne_id.strip()
        if not identifiant:
            return _plain_reading_page("Quelle ligne ?", _link("/rechercher", "Rechercher une ligne"))
        trouvee = find_line(timetable, identifiant)
        if trouvee is None:
            return _plain_reading_page(
                "Cette ligne n'est pas dans le GTFS national.",
                _link("/rechercher", "Rechercher une ligne"),
            )
        return _line_page(
            trouvee,
            _saisies_de_ligne(database, timetable, identifiant),
            line_stops(timetable, identifiant, data_dir / "stops.db"),
        )

    @app.get("/rechercher", response_class=HTMLResponse)
    def rechercher(q: str = Query("", max_length=80)) -> str:
        return _search_page(q, stops_database, timetable)

    @app.get("/methode", response_class=HTMLResponse)
    def methode() -> str:
        return _method_page()

    @app.get("/api/export.csv")
    def export_csv() -> PlainTextResponse:
        return PlainTextResponse(
            _export_csv(_list_saisies(database)),
            media_type="text/csv; charset=utf-8",
        )

    @app.get("/api/publish", response_class=PlainTextResponse)
    def publish_status() -> PlainTextResponse:
        """L'état de la publication automatique, sans le secret.

        L'hébergeur doit pouvoir voir si c'est actif, quand ça a tourné, et
        ce qui a échoué, sans ouvrir un shell dans le conteneur. La clé API
        n'y est pas, et elle n'y sera pas.
        """
        return PlainTextResponse(
            json.dumps(publication.status(), ensure_ascii=False),
            media_type="application/json",
        )

    @app.post("/admin/publier", response_class=HTMLResponse)
    def admin_publish(request: Request) -> str:
        """Publier tout de suite, pour vérifier la clé sans attendre minuit."""
        if not admin_open(request):
            raise HTTPException(status_code=401, detail="connexion requise")
        return _admin_list(_list_saisies(database), publication.publish_now())

    @app.post("/api/sessions")
    def sessions(body: dict) -> dict:
        kind = "serpent" if body.get("kind") == "serpent" else "count"
        return _save_saisie(
            database, body, kind=kind, timetable=timetable, stops_database=stops_database
        )

    @app.post("/api/missing")
    def missing(body: dict) -> dict:
        return _save_saisie(database, body, kind="missing")

    def admin_open(request: Request) -> bool:
        cookie = request.cookies.get("comptagefer_admin", "")
        if not cookie:
            return False
        opened = admin_sessions.get(cookie)
        if opened is None:
            return False
        if time.time() - opened > SESSION_SECONDS:
            admin_sessions.pop(cookie, None)
            return False
        return True

    @app.get("/admin", response_class=HTMLResponse)
    def admin(request: Request) -> str:
        if not admin_open(request):
            return _admin_login()
        return _admin_list(_list_saisies(database))

    @app.post("/admin/login", response_class=HTMLResponse)
    def admin_login(response: Response, token: str = Form("")) -> str:
        if not _admin_token_matches(token, admin_token):
            raise HTTPException(status_code=401, detail="jeton refusé")
        cookie = secrets.token_urlsafe(32)
        admin_sessions[cookie] = time.time()
        _prune_sessions(admin_sessions)
        response.set_cookie(
            "comptagefer_admin",
            cookie,
            httponly=True,
            samesite="lax",
            max_age=SESSION_SECONDS,
            path="/",
        )
        return _admin_list(_list_saisies(database))

    @app.post("/admin/supprimer", response_class=HTMLResponse)
    def admin_delete(request: Request, client_id: str = Form("")) -> str:
        if not admin_open(request):
            raise HTTPException(status_code=401, detail="connexion requise")
        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM saisie WHERE client_id = ?", (client_id,))
        return _admin_list(_list_saisies(database))

    return app


def create_production_app() -> FastAPI:
    data_dir = Path(os.environ.get("COMPTAGEFER_DATA", "data"))
    database = data_dir / "app.db"
    publication = Publication(
        config_from_env(),
        data_dir / "publish.json",
        # L'appelable est évalué à chaque publication, pas au démarrage : un
        # CSV capturé une fois en mémoire republicuerait le fichier de la
        # veille pour toujours.
        lambda: render_csv(_list_saisies(database)),
    )
    app = create_app(data_dir, publication=publication)
    thread = threading.Thread(
        target=_poll_forever,
        args=(data_dir / "rt.db",),
        name="gtfs-rt",
        daemon=True,
    )
    thread.start()
    publication.start()
    return app


def _fetch(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def _poll_forever(database: Path) -> None:
    log = logging.getLogger("comptagefer.rt")
    try:
        ensure_referential(database.parent)
    except Exception:
        log.exception("import des noms de gares échoué")
    # SIRI ne sert qu'une fois, en filet d'un flux GTFS-RT vide. Sans cet état,
    # la boucle le retéléchargeait toutes les 120 secondes.
    siri_tente = False
    while True:
        try:
            siri_tente = poll_once(
                database,
                fetch_trips=lambda: _fetch(TU_URL),
                fetch_siri=lambda: _fetch(SIRI_URL, timeout=120),
                fetch_alerts=lambda: _fetch(ALERTS_URL),
                now=datetime.now(timezone.utc),
                siri_tente=siri_tente,
            )
        except Exception:
            log.exception("poll GTFS-RT échoué")
        time.sleep(120)


def _extract(payload: bytes, folder: Path, names: tuple[str, ...]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        for name in names:
            if name not in archive.namelist():
                continue
            with archive.open(name) as raw, (folder / name).open("wb") as target:
                shutil.copyfileobj(raw, target)


def ensure_referential(data_dir: Path) -> None:
    from comptagefer.offer import import_stop_names, open_stops
    from comptagefer.timetable import import_timetable

    stops = data_dir / "stops.db"
    timetable = data_dir / "timetable.db"
    need_stops = True
    if stops.exists():
        with open_stops(stops) as connection:
            need_stops = connection.execute("SELECT COUNT(*) FROM stop").fetchone()[0] == 0
    need_times = not timetable.exists() or not _timetable_has_lignes(timetable)
    # Une base importée avant les franciliens a des lignes mais pas les leurs :
    # sans ce test, l'application ne les chercherait jamais. On regarde si le jeu
    # francilien est déjà là plutôt que de comparer un nombre de lignes, qui
    # changerait à chaque nouveau GTFS et forcerait un réimport à chaque
    # démarrage.
    need_transilien = not _timetable_has_transilien(timetable)
    if not need_stops and not need_times and not need_transilien:
        return
    folder = data_dir / "import"
    transilien_folder = data_dir / "import-transilien"
    # Déclaré ici et pas dans le `if` : il est utilisé par les deux branches et
    # par le remplacement final, et une liaison conditionnelle donnerait un
    # `possibly unbound` qui masque le vrai risque — celui d'un remplacement
    # exécuté sans import.
    importing = data_dir / "timetable.importing"
    # On reparte de la base en place, pas d'un fichier neuf. Le cas « national
    # déjà importé, francilien manquant » est le cas normal d'une installation
    # qui tourne déjà : repartir de zéro n'écraserait pas la base mais
    # construirait un `timetable.importing` **sans** les 731 lignes nationales,
    # qui le remplacerait ensuite. Les 60 000 trips TER disparaîtraient au
    # démarrage suivant, et rien ne le signalerait.
    if need_transilien and not need_times and timetable.exists():
        shutil.copyfile(timetable, importing)
    try:
        _extract(_fetch(STOPS_URL, timeout=180), folder, GTFS_FILES)
        if need_stops:
            import_stop_names(stops, folder / "stops.txt")
        if need_times:
            import_timetable(
                importing,
                folder / "trips.txt",
                folder / "stop_times.txt",
                folder / "calendar_dates.txt",
                folder / "routes.txt",
            )
        if need_transilien:
            _extract(_fetch(TRANSILIEN_URL, timeout=300), transilien_folder, GTFS_FILES)
            # Les gares franciliennes aussi : sans elles, l'usager ne peut ni
            # choisir une gare parisienne, ni faire de géolocalisation. Elles ont
            # leurs propres `stop_id`, donc rien n'écrase le national.
            import_stop_names(stops, transilien_folder / "stops.txt")
            import_timetable(
                importing,
                transilien_folder / "trips.txt",
                transilien_folder / "stop_times.txt",
                transilien_folder / "calendar_dates.txt",
                transilien_folder / "routes.txt",
            )
        if need_times or need_transilien:
            # Le remplacement n'a lieu qu'une fois, les deux jeux importés. Une
            # interruption entre les deux laisserait une base à moitié
            # francilienne, qu'aucun test ne détecterait ensuite : `timetable.db`
            # serait là, donc plus jamais réimporté.
            importing.replace(timetable)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        shutil.rmtree(transilien_folder, ignore_errors=True)


def _timetable_has_transilien(timetable: Path) -> bool:
    """Le jeu francilien est-il déjà dans la base ?

    On reconnaît le préfixe de ses `trip_id`, pas un nom de ligne : un nom
    change avec la refonte d'une ligne, un préfixe d'exploitant non. Et on
    regarde `circulation`, pas `ligne` : les RER sont 25 routes sur 38, donc
    compter les routes donnerait un faux positif dès qu'une seule est présente.
    """
    if not timetable.exists():
        return False
    try:
        with sqlite3.connect(timetable) as connection:
            row = connection.execute(
                "SELECT 1 FROM circulation WHERE trip_id LIKE 'IDFM:TN:SNCF:%' LIMIT 1"
            ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def _timetable_has_lignes(timetable: Path) -> bool:
    """Une base importée avant les pages ligne n'a pas ces deux tables.

    On la réimporte au prochain démarrage plutôt que de laisser une recherche
    de ligne muette : mieux vaut un import de cinq minutes qu'une page qui
    répond « aucune ligne » alors que la feed en contient 725.
    """
    if not timetable.exists():
        return False
    try:
        from comptagefer.timetable import _has_lines

        return _has_lines(timetable)
    except sqlite3.DatabaseError:
        return False


def _admin_token_matches(provided: str, expected: str) -> bool:
    if not expected:
        return False
    return hmac.compare_digest(
        hashlib.sha256(provided.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


def _admin_login() -> str:
    return """<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Admin</title>
<style>
  body { margin: 0; font: 18px/1.35 system-ui, sans-serif; background: #f4f1ea; color: #1c1915; }
  main { max-width: 32rem; margin: 0 auto; padding: 1rem; }
  input, button { font: inherit; width: 100%; min-height: 3.2rem; box-sizing: border-box; }
  button { border: 0; border-radius: 0.8rem; background: #1c1915; color: #fff; }
  input { border: 1px solid #c9c1b4; border-radius: 0.8rem; padding: 0.6rem 0.8rem; }
</style>
</head><body><main>
  <h1>Admin</h1>
  <p>Le jeton est celui du conteneur. Il n'est pas un compte.</p>
  <form method="post" action="/admin/login">
    <label for="token">Jeton</label>
    <p><input id="token" name="token" type="password" autocomplete="current-password"></p>
    <button type="submit">Ouvrir</button>
  </form>
</main></body></html>
"""


def _admin_list(rows: list[dict], publication: dict | None = None) -> str:
    cards = []
    for row in rows:
        who = escape(row["pseudo"]) if row["pseudo"] else "anonyme"
        origin = escape(row["origin_name"] or row.get("origin_stop_id") or "")
        destination = escape(row["destination_name"] or "")
        passengers = "" if row["passengers"] is None else row["passengers"]
        client_id = escape(row["client_id"])
        # Le commentaire explique une charge atypique (car de substitution,
        # train supprimé). C'est l/admin qui le lit : sans lui, l'information
        # est stockée et jamais consultée.
        note = f"<p>{escape(row['comment'])}</p>" if row.get("comment") else ""
        cards.append(
            "<article class='card'>"
            f"<strong>{origin} → {destination}</strong>"
            f"<p>{passengers} voyageurs · {who}</p>{note}"
            "<form method='post' action='/admin/supprimer'>"
            f"<input type='hidden' name='client_id' value='{client_id}'>"
            "<button type='submit'>Supprimer</button>"
            "</form></article>"
        )
    body = "\n".join(cards) or "<p>Aucun comptage.</p>"
    return f"""<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Admin</title>
<style>
  body {{ margin: 0; font: 18px/1.35 system-ui, sans-serif; background: #f4f1ea; color: #1c1915; }}
  main {{ max-width: 32rem; margin: 0 auto; padding: 1rem; }}
  .card {{ background: #fff; border-radius: 0.8rem; padding: 0.8rem; margin: 0.6rem 0; }}
  button {{ font: inherit; min-height: 3.2rem; width: 100%; border: 0; border-radius: 0.8rem; background: #8a2b1b; color: #fff; }}
</style>
</head><body><main>
  <h1>Admin</h1>
  <p>Supprimer retire le comptage de la liste et du CSV.</p>
  {body}
  {"" if publication is None else _publication_panel(publication)}
</main></body></html>
"""


def _publication_panel(publication: dict) -> str:
    """Le résultat d'une publication à la demande, et le bouton pour la refaire.

    Publier sans attendre minuit est le seul moyen de savoir si la clé est
    bonne sans attendre le lendemain, donc le bouton reste sur la page.
    """
    if publication.get("ok"):
        if publication.get("skipped"):
            message = "Rien à publier : aucun comptage dans la base."
        else:
            message = f"CSV publié, {publication.get('bytes', 0)} octets."
    else:
        message = f"Publication échouée : {publication.get('error') or 'raison inconnue'}"
    return (
        f"<p>{escape(message)}</p>"
        "<form method='post' action='/admin/publier'>"
        "<button type='submit'>Publier maintenant sur data.gouv</button>"
        "</form>"
    )


def _materiel(body: dict) -> tuple[str | None, str | None, str | None]:
    """Le matériel roulant, sa composition, et ce que l'effectif compte.

    Les trois vont ensemble : « Z 20500 » seul ne dit rien de l'échelle, et une
    composition seule ne dit pas si l'usager a compté une voiture ou les trois.
    Pris séparément, ils sont inexploitables ; ensemble, l'effectif devient
    comparable d'une rame à l'autre.

    Le trio est facultatif, mais cohérent ou rien. Une composition sans
    périmètre se contredit : dire « UM3 » en ne disant pas ce qu'on a compté
    laisse croire qu'on a compté la rame, ce qui est faux deux fois sur trois
    pour un Francilien. Et un périmètre sans composition n'a pas d'échelle. On
    refuse donc le mélange, parce qu'un CSV où la moitié des lignes a un
    périmètre et l'autre non se lit comme une absence d'information alors que
    c'est une information fausse.
    """
    composition = str(body.get("composition") or "").strip().upper()
    perimetre = str(body.get("perimetre") or "").strip().lower()
    if composition and composition not in COMPOSITIONS:
        raise HTTPException(
            status_code=422,
            detail="composition inconnue : US, UM2 ou UM3",
        )
    if perimetre and perimetre not in PERIMETRES:
        raise HTTPException(status_code=422, detail="périmètre inconnu : voiture ou um")
    if bool(composition) != bool(perimetre):
        raise HTTPException(
            status_code=422,
            detail="composition et périmètre vont ensemble : l'un sans l'autre n'est pas exploitable",
        )
    # Une US, c'est une voiture. Demander si l'on a compté une voiture ou la
    # rame n'a pas de sens, et accepter « UM / voiture » sur une US rendrait
    # l'effectif indéfini.
    if composition == "US" and perimetre == "um":
        raise HTTPException(
            status_code=422,
            detail="une US est une seule voiture : le périmètre « um » ne s'y applique pas",
        )
    materiel = str(body.get("materiel") or "").strip()[:40]
    return materiel or None, composition or None, perimetre or None


def _save_saisie(
    database: Path,
    body: dict,
    kind: str,
    timetable: Path | None = None,
    stops_database: Path | None = None,
) -> dict:
    client_id = str(body.get("client_id") or "")
    origin = str(body.get("origin_stop_id") or "")
    destination = str(body.get("destination_stop_id") or "")
    if not client_id or not origin or not destination:
        raise HTTPException(status_code=422, detail="origine, destination et jeton requis")
    passengers = body.get("passengers")
    reliability = body.get("reliability")
    if kind == "serpent":
        legs_text = _clean_legs(body.get("legs"))
        if not isinstance(reliability, int) or not 0 <= reliability <= 100:
            raise HTTPException(status_code=422, detail="fiabilité invalide")
        passengers = json.loads(legs_text)[0]["onboard"]
    elif kind == "count":
        legs_text = None
        if not isinstance(passengers, int) or passengers < 0:
            raise HTTPException(status_code=422, detail="effectif invalide")
        if not isinstance(reliability, int) or not 0 <= reliability <= 100:
            raise HTTPException(status_code=422, detail="fiabilité invalide")
    else:
        legs_text = None
    standing = _indicator(body.get("standing"))
    seats_free = _indicator(body.get("seats_free"))
    imbalance = _indicator(body.get("imbalance"))
    materiel, composition, perimetre = _materiel(body)
    snapshot = body.get("snapshot")
    snapshot_text = json.dumps(snapshot, ensure_ascii=False) if snapshot is not None else None
    if snapshot_text and len(snapshot_text) > 20_000:
        snapshot_text = None
    trajet_text = _freeze_trajet(timetable, stops_database, body)
    with sqlite3.connect(database) as connection:
        existing = connection.execute(
            "SELECT client_id, kind FROM saisie WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        # Le doublon ne vaut que s'il est du même genre. Un « train signalé »
        # consomme le jeton du navigateur, et le comptage réel qui suit
        # arrive avec le même : le rejeter ici perdait le comptage sans rien
        # dire, pendant que l'écran affichait « c'est noté ».
        if existing and existing[1] == kind:
            return {"client_id": existing[0], "kind": existing[1], "stored": False}
        connection.execute(
            """
            INSERT INTO saisie (
                client_id, origin_stop_id, destination_stop_id, origin_name, destination_name, trip_id,
                passengers, reliability, pseudo, comment, standing, seats_free, imbalance,
                materiel, composition, perimetre, snapshot, legs, trajet, kind, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_id,
                origin,
                destination,
                (body.get("origin_name") or "")[:80] or None,
                (body.get("destination_name") or "")[:80] or None,
                body.get("trip_id"),
                passengers if kind in {"count", "serpent"} else None,
                reliability if kind in {"count", "serpent"} else None,
                (body.get("pseudo") or "")[:40] or None,
                (body.get("comment") or "")[:280] or None,
                standing,
                seats_free,
                imbalance,
                materiel,
                composition,
                perimetre,
                snapshot_text,
                legs_text,
                trajet_text,
                kind,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return {"client_id": client_id, "kind": kind, "stored": True}


def _freeze_trajet(
    timetable: Path | None,
    stops_database: Path | None,
    body: dict,
) -> str | None:
    """Le trajet complet du train, figé au moment du comptage.

    Un comptage ne parle que du tronçon où l'on a compté. Le train, lui, venait
    d'ailleurs et continue ailleurs, et cette charge-là est exactement ce
    qu'une estimation de fréquentation cherche plus tard. Alors on le copie
    dans la saisie au moment où on sait de quel train il s'agit.

    On fige pour deux raisons, et ce n'est pas la même. La première est
    l'historique : le GTFS est rechargé, une ligne peut changer de gares, et la
    saisie doit dire ce qu'elle a vu. La deuxième est qu'un `trip_id` du GTFS
    théorique ne garantit plus l'existence du train le jour du comptage ; sans
    copie, on ne peut pas distinguer « on l'a compté avant le rechargement » de
    « on l'a compté après ».

    Un « train signalé » n'a pas de trajet : c'est un doute sur une ligne, pas
    une observation, donc rien à figer. Sans `trip_id` non plus : on ne sait
    pas quel train on a compté, et deviner serait fabriquer de la donnée.
    """
    if timetable is None or not Path(timetable).exists():
        return None
    trip_id = body.get("trip_id")
    if not trip_id or not isinstance(trip_id, str):
        return None
    arrets = trip_stops_all(timetable, trip_id, stops_database)
    if len(arrets) < 2:
        return None
    return json.dumps(
        {
            "trip_id": trip_id,
            "arrets": [
                {"stop_id": item["stop_id"], "name": item["name"], "depart_sec": item["depart_sec"]}
                for item in arrets
            ],
        },
        ensure_ascii=False,
    )


def _clean_legs(value: object) -> str:
    if not isinstance(value, list) or len(value) < 2:
        raise HTTPException(status_code=422, detail="serpent incomplet")
    cleaned = []
    for index, leg in enumerate(value):
        if not isinstance(leg, dict) or not leg.get("stop_id"):
            raise HTTPException(status_code=422, detail="arrêt invalide")
        item = {
            "stop_id": str(leg["stop_id"])[:80],
            "stop_name": str(leg.get("stop_name") or "")[:80],
        }
        if index == 0:
            onboard = leg.get("onboard")
            if not isinstance(onboard, int) or onboard < 0:
                raise HTTPException(status_code=422, detail="effectif invalide")
            item["onboard"] = onboard
        else:
            boarded = leg.get("boarded")
            alighted = leg.get("alighted")
            if not isinstance(boarded, int) or boarded < 0:
                raise HTTPException(status_code=422, detail="montées invalides")
            if alighted is not None and (not isinstance(alighted, int) or alighted < 0):
                raise HTTPException(status_code=422, detail="descentes invalides")
            item["boarded"] = boarded
            item["alighted"] = alighted
            for key in ("standing", "seats_free", "imbalance"):
                if leg.get(key) is not None:
                    item[key] = _indicator(leg.get(key))
        cleaned.append(item)
    return json.dumps(cleaned, ensure_ascii=False)


def _indicator(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or not 0 <= value <= 100:
        raise HTTPException(status_code=422, detail="indicateur invalide")
    return value


def _list_saisies(database: Path) -> list[dict]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT client_id, origin_stop_id, destination_stop_id, origin_name, destination_name,
                   trip_id, passengers, reliability, pseudo, comment, standing, seats_free, imbalance,
                   materiel, composition, perimetre,
                   snapshot, kind, created_at, legs, trajet
            FROM saisie
            ORDER BY created_at
            """
        ).fetchall()
    listed = []
    for row in rows:
        snapshot = json.loads(row[16]) if row[16] else None
        listed.append(
            {
                "client_id": row[0],
                "origin_stop_id": row[1],
                "destination_stop_id": row[2],
                "origin_name": row[3],
                "destination_name": row[4],
                "trip_id": row[5],
                "passengers": row[6],
                "reliability": row[7],
                "pseudo": row[8],
                # Le commentaire est demandé par /methode (« les commentaires
                # sont précieux ») : sans le relire nulle part, il resterait
                # inexploitable, et la promesse de la page serait vide.
                "comment": row[9],
                "standing": row[10],
                "seats_free": row[11],
                "imbalance": row[12],
                "materiel": row[13],
                "composition": row[14],
                "perimetre": row[15],
                "snapshot": snapshot,
                "kind": row[17],
                "created_at": row[18],
                "legs": json.loads(row[19]) if row[19] else None,
                # Le trajet figé au moment du comptage, pas relu au moment de la
                # lecture : c'est tout l'intérêt. Voir `_freeze_trajet`.
                "trajet": json.loads(row[20]) if row[20] else None,
            }
        )
    return listed


def _legs_text(legs: object) -> str:
    if not isinstance(legs, list):
        return ""
    lines = []
    for index, leg in enumerate(legs):
        if not isinstance(leg, dict):
            continue
        name = escape(str(leg.get("stop_name") or leg.get("stop_id") or ""))
        if index == 0:
            lines.append(f"<li>{name} : {leg.get('onboard')} à bord</li>")
        else:
            alighted = "non comptées" if leg.get("alighted") is None else leg.get("alighted")
            lines.append(f"<li>{name} : {leg.get('boarded')} montées, {alighted} descentes</li>")
    return "<ul>" + "".join(lines) + "</ul>" if lines else ""


def _photo_status(snapshot: object, key: str) -> str:
    if not isinstance(snapshot, dict):
        return ""
    item = snapshot.get(key) or {}
    if not isinstance(item, dict):
        return ""
    return str(item.get("status") or "")


def _photo_label(snapshot: object, key: str) -> str:
    if not isinstance(snapshot, dict):
        return _photo_status(snapshot, key)
    item = snapshot.get(key) or {}
    if not isinstance(item, dict):
        return ""
    kind = item.get("kind") or ""
    etat = item.get("etat") or item.get("status") or ""
    delay = item.get("delay_seconds")
    minutes = f" {round(delay / 60)} min" if delay else ""
    return f"{kind} {etat}{minutes}".strip()


def _saisies_de_ligne(database: Path, timetable: Path, route_id: str) -> list[dict]:
    """Les comptages rattachés à une ligne.

    Le rattachement passe par le trip : c'est le seul lien écrit quand le
    comptage a été fait, et il dit la ligne exacte, ce qu'une paire
    origine-destination ne dit pas — deux lignes se partagent souvent le même
    corridor. Un comptage sans trip_id, et un « train signalé », n'ont pas de
    ligne : on ne les invente pas, on ne les affiche pas ici.
    """
    from comptagefer.timetable import _has_lines

    if not timetable.exists() or not _has_lines(timetable):
        return []
    with sqlite3.connect(timetable) as connection:
        trips = {row[0] for row in connection.execute(
            "SELECT trip_id FROM trip_ligne WHERE route_id = ?", (route_id,)
        )}
    if not trips:
        return []
    marques = ",".join("?" for _ in trips)
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            f"""
            SELECT client_id, origin_stop_id, destination_stop_id, origin_name, destination_name,
                   trip_id, passengers, reliability, pseudo, standing, seats_free, imbalance,
                   materiel, composition, perimetre,
                   snapshot, kind, created_at, legs
            FROM saisie
            WHERE trip_id IN ({marques}) AND kind IN ('count', 'serpent')
            ORDER BY created_at
            """,
            tuple(trips),
        ).fetchall()
    return _saisie_dicts(rows)


def _saisie_dicts(rows: list) -> list[dict]:
    listed = []
    for row in rows:
        listed.append(
            {
                "client_id": row[0],
                "origin_stop_id": row[1],
                "destination_stop_id": row[2],
                "origin_name": row[3],
                "destination_name": row[4],
                "trip_id": row[5],
                "passengers": row[6],
                "reliability": row[7],
                "pseudo": row[8],
                "standing": row[9],
                "seats_free": row[10],
                "imbalance": row[11],
                "materiel": row[12],
                "composition": row[13],
                "perimetre": row[14],
                "snapshot": json.loads(row[15]) if row[15] else None,
                "kind": row[16],
                "created_at": row[17],
                "legs": json.loads(row[18]) if row[18] else None,
            }
        )
    return listed


def _line_page(ligne: dict, rows: list[dict], arrets: list[dict]) -> str:
    """La page d'une ligne : ses arrêts, ses comptages, ou une invitation."""
    titre = escape(ligne["titre"])
    mode = {"train": "train", "car": "car", "tramway": "tramway"}.get(ligne.get("mode") or "", "")

    if arrets:
        liste_arrets = "<ol class='stops'>" + "".join(
            f"<li>{escape(arret['name'])}</li>" for arret in arrets
        ) + "</ol>"
    else:
        liste_arrets = "<p>Les arrêts de cette ligne ne sont pas dans l'horaire importé.</p>"

    if rows:
        corps = _reading_cards(rows)
    else:
        # Pas de carte blanche : l'invitation à compter est la page, et la
        # liste des arrêts est déjà ce qu'il faut pour savoir où monter. Le
        # lien est un bouton visible : un lien en fin de paragraphe, dans une
        # page faite pour être lue dans un train, passe inaperçu.
        corps = (
            "<div class='card'>"
            "<p><strong>Aucun comptage sur cette ligne pour l'instant.</strong></p>"
            "<p>Les lignes se comptent dans le train, sur un trajet. "
            "Une ligne sans comptage n'est pas une ligne vide : elle est "
            "simplement encore muette.</p>"
            "<p><a class='bouton' href='/'>Compter un train</a></p>"
            "</div>"
        )

    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titre} — ComptagesFer</title>
<style>
  body {{ margin: 0; font: 18px/1.35 system-ui, sans-serif; background: #f4f1ea; color: #1c1915; }}
  main {{ max-width: 32rem; margin: 0 auto; padding: 1rem 1rem 3rem; }}
  .card {{ background: #fff; border-radius: 0.8rem; padding: 0.8rem; margin: 0.6rem 0; }}
  a {{ color: #1c1915; }}
  .mode {{ font-size: 0.85rem; color: #5c554b; text-transform: uppercase; letter-spacing: 0.04em; }}
  ol.stops {{ padding-left: 1.2rem; }}
  ol.stops li {{ margin: 0.25rem 0; }}
  .status {{ font-size: 0.85rem; color: #5c554b; }}
  a.bouton {{ display: inline-block; background: #1c1915; color: #fff; text-decoration: none;
              padding: 0.7rem 1.1rem; border-radius: 0.6rem; font-weight: 600; }}
</style>
</head>
<body>
<main>
  <h1>{titre}</h1>
  {f"<p class='mode'>{mode}</p>" if mode else ""}
  <p>Ce n'est pas une fréquentation officielle. Les partages sont sous Licence Ouverte 2.0.</p>
  <p><a href="/rechercher">Rechercher</a> · <a href="/carte">Carte</a> · <a href="/comptages">Tous les comptages</a> · <a href="/">Compter</a> · <a href="/methode">Méthode</a></p>
  <h2>Arrêts</h2>
  {liste_arrets}
  <h2>Comptages</h2>
  {corps}
</main>
</body>
</html>
"""


def _link(href: str, text: str) -> str:
    return f'<a href="{href}">{escape(text)}</a>'


def _plain_reading_page(titre: str, corps: str) -> str:
    """Une page de lecture sans liste : ni ligne inconnue, ni paramètre oublié.

    Elle garde les mentions et la navigation, sinon on pourrait atterrir sur une
    page qui ne dit ni ce que sont ces chiffres, ni comment revenir.
    """
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(titre)} — ComptagesFer</title>
<style>
  body {{ margin: 0; font: 18px/1.35 system-ui, sans-serif; background: #f4f1ea; color: #1c1915; }}
  main {{ max-width: 32rem; margin: 0 auto; padding: 1rem 1rem 3rem; }}
  .card {{ background: #fff; border-radius: 0.8rem; padding: 0.8rem; margin: 0.6rem 0; }}
  a {{ color: #1c1915; }}
</style>
</head>
<body>
<main>
  <h1>{escape(titre)}</h1>
  <p>Ce n'est pas une fréquentation officielle. Les partages sont sous Licence Ouverte 2.0.</p>
  <p>{_link("/rechercher", "Rechercher")} · {_link("/carte", "Carte")} · {_link("/comptages", "Comptages")} · {_link("/", "Compter")} · {_link("/methode", "Méthode")}</p>
  <div class="card"><p>{corps}</p></div>
</main>
</body>
</html>
"""


def _search_page(query: str, stops_database: Path, timetable: Path) -> str:
    """Un seul champ pour une gare ou une ligne.

    Le cas d'usage est « Lyon » sans savoir si c'est une gare ou un nom de
    ligne : deux champs feraient choisir avant de savoir quoi chercher.
    """
    requete = query.strip()
    gares = search_stops(stops_database, requete) if requete else []
    lignes = search_lines(timetable, requete) if requete and _lignes_disponibles(timetable) else []

    if not requete:
        corps = (
            "<div class='card'><p>Écrivez un nom de gare ou de ligne. "
            "« Lyon » trouve les deux : la gare, et les lignes qui la traversent.</p></div>"
        )
    else:
        morceaux = []
        if gares:
            morceaux.append(
                "<h2>Gares</h2><ul class='stops'>"
                + "".join(f"<li>{escape(gare['name'])}</li>" for gare in gares)
                + "</ul>"
            )
        if lignes:
            morceaux.append(
                "<h2>Lignes</h2><ul class='stops'>"
                + "".join(
                    f"<li><a href=\"/ligne?ligne={quote(found['route_id'])}\">{escape(found['titre'])}</a></li>"
                    for found in lignes
                )
                + "</ul>"
            )
        if not morceaux:
            corps_morceaux = (
                "<div class='card'><p>Rien pour cette recherche.</p>"
                "<p>Les gares viennent du GTFS national. Les lignes aussi, "
                "mais seulement si l'import a été refait depuis la dernière mise à jour.</p></div>"
            )
        else:
            corps_morceaux = "".join(morceaux)
        corps = corps_morceaux

    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rechercher — ComptagesFer</title>
<style>
  body {{ margin: 0; font: 18px/1.35 system-ui, sans-serif; background: #f4f1ea; color: #1c1915; }}
  main {{ max-width: 32rem; margin: 0 auto; padding: 1rem 1rem 3rem; }}
  .card {{ background: #fff; border-radius: 0.8rem; padding: 0.8rem; margin: 0.6rem 0; }}
  a {{ color: #1c1915; }}
  input {{ width: 100%; box-sizing: border-box; font: inherit; padding: 0.7rem; border-radius: 0.6rem;
           border: 1px solid #b9b2a6; background: #fff; }}
  ul.stops {{ list-style: none; padding: 0; }}
  ul.stops li {{ background: #fff; border-radius: 0.6rem; padding: 0.6rem 0.8rem; margin: 0.3rem 0; }}
</style>
</head>
<body>
<main>
  <h1>Rechercher</h1>
  <form action="/rechercher" method="get">
    <label for="q">Gare ou ligne</label>
    <input id="q" type="search" name="q" enterkeyhint="search" autocomplete="off"
           value="{escape(requete)}" placeholder="Lyon, C13, Bourg-en-Bresse">
    <button type="submit">Chercher</button>
  </form>
  {corps}
  <p><a href="/comptages">Comptages</a> · <a href="/carte">Carte</a> · <a href="/rechercher">Rechercher</a> · <a href="/">Compter</a> · <a href="/methode">Méthode</a></p>
</main>
</body>
</html>
"""


def _lignes_disponibles(timetable: Path) -> bool:
    from comptagefer.timetable import _has_lines

    return timetable.exists() and _has_lines(timetable)


def _materiel_texte(row: dict) -> str:
    """Le matériel, sa composition et le périmètre, en une phrase lisible.

    « UM3, compté sur une voiture » est plus clair que trois cases vides pour
    celui qui relit un comptage sur une page ligne. Rien ne s'affiche quand les
    trois sont absents : un relevé sans matériel ne doit pas laisser une ligne
    vide qui ressemble à une information manquante alors que c'est un choix.
    """
    composition = row.get("composition") or ""
    perimetre = row.get("perimetre") or ""
    materiel = row.get("materiel") or ""
    if not composition and not perimetre:
        return ""
    parties = []
    if materiel:
        parties.append(escape(materiel))
    if composition:
        etendue = {"voiture": "compté sur une voiture", "um": "compté sur toute la rame"}.get(perimetre)
        label = f"{escape(composition)}" + (f", {etendue}" if etendue else "")
        parties.append(label)
    return " · ".join(parties)


def _reading_cards(rows: list[dict]) -> str:
    cards = []
    for row in rows:
        who = escape(row["pseudo"]) if row["pseudo"] else "anonyme"
        origin = escape(row["origin_name"] or "")
        destination = escape(row["destination_name"] or "")
        passengers = "" if row["passengers"] is None else row["passengers"]
        mode = "serpent" if row["kind"] == "serpent" else "unique"
        materiel = _materiel_texte(row)
        ligne_materiel = f"<p class='status'>{materiel}</p>" if materiel else ""
        cards.append(
            "<article class='card'>"
            f"<strong>{origin} → {destination}</strong>"
            f"<p>{passengers} voyageurs · {who} · {mode}</p>"
            f"{ligne_materiel}"
            f"{_legs_text(row.get('legs'))}"
            f"<p class='status'>précédent {_photo_label(row['snapshot'], 'precedent')} · "
            f"même type {_photo_label(row['snapshot'], 'precedent_meme_type')} · "
            f"choisi {_photo_label(row['snapshot'], 'courant')} · "
            f"suivant {_photo_label(row['snapshot'], 'suivant')} · "
            f"même type {_photo_label(row['snapshot'], 'suivant_meme_type')}</p>"
            "</article>"
        )
    return "\n".join(cards) or "<p>Aucun comptage pour l'instant.</p>"


def _reading_page(rows: list[dict]) -> str:
    body = _reading_cards(rows)
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Comptages</title>
<style>
  body {{ margin: 0; font: 18px/1.35 system-ui, sans-serif; background: #f4f1ea; color: #1c1915; }}
  main {{ max-width: 32rem; margin: 0 auto; padding: 1rem; }}
  .card {{ background: #fff; border-radius: 0.8rem; padding: 0.8rem; margin: 0.6rem 0; }}
  a {{ color: #1c1915; }}
</style>
</head>
<body>
<main>
  <h1>Comptages</h1>
  <p>Ce n'est pas une fréquentation officielle. Les partages sont sous Licence Ouverte 2.0.</p>
  <p><a href="/rechercher">Rechercher</a> · <a href="/carte">Voir la carte</a> · <a href="/api/export.csv">Télécharger le CSV</a> · <a href="/">Compter</a> · <a href="/methode">Méthode</a></p>
  {body}
</main>
</body>
</html>
"""


def _method_page() -> str:
    """La méthode, en clair, à côté des chiffres.

    Cette page est celle que le plan rend obligatoire avant toute
    estimation. Tant qu'elle n'existe pas, on ne publie que du brut, et
    c'est délibéré : un effectif saisi par un voyageur n'est pas une
    fréquentation, et un échantillon de passionnés n'est pas un sondage.
    """
    return """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Méthode — ComptagesFer</title>
<style>
  body { margin: 0; font: 18px/1.5 system-ui, sans-serif; background: #f4f1ea; color: #1c1915; }
  main { max-width: 34rem; margin: 0 auto; padding: 1rem 1rem 3rem; }
  h1 { margin-bottom: 0.2rem; }
  h2 { margin-top: 2rem; font-size: 1.15rem; }
  h3 { margin-top: 1.4rem; font-size: 1rem; }
  .lead { font-weight: 600; }
  ul { padding-left: 1.2rem; }
  li { margin: 0.35rem 0; }
  a { color: #1c1915; }
  .note { background: #fff; border-radius: 0.8rem; padding: 0.8rem 1rem; margin: 1rem 0; }
  footer { margin-top: 2.5rem; font-size: 0.85rem; color: #5c554b; }
</style>
</head>
<body>
<main>

<h1>Méthode</h1>
<p class="mode">Ce que l'outil fait, et comment lire un comptage.</p>

<p>Bienvenue sur ComptagesFer. Ce site permet de contribuer à la connaissance
des flux ferroviaires (+ certains cars TER) en France, y compris sur les trains
franciliens (RER, ligne U) et les TER d'Île-de-France. C'est précieux pour ouvrir
ces données au plus grand nombre. Vous pouvez consulter et exporter les
comptages réalisés, sans restrictions, mais en gardant en tête qu'il s'agit de
chiffres collectés par des particuliers, sans garantie de fiabilité.</p>

<p>Note&nbsp;: les comptages officiels commandés notamment par les Régions pour
les TER, et réalisés par des sociétés spécialisées, sont généralement
confidentiels. Certains peuvent néanmoins être rendus publics, par exemple dans
les comités de ligne, et désormais dans les COREST (comités régionaux des
Services de Transport).</p>

<p>Tout un chacun peut contribuer, en réalisant un ou des comptages à
l'occasion d'un voyage quel qu'il soit&nbsp;: voire pendant l'attente dans une
grande gare, en comptant le nombre de voyageurs montant dans le train avant son
départ, ou descendant d'un train qui termine son trajet là. Ce dernier cas est
intéressant, car c'est souvent à la gare « centrale », origine ou terminus, que
le train est le plus chargé.</p>

<div class="note">
<p><strong>Ce n'est pas une fréquentation officielle.</strong> Les données officielles
de fréquentation des trains régionaux ne sont pas publiques, ou le sont sous des
conditions étroites. Ce que vous voyez ici vient de gens qui ont compté, dans
leur train, à leur main.</p>
</div>

<h2>Comment ça marche</h2>

<p>Cliquez sur «&nbsp;ComptagesFer&nbsp;» ou sur «&nbsp;Compter&nbsp;», puis choisissez
l'origine et la destination du <strong>trajet compté</strong> — et non pas
l'origine-destination de la ligne. L'outil doit associer le comptage à
l'interstation réellement observée. Ensuite, deux cas de figure.</p>

<h3>1) Comptage unique</h3>

<p>L'outil permet d'enregistrer le nombre de voyageurs entre deux gares, de
préférence deux gares desservies successives, afin d'associer le comptage à une
«&nbsp;interstation&nbsp;» précise. Indiquez seulement les deux gares encadrantes
le comptage, et non pas l'OD du trajet effectué. Le champ «&nbsp;fiabilité du
compte&nbsp;» permet de préciser la qualité du comptage, qui peut être plus
faible en cas de charge importante et/ou de difficulté à se déplacer dans le
train. En cliquant sur «&nbsp;indicateurs, pseudo, commentaire&nbsp;», des
options facultatives apparaissent. Et c'est tout&nbsp;!</p>

<p>Note&nbsp;: c'est cette option «&nbsp;comptage unique&nbsp;» qu'il faut utiliser
pour compter un train tout en restant dans une gare, c'est-à-dire compter les
gens qui descendent ou montent sans emprunter le train. Dans ce cas, précisez la
dernière gare desservie avant l'arrivée (gare terminus) ou la première gare
desservie après le départ (gare d'origine), afin d'affecter le compte à une
interstation précise.</p>

<h3>2) Comptage « serpent de charge »</h3>

<p>L'outil permet d'enregistrer toutes les montées et descentes au fil d'un
trajet donné. Indiquez la gare de début du comptage et la gare de fin, même si
la ligne est plus longue, et même si le trajet effectué est plus long. Si
l'origine et la destination sont bien renseignées et le bon train choisi,
l'outil connaît toutes les gares intermédiaires desservies. Il suffit de
remplir les montées et descentes à chaque gare. En cliquant sur
«&nbsp;indicateurs de cette interstation&nbsp;», vous pouvez compléter à chaque
gare certaines informations sur l'interstation qu'elle conclut&nbsp;:
estimation de la part de gens debout, part de places assises restantes, écart
de charge en&nbsp;% entre les différentes voitures du train. À chaque gare,
vous saurez ainsi quel est l'effectif du train, en soustrayant les voyageurs
descendus et en ajoutant les voyageurs montés.</p>

<p>Dans les deux cas, les commentaires sont précieux&nbsp;: train précédent
supprimé, train très en retard qui expliquerait une forte charge, mise en place
d'un car de substitution qui expliquerait à l'inverse une charge plus faible,
etc.</p>

<p>À chaque comptage, l'outil enregistre aussi le <strong>trajet complet du
train</strong>, pas seulement le tronçon que vous avez compté&nbsp;: toutes les
gares qu'il dessert, du départ à l'arrivée, avec l'heure de chacune. Vous ne
faites rien de plus, et la saisie ne change pas. C'est pour plus tard, quand on
voudra estimer une fréquentation&nbsp;: la charge qu'un train emporte au-delà du
tronçon compté est exactement ce qu'un effectif à un endroit ne dit pas. Ce
trajet est une copie figée au moment du comptage, et non une lecture de
l'horaire au moment où vous consultez cette page&nbsp;: si une ligne change de
gares plus tard, votre comptage dira toujours ce que vous avez vu ce jour-là. Si
vous n'avez pas choisi de train, il n'y a rien&nbsp;: l'outil ne devine pas.</p>

<h2>D'où viennent les données</h2>

<ul>
  <li><strong>L'offre des trains</strong> vient du GTFS national « Réseau SNCF
      TGV, Intercités et TER » (données ouvertes SNCF, Licence Ouverte 2.0),
      et, pour l'Île-de-France, du GTFS « Transilien » de la même source, qui
      donne le RER et la ligne U. Il donne des horaires théoriques, pas la
      réalité du jour.</li>
  <li><strong>L'état des trains</strong> vient des flux GTFS-RT Trip Updates et
      Service Alerts, rafraîchis toutes les 2 minutes et conservés 6 heures. Si
      Trip Updates est vide, SIRI ET Lite est tenté une fois. L'état affiché
      est celui connu à cet instant&nbsp;: « programmé » veut dire
      « l'horaire existe, le retard n'est pas encore connu&nbsp;», pas
      « à l'heure&nbsp;».
      <br><strong>Les trains franciliens n'ont pas de flux temps réel ici.</strong>
      Les RER publient leur état par SIRI sur la plateforme d'Île-de-France
      Mobilités, qui n'est pas au format GTFS-RT. Un RER affiché
      « programmé&nbsp;» l'est donc <em>toujours</em>, même en retard&nbsp;: c'est
      une absence de donnée, pas une absence de retard.</li>
  <li><strong>Les comptages</strong> viennent des gens. C'est la seule source qui
      ne soit ni un horaire théorique ni un flux automatique.</li>
  <li><strong>Le tracé de la carte</strong> suit la voie ferrée réelle, à partir
      du réseau ferré national publié par le Cerema (Licence Etalab 2.0).
      Quand le réseau ne relie pas deux arrêts, le segment reste droit, et le
      bas de la page dit combien de tracés sont dans ce cas. Une gare dont la
      voie n'est pas dans ce jeu — faisceau couvert, tunnel — reste donc en
      segment droit&nbsp;: c'est un trou dans la source, pas dans votre saisie.
      Un car de substitution n'a pas de voie du tout.</li>
</ul>

<p>Si un train qui a circulé n'apparaît pas, ce n'est pas une ligne à corriger à
la main. Le signaler crée une trace pour voir les trous de l'offre, et rien
d'autre.</p>

<h2>Vos données</h2>

<p>Pas de compte, pas de mot de passe, aucun moyen de vous rattacher à une
saisie. Le pseudo est facultatif, et un pseudo n'est ni une identité ni un
droit&nbsp;: il signe un comptage, rien de plus.</p>

<p>Le commentaire, lui, est public&nbsp;: il part dans le CSV et dans le jeu de
données de data.gouv.fr, avec le pseudo. C'est ce qui le rend utile — un tiers
peut comprendre pourquoi un train était chargé — mais c'est du texte libre, donc
une donnée personnelle dès qu'un nom, un numéro ou une entreprise y apparaît.
Mieux vaut écrire « car de substitution » que « M. Dupont, directeur de la
Société X, à bord du 8 h 12 ».</p>

<p>La géolocalisation peut proposer la gare la plus proche, et elle n'est
jamais enregistrée. Aucune coordonnée GPS n'est stockée, ni envoyée au serveur.
L'application fonctionne hors ligne dans un train&nbsp;: si l'envoi échoue, la
saisie reste sur votre téléphone et part au retour du réseau, une seule fois,
sans créer de doublon.</p>

<p>Un administrateur peut masquer une saisie. Il n'y a pas de compte
administrateur&nbsp;: l'administration est un jeton que détient la personne qui
lance le conteneur.</p>

<h2>Licences</h2>

<p>Les comptages partagés sont sous <strong>Licence Ouverte 2.0</strong>. Le code
de l'application est sous <strong>GPL-3.0</strong>. Les deux ne se mélangent
pas&nbsp;: les chiffres que vous exportez relèvent de la première, le logiciel
qui les affiche de la seconde.</p>

<footer>
<p>Cette page décrit ce que l'outil fait aujourd'hui. Elle sera mise à jour
chaque fois qu'une règle change.</p>
<p><a href="/comptages">Voir les comptages</a> · <a href="/rechercher">Rechercher</a> · <a href="/carte">Carte</a> · <a href="/api/export.csv">Télécharger le CSV</a> · <a href="/">Compter</a></p>
</footer>

</main>
</body>
</html>
"""


def _export_csv(rows: list[dict]) -> str:
    """Le CSV des comptages.

    Délégué à `comptagefer.publish`, qui rend aussi le fichier publié sur
    data.gouv : une seule fonction pour le téléchargement et l'export
    automatique, sinon les deux divergent sans qu'on le voie.
    """
    return render_csv(rows)
