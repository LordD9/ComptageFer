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
from datetime import UTC, datetime
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse

from comptagefer import compte as compte_module
from comptagefer import score as score_module
from comptagefer.affichage import chrome
from comptagefer.carte import counted_features
from comptagefer.carte import map_page as carte_page
from comptagefer.compte import COOKIE_COMPTE, compte_de_session
from comptagefer.compte import preparer as preparer_compte
from comptagefer.filtres import (
    PAR_PAGE,
    Filtres,
    conditions,
    par_paire,
    trier_paires,
)

# Importé sous un autre nom : `_reading_table` a une variable locale
# `lien` pour un en-tête de colonne, et le même nom pour la fabrique
# d'URL serait un piège à lecture.
from comptagefer.filtres import lien as lien_comptages
from comptagefer.filtres import (
    lire as lire_filtres,
)
from comptagefer.offer import nearest_stops, open_stops, search_stops, trips_serving
from comptagefer.page import page_comptage
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
from comptagefer.securite import https_actif, origine_autorisee
from comptagefer.timetable import (
    find_line,
    listed_trips,
    stops_between,
    trip_stops_all,
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
    # `PRAGMA table_info` porte en colonne 5 le rang dans la clé primaire, et
    # **0 signifie « hors clé »** — pas « clé simple ». Une base neuve est
    # créée par le `CREATE TABLE` de `create_app`, qui ne pose aucune clé : le
    # test ci-dessous lisait donc 0, croyait la table déjà composite, et
    # rendait la main. Une base neuve n'a jamais eu de clé primaire du tout.
    #
    # Ce n'était visible par aucun test parce que la seule garantie d'idempotence
    # est un `SELECT` applicatif, qui masquait l'absence de contrainte. Elle est
    # fausse dès qu'un même `client_id` produit deux genres et que le `SELECT`
    # tombe sur l'autre ligne : voir `_save_saisie`.
    #
    # La clé est donc composite si — et seulement si — `kind` en fait partie.
    if colonnes["kind"][5] > 0:
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
        # `compte_id` vient de la phase 9 et n'est pas dans `SCHEMA_SAISIE` :
        # cette liste sert aussi à la copie de migration de `_clef_par_genre`,
        # et une colonne qui n'y est pas disparaît au passage. D'où l'ordre :
        # la clé d'abord, la colonne du compte ensuite. C'est le piège que la
        # migration de `trajet` avait déjà pris, et le corriger à l'envers
        # casserait toute base déjà migrée.
        _clef_par_genre(connection)
        preparer_compte(connection)

    timetable = data_dir / "timetable.db"
    stops_database = data_dir / "stops.db"

    https_actif()  # Valider la configuration avant de servir des cookies.
    app = FastAPI(title="ComptagesFer")

    def page_privee(request: Request) -> bool:
        return any(
            request.url.path == prefixe or request.url.path.startswith(prefixe + "/")
            for prefixe in ("/compte", "/admin")
        )

    def confidentialite(reponse: Response) -> None:
        reponse.headers["Cache-Control"] = "no-store"
        # no-referrer sur les formulaires rend Origin opaque (« null ») dans
        # Chromium. La page du secret, sans formulaire, l'impose elle-même.
        reponse.headers.setdefault("Referrer-Policy", "same-origin")
        reponse.headers["X-Frame-Options"] = "DENY"
        reponse.headers["Content-Security-Policy"] = "frame-ancestors 'none'"

    @app.exception_handler(Exception)
    async def erreur_interne(request: Request, exc: Exception) -> Response:
        # Les erreurs non interceptées sont rendues hors du middleware HTTP.
        reponse = PlainTextResponse("erreur interne", status_code=500)
        if page_privee(request):
            confidentialite(reponse)
        return reponse

    @app.middleware("http")
    async def proteger_comptes(request: Request, call_next):
        if request.method == "POST" and not origine_autorisee(request):
            reponse = PlainTextResponse("origine refusée", status_code=403)
        else:
            reponse = await call_next(request)
        if page_privee(request):
            confidentialite(reponse)
        return reponse
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
    def home(request: Request) -> str:
        """Le formulaire de comptage, avec le pseudo de la session ouverte.

        `PAGE` est une constante et le pseudo du compte est une donnée de la
        session : sans les assembler, la personne qui a un compte devait
        retaper son pseudo à chaque comptage, en plus du trajet. C'est le
        genre d'oubli qui fait qu'on arrête de signer ses relevés.

        Le pseudo vient de `compte_de_session`, qui lit la base : le cookie
        est `httpOnly`, donc la page ne peut pas le lire, et un jeton signé
        par la page serait un jeton qu'une injection lit (voir
        `docs/regles.md` § 1). Sans session, la page est rendue telle
        quelle — c'est le cas le plus fréquent et il ne doit coûter aucune
        lecture en base.
        """
        identifiant = _compte_de_cookie(request)
        if identifiant is None:
            return page_comptage()
        try:
            pseudo = compte_module.pseudo_de(database, identifiant)
        except sqlite3.DatabaseError:
            # Une base verrouillée ne doit pas faire échouer la saisie, qui
            # est le chemin par défaut du site : on rend la page sans pseudo
            # pré-rempli, et le comptage reste possible.
            return page_comptage()
        return page_comptage(pseudo)

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
            parsed = parsed.replace(tzinfo=UTC)
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
    def comptages(
        tri: str = Query("", max_length=20),
        sens: str = Query("", max_length=4),
        depuis: str = Query("", max_length=10),
        jusqu: str = Query("", max_length=10),
        mode: str = Query("", max_length=12),
        ligne: str = Query("", max_length=120),
        vue: str = Query("", max_length=16),
        # `str` et pas `int` : une URL reçoit des fautes de frappe, et
        # `?page=beaucoup` doit donner une page lisible, pas une 422.
        # Une 422 pour un nombre mal tapé est une page perdue — le
        # message dit «Champ invalide» et ne dit pas ce qu'il fallait
        # écrire, alors que la page suivante, elle, se lit.
        page: str = Query("1", max_length=8),
    ) -> str:
        """La liste des comptages, filtrable, et la vue par paire.

        Le tri, le sens et les filtres sont des paramètres d'URL et non
        un état côté navigateur : une liste filtrée se partage, se met
        en signet et se teste. Le défaut est le plus récent d'abord,
        parce qu'une liste dont le bas est le plus récent oblige à faire
        défiler toute la page pour voir ce qui vient d'arriver.

        `vue=paire` regroupe les mêmes relevés par origine-destination.
        C'est un paramètre de plus sur la même URL et non une page de
        plus : une page de plus serait un endroit de plus où les mêmes
        données peuvent diverger, et le contexte de la liste s'y perdrait.
        """
        filtres = lire_filtres(
            depuis,
            jusqu,
            mode,
            ligne,
            lignes_disponibles=lambda nom: (
                find_line(timetable, nom) if _lignes_disponibles(timetable) else None
            ),
        )
        trips = _trips_de_ligne(timetable, filtres.ligne)
        rows, total = _saisies_filtrees(database, filtres, trips)
        return _reading_page(
            rows,
            tri=tri,
            sens=sens,
            filtres=filtres,
            vue=vue,
            page=_page_depuis(page),
            total=total,
        )

    @app.get("/carte", response_class=HTMLResponse)
    def carte() -> str:
        rows = _list_saisies(database)
        # `total` est le nombre de relevés collectés, `placos` le nombre de
        # traits dessinés : l'écart entre les deux est l'information utile, il
        # ne faut donc pas les confondre. Mais le décompte ignorait les trains
        # signalés, qui sont eux dessinés — la page pouvait alors annoncer
        # « 0 comptage au total, 1 sur la carte ».
        return carte_page(counted_features(data_dir / "stops.db", rows), len(rows))

    @app.get("/gare", response_class=HTMLResponse)
    def gare(stop: str = Query("", alias="stop", max_length=120)) -> str:
        """Les comptages d'une gare, et rien d'autre.

        Cette page reste accessible par identifiant de gare et montre les
        comptages qui la touchent.

        Une gare sans comptage n'est pas une page d'erreur : la page dit
        qu'il n'y en a pas encore et propose d'en compter un. Une gare muette
        attend son premier comptage, ce n'est pas une gare inexistante.
        """
        return _gare_page(database, stops_database, stop.strip())

    @app.get("/releve", response_class=HTMLResponse)
    def releve(
        client_id: str = Query("", max_length=120),
        kind: str = Query("count", max_length=16),
    ) -> str:
        """Un comptage en entier, sur sa propre page.

        `/comptages` résume : OD, effectif, auteur, date. Le reste du relevé
        — la composition de la rame, les indicateurs de charge, le
        commentaire, les arrêts du serpent, la photo du temps réel —
        n'était lisible qu'en téléchargeant le CSV. Sur un téléphone, dans un
        train, ce n'est pas une option : c'est la seule façon de vérifier
        qu'un effectif se lit à l'échelle de la rame.

        La page est indexable et partageable : son URL est la clé
        `(client_id, kind)`, les deux colonnes de la clé primaire. Le
        `client_id` vient du navigateur, donc il est échappé comme n'importe
        quelle donnée client — règle 1 de `docs/regles.md`.
        """
        return _releve_page(database, client_id, kind)

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
    def sessions(request: Request, body: dict) -> dict:
        kind = "serpent" if body.get("kind") == "serpent" else "count"
        return _save_saisie(
            database,
            body,
            kind=kind,
            timetable=timetable,
            stops_database=stops_database,
            # Le compte vient du cookie et jamais du corps de la requête : un
            # `compte_id` envoyé par le navigateur serait une valeur que le
            # client choisit, donc un relevé qu'on s'attribue. Un cookie
            # inconnu — un navigateur qui garde une session close, une
            # tentative forgée — donne `None`, et le relevé est enregistré
            # sans compte. Compter reste le chemin par défaut.
            compte_id=_compte_de_cookie(request),
        )

    @app.post("/api/missing")
    def missing(request: Request, body: dict) -> dict:
        return _save_saisie(database, body, kind="missing", compte_id=_compte_de_cookie(request))

    def _compte_de_cookie(request: Request) -> str | None:
        """Le compte du cookie de session, ou `None`.

        La lecture touche la base à chaque `POST`, donc une connexion à chaque
        comptage. Sur le volume du projet c'est indolore, et c'est le prix d'un
        `compte_id` qui ne peut pas être fourni par le client : le cookie est la
        seule preuve, et la base est la seule qui sache si la session est
        encore ouverte.
        """
        jeton = request.cookies.get(COOKIE_COMPTE, "")
        if not jeton:
            return None
        try:
            return compte_de_session(database, jeton)
        except sqlite3.DatabaseError:
            # Une base verrouillée ou absente ne doit pas faire perdre un
            # comptage déjà fait dans le train. Le relevé part sans compte.
            return None

    @app.get("/compte", response_class=HTMLResponse)
    def compte_page(request: Request) -> str:
        """L'historique ou les formulaires ; un GET ne rend jamais de secret."""
        identifiant = _compte_de_cookie(request)
        if identifiant is not None:
            return _compte_historique(identifiant)
        return _compte_acces()

    @app.post("/compte/creer")
    def compte_creer(request: Request, pseudo: str = Form("")) -> HTMLResponse:
        """Un compte, son secret affiché une seule fois, et une session ouverte.

        La session est ouverte tout de suite : demander un secret à quelqu'un
        pour qu'il se connecte dans la foulée serait lui faire recopier un
        caractère pour rien.

        Le POST rend directement la page : une redirection contenant le secret
        le divulguait dans l'historique, les journaux et les Referer.

        Le pseudo est exigé ici, et pas seulement par le `required` du champ. Un
        `POST` peut être envoyé sans navigateur — un `curl`, un script — et la
        route acceptait un corps vide : elle créait un compte sans pseudo, et le
        classement le rangeait sous « un compte sans pseudo ». Un rang sans
        auteur est une ligne de classement qui ne veut rien dire, donc la
        contrainte est dans la route, où elle vaut pour les deux chemins.
        """
        pseudo = pseudo.strip()
        if not pseudo:
            raise HTTPException(status_code=422, detail="un pseudo est requis")
        try:
            jeton, expire, secret = compte_module.creer_compte_et_session(
                database, pseudo, request.cookies.get(COOKIE_COMPTE, "")
            )
        except compte_module.QuotaCreationAtteint:
            raise HTTPException(
                status_code=429,
                detail="trop de créations de comptes ; réessayez plus tard",
                headers={"Retry-After": "3600"},
            ) from None
        except sqlite3.DatabaseError:
            raise HTTPException(
                status_code=503, detail="création temporairement indisponible"
            ) from None
        reponse = HTMLResponse(
            _compte_secret(secret), headers={"Referrer-Policy": "no-referrer"}
        )
        # Le cookie est posé sur `reponse`, et `reponse` est ce qui part. Le
        # poser sur un autre objet et renvoyer celui-ci serait perdu — c'est pour
        # ça que la réponse est construite ici et pas dans le décorateur.
        compte_module.poser_cookie(reponse, jeton, expire)
        return reponse

    @app.post("/compte/se-connecter")
    def compte_se_connecter(request: Request, secret: str = Form("")) -> HTMLResponse:
        """Un secret ouvre une session, ou rien.

        Le secret va dans le corps du `POST` et jamais dans l'URL : une URL finit
        dans l'historique du navigateur, dans un journal de serveur et dans
        l'en-tête `Referer` de la page suivante. Le `303` renvoie ensuite sur
        `/compte`, où plus rien du secret ne figure dans la barre d'adresse.
        """
        reponse = HTMLResponse(status_code=303, headers={"location": "/compte"})
        identifiant = None
        if secret.strip():
            try:
                identifiant = compte_module.compte_de_secret(database, secret)
            except sqlite3.DatabaseError:
                raise HTTPException(
                    status_code=503, detail="connexion temporairement indisponible"
                ) from None
        if identifiant is not None:
            try:
                jeton, expire = compte_module.ouvrir_session(
                    database, identifiant, request.cookies.get(COOKIE_COMPTE, "")
                )
            except sqlite3.DatabaseError:
                raise HTTPException(
                    status_code=503, detail="connexion temporairement indisponible"
                ) from None
            compte_module.poser_cookie(reponse, jeton, expire)
        return reponse

    @app.post("/compte/signaler")
    def compte_signaler(
        request: Request,
        client_id: str = Form(""),
        kind: str = Form(""),
        motif: str = Form(""),
    ) -> HTMLResponse:
        """Un relevé signalé, et rien d'autre.

        Le signalement ne modifie ni `saisie` ni le score : il écrit une ligne que
        l'admin lit. C'est écrit dans le plan et répété ici, parce que c'est le
        genre de geste qui dérive en suppression dès qu'on n'a pas la phrase sous
        les yeux.

        Sans session, la réponse est un refus et **rien n'est écrit**. Le
        `client_id` et le `kind` viennent du formulaire, donc ils sont contrôlables
        — le contrôle qui compte est dans `signaler`, qui vérifie que le relevé
        appartient bien au compte connecté. Une session ouverte ne donne le droit
        de signaler que ses propres relevés.
        """
        reponse = HTMLResponse(status_code=303, headers={"location": "/compte"})
        identifiant = _compte_de_cookie(request)
        if identifiant is None:
            return reponse
        compte_module.signaler(database, identifiant, client_id, kind, motif)
        return reponse

    @app.post("/compte/deconnecter", response_class=HTMLResponse)
    def compte_deconnecter(request: Request, response: Response) -> str:
        response.status_code = 303
        response.headers["location"] = "/compte"
        # La fermeture prend le jeton du cookie et non l'identifiant : c'est
        # la ligne de `session` qu'il faut supprimer, et elle est addressée par
        # son jeton. Un cookie absent n'a rien à fermer.
        compte_module.fermer_session(database, request.cookies.get(COOKIE_COMPTE, ""))
        compte_module.retirer_cookie(response)
        return response

    @app.get("/classement", response_class=HTMLResponse)
    def classement() -> str:
        try:
            lignes = _classement(database)
        except sqlite3.DatabaseError:
            # Une base verrouillée rendait un 500 nu : la page publique du
            # classement disparaissait, sans explication, et le visiteur ne
            # pouvait pas distinguer « le site est cassé » de « il n'y a
            # personne ». C'est la règle 2 de `docs/regles.md` appliquée à une
            # lecture : elle ne dit pas « personne » — ce serait un mensonge —
            # elle dit qu'elle n'a pas pu lire.
            #
            # `/compte` et `/api/sessions` traitent déjà `DatabaseError` de la
            # même façon, chacun dans sa fonction. `score.classement` ne le
            # faisait pas : c'est le chemin le plus lu et le seul qui rendait
            # 500.
            return _classement_page([], indisponible=True)
        return _classement_page(lignes)

    def _compte_historique(identifiant: str) -> str:
        """Les relevés de la personne, son score, et le bouton qui la déconnecte.

        La liste est tronquée par `releves_de`, et le total est compté à part :
        déduire le total de la liste rendue ferait dire « 200 relevés » à une
        personne qui en a 4 000.

        Les signalements déjà posés sont lus en une requête et passés à
        `_bouton_signaler`, qui s'en sert pour écrire « déjà signalé » sur le
        bouton. Le dire **avant** le clic compte : un formulaire qui revient sans
        changement visible laisse croire que l'action n'a rien fait.
        """
        releves = compte_module.releves_de(database, identifiant)
        total = compte_module.nombre_de_releves(database, identifiant)
        deja = compte_module.deja_signales(database, identifiant)
        # Le score est dit ici, sinon le classement classe des efforts qu'on ne
        # voit pas : quelqu'un qui vient de passer premier n'a aucun moyen de
        # savoir ce que ce rang vaut ni sur quoi il repose. Un compte sans aucun
        # relevé n'a pas de score, et la page le dit — « aucun point » se lirait
        # comme un score nul alors que c'est une absence.
        score = score_module.score_de(database, identifiant)
        pluriel = "" if total == 1 else "s"
        # La déconnexion est hors de la branche « a des relevés ». Elle en était
        # dedans : un compte sans aucun relevé n'avait aucun bouton, donc
        # personne ne pouvait quitter sa session. C'est le pire endroit pour un
        # oubli de ce genre — le compte le plus récent est précisément celui qui
        # n'a pas encore compté, donc celui qu'on vient juste de créer et qu'on
        # veut pouvoir refermer.
        deconnecter = (
            '<form method="post" action="/compte/deconnecter">'
            '<button class="ghost" type="submit">Se deconnecter</button>'
            "</form>"
        )
        if not releves:
            corps = (
                "<h1>Vos releves</h1>"
                "<p>Aucun releve n'est encore rattache a ce compte. "
                "Comptage et serpent y sont rattaches automatiquement.</p>"
                '<p><a class="bouton" href="/">Compter</a></p>'
                + deconnecter
            )
            return chrome("Votre compte", corps, actif="/compte")
        cartes = []
        for releve in releves:
            effectif = releve["passengers"]
            valeur = "—" if effectif is None else str(effectif)
            titre = "Train signale" if releve["kind"] == "missing" else valeur + " voyageurs"
            depart = escape(str(releve["origin_name"] or ""))
            arrivee = escape(str(releve["destination_name"] or ""))
            fiabilite = escape(str(releve["reliability"] or "—"))
            cartes.append(
                '<article class="carte">'
                + "<h2>" + depart + " → " + arrivee + "</h2>"
                + "<p>" + escape(titre) + "</p>"
                + '<p class="mention">Fiabilité ' + fiabilite + "</p>"
                + '<p><a href="/compte/modifier?client_id=' + quote(str(releve["client_id"]))
                + '&amp;kind=' + quote(str(releve["kind"])) + '">Modifier</a></p>'
                + '<form method="post" action="/compte/supprimer">'
                + '<input type="hidden" name="client_id" value="' + escape(str(releve["client_id"])) + '">'
                + '<input type="hidden" name="kind" value="' + escape(str(releve["kind"])) + '">'
                + '<label><input type="checkbox" name="confirmation" value="oui" required> Confirmer la suppression</label>'
                + '<button type="submit">Supprimer</button></form>'
                + _bouton_signaler(deja, releve)
                + "</article>"
            )
        suite = (
            "<p>Les " + str(total) + " plus recents sont montres.</p>"
            if total > len(releves)
            else ""
        )
        corps = (
            "<h1>Vos releves</h1>"
            + "<p>" + str(total) + " releve" + pluriel + " rattache" + pluriel
            + " a ce compte.</p>"
            + "<p>" + _nombre(f"{score.points:g}".replace(".", ",")) + " point"
            + ("" if score.points == 1 else "s")
            + " au classement, sur ces " + str(total) + " releve" + pluriel
            + ", dont " + f"{score.inedit:g}" + " sur des corridors qu'aucun "
            + "releve ne portait.</p>"
            + "".join(cartes)
            + suite
            + deconnecter
        )
        return chrome("Votre compte", corps, actif="/compte")

    def releve_a_modifier(client_id: str, kind: str, compte_id: str | None = None) -> dict | None:
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            sql = "SELECT * FROM saisie WHERE client_id = ? AND kind = ?"
            params: tuple = (client_id, kind)
            if compte_id is not None:
                sql += " AND compte_id = ?"
                params += (compte_id,)
            row = connection.execute(sql, params).fetchone()
        return None if row is None else dict(row)

    def compte_editeur(request: Request) -> str:
        jeton = request.cookies.get(COOKIE_COMPTE, "")
        if not jeton:
            raise HTTPException(status_code=401, detail="connexion requise")
        try:
            identifiant = compte_de_session(database, jeton)
        except sqlite3.DatabaseError:
            raise HTTPException(status_code=503, detail="session temporairement indisponible") from None
        if identifiant is None:
            raise HTTPException(status_code=401, detail="connexion requise")
        return identifiant

    def mettre_a_jour_releve(
        client_id: str, kind: str, values: dict, compte_id: str | None = None,
        ancienne_structure: str | None = None, verifier_structure: bool = False,
    ) -> bool:
        sets = ", ".join(f"{name} = ?" for name in values)
        params = tuple(values.values()) + (client_id, kind)
        sql = f"UPDATE saisie SET {sets} WHERE client_id = ? AND kind = ?"
        if compte_id is not None:
            sql += " AND compte_id = ?"
            params += (compte_id,)
        if verifier_structure:
            sql += " AND legs IS ?"
            params += (ancienne_structure,)
        with sqlite3.connect(database) as connection:
            cursor = connection.execute(sql, params)
            return cursor.rowcount == 1

    @app.get("/compte/modifier", response_class=HTMLResponse)
    def compte_modifier_page(request: Request, client_id: str = Query(""), kind: str = Query("")) -> str:
        compte_id = compte_editeur(request)
        if kind not in {"count", "serpent", "missing"}:
            raise HTTPException(status_code=404, detail="relevé introuvable")
        try:
            row = releve_a_modifier(client_id, kind, compte_id)
        except sqlite3.DatabaseError:
            raise HTTPException(status_code=503, detail="relevés temporairement indisponibles") from None
        if row is None:
            raise HTTPException(status_code=404, detail="relevé introuvable")
        return chrome("Modifier le relevé", _edition_page(row), actif="/compte", extra_css=_EDITION_CSS)

    @app.post("/compte/modifier", response_class=HTMLResponse)
    async def compte_modifier(request: Request) -> HTMLResponse:
        compte_id = compte_editeur(request)
        form = await request.form()
        client_id, kind = str(form.get("client_id") or ""), str(form.get("kind") or "")
        if kind not in {"count", "serpent", "missing"}:
            raise HTTPException(status_code=422, detail="genre de relevé invalide")
        try:
            # Le contrôle d'accès et l'écriture filtrent tous deux par la clé et
            # le propriétaire ; le compte fourni dans le formulaire est ignoré.
            row = releve_a_modifier(client_id, kind, compte_id)
            if row is None:
                raise HTTPException(status_code=404, detail="relevé introuvable")
            values = _edition_values(dict(form), kind, row.get("legs"))
            if not mettre_a_jour_releve(client_id, kind, values, compte_id, row.get("legs"), kind == "serpent"):
                raise HTTPException(status_code=409, detail="le relevé a changé, rechargez la page")
        except sqlite3.DatabaseError:
            raise HTTPException(status_code=503, detail="relevés temporairement indisponibles") from None
        return HTMLResponse(status_code=303, headers={"location": "/compte"})

    @app.post("/compte/supprimer", response_class=HTMLResponse)
    async def compte_supprimer(request: Request) -> HTMLResponse:
        compte_id = compte_editeur(request)
        form = await request.form()
        client_id, kind = str(form.get("client_id") or ""), str(form.get("kind") or "")
        if kind not in {"count", "serpent", "missing"}:
            raise HTTPException(status_code=422, detail="genre de relevé invalide")
        if form.get("confirmation") != "oui":
            raise HTTPException(status_code=422, detail="confirmation requise")
        try:
            with sqlite3.connect(database) as connection:
                cursor = connection.execute(
                    "DELETE FROM saisie WHERE client_id = ? AND kind = ? AND compte_id = ?",
                    (client_id, kind, compte_id),
                )
                if cursor.rowcount != 1:
                    raise HTTPException(status_code=404, detail="relevé introuvable")
        except sqlite3.DatabaseError:
            raise HTTPException(status_code=503, detail="relevés temporairement indisponibles") from None
        return HTMLResponse(status_code=303, headers={"location": "/compte"})

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
        return _admin_list(_list_saisies(database), signalements=compte_module.signalements(database))

    @app.get("/admin/modifier", response_class=HTMLResponse)
    def admin_modifier_page(request: Request, client_id: str = Query(""), kind: str = Query("")) -> str:
        if not admin_open(request):
            raise HTTPException(status_code=401, detail="connexion requise")
        if kind not in {"count", "serpent", "missing"}:
            raise HTTPException(status_code=404, detail="relevé introuvable")
        try:
            row = releve_a_modifier(client_id, kind)
        except sqlite3.DatabaseError:
            raise HTTPException(status_code=503, detail="relevés temporairement indisponibles") from None
        if row is None:
            raise HTTPException(status_code=404, detail="relevé introuvable")
        return chrome("Modifier le relevé", _edition_page(row, admin=True), actif="/admin", extra_css=_EDITION_CSS)

    @app.post("/admin/modifier", response_class=HTMLResponse)
    async def admin_modifier(request: Request) -> str:
        if not admin_open(request):
            raise HTTPException(status_code=401, detail="connexion requise")
        form = await request.form()
        client_id, kind = str(form.get("client_id") or ""), str(form.get("kind") or "")
        if kind not in {"count", "serpent", "missing"}:
            raise HTTPException(status_code=422, detail="genre de relevé invalide")
        try:
            row = releve_a_modifier(client_id, kind)
            if row is None:
                raise HTTPException(status_code=404, detail="relevé introuvable")
            values = _edition_values(dict(form), kind, row.get("legs"))
            if not mettre_a_jour_releve(client_id, kind, values, ancienne_structure=row.get("legs"),
                                        verifier_structure=kind == "serpent"):
                raise HTTPException(status_code=409, detail="le relevé a changé, rechargez la page")
        except sqlite3.DatabaseError:
            raise HTTPException(status_code=503, detail="relevés temporairement indisponibles") from None
        return _admin_list(_list_saisies(database), signalements=compte_module.signalements(database))

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
            secure=https_actif(),
            max_age=SESSION_SECONDS,
            path="/",
        )
        return _admin_list(_list_saisies(database), signalements=compte_module.signalements(database))

    @app.post("/admin/supprimer", response_class=HTMLResponse)
    def admin_delete(
        request: Request,
        client_id: str = Form(""),
        kind: str = Form(""),
    ) -> str:
        """Retirer un relevé de la liste et du CSV. L'admin seulement.

        Le `kind` voyage avec le `client_id` parce que la clé primaire de `saisie`
        est ce couple : supprimer par `client_id` seul effacerait aussi le train
        manquant du même navigateur, et un admin qui écarte un comptage erroné
        supprimerait par accident un signalement de train qui est une information
        en soi.

        Le `kind` est donc obligatoire ici. Une requête ancienne qui n'enverrait
        que le `client_id` ne supprimerait plus rien : c'est un changement de
        comportement visible, et il vaut mieux que la suppression silencieusement
        élargie. Le bouton de la page envoie les deux.
        """
        if not admin_open(request):
            raise HTTPException(status_code=401, detail="connexion requise")
        if kind not in {"count", "serpent", "missing"}:
            raise HTTPException(status_code=422, detail="genre de relevé invalide")
        try:
            with sqlite3.connect(database) as connection:
                cursor = connection.execute(
                    "DELETE FROM saisie WHERE client_id = ? AND kind = ?",
                    (client_id, kind),
                )
                if cursor.rowcount != 1:
                    raise HTTPException(status_code=404, detail="relevé introuvable")
        except sqlite3.DatabaseError:
            raise HTTPException(status_code=503, detail="relevés temporairement indisponibles") from None
        return _admin_list(_list_saisies(database), signalements=compte_module.signalements(database))

    return app


def _classement(database: Path) -> list[dict]:
    """Les comptes et leurs points.

    Le calcul est dans `comptagefer.score` et nulle part ici : le score n'est pas
    une colonne, il serait faux dès que la formule change, et il faudrait le
    recalculer — donc le migrer — à chaque réglage. Cette fonction n'est plus qu'un
    nom, et elle le garde parce que l'appel est dans `create_app` et qu'un
    rechargement de module n'a pas à changer les routes.
    """
    return score_module.classement(database)


def _champ(nom: str, libelle: str, attributs: str) -> str:
    """Un champ étiqueté, pour les deux formulaires de `/compte`.

    Le champ est rendu **à l'intérieur** du `<form>`, jamais à côté. Une version
    précédente renvoyait `label + input` puis laissait l'appelant refermer son
    formulaire, donc le `</form>` tombait après l'`input` : le champ était dans le
    DOM mais hors de son formulaire, et **rien** ne le soumettait. Créer un compte
    depuis un navigateur enregistrait donc un pseudo vide — sans erreur, sans
    422, et le pseudo du compte venait à manquer au classement.

    C'est exactement le genre de faute qu'un test qui relit le HTML ne voit pas :
    le `<input name="pseudo">` est bien dans la page. Il faut un vrai navigateur
    qui envoie le formulaire pour le voir, d'où `tests/test_browser_compte.py`.

    Le `label` porte le `for` qui va avec l'`id` de l'input : le clic sur le
    libellé doit placer le curseur dans le champ, et c'est ce que vérifie
    l'accessibilité.
    """
    return (
        f'<label for="{nom}">{escape(libelle)}</label>'
        f'<input id="{nom}" name="{nom}" {attributs}>'
    )


def _formulaire(action: str, champ: str, texte_bouton: str) -> str:
    """Un `POST`, son champ, et son bouton — dans cet ordre, dans la même balise.

    La fonction prend le **corps** du formulaire et noue les trois morceaux. Elle
    remplace les deux fonctions qui rendaient le champ puis le bouton séparément,
    et l'ordre de rendu devenait une affaire de ponctuation : le `</form>` se
    refermait après l'`input`, et le champ sortait du formulaire.

    C'est la seule façon que l'erreur ne puisse plus arriver : il n'y a plus d'en-
    tre deux appels dont l'ordre compte.
    """
    return (
        f'<form method="post" action="{action}">'
        f"{champ}"
        f'<button type="submit">{escape(texte_bouton)}</button>'
        "</form>"
    )


def _compte_acces() -> str:
    """Créer un compte, ou s'y reconnecter. Aucun email, aucun mot de passe.

    Les deux gestes sont sur la même page parce qu’une personne qui revient ne
    sait pas encore si elle a un compte : lui faire choisir à l’avance serait lui
    demander de le savoir.

    Sans paramètre : le secret neuf est rendu par `_compte_secret` avant d'arriver
    ici, donc cette page est celle où il n'y a **aucune** session et aucun secret à
    afficher. Elle n'a plus de paramètre du tout : l'ordre des états est le
    problème de `compte_page`, qui est le seul endroit qui connaît les quatre.
    """
    corps = (
        "<h1>Votre compte</h1>"
        "<p>Le compte est facultatif : compter n’en demande pas. Il sert à "
        "retrouver vos relevés, et à apparaître au classement.</p>"
        "<h2>Revenir</h2>"
        + _formulaire(
            "/compte/se-connecter",
            _champ(
                "secret",
                "Votre secret",
                'type="text" required autocomplete="off" autocapitalize="none" '
                'spellcheck="false"',
            ),
            "Ouvrir mon compte",
        )
        + "<h2>En créer un</h2>"
        + _formulaire(
            "/compte/creer",
            _champ(
                "pseudo",
                "Votre pseudo",
                # Le `required` est ce qui rend la saisie obligatoire dans un
                # navigateur. Il ne suffit pas : `POST /compte/creer` accepte
                # n'importe quel corps, et un appel sans pseudo créait un compte
                # vide — que le classement affiche comme « un compte sans
                # pseudo », donc un rang sans auteur. La borne est donc
                # aussi dans la route, où elle vaut pour tout le monde.
                'type="text" required maxlength="40" autocomplete="nickname"',
            ),
            "Créer mon compte",
        )
        + '<p class="mention">Aucune adresse email n’est demandée ni conservée. '
        "Vous obtenez un secret que vous gardez : c’est lui qui ouvre votre "
        "compte. Le perdre, c’est perdre le compte.</p>"
    )
    return chrome("Votre compte", corps, actif="/compte")


def _compte_secret(secret: str) -> str:
    """Le secret, une fois, avec le bouton qui le copie.

    Deux règles dans cette page, et elles se contredisent assez pour qu’il faille
    les écrire.

    La page **doit** rester utilisable sans le bouton : l’API presse-papiers peut
    être refusée, et une personne qui ne peut pas récupérer son compte parce
    qu’un bouton ne marche pas a perdu son compte. Donc le secret est dans un
    `<code>` sélectionnable, et le bouton n’est qu’un confort.

    Le bouton **ne doit pas** se taire quand il échoue : il bascule en
    « sélectionner », et il écrit ce qu’il a fait dans la page. Une alerte
    disparaît, et le doute de ne pas avoir copié reste.

    Le secret reste dans le corps de la réponse au POST de création, jamais
    dans une URL ni dans un cookie. Aucun GET ne peut le réafficher.
    """
    echappe = escape(secret)
    corps = (
        "<h1>Votre secret</h1>"
        '<p class="mention">Il ne sera plus jamais affiché. Copiez-le, puis '
        "gardez-le : c’est lui qui rouvre votre compte.</p>"
        + '<p><code id="secret-texte">'
        + echappe
        + "</code> "
        + '<button id="copier-secret" type="button" data-secret="'
        + echappe
        + '">Copier</button> '
        + '<span id="copie-retour" role="status"></span></p>'
        "<h2>Comment le garder</h2>"
        "<p>Une note dans votre gestionnaire de mots de passe. Pas sur un papier "
        "qui traîne, et surtout pas dans un email que vous renverriez à cet "
        "outil.</p>"
        '<p><a class="bouton" href="/compte">J’ai copié mon secret</a></p>'
    )
    return chrome(
        "Votre secret",
        corps,
        actif="/compte",
        extra_script=_SCRIPT_COPIER,
        extra_head='<meta name="robots" content="noindex">',
    )


# Le bouton de copie. Deux lignes de logique, une garde pour l'absence, et un
# repli : si l'API refuse — page non sécurisée, vieux navigateur, permission
# refusée — le secret est sélectionné et le dit. Le repli n'est pas un détail
# cosmétique : sur une installation en `http://10.x`, `navigator.clipboard` est
# absent, et un bouton sans repli serait un bouton mort sur le Pi d'origine.
_SCRIPT_COPIER = """<script>
(function () {
  var texte = document.getElementById("secret-texte");
  var bouton = document.getElementById("copier-secret");
  var retour = document.getElementById("copie-retour");
  if (!texte || !bouton || !retour) return;
  function selectionner() {
    var plage = document.createRange();
    plage.selectNodeContents(texte);
    var choix = window.getSelection();
    choix.removeAllRanges();
    choix.addRange(plage);
    retour.textContent = "Sélectionné : copiez-le avec Ctrl+C.";
  }
  bouton.onclick = function () {
    if (!navigator.clipboard) { selectionner(); return; }
    navigator.clipboard.writeText(bouton.dataset.secret).then(
      function () { retour.textContent = "Secret copié."; },
      selectionner
    );
  };
})();
</script>
"""


def _bouton_signaler(deja: set[tuple[str, str]], releve: dict) -> str:
    """Le formulaire « celui-ci est faux », ou la mention qu'il l'est déjà.

    Le `client_id` et le `kind` voyagent dans des champs cachés : ce sont les
    deux colonnes de la clé primaire de `saisie`, et les renvoyer ensemble est ce
    qui évite que le formulaire vise le relevé jumeau. Ils sont échappés parce
    qu'ils viennent de la base, donc d'une donnée, même si aujourd'hui ils sont
    écrits par ce dépôt.

    Le champ motif est un `required` : un signalement sans motif donne à l'admin
    une ligne à traiter sans rien pour la traiter. La borne est posée côté
    serveur (`LONGUEUR_MOTIF`), le `maxlength` n'étant qu'un confort.

    La mention « déjà signalé » est du texte simple, pas un bouton désactivé :
    un `<button disabled>` ne se lit pas au lecteur d'écran de la même façon, et
    surtout il donne l'impression d'une action qui échouerait.
    """
    if (str(releve["client_id"]), str(releve["kind"])) in deja:
        return '<p class="mention">Deja signale.</p>'
    return (
        '<details><summary>Celui-ci est faux</summary>'
        "<form method='post' action='/compte/signaler'>"
        f"<input type='hidden' name='client_id' value='{escape(str(releve['client_id']))}'>"
        f"<input type='hidden' name='kind' value='{escape(str(releve['kind']))}'>"
        f"<p><input name='motif' maxlength='{compte_module.LONGUEUR_MOTIF}' "
        "required placeholder='Ce qui ne va pas'></p>"
        "<button class='ghost' type='submit'>Signaler</button>"
        "</form></details>"
    )


def _classement_page(lignes: list[dict], indisponible: bool = False) -> str:
    if indisponible:
        # L'échec est dit, et il est distinct de la liste vide. Une page qui
        # affiche « personne n'a encore de compte » quand la base est verrouillée
        # ment sur une absence de données — le mensonge que `tools/calibrer_score.py`
        # refuse de faire, et pour la même raison.
        corps = (
            "<h1>Classement</h1>"
            "<p>Le classement n'a pas pu être lu : la base est momentanément "
            "occupée. Les relevés ne sont pas perdus, la page est juste "
            "indisponible. Réessayez dans un instant.</p>"
            '<p><a class="bouton" href="/">Compter</a></p>'
        )
        return chrome("Classement", corps, actif="/classement")
    if not lignes:
        corps = (
            "<h1>Classement</h1>"
            "<p>Personne n'a encore de compte. Le classement se remplira avec "
            "les relevés rattachés à un compte — les relevés anonymes comptent "
            "dans les données et n'apparaissent pas ici.</p>"
            '<p><a class="bouton" href="/">Compter</a></p>'
        )
        return chrome("Classement", corps, actif="/classement")
    # Le dénominateur est annoncé, comme sur `/comptages` : une liste tronquée
    # qui ne dit pas ce qu'elle tronque se prend pour l'ensemble.
    #
    # C'est un **tableau**, pas une pile de cartes. Un classement se compare :
    # deux lignes mises l'une sous l'autre ne se comparent pas, il faut les
    # voir dans la même colonne et dans le même ordre. Les trois nombres qui
    # font le score — points, relevés, paires — occupent chacun une colonne,
    # donc les aligner verticalement, ce qu'aucune carte ne fait.
    #
    # Le rang est écrit explicitement même trié : une liste triée dont on
    # ne voit pas le tri se prend pour un ordre arbitraire, et l'ex-aequo
    # mérite d'être visible plutôt que résolu en silence. Deux comptes à
    # égalité partagent donc le même rang, comme sur un podium.
    meilleur = max((ligne["points"] for ligne in lignes), default=0)
    lignes_html = []
    cartes_html = []
    rang_courant = 0
    points_precedents: float | None = None
    for position, ligne in enumerate(lignes, start=1):
        points = ligne["points"]
        # Un nouveau rang seulement quand les points changent : c'est la
        # définition d'un ex-aequo, et écrire 1, 2, 3 pour trois comptes à
        # 12 points inventerait une hiérarchie que le score ne dit pas.
        if points != points_precedents:
            rang_courant = position
            points_precedents = points
        # La barre est proportionnelle au premier, donc sa longueur se lit
        # comme un score. Un score nul ne donne pas une division par zéro :
        # il donne une barre vide, ce qui est la même information.
        proportion = 100.0 if meilleur <= 0 else max(0.0, min(100.0, points / meilleur * 100))
        points_texte = f"{points:g}".replace(".", ",")
        dernier = _date_fr(ligne["dernier"]).split(" à ")[0] if ligne["dernier"] else "—"
        inedit = f"{ligne['inedit']:g}".replace(".", ",")
        lignes_html.append(
            "<tr>"
            f"<td class='nombre rang'>{rang_courant}</td>"
            f"<td class='pseudo'>{escape(ligne['pseudo'])}</td>"
            "<td class='points'>"
            f"<span class='valeur'>{_nombre(points_texte)}</span>"
            # Le dénominateur reste sous le score, dans la même cellule : un
            # score sans « sur N relevés » se lit « 5 voyageurs » quand il
            # veut dire « 5 points pour un relevé ». C'est la règle du §9
            # du plan, et elle vaut dans un tableau autant que dans une
            # carte — la colonne « Relevés » informe, elle ne rattache pas le
            # nombre à ce qu'il mesure.
            f"<span class='sur'>sur {ligne['releves']} relevé"
            f"{'' if ligne['releves'] == 1 else 's'}</span>"
            f"<span class='jauge' aria-hidden='true'>"
            f"<span class='remplissage' style='width:{proportion:.1f}%'></span></span>"
            "</td>"
            f"<td class='nombre'>{ligne['releves']}</td>"
            f"<td class='nombre'>{ligne['paires']}</td>"
            f"<td class='nombre'>{inedit}</td>"
            f"<td class='nombre'>{escape(dernier)}</td>"
            "</tr>"
        )
        # La même ligne, en carte : c'est le téléphone qui ne peut pas
        # aligner sept colonnes. La carte garde le rang, le nom, le score et
        # son dénominateur en tête — les trois nombres qu'on compare — et
        # passe le reste en une ligne de contexte.
        cartes_html.append(
            "<article>"
            "<p class='entete'>"
            f"<span class='rang'>{rang_courant}</span>"
            f"<span class='nom'>{escape(ligne['pseudo'])}</span>"
            f"<span class='score'>{_nombre(points_texte)}</span>"
            "</p>"
            f"<p class='sur'>point{'' if points == 1 else 's'} "
            f"sur {ligne['releves']} relevé{'' if ligne['releves'] == 1 else 's'}</p>"
            f"<span class='jauge' aria-hidden='true'>"
            f"<span class='remplissage' style='width:{proportion:.1f}%'></span></span>"
            f"<p class='detail'>{ligne['paires']} paire{'' if ligne['paires'] == 1 else 's'} "
            f"de gares couvertes, dont {inedit} sur des corridors inédits"
            f"{' · dernier relevé le ' + escape(dernier) if dernier != '—' else ''}.</p>"
            "</article>"
        )
    releves = sum(ligne["releves"] for ligne in lignes)
    corps = (
        "<h1>Classement</h1>"
        f"<p>{len(lignes)} compte{'' if len(lignes) == 1 else 's'} "
        f"avec des relevés rattachés, {releves} relevé{'' if releves == 1 else 's'} "
        "au total. Les comptes à égalité partagent le même rang.</p>"
        "<table class='tableau palmares'>"
        "<caption>Les comptes classés du meilleur au moins bon. La barre est "
        "proportionnelle au score du premier.</caption>"
        "<thead><tr>"
        "<th>Rang</th><th>Compte</th><th>Points</th>"
        "<th>Relevés</th><th>Paires de gares</th><th> dont inédit</th><th>Dernier relevé</th>"
        "</tr></thead>"
        f"<tbody>{''.join(lignes_html)}</tbody>"
        "</table>"
        # La lecture téléphone : les mêmes nombres, en cartes. Elle n'est pas
        # retirée du rendu quand le tableau s'affiche, ni l'inverse — les
        # deux existent dans le HTML et la feuille de style en choisit un,
        # comme le fait la liste des relevés. Les faire coexister ferait lire
        # le classement deux fois à qui a un écran large.
        f"<div class='palmares-cartes'>{''.join(cartes_html)}</div>"
        "<p class='mention'>Les paires de gares disent la couverture : deux "
        "comptes peuvent avoir le même nombre de relevés et couvrir des "
        "corridors différents.</p>"
        + _regle_du_classement()
    )
    return chrome(
        "Classement",
        corps,
        actif="/classement",
        extra_css="""
  /* Le classement : un tableau sur grand écran, une liste sur téléphone.

     Un tableau à sept colonnes **ne tient pas** en 390 px, et le projet le
     vérifie : `tests/test_browser_compte.py` mesure le débordement et
     échouait de 145 px. Le tableau ne peut donc pas rester affiché sous
     48 rem, contrairement à la vue par paire — il n'a pas de lecture
     dégradée à lui opposer.

     La lecture mobile est la **même donnée** en cartes, pas une autre
     liste : le score, son dénominateur, le rang et le nombre de relevés.
     Les colonnes de comparaison (paires, inédit, dernier relevé) se lisent
     dans la carte en une ligne chacune, donc rien n'est perdu — c'est
     seulement la comparaison verticale qui disparaît, et elle demande un
     écran large pour être utile de toute façon. */
  .palmares-cartes { display: grid; gap: 0.6rem; }
  .palmares-cartes article { background: #fff; border-radius: 0.8rem; padding: 0.7rem 0.8rem; }
  .palmares-cartes .entete { display: flex; align-items: baseline; gap: 0.6rem; }
  .palmares-cartes .rang { font-weight: 700; color: var(--gris); min-width: 1.6rem; }
  .palmares-cartes .nom { font-weight: 700; font-size: 1.1rem; flex: 1; }
  .palmares-cartes .score { font-variant-numeric: tabular-nums; font-weight: 700; }
  .palmares-cartes .sur { color: var(--gris); font-size: 0.85rem; }
  .palmares-cartes .detail { margin: 0.3rem 0 0; font-size: 0.85rem; color: var(--gris); }
  .palmares-cartes .jauge { display: block; height: 0.5rem; margin-top: 0.4rem;
                             background: #ece7dc; border-radius: 1rem; overflow: hidden; }
  .palmares-cartes .remplissage { display: block; height: 100%; background: var(--encre); }
  .tableau.palmares { display: none; }
  @media (min-width: 48rem) {
    .palmares-cartes { display: none; }
    .tableau.palmares { display: table; font-size: 0.9rem; }
    .tableau.palmares .rang { font-weight: 700; font-size: 1.05rem; }
    .tableau.palmares .pseudo { font-weight: 600; }
    .tableau.palmares .points { min-width: 9rem; }
    .tableau.palmares .valeur { font-variant-numeric: tabular-nums; font-weight: 700; }
    .tableau.palmares .sur { display: block; font-size: 0.78rem; color: var(--gris); }
  }
  /* La jauge est une barre, pas un graphique : elle dit l'écart au premier
     d'un coup d'œil, sans axe ni graduation — un axe sur des scores non
     calibrés promettrait une précision que le plan dit ne pas avoir. */
  .tableau.palmares .jauge { display: block; height: 0.5rem; margin-top: 0.25rem;
                             background: #ece7dc; border-radius: 1rem; overflow: hidden; }
  .tableau.palmares .remplissage { display: block; height: 100%; background: var(--encre); }
""",
    )


def _nombre(texte: str) -> str:
    """Le score formaté à la française, 1 234 points et non 1234.0."""
    entier, _, decimales = texte.partition(",")
    groupes = []
    while len(entier) > 3:
        groupes.insert(0, entier[-3:])
        entier = entier[:-3]
    groupes.insert(0, entier)
    return " ".join(groupes) + ("," + decimales if decimales else "")


def _regle_du_classement() -> str:
    """La règle, en clair, sur la page qui l'applique.

    Un classement dont on ne connaît pas la règle est une page de Vanity. Le plan
    demande les coefficients « en clair, avec la mesure qui les a choisis » — donc
    cette page dit ce que le score récompense, dans l'ordre du plan, et **dit que
    les poids ne sont pas calibrés**. Les annoncer comme définitifs serait la même
    faute que le statut qui mentait dans le plan : une affirmation que le code ne
    tient pas. La calibration se fait sur la base réelle, avec
    `tools/calibrer_score.py`.
    """
    c = score_module.PAR_DEFAUT
    return (
        '<section class="mention">'
        "<h2>Ce que le score récompense</h2>"
        "<p>Un relevé compte d'abord s'il se lit à l'échelle de la rame entière, "
        "c'est-à-dire mesuré sur une UM et non sur une voiture. Vient ensuite le "
        "serpent de charge, qui dit où la charge monte et où elle descend. Puis un "
        "couloir origine-destination qu'aucun relevé ne portait encore, et enfin un "
        "couloir qui n'avait pas été vu depuis "
        f"{c.fenetre_jours} jours. Compter le même trajet deux fois le même jour "
        "ne rapporte rien de plus, et la fiabilité déclarée ne rapporte rien du "
        "tout : elle est déclarée par celui qui compte.</p>"
        f"<p>Ces poids — {c.base:g} par relevé, {c.um:g} de plus à l'échelle de la "
        f"rame, {c.serpent:g} de plus pour un serpent, {c.corridor_inedit:g} pour un "
        f"couloir inédit, {c.corridor_vieux:g} pour un couloir vu il y a plus de "
        f"{c.fenetre_jours} jours — ne sont pas encore calibrés. Ils ont été choisis "
        "pour être lisibles, et la distribution obtenue sur la base réelle décidera "
        "des vrais nombres.</p>"
        "</section>"
    )


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
                now=datetime.now(UTC),
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


def _admin_list(
    rows: list[dict],
    publication: dict | None = None,
    signalements: list[dict] | None = None,
) -> str:
    cards = []
    for row in rows:
        who = escape(row["pseudo"]) if row["pseudo"] else "anonyme"
        origin = escape(row["origin_name"] or row.get("origin_stop_id") or "")
        destination = escape(row["destination_name"] or "")
        passengers = "" if row["passengers"] is None else row["passengers"]
        client_id = escape(row["client_id"])
        # Le `kind` part avec le `client_id` : c'est la clé primaire de `saisie`,
        # et sans lui la route refuse la suppression. Le poser ici plutôt que dans
        # la route garde le formulaire et la requête d'accord sur la même cible.
        genre = escape(str(row.get("kind") or ""))
        # Le commentaire explique une charge atypique (car de substitution,
        # train supprimé). C'est l'admin qui le lit : sans lui, l'information
        # est stockée et jamais consultée.
        note = f"<p>{escape(row['comment'])}</p>" if row.get("comment") else ""
        cards.append(
            "<article class='card'>"
            f"<strong>{origin} → {destination}</strong>"
            f"<p>{passengers} voyageurs · {who}</p>{note}"
            "<form method='get' action='/admin/modifier'>"
            f"<input type='hidden' name='client_id' value='{client_id}'>"
            f"<input type='hidden' name='kind' value='{genre}'>"
            "<button type='submit'>Modifier</button></form>"
            "<form method='post' action='/admin/supprimer'>"
            f"<input type='hidden' name='client_id' value='{client_id}'>"
            f"<input type='hidden' name='kind' value='{genre}'>"
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
  .signalement {{ background: #fff; border-left: 4px solid #8a2b1b; border-radius: 0.8rem; padding: 0.8rem; margin: 0.6rem 0; }}
</style>
</head><body><main>
  <h1>Admin</h1>
  <p>Supprimer retire le comptage de la liste et du CSV.</p>
  {_panneau_signalements(signalements or [])}
  {body}
  {"" if publication is None else _publication_panel(publication)}
</main></body></html>
"""


def _panneau_signalements(lignes: list[dict]) -> str:
    """Les signalements, lus par l'admin. Un signalement n'efface rien.

    Le panneau est **avant** la liste des comptages, pas après : c'est une file de
    travail, et une file qui se lit après la donnée à traiter se fait passer pour
    une annexe.

    Chaque ligne montre le relevé signalé avec son trajet et son motif, et le
    bouton de suppression déjà présent dans la liste en dessous. Le geste reste
    donc à l'admin : quelqu'un qui signale son propre relevé ne peut pas le
    retirer, il peut seulement le mettre en avant.

    Un signalement dont le relevé n'existe plus s'affiche quand même, marqué
    « relevé supprimé ». C'est le cas le plus urgent des deux — quelqu'un a
    signalé, et l'objet du signalement a disparu avant d'être traité.
    """
    if not lignes:
        return "<h2>Signalements</h2><p>Aucun signalement.</p>"
    cartes = []
    for ligne in lignes:
        origine = escape(str(ligne["origin_name"] or ""))
        arrivee = escape(str(ligne["destination_name"] or ""))
        trajet = (
            f"{origine} → {arrivee}"
            if origine or arrivee
            else "relevé supprimé"
        )
        effectif = "—" if ligne["passengers"] is None else str(ligne["passengers"])
        auteur = escape(str(ligne["auteur"] or "compte sans pseudo"))
        cartes.append(
            "<div class='signalement'>"
            f"<strong>{trajet}</strong>"
            f"<p>{effectif} voyageurs · signalé par {auteur}</p>"
            f"<p>{escape(str(ligne['motif']))}</p>"
            "</div>"
        )
    return (
        "<h2>Signalements</h2>"
        f"<p>{len(cartes)} signalement{'' if len(cartes) == 1 else 's'}, "
        "les plus récents d'abord. Un signalement ne supprime rien : c'est "
        "l'admin qui décide.</p>"
        + "".join(cartes)
    )


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
    compte_id: str | None = None,
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
            "SELECT client_id, kind FROM saisie WHERE client_id = ? AND kind = ?",
            (client_id, kind),
        ).fetchone()
        # Le doublon ne vaut que s'il est du même genre. Un « train signalé manquant »
        # consomme le jeton du navigateur, et le comptage réel qui suit
        # arrive avec le même : le rejeter ici perdait le comptage sans rien
        # dire, pendant que l'écran affichait « c'est noté ».
        #
        # La requête filtre donc sur les **deux** colonnes. Sur le seul
        # `client_id`, `fetchone` rendait la ligne d'un autre genre — un
        # `missing` déjà écrit pour ce jeton — et le test `existing[1] == kind`
        # était faux, donc le doublon passait. Sans clé primaire en base pour
        # l'arrêter, la seconde ligne s'écrivait : deux « count » pour un même
        # navigateur, tous deux comptés au score.
        if existing and existing[1] == kind:
            return {"client_id": existing[0], "kind": existing[1], "stored": False}
        connection.execute(
            """
            INSERT INTO saisie (
                client_id, origin_stop_id, destination_stop_id, origin_name, destination_name, trip_id,
                passengers, reliability, pseudo, comment, standing, seats_free, imbalance,
                materiel, composition, perimetre, snapshot, legs, trajet, kind, created_at, compte_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                datetime.now(UTC).isoformat(),
                compte_id,
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

    Un « train signalé manquant » n'a pas de trajet : c'est un doute sur une ligne, pas une charge mesurée.
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


def _edition_values(body: dict, kind: str, frozen_legs: str | None = None) -> dict[str, object]:
    """Valider les seules colonnes qu'un relevé peut modifier."""
    if kind not in {"count", "serpent", "missing"}:
        raise HTTPException(status_code=422, detail="genre de relevé invalide")
    values: dict[str, object] = {
        "pseudo": (str(body.get("pseudo") or "")[:40] or None),
        "comment": (str(body.get("comment") or "")[:280] or None),
    }
    if kind == "missing":
        return values
    def entier(nom: str, min_: int, max_: int | None = None) -> int:
        raw = body.get(nom)
        try:
            parsed = int(str(raw))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"{nom} invalide") from None
        if str(parsed) != str(raw).strip() or parsed < min_ or (max_ is not None and parsed > max_):
            raise HTTPException(status_code=422, detail=f"{nom} invalide")
        return parsed
    if kind == "serpent":
        try:
            legs_body = body.get("legs")
            if legs_body is None and frozen_legs:
                legs_body = json.loads(frozen_legs)
                for index, leg in enumerate(legs_body):
                    names = ("onboard",) if index == 0 else ("boarded", "alighted", "standing", "seats_free", "imbalance")
                    for name in names:
                        raw = body.get(f"legs_{index}_{name}")
                        if raw is None:
                            continue
                        if raw == "" and name in {"alighted", "standing", "seats_free", "imbalance"}:
                            leg[name] = None
                        else:
                            leg[name] = int(str(raw))
            elif isinstance(legs_body, str):
                legs_body = json.loads(legs_body)
            if isinstance(legs_body, list):
                for leg in legs_body:
                    if isinstance(leg, dict) and any(
                        isinstance(leg.get(key), bool)
                        for key in ("onboard", "boarded", "alighted", "standing", "seats_free", "imbalance")
                    ):
                        raise HTTPException(status_code=422, detail="valeur entière invalide")
            legs_text = _clean_legs(legs_body)
        except (json.JSONDecodeError, TypeError, ValueError):
            raise HTTPException(status_code=422, detail="serpent invalide") from None
        cleaned_legs = json.loads(legs_text)
        if frozen_legs:
            original_legs = json.loads(frozen_legs)
            identity = lambda leg: (leg.get("stop_id"), leg.get("stop_name", ""))
            if [identity(leg) for leg in cleaned_legs] != [identity(leg) for leg in original_legs]:
                raise HTTPException(status_code=422, detail="les arrêts du trajet ne sont pas modifiables")
        values["legs"] = legs_text
        values["passengers"] = cleaned_legs[0]["onboard"]
        values["reliability"] = entier("reliability", 0, 100)
    else:
        values["passengers"] = entier("passengers", 0)
        values["reliability"] = entier("reliability", 0, 100)
    for nom in ("standing", "seats_free", "imbalance"):
        raw = body.get(nom)
        if raw in (None, ""):
            values[nom] = None
        else:
            try:
                parsed = int(str(raw))
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"{nom} invalide") from None
            if str(parsed) != str(raw).strip():
                raise HTTPException(status_code=422, detail=f"{nom} invalide")
            values[nom] = _indicator(parsed)
    materiel, composition, perimetre = _materiel(body)
    values.update(materiel=materiel, composition=composition, perimetre=perimetre)
    return values


_EDITION_CSS = """
  .edition { max-width: 40rem; }
  .edition label { display: block; margin: 0.9rem 0; }
  .edition input:not([type="hidden"]), .edition select, .edition textarea {
    display: block; width: 100%; min-height: 2.8rem; font: inherit;
    border: 1px solid var(--bord); border-radius: 0.5rem; padding: 0.5rem;
  }
  .edition textarea { min-height: 6rem; }
  .edition fieldset { min-width: 0; margin: 1rem 0; border: 1px solid var(--bord); }
  .edition button { font: inherit; padding: 0.7rem 1rem; cursor: pointer; }
"""


def _edition_page(row: dict, admin: bool = False) -> str:
    """Formulaire d'édition : seules les données de comptage sont éditables."""
    action = "/admin/modifier" if admin else "/compte/modifier"
    inputs = []
    champs = [("pseudo", "Pseudo"), ("comment", "Commentaire")]
    if row.get("kind") != "missing":
        if row.get("kind") == "count":
            champs.append(("passengers", "Effectif"))
        champs.extend((("reliability", "Fiabilité (%)"), ("standing", "Voyageurs debout (%)"),
                       ("seats_free", "Places libres (%)"), ("imbalance", "Déséquilibre (%)"),
                       ("materiel", "Matériel"), ("composition", "Composition"),
                       ("perimetre", "Périmètre")))
    for name, label in champs:
        value = "" if row.get(name) is None else str(row[name])
        if name == "comment":
            inputs.append(f'<label>{escape(label)}<textarea name="comment" maxlength="280">{escape(value)}</textarea></label>')
        elif name == "composition":
            inputs.append(_edition_select(name, label, value, ("US", "UM2", "UM3")))
        elif name == "perimetre":
            inputs.append(_edition_select(name, label, value, ("voiture", "um")))
        else:
            maxlen = ' maxlength="40"' if name in {"pseudo", "materiel"} else ""
            maximum = ' max="100"' if name in {"reliability", "standing", "seats_free", "imbalance"} else ""
            numeric = name in {"passengers", "reliability", "standing", "seats_free", "imbalance"}
            inputs.append(f'<label>{escape(label)}<input name="{name}" value="{escape(value)}"'
                          + (' type="number" min="0"' if numeric else ' type="text"') + maximum + maxlen
                          + (' required' if name in {"passengers", "reliability"} else '') + '></label>')
    if row.get("kind") == "serpent":
        legs = json.loads(row.get("legs") or "[]")
        inputs.append('<fieldset><legend>Comptage par arrêt</legend>')
        for index, leg in enumerate(legs):
            nom = escape(str(leg.get("stop_name") or leg.get("stop_id") or f"Arrêt {index + 1}"))
            inputs.append(f'<section><h2>{nom}</h2><p>{nom} · arrêt inchangé</p>')
            keys = (("onboard", "Voyageurs à bord", None),) if index == 0 else (
                ("boarded", "Montées", "voyageurs"), ("alighted", "Descentes", "voyageurs"),
                ("standing", "Voyageurs debout", "%"), ("seats_free", "Places libres", "%"),
                ("imbalance", "Déséquilibre", "%"),
            )
            for name, label, unit in keys:
                value = "" if leg.get(name) is None else str(leg[name])
                maxval = ' max="100"' if name in {"standing", "seats_free", "imbalance"} else ""
                inputs.append(f'<label>{label}' + (f' ({unit})' if unit else '')
                              + f'<input type="number" min="0"{maxval} name="legs_{index}_{name}" value="{escape(value)}"'
                              + (' required' if name in {"onboard", "boarded"} else '') + '></label>')
            inputs.append('</section>')
        inputs.append('</fieldset>')
    key = (f'<input type="hidden" name="client_id" value="{escape(str(row["client_id"]))}">'
           f'<input type="hidden" name="kind" value="{escape(str(row["kind"]))}">')
    origin = escape(str(row.get("origin_name") or row.get("origin_stop_id") or ""))
    destination = escape(str(row.get("destination_name") or row.get("destination_stop_id") or ""))
    return (f'<h1>Modifier le relevé : {origin} → {destination}</h1>'
            f'<p>Trajet, train, contexte figé et date de création ne sont pas modifiables.</p>'
            f'<form class="edition" method="post" action="{action}">{key}' + "".join(inputs)
            + '<button type="submit">Enregistrer</button></form>'
            + f'<a href="{("/admin" if admin else "/compte")}">Annuler</a>')


def _edition_select(name: str, label: str, value: str, options: tuple[str, ...]) -> str:
    choices = ''.join(f'<option value="{option}"{" selected" if value == option else ""}>{option}</option>'
                      for option in options)
    return f'<label>{escape(label)}<select name="{name}"><option value="">Non renseigné</option>{choices}</select></label>'


# Les colonnes de `saisie`, dans l'ordre où `_ligne_saisie` les rend. La
# requête est écrite une fois, ici : la dupliquer pour ajouter un filtre
# ferait diverger les deux listes au prochain changement de schéma, et le
# premier divergence visible serait un champ qui sort dans l'export et
# pas dans la page.
_COLONNES_SAISIE = """
            SELECT client_id, origin_stop_id, destination_stop_id, origin_name, destination_name,
                   trip_id, passengers, reliability, pseudo, comment, standing, seats_free, imbalance,
                   materiel, composition, perimetre,
                   snapshot, kind, created_at, legs, trajet
            FROM saisie
"""


def _saisie_par_cle(database: Path, client_id: str, kind: str) -> dict | None:
    """Un relevé par sa clé primaire, ou `None`.

    La clé est `(client_id, kind)` et rien d'autre : c'est elle qui
    distingue deux genres de relevés du même navigateur. Une lecture par
    `client_id` seul rendrait le premier genre trouvé, donc `/releve` sans
    `kind` afficherait un serpent sous l'identité d'un comptage unique — ou
    l'inverse, selon la ligne que SQLite rend en premier.

    Les deux paramètres viennent de l'URL, donc ils sont des données client
    au sens de `docs/regles.md` § 1 : ils sont liés en paramètres, jamais
    concaténés dans la chaîne SQL. La ligne du `SELECT` est reprise de
    `_COLONNES_SAISIE` pour que la page détail ne puisse pas afficher un
    champ de moins que la liste — c'est le même motif que `_ligne_saisie`.
    """
    if not client_id:
        return None
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            _COLONNES_SAISIE + " WHERE client_id = ? AND kind = ?",
            (client_id, kind),
        ).fetchone()
    return _ligne_saisie(row) if row is not None else None


def _lien_releve(row: dict) -> str:
    """L'URL d'un relevé, et le paramètre `kind` qui l'accompagne.

    Le `kind` voyage avec le `client_id` parce que les deux forment la clé
    : sans lui, un serpent et un comptage unique du même navigateur
    partageraient une URL, et le second affiché ne serait pas le bon.
    """
    return (
        f"/releve?client_id={quote(str(row.get('client_id') or ''), safe='')}"
        # `&amp;` et non `&` : un attribut HTML porte du `&` qui n'est pas
        # une entité, et le validateur le refuse. Les navigateurs le
        # tolèrent, donc le défaut ne se voit pas — jusqu'au jour où un
        # validateur ou un parseur strict entre dans la chaîne.
        f"&amp;kind={quote(str(row.get('kind') or 'count'), safe='')}"
    )


def _releve_page(database: Path, client_id: str, kind: str) -> str:
    """Un comptage, tout entier, sur une page qui se partage.

    La liste des comptages est un résumé : elle compare des relevés, donc
    elle garde ce qu'on compare et jette le reste. Le reste n'était
    accessible que par le CSV, ce qui est une façon de dire « pas
    accessible » pour qui compte dans un train : le téléphone n'a pas le
    fichier sous la main au moment où la question se pose.

    Ce que la page ajoute, et que la liste n'a pas : la composition de la
    rame et le périmètre compté (sans quoi 180 voyageurs dans une voiture
    d'une UM3 et 180 dans les trois sont le même chiffre), les trois
    indicateurs de charge, le commentaire, les arrêts du serpent avec leurs
    montées et descentes, et la photo du temps réel/train par train.
    """
    row = _saisie_par_cle(database, client_id, kind)
    if row is None:
        # Une page qui dit « pas trouvé » plutôt qu'une 404 : l'URL est
        # partageable, donc elle peut arriver après une suppression, et un
        # lien mort doit dire où il mène.
        return _plain_reading_page(
            "Ce comptage n'existe pas",
            "Ce lien ne correspond à aucun comptage enregistré. "
            "Il a pu être supprimé depuis qu'il a été copié, "
            "ou le lien a été tronqué.",
        )
    titre = "Train signalé" if row["kind"] == "missing" else "Comptage"
    # Chaque fait est dans une ligne `dt`/`dd` : une liste de couples
    # nom/valeur se lit en descendant la colonne, ce qu'un `<p>` libre ne
    # garantit pas — deux paragraphes qui se suivent se lisent comme une
    # phrase.
    faits = [
        ("Date", _date_fr(row.get("created_at"))),
        ("Trajet", f"{row.get('origin_name') or '—'} → {row.get('destination_name') or '—'}"),
        ("Mode", _mode_texte(row.get("kind"))),
        (
            "Effectif",
            "sans effectif" if row.get("passengers") is None else f"{row['passengers']} voyageurs",
        ),
        ("Fiabilité", _fiabilite_texte(row) or "—"),
        ("Par", escape(row["pseudo"]) if row.get("pseudo") else "anonyme"),
    ]
    materiel = _materiel_texte(row)
    if materiel:
        faits.append(("Matériel", materiel))
    legs = _legs_text(row.get("legs"))
    if legs:
        faits.append(("Serpent", legs))
    photo = _photo_phrase(row)
    if photo:
        faits.append(("Temps réel au moment du comptage", escape(photo)))
    commentaire = (row.get("comment") or "").strip()
    if commentaire:
        faits.append(("Commentaire", escape(commentaire)))
    description = "".join(f"<dt>{escape(intitule)}</dt><dd>{valeur}</dd>" for intitule, valeur in faits)
    corps = (
        f"<article class='card releve'>"
        f"{_card_tete(row)}"
        f"<dl class='faits'>{description}</dl>"
        f"{_pastilles(row)}"
        f"{_commentaire(row)}"
        "<p class='status'><a href='/comptages'>Retour aux comptages</a></p>"
        "</article>"
    )
    return chrome(
        titre,
        corps,
        actif="/comptages",
        extra_css="""
  /* La page détail : une liste de couples nom/valeur, pas des paragraphes.
     Le `<dl>` a un rendu par défaut que l'on neutralise — les `dt` et les
     `dd` sont sur la même ligne en CSS, empilés en colonne sur téléphone. */
  dl.faits { margin: 0.6rem 0; display: grid; grid-template-columns: minmax(0, 1fr);
            gap: 0.1rem 0.8rem; }
  dl.faits dt { font-size: 0.82rem; color: var(--gris); text-transform: uppercase;
                letter-spacing: 0.04em; padding-top: 0.35rem; }
  dl.faits dd { margin: 0; padding: 0.35rem 0; border-bottom: 1px solid #e6e0d5; }
  dl.faits dd:last-of-type { border-bottom: 0; }
  @media (min-width: 48rem) {
    /* Deux colonnes nom/valeur dès qu'il y a de la place : une fiche se lit
       en descendant, et une colonne de 72 rem laisserait la moitié de
       l'écran vide. */
    dl.faits { grid-template-columns: minmax(0, 14rem) minmax(0, 1fr); }
  }
""",
    )


def _saisies_de_gare(database: Path, stops_database: Path, stop_id: str) -> list[dict]:
    """Les relevés dont la gare est une extrémité, ou un arrêt du serpent.

    Le rattachement se fait par la **famille** de la gare, pas par
    l'identifiant exact : le même quai peut s'appeler `StopArea:Annecy` dans
    une offre et `StopPoint:AnnecyA` dans une autre, et un comptage fait
    depuis l'un n'apparaîtrait pas si l'on ne cherchait que l'autre.
    `_stop_family` est la fonction qui sait quels identifiants appartiennent
    à une gare ; c'est elle que le serpent et le temps réel utilisent déjà,
    donc l'utiliser ici évite une troisième définition du même mot.

    Les arrêts du serpent comptent aussi : un relevé dont l'origine est
    ailleurs mais qui passe par cette gare est un relevé **de** cette gare,
    et l'exclure donnerait une page qui ment sur la couverture.

    Le serpent se lit en Python, pas avec un `LIKE` sur la colonne `legs` :
    cette colonne est du JSON, donc une sous-chaîne peut apparaître dans un
    nom de gare ou dans une valeur, et le comptage afficherait un relevé
    passé par la gare parce que le nom d'une autre gare contient son
    identifiant. Une lecture de plus, et le filtre est exact.
    """
    from comptagefer.offer import _stop_family

    famille = set(_stop_family(stops_database if stops_database.exists() else None, stop_id))
    marques = ",".join("?" for _ in famille)
    with sqlite3.connect(database) as connection:
        extremites = connection.execute(
            _COLONNES_SAISIE
            + f"""
            WHERE origin_stop_id IN ({marques})
               OR destination_stop_id IN ({marques})
            ORDER BY created_at
            """,
            (*famille, *famille),
        ).fetchall()
        serpents = connection.execute(
            _COLONNES_SAISIE + " WHERE legs IS NOT NULL ORDER BY created_at"
        ).fetchall()
    retenus = [_ligne_saisie(row) for row in extremites]
    vus = {(row["client_id"], row["kind"]) for row in retenus}
    for row in serpents:
        releve = _ligne_saisie(row)
        if (releve["client_id"], releve["kind"]) in vus:
            continue
        if _passe_par(releve.get("legs"), famille):
            retenus.append(releve)
    return sorted(retenus, key=lambda r: r.get("created_at") or "")


def _passe_par(legs: object, famille: set[str]) -> bool:
    """Un serpent passe-t-il par l'un de ces arrêts ?

    Chaque arrêt est comparé **exactement** à un identifiant de la famille.
    Une comparaison partielle (`in` sur la chaîne du JSON) confondrait une
    gare avec une autre dont le nom la contient.
    """
    if not isinstance(legs, list):
        return False
    return any(
        isinstance(leg, dict) and leg.get("stop_id") in famille for leg in legs
    )


def _gare_page(database: Path, stops_database: Path, stop_id: str) -> str:
    """La gare, ses comptages, ou une invitation à en compter un.

    Le nom vient du catalogue des gares, jamais de l'URL : un nom passé en
    paramètre serait une donnée client affichée sans être échappée, et il
    pourrait nommer une gare qui n'existe pas. Si le catalogue ne connaît
    pas cet identifiant, la page le dit et propose la recherche — elle ne
    devine pas un nom.
    """
    if not stop_id:
        return _plain_reading_page("Quelle gare ?", _link("/comptages", "Voir les comptages"))
    nom = _nom_de_gare(stops_database, stop_id)
    if nom is None:
        return _plain_reading_page(
            "Cette gare n'est pas dans le catalogue",
            "Les gares viennent du GTFS national. "
            "Un identifiant qui n'y est pas n'ouvre aucune page de comptages.",
        )
    rows = _saisies_de_gare(database, stops_database, stop_id)
    if rows:
        # Le lien `/comptages` garde le corridor entier dans l'URL, donc la
        # page se partage et se marque. `/gare` n'a pas besoin d'être
        # dans la navigation : c'est la sortie d'une recherche, pas une
        # destination qu'on visite.
        contenu = (
            "<p class='status'>"
            f"{len(rows)} comptage{'' if len(rows) == 1 else 's'} "
            f"touchent {escape(nom)}.</p>"
            f"<div class='grille'>{_reading_cards(rows)}</div>"
        )
    else:
        contenu = (
            "<div class='card'>"
            f"<p><strong>Aucun comptage sur {escape(nom)} pour l'instant.</strong></p>"
            "<p>Une gare sans comptage n'est pas une gare vide : c'est une gare "
            "qui attend son premier train compté. Le comptage se fait dans le "
            "train, sur un trajet — la gare n'est qu'une des deux extrémités.</p>"
            "<p><a class='bouton' href='/'>Compter un train</a></p>"
            "</div>"
        )
    return chrome(
        nom,
        contenu,
    )


def _nom_de_gare(stops_database: Path, stop_id: str) -> str | None:
    """Le nom d'une gare dans le catalogue, ou `None`.

    La gare peut être donnée par l'aire ou par un de ses points : les deux
    portent le même nom, donc la lecture demande le `parent` d'abord et
    remonte au point quand il n'y en a pas. C'est ce qui permet à un lien
    construit depuis un comptage — donc depuis un `StopPoint` — d'ouvrir la
    même page que le lien construit depuis la recherche.
    """
    if not stops_database.exists():
        return None
    with open_stops(stops_database) as connection:
        row = connection.execute(
            "SELECT name, parent FROM stop WHERE stop_id = ?", (stop_id,)
        ).fetchone()
        if row is None:
            return None
        nom, parent = row
        if parent:
            aire = connection.execute(
                "SELECT name FROM stop WHERE stop_id = ?", (parent,)
            ).fetchone()
            if aire is not None:
                return str(aire[0])
    return str(nom)


def _lien_gare(stop_id: str) -> str:
    """L'URL d'une gare, son `stop_id` encodé."""
    return f"/gare?stop={quote(str(stop_id), safe='')}"


def _ligne_saisie(row) -> dict:
    """Un relevé, sous la forme d'un dictionnaire.

    Le commentaire est demandé par /methode (« les commentaires sont
    précieux ») : sans le relire nulle part, il resterait inexploitable,
    et la promesse de la page serait vide. Le trajet figé au moment du
    comptage, lui, n'est pas relu au moment de la lecture : c'est tout
    l'intérêt. Voir `_freeze_trajet`.
    """
    return {
        "client_id": row[0],
        "origin_stop_id": row[1],
        "destination_stop_id": row[2],
        "origin_name": row[3],
        "destination_name": row[4],
        "trip_id": row[5],
        "passengers": row[6],
        "reliability": row[7],
        "pseudo": row[8],
        "comment": row[9],
        "standing": row[10],
        "seats_free": row[11],
        "imbalance": row[12],
        "materiel": row[13],
        "composition": row[14],
        "perimetre": row[15],
        "snapshot": json.loads(row[16]) if row[16] else None,
        "kind": row[17],
        "created_at": row[18],
        "legs": json.loads(row[19]) if row[19] else None,
        "trajet": json.loads(row[20]) if row[20] else None,
    }


def _list_saisies(database: Path) -> list[dict]:
    """Tous les relevés, tous modes confondus, dans l'ordre de la base.

    Cette fonction sert aussi au CSV et à la publication : aucun
    paramètre d'affichage n'entre ici, donc l'export ne peut pas
    changer de contenu parce qu'un lecteur a trié une page web.
    """
    with sqlite3.connect(database) as connection:
        rows = connection.execute(_COLONNES_SAISIE + " ORDER BY created_at").fetchall()
    return [_ligne_saisie(row) for row in rows]


def _saisies_filtrees(
    database: Path, filtres: "Filtres", trips: frozenset[str] = frozenset()
) -> tuple[list[dict], int]:
    """Les relevés qui passent le filtre, et combien ils sont au total.

    Le total est la réponse à « 12 sur 5 000 », donc il est compté en SQL
    et pas déduit de la longueur de la liste rendue : sans lui, une page
    de résultats se prend pour l'ensemble des données, ce qui est
    exactement la faute que la vague 2 corrige.

    Le tri et la pagination restent en Python, sur cette tranche. Un
    `ORDER BY` en SQL serait plus rapide, mais il faudrait dupliquer la
    table des clés entre la requête et `_trier`, et les deux finitont par
    divergir — ce que le tri a déjà payé une fois dans cette PR.
    """
    where, params = conditions(filtres, trips)
    with sqlite3.connect(database) as connection:
        total = connection.execute(
            f"SELECT COUNT(*) FROM saisie WHERE {where}" if where else "SELECT COUNT(*) FROM saisie",
            params,
        ).fetchone()[0]
        rows = connection.execute(
            _COLONNES_SAISIE + (f" WHERE {where}" if where else "") + " ORDER BY created_at",
            params,
        ).fetchall()
    return [_ligne_saisie(row) for row in rows], total


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


def _page_depuis(valeur: str) -> int:
    """Le numéro de page d'une URL, ou 1.

    Une URL reçoit des fautes de frappe, et le tri de la page a déjà posé
    la règle : une clé inconnue retombe sur le défaut au lieu de lever.
    `?page=beaucoup` est donc la page 1, pas une 422 — une page d'erreur
    pour un nombre mal tapé est une page perdue.
    """
    try:
        return max(1, int(valeur))
    except (TypeError, ValueError):
        return 1


def _trips_de_ligne(timetable: Path, route_id: str) -> frozenset[str]:
    """Les circulations d'une ligne, pour le filtre `?ligne=`.

    Cette lecture est ici pour que le filtre n'ait pas à connaître la base
    timetable : `filtres.py` ne lit aucun fichier.
    """
    if not route_id or not _lignes_disponibles(timetable):
        return frozenset()
    with sqlite3.connect(timetable) as connection:
        return frozenset(
            row[0]
            for row in connection.execute(
                "SELECT trip_id FROM trip_ligne WHERE route_id = ?", (route_id,)
            )
        )


def _link(href: str, text: str) -> str:
    return f'<a href="{href}">{escape(text)}</a>'


def _plain_reading_page(titre: str, corps: str) -> str:
    """Une page de lecture sans liste, ni paramètre oublié.

    Elle garde les mentions et la navigation, sinon on pourrait atterrir sur une
    page qui ne dit ni ce que sont ces chiffres, ni comment revenir.
    """
    return chrome(titre, f"<div class='card'><p>{corps}</p></div>")


def _lignes_disponibles(timetable: Path) -> bool:
    from comptagefer.timetable import _has_lines

    return timetable.exists() and _has_lines(timetable)


def _materiel_texte(row: dict) -> str:
    """Le matériel, sa composition et le périmètre, en une phrase lisible.

    « UM3, compté sur une voiture » est plus clair que trois cases vides pour
    celui qui relit un comptage. Rien ne s'affiche quand les
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


def _date_fr(created_at: object) -> str:
    """`created_at` en date française, lisible sans ambiguïté.

    La colonne est un ISO UTC, donc lisible mais pas naturel : «
    2026-09-30T13:04:11+00:00 » ne se lit pas d'un coup d'œil, et « 30/09 »
    seul non plus puisque deux comptages le même jour n'ont pas la même
    heure. On convertit en Europe/Paris, parce qu'un comptage se fait à
    l'heure locale, et on garde le jour et l'heure.

    Une date illisible ne s'affiche pas : une saisie sans date n'est pas
    une saisie qui s'est passée hier, elle est une saisie dont on ignore
    quand elle a eu lieu, et l'écrire le dit.
    """
    if not isinstance(created_at, str) or not created_at:
        return "date inconnue"
    try:
        moment = datetime.fromisoformat(created_at)
    except ValueError:
        return "date inconnue"
    if moment.tzinfo is not None:
        moment = moment.astimezone(ZoneInfo("Europe/Paris"))
    return moment.strftime("%d/%m/%Y à %H:%M")


def _indicateur_texte(valeur: object, unite: str) -> str:
    """Une pastille d'indicateur, ou rien du tout.

    Un relevé sans indicateur ne laisse pas de pastille vide : « debout —
    » se lit comme une information perdue, alors que c'est un choix de
    celui qui a compté.

    L'unité n'est pas échappée : ce sont des constantes du module, écrites
    ici, jamais rien qui vienne d'un navigateur. Les échapper produirait
    « d&#x27;écart » dans la page et dans le CSV, pour rien.
    """
    if valeur is None:
        return ""
    return f"<span class='pastille'>{escape(str(valeur))} {unite}</span>"


def _passagers_texte(row: dict) -> str:
    """L'effectif, ou la mention de son absence.

    Un « train signalé » n'a pas d'effectif parce qu'il n'a pas été compté :
    écrire « sans effectif » le dit, un tiret pourrait se lire comme un
    zéro.
    """
    if row.get("passengers") is None:
        return "sans effectif"
    return escape(str(row["passengers"]))


def _pastilles(row: dict) -> str:
    """Les indicateurs de charge, en pastilles.

    Debout, places libres et écart de charge sont déjà dans le CSV et déjà
    dans la base. Ils n'étaient sur aucune page de lecture : la donnée
    sortait sans qu'un lecteur du site puisse la voir, ce qui la rendait
    inexploitable pour qui n'a pas téléchargé le fichier.
    """
    morceaux = [
        _indicateur_texte(row.get("standing"), "debout"),
        _indicateur_texte(row.get("seats_free"), "% de places libres"),
        _indicateur_texte(row.get("imbalance"), "% d'écart de charge"),
    ]
    retenus = [m for m in morceaux if m]
    return f"<p class='pastilles'>{''.join(retenus)}</p>" if retenus else ""


def _commentaire(row: dict) -> str:
    """Le commentaire de celui qui a compté.

    Il est publié dans le CSV et /methode le dit, mais la page de lecture ne
    l'affichait pas : le champ existait pour le lecteur du fichier et pas
    pour celui du site. Or c'est le commentaire qui explique une charge
    atypique — un car de substitution, un train supprimé — et une charge
    sans explication se lit comme une erreur de comptage.
    """
    texte = (row.get("comment") or "").strip()
    if not texte:
        return ""
    return f"<p class='commentaire'>{escape(texte)}</p>"


def _fiabilite_texte(row: dict) -> str:
    """La fiabilité du compte, lisible en un mot.

    La saisie demande un pourcentage entre 0 et 100. « 70 % » ne dit rien
    à un lecteur qui ne connaît pas l'échelle ; l'adjectif le dit. Les deux
    sont affichés, l'adjectif d'abord : c'est lui qu'on compare d'un relevé
    à l'autre.
    """
    valeur = row.get("reliability")
    if not isinstance(valeur, int):
        return ""
    if valeur >= 80:
        mot = "fiable"
    elif valeur >= 50:
        mot = "moyen"
    else:
        mot = "incertain"
    return f"{mot} ({valeur} %)"


def _photo_phrase(row: dict) -> str:
    """La photo du temps réel, en une ligne lisible.

    Les cinq voisins sont dans la base et dans le CSV. Écrits en prose au
    milieu d'une carte, ils prenaient la moitié de la place pour dire
    « TER à l'heure · TER à l'heure · TER en retard · … » : cinq fois la
    même information, dans le désordre de la saisie. Ils passent en
    dessous, en un paragraphe, ce qui est la place d'une information de
    contexte et pas d'un titre.
    """
    snapshot = row.get("snapshot")
    morceaux = [
        _photo_label(snapshot, "precedent"),
        _photo_label(snapshot, "precedent_meme_type"),
        _photo_label(snapshot, "courant"),
        _photo_label(snapshot, "suivant"),
        _photo_label(snapshot, "suivant_meme_type"),
    ]
    retenus = [m for m in morceaux if m]
    if not retenus:
        return "aucune photo du temps réel"
    return " · ".join(retenus)


def _mode_texte(kind: object) -> str:
    """Ce que le relevé est, en un mot.

    Trois natures, pas deux : `serpent` et `count` sont des comptages,
    `missing` est un train signalé — un relevé de l'offre qui manque, pas
    une charge mesurée. Dire « unique » d'un train signalé le faisait
    passer pour un comptage, et c'est le défaut que /methode interdit par
    tout ailleurs.
    """
    return {"serpent": "serpent", "count": "unique"}.get(str(kind or ""), "signalé")


def _card_tete(row: dict) -> str:
    """Le bloc commun à la carte et à la ligne du tableau : OD, effectif,
    auteur, mode. C'est l'identité d'un comptage, donc elle est écrite une
    fois — une carte et une ligne qui divergent sur le nombre affiché
    seraient deux chiffres pour un relevé.

    Le trajet est un lien vers `/releve`. C'est le seul élément cliquable de
    la carte : une carte entièrement cliquable avale les sélections de texte
    et se déclenche au premier glissement de doigt sur un téléphone, ce qui
    est le geste qu'on fait en lisant. Un lien sur le titre, lui, se voit et
    se vise.
    """
    who = escape(row["pseudo"]) if row["pseudo"] else "anonyme"
    destination = escape(row["destination_name"] or "")
    titre = (
        f"<a href='{_lien_releve(row)}'>"
        f"{escape(row['origin_name'] or '')} → {destination}</a>"
    )
    # Un train signalé n'a pas d'effectif : écrire « sans effectif voyageurs »
    # serait un faux pluriel et une fausse unité.
    if row.get("passengers") is None:
        return f"<strong>{titre}</strong><p>{_mode_texte(row.get('kind'))} · {who}</p>"
    return (
        f"<strong>{titre}</strong>"
        f"<p>{_passagers_texte(row)} voyageurs · {who} · {_mode_texte(row.get('kind'))}</p>"
    )


def _reading_cards(rows: list[dict]) -> str:
    cards = []
    for row in rows:
        materiel = _materiel_texte(row)
        ligne_materiel = f"<p class='status'>{materiel}</p>" if materiel else ""
        # Le lien de détail est aussi en bas de carte, et pas seulement dans
        # le titre : sur téléphone le titre se lit vite et se tape par
        # habitude, alors qu'un lien nommé ne demande pas de deviner qu'il
        # est cliquable.
        cards.append(
            "<article class='card'>"
            + _card_tete(row)
            + f"<p class='status'>{_date_fr(row.get('created_at'))}</p>"
            + ligne_materiel
            + _pastilles(row)
            + _legs_text(row.get("legs"))
            + _commentaire(row)
            + f"<p class='status'>{_photo_phrase(row)}</p>"
            + f"<p class='status'><a href='{_lien_releve(row)}'>Détail du comptage</a></p>"
            + "</article>"
        )
    return "".join(cards) or "<p>Aucun comptage pour l'instant.</p>"


def _trier(rows: list[dict], tri: str, sens: str) -> list[dict]:
    """L'ordre de la liste, à partir de l'URL.

    Le tri se fait en Python et non dans la requête : `_list_saisies` rend
    aussi le CSV publié, et l'ordre du fichier exporté ne doit pas dépendre
    d'un paramètre d'affichage d'une page web.

    Une clé inconnue retombe sur la date au lieu de lever : une URL reçoit
    des fautes de frappe, et une page d'erreur pour un `tri=voyageurs` mal
    orthographié est une page perdue.
    """
    ranges = {
        "date": lambda r: r.get("created_at") or "",
        "passengers": lambda r: r.get("passengers") or 0,
        "reliability": lambda r: r.get("reliability") or 0,
        "pseudo": lambda r: (r.get("pseudo") or "").lower(),
        "trajet": lambda r: f"{r.get('origin_name') or ''} {r.get('destination_name') or ''}".lower(),
        "materiel": lambda r: _materiel_texte(r).lower(),
    }
    cle = tri if tri in ranges else "date"
    # Sans paramètre de tri, l'ordre est le plus récent d'abord. Un tri
    # demandé sans sens part en croissant : un nom commence par A.
    decroissant = sens == "desc" or (not sens and not tri)
    tries = sorted(rows, key=ranges[cle], reverse=decroissant)
    if cle == "date":
        # La date est toujours renseignée : rien à remettre en fin de liste.
        return tries
    # Les relevés sans valeur pour la clé triée vont en dernier dans les DEUX
    # sens. Un `reverse=True` qui porterait sur un « la valeur manque »
    # booléen les remonterait en tête du tri décroissant, et un train signalé
    # sans effectif se lirait comme le relevé le plus chargé — ce qu'il n'est
    # pas. Ils sont donc retirés, triés, puis remis à la fin.
    sans = [r for r in tries if r.get(cle) is None]
    avec = [r for r in tries if r.get(cle) is not None]
    return avec + sans


def _reading_table(rows: list[dict], tri: str, sens: str, filtres: Filtres) -> str:
    """La liste en tableau, pour un écran large.

    Le tableau est la même donnée que les cartes, pas une autre : il rend
    la même liste, et il n'apparaît qu'au-dessus de 48 rem. Sur un
    téléphone la feuille de style le retire du rendu.

    Chaque en-tête de colonne est un lien : le tri est une URL, donc il se
    partage et il se teste, et il n'y a pas de JavaScript dans une page
    dont le JavaScript n'est jamais exécuté par la suite de tests.

    Les liens de tri traversent `filtres.lien`, donc ils gardent les
    filtres actifs. Un lien qui les perdrait afficherait la liste
    entière : le lecteur verrait des relevés qu'il vient d'exclure et
    croirait que son filtre n'a rien donné.
    """
    if not rows:
        return ""
    colonnes = (
        ("date", "Date"),
        ("trajet", "Trajet"),
        ("passengers", "Voyageurs"),
        ("materiel", "Matériel"),
        ("reliability", "Fiabilité"),
        ("pseudo", "Par"),
    )
    entetes = []
    for cle, titre in colonnes:
        if cle == tri and sens:
            # Recliquer sur la colonne active inverse le sens. Le lien doit
            # viser l'état OPPOSÉ à celui qu'on regarde : un lien vers
            # l'état courant ne se distingue pas d'un lien mort, et
            # l'utilisateur qui reclique pour « l'inverser » ne voit rien
            # se passer. Le tri repart aussi de la première page : on
            # inverse un tri, on ne reste pas au bout de la liste.
            cible_sens = "asc" if sens == "desc" else "desc"
            marque = " ▾" if sens == "desc" else " ▴"
            lien = f"<a href='{lien_comptages(filtres, cle, cible_sens)}'>{titre}{marque}</a>"
        else:
            # Sans tri actif, un nom commence par A et un effectif du plus
            # petit au plus grand : c'est le sens qui sert à comparer.
            lien = f"<a href='{lien_comptages(filtres, cle, 'asc')}'>{titre}</a>"
        entetes.append(f"<th>{lien}</th>")

    corps = []
    vide = '<span class="vide">non précisé</span>'
    for row in rows:
        materiel = _materiel_texte(row) or vide
        fiabilite = _fiabilite_texte(row) or vide
        pseudo = escape(row["pseudo"]) if row["pseudo"] else vide
        moment = escape(_date_fr(row.get("created_at")))
        # Le trajet est un lien vers le relevé. Sur grand écran c'est la
        # seule colonne qui décrit le relevé, donc c'est elle qui doit
        # porter la sortie vers le détail — un lien nommé en fin de ligne
        # ferait une colonne de plus, et du bruit visuel dans chaque ligne.
        trajet = (
            f"<a href='{_lien_releve(row)}'>"
            f"{escape(row['origin_name'] or '')} → {escape(row['destination_name'] or '')}</a>"
        )
        cellules = [
            f"<td class='nombre'>{moment}</td>",
            f"<td>{trajet}</td>",
            # Le train signalé n'a pas d'effectif : la colonne dit son
            # mode, pas un nombre à côté d'un vide.
            f"<td class='nombre'>{_mode_texte(row.get('kind')) if row.get('passengers') is None else _passagers_texte(row)}</td>",
            f"<td>{materiel}</td>",
            f"<td>{fiabilite}</td>",
            f"<td>{pseudo}</td>",
        ]
        corps.append("<tr>" + "".join(cellules) + "</tr>")

    return (
        "<table class='tableau'>"
        "<caption>Les mêmes comptages, en tableau. Un en-tête de colonne trie la "
        "liste, un trajet ouvre le relevé en entier.</caption>"
        f"<thead><tr>{''.join(entetes)}</tr></thead>"
        f"<tbody>{''.join(corps)}</tbody>"
        "</table>"
    )


def _resume(affiches: list[dict], tranche: list[dict], total: int) -> str:
    """Le décompte en tête de liste.

    Une liste de comptages sans dire combien elle en contient oblige le
    lecteur à la faire défiler pour le savoir. Le décompte indique aussi la
    période, ce qui répond à la question que personne ne formule mais que
    tout le monde se pose en tombant sur une page de relevés : « jusqu'à
    quand ? ».

    `affiches` est la liste complète qui passe le filtre, `tranche` ce
    que cette page en montre, et `total` le nombre compté en SQL. Les
    trois sont distincts dès qu'il y a plus d'une page, et le dire est
    le but : sans l'écart, 200 relevés affichés se prennent pour les 200
    seuls existants.

    Rien n'est annoncé quand la liste est vide : « 0 comptage, du 01/01 au
    01/01 » serait une période inventée.
    """
    if not affiches:
        return ""
    dates = sorted(
        r["created_at"]
        for r in affiches
        if isinstance(r.get("created_at"), str) and r["created_at"]
    )
    if not dates:
        return f"<p class='status'>{len(affiches)} comptage{_pluriel(len(affiches))}, sans date.</p>"
    debut = _date_fr(dates[0]).split(" à ")[0]
    fin = _date_fr(dates[-1]).split(" à ")[0]
    compte = f"{len(affiches)} comptage{_pluriel(len(affiches))}"
    if total > len(tranche):
        # « 200 sur 5 000 » : c'est le total qui répond à la question, le
        # nombre affiché ne répond à rien. La comparaison se fait avec
        # `tranche` et non avec `affiches` — la liste filtrée complète,
        # dont la taille EST le total, donc la page 2 afficherait « 5
        # sur 205 » et se prendrait pour le jeu entier.
        compte = f"{len(tranche)} sur {total}"
    return f"<p class='status'>{compte} du {debut} au {fin}.</p>"


def _resume_paires(paires: list, tranche: list) -> str:
    """Le décompte de la vue par paire, avec la même tranche que la liste.

    « 200 paires sur 5 000 » et non « 200 paires » : sans l'écart, la
    tranche se prend pour l'ensemble des corridors, qui est exactement la
    faute que la pagination de la liste corrige. Même règle, même forme
    que `_resume` — les deux décomptes sont le même décompte.
    """
    if not paires:
        return ""
    if len(paires) > len(tranche):
        return (
            f"<p class='status'>{len(tranche)} sur {len(paires)} paires de gares.</p>"
        )
    return (
        f"<p class='status'>{len(paires)} paire{_pluriel(len(paires))} de gares.</p>"
    )


def _pluriel(nombre: int) -> str:
    return "s" if nombre > 1 else ""


def _filtres_html(filtres: Filtres, vue: str) -> str:
    """Le formulaire de filtre, et l'état de ce qui est appliqué.

    Un formulaire GET, sans JavaScript : les filtres sont dans l'URL,
    donc c'est un formulaire natif qui les produit, et la même URL peut
    être partagée, signetée et testée.

    Le champ « ligne » est un texte libre, parce que le GTFS national
    attribue le même « C13 » à plusieurs lignes. Il prend le `route_id`
    quand il est connu et annonce le résultat en cas d'erreur.
    """
    mode_options = "".join(
        f"<option value='{escape(nom)}'"
        + (" selected" if filtres.mode == nom else "")
        + f">{titre}</option>"
        for nom, titre in (("", "tous"), ("unique", "comptage unique"), ("serpent", "serpent"), ("signale", "train signalé manquant"))
    )
    # Les filtres écartés et les bornes inversées sont affichés ici, et pas
    # seulement dans l'écran « liste vide » : un filtre illisible sur une
    # liste non vide est le cas le plus fréquent — on tape une date de
    # travers et la liste s'affiche entière — et c'est justement celui où
    # le silence fait croire que le filtre a été appliqué.
    alertes = ""
    if filtres.erreurs:
        items = "".join(f"<li>{e}</li>" for e in filtres.erreurs)
        alertes = f"<ul class='erreurs'>{items}</ul>"
    # Les chips disent ce qui EST appliqué, pour qu'un filtre écarté ne
    # laisse pas croire qu'il filtre encore. Ils sont dans une boîte à part
    # qui prend toute la largeur : en enfants directs de la grille, chaque
    # chip prenait une colonne, et la rangée des filtres se trouvait
    # repoussée vers la droite selon le nombre de filtres actifs.
    chips = "".join(
        f"<span class='chip'>{escape(e)}</span>" for e in filtres.etiquettes()
    )
    if chips:
        chips = f"<p class='chips'>{chips}</p>"
    return (
        "<form class='filtres' action='/comptages' method='get'>"
        # Chaque champ est dans son propre `<p>`. Les `label` et les `input`
        # étaient des enfants directs de la grille, donc la grille les
        # répartissait dans ses colonnes **alternativement** : à cinq
        # colonnes, le libellé « Depuis le » tombait dans la première, son
        # champ dans la deuxième, le libellé « Mode » dans la cinquième et
        # le champ `mode` dans la première de la rangée suivante. Aucun
        # filtre n'était sous son libellé, et « Ligne » se lisait au-dessus
        # de la liste déroulante. Un enfant de grille par *champ*, pas par
        # *balise*, est la seule forme qui tienne dans n'importe quel nombre
        # de colonnes.
        f"{_champ_texte('depuis', 'Depuis le', filtres.depuis, '2026-01-31')}"
        f"{_champ_texte('jusqu', 'Jusqu’au le', filtres.jusqu, '2026-01-31')}"
        "<p class='champ'>"
        "<label for='mode'>Mode</label>"
        f"<select id='mode' name='mode'>{mode_options}</select>"
        "</p>"
        "<p class='champ'>"
        "<label for='ligne'>Ligne (route_id)</label>"
        f"<input id='ligne' type='text' name='ligne' autocomplete='off' value='{escape(filtres.ligne, quote=True)}'"
        " placeholder='C13 ou le route_id'>"
        "</p>"
        f"{_champ_cache(vue)}"
        f"{chips}"
        f"{alertes}"
        "<div class='filtres-actions'>"
        "<button type='submit'>Filtrer</button>"
        # Même règle que le lien de la page vide : « Tout enlever » retire
        # tout, sinon le lecteur se retrouve sur une page qui reste
        # filtrée et se demande ce qu'il n'a pas enlevé.
        f"<a class='retirer' href='{lien_comptages(filtres, vue=vue, tri='', sens='', page='')}'>Tout enlever</a>"
        "</div>"
        "</form>"
    )


def _champ_texte(nom: str, etiquette: str, valeur: str, exemple: str) -> str:
    """Une date, dans la boîte qui aligne le libellé et son champ.

    Le `<p>` n'est pas un habillage : c'est l'unité de grille. Sans lui, le
    `label` et l'`input` occupent deux cellules et le nombre de colonnes
    décide lequel est au-dessus de quoi.
    """
    return (
        "<p class='champ'>"
        f"<label for='{nom}'>{escape(etiquette)}</label>"
        f"<input id='{nom}' type='date' name='{nom}' value='{escape(valeur, quote=True)}'>"
        "</p>"
    )


def _champ_cache(vue: str) -> str:
    """La vue par paire traverse le formulaire, sinon filtrer la perd.

    Un champ caché qui n'est pas là coûte un clic à chaque fois, et pas
    un clic mais une confusion : le lecteur filtre, la vue change, et il
    ne sait pas pourquoi.
    """
    return f"<input type='hidden' name='vue' value='{escape(vue, quote=True)}'>" if vue else ""


def _filtre_vide(filtres: Filtres) -> str:
    """L'écran « aucun résultat », qui explique et offre la sortie.

    C'est le verrou de la vague 2 : **un filtre qui vide la liste doit le
    dire et proposer de l'enlever.** Une page vide sans explication se lit
    comme une absence de données, pas comme un filtre — et le lecteur qui
    conclut à tort qu'il n'y a rien ici ne revient pas chercher.

    Les deux moitiés sont dans la même phrase : le constat et la sortie.
    Le constat seul laisse croire à une absence de données. La sortie
    seule, un lien « réessayer » sur une liste vide n'explique rien.
    """
    if filtres.vide():
        return ""
    chips = " ".join(f"<span class='chip'>{escape(e)}</span>" for e in filtres.etiquettes())
    # Les erreurs sont déjà dans le formulaire, au-dessus. Les redire ici
    # doublerait le message sans rien ajouter : le lecteur qui filtre à
    # vide vient de taper, il les a encore sous les yeux.
    # Le lien de sortie retire TOUS les filtres, pas « celui-ci » : une
    # page vide ne dit pas quel filtre a échoué, donc il n'y a pas
    # « celui-ci ». `lien_comptages(filtres)` sans surcharge conserverait
    # `depuis=` et `jusqu=`, et le lien mènerait à la même page vide —
    # un bouton « réessayer » qui ne réessaie rien.
    sortie = {cle: "" for cle in ("depuis", "jusqu", "mode", "ligne", "vue", "tri", "sens", "page")}
    return (
        "<div class='card vide-filtre'>"
        f"<p>Aucun comptage ne correspond à ce filtre : {chips}.</p>"
        f"<p><a href='{lien_comptages(filtres, **sortie)}'>Enlever le filtre et voir tous les comptages</a></p>"
        "</div>"
    )


def _paires_table(paires: list, tri: str, sens: str, filtres: Filtres) -> str:
    """La vue par paire, en tableau.

    Le tableau est le seul rendu de cette vue : une paire de gares se lit
    en comparant des colonnes, et des cartes empilées demanderaient de
    faire défiler pour lire un nombre par carte. Sur téléphone la
    feuille de style garde le tableau — la vue par paire est une vue de
    comparaison, elle n'a pas de lecture dégradée ici.
    """
    if not paires:
        return ""
    # Le trajet en premier : c'est l'identité du corridor, et une ligne
    # dont on ne sait pas de quelle gare à quelle gare n'est pas
    # encore un résultat. Les colonnes numériques suivent, dans l'ordre
    # où on les compare.
    colonnes = (
        ("trajet", "Trajet"),
        ("releves", "Relevés"),
        ("moyenne", "Moyenne"),
        ("minimum", "Minimum"),
        ("maximum", "Maximum"),
    )
    entetes = []
    for cle, titre in colonnes:
        if cle == tri and sens:
            cible_sens = "asc" if sens == "desc" else "desc"
            marque = " ▾" if sens == "desc" else " ▴"
            url = lien_comptages(filtres, cle, cible_sens, vue="paire")
            entetes.append(f"<th><a href='{url}'>{titre}{marque}</a></th>")
        else:
            url = lien_comptages(filtres, cle, "asc", vue="paire")
            entetes.append(f"<th><a href='{url}'>{titre}</a></th>")
    # La période n'est pas triée : c'est une information de contexte, pas
    # une colonne qu'on compare. Un en-tête vide la dit mieux qu'un lien
    # qui ne changerait rien.
    entetes.append("<th>Période</th>")

    corps = []
    for paire in paires:
        # Un décimal quand la moyenne n'est pas entière : arrondir 149,5
        # à 150 ferait lire un nombre que personne n'a compté. La moyenne
        # est déjà arrondie à une décimale par `par_paire`.
        moyenne = (
            f"{paire.moyenne:g}" if paire.moyenne is not None else '<span class="vide">—</span>'
        )
        minimum = str(paire.minimum) if paire.minimum is not None else '<span class="vide">—</span>'
        maximum = str(paire.maximum) if paire.maximum is not None else '<span class="vide">—</span>'
        periode = ""
        if paire.premier and paire.dernier:
            debut = _date_fr(paire.premier).split(" à ")[0]
            fin = _date_fr(paire.dernier).split(" à ")[0]
            periode = debut if debut == fin else f"{debut} → {fin}"
        if paire.avec_effectif < paire.releves:
            # Un corridor qui compte des trains signalés : le dire vaut
            # mieux que faire croire que la moyenne porte sur tous les
            # relevés du corridor.
            periode = (periode + " · " if periode else "") + (
                f"{paire.releves - paire.avec_effectif} sans effectif"
            )
        cellules = [
            f"<td>{escape(paire.origine)} → {escape(paire.destination)}</td>",
            f"<td class='nombre'>{paire.releves}</td>",
            f"<td class='nombre'>{moyenne}</td>",
            f"<td class='nombre'>{minimum}</td>",
            f"<td class='nombre'>{maximum}</td>",
            f"<td class='status'>{escape(periode) or '—'}</td>",
        ]
        corps.append("<tr>" + "".join(cellules) + "</tr>")

    return (
        # La classe n'est pas `tableau` : celle-ci est retirée du rendu sous
        # 48 rem, ce qui laisserait la vue par paire vide sur un téléphone.
        # La vue par paire n'a qu'un rendu, un tableau dense qui déborde
        # horizontalement — lisible en faisant défiler de côté, ce qui est
        # un défaut accepté contre une page vide.
        "<table class='tableau paires'>"
        "<caption>Les mêmes comptages, regroupés par paire de gares. "
        "Un en-tête de colonne trie la vue.</caption>"
        f"<thead><tr>{''.join(entetes)}</tr></thead>"
        f"<tbody>{''.join(corps)}</tbody>"
        "</table>"
    )


def _tranche(tries: list[dict], page: int) -> list[dict]:
    """La portion de liste qu'affiche cette page.

    La découpe se fait **après** le tri, jamais avant : une page de
    résultats qui sont les 200 plus anciens se reconnaît au numéro de
    page, mais une page affichée avant tri se reconnaît à rien du tout,
    et un relevé absent de la page 1 sans explication se lit comme un
    relevé qui n'existe pas.

    La page hors bornes est ramenée dans les bornes, comme dans
    `_pagination` : les deux doivent tomber d'accord, sinon un lien
    « page 99 » annoncé à l'écran mènerait à une page vide.
    """
    return _couper(tries, page)


def _couper(elements: list, page: int) -> list:
    """Les `PAR_PAGE` éléments de cette page, après tri.

    La règle ne dépend pas de ce qu'on découpe, donc une seule fonction
    la porte pour la liste et pour la vue par paire. Deux fonctions qui
    l'énoncent chacune finissent par divergir — et cette PR a déjà vu
    deux tris diverger pour exactement cette raison.
    """
    if not elements:
        return []
    pages = max(1, -(-len(elements) // PAR_PAGE))
    courante = min(max(1, page), pages)
    debut = (courante - 1) * PAR_PAGE
    return elements[debut : debut + PAR_PAGE]


def _pagination(filtres: Filtres, tri: str, sens: str, vue: str, page: int, total: int) -> str:
    """Les liens de page, quand il y en a plus d'une.

    Les numéros de page sont des liens et non un bouton avec du
    JavaScript : la page courante est une URL, donc elle se partage et
    elle se teste. Et surtout, la pagination ne se déclenche qu'au-delà de
    `PAR_PAGE` relevés : sous ce seuil, elle n'aurait qu'un bouton « 1 »
    à afficher, et un tel bouton se lit comme un sélecteur de page qui ne
    fait rien.

    La page demandée au-delà de la dernière revient à la dernière, et non
    à une page vide : une URL de signet prise avant l'ajout de relevés ne
    doit pas devenir une page muette.
    """
    pages = max(1, -(-total // PAR_PAGE))
    if pages <= 1:
        return ""
    courante = min(max(1, page), pages)
    morceaux = []
    if courante > 1:
        precedent = lien_comptages(filtres, tri, sens, vue=vue, page=courante - 1)
        morceaux.append(f"<a href='{precedent}' rel='prev'>‹ Page précédente</a>")
    milieu = (
        f"<span class='status'>Page {courante} sur {pages}</span>"
    )
    morceaux.append(milieu)
    if courante < pages:
        suivant = lien_comptages(filtres, tri, sens, vue=vue, page=courante + 1)
        morceaux.append(f"<a href='{suivant}' rel='next'>Page suivante ›</a>")
    return f"<nav class='pagination'>{' '.join(morceaux)}</nav>"


def _reading_page(
    rows: list[dict],
    tri: str = "",
    sens: str = "",
    *,
    filtres: Filtres | None = None,
    vue: str = "",
    page: int = 1,
    total: int | None = None,
) -> str:
    """La liste des comptages, la plus récente d'abord.

    Le défaut est l'ordre décroissant sur la date. L'ordre croissant
    initial venait de l'ordre d'insertion en base, ce qui est un détail de
    stockage et pas un choix de lecture : le plus récent d'abord est ce
    qu'on veut voir en arrivant sur la page.

    `filtres`, `vue` et `page` ont des valeurs par défaut pour que
    l'appel direct reste possible ; la route les fournit tous.
    """
    filtres = filtres or Filtres()
    total = total if total is not None else len(rows)
    # `en_paires`, pas `par_paire` : ce dernier nom est celui de la
    # fonction importée, et le masquer à un seul endroit du fichier
    # ferait échouer la ligne suivante d'une façon obscure.
    en_paires = vue == "paire"
    entete = _filtres_html(filtres, vue)
    bascule = (
        f"<p><a href='{lien_comptages(filtres, tri, sens, vue='')}'>Voir la liste des relevés</a></p>"
        if en_paires
        else f"<p><a href='{lien_comptages(filtres, tri, sens, vue='paire')}'>Voir par paire de gares</a></p>"
    )

    if en_paires:
        # La vue par paire regroupe ce qui a été filtré, pas la base
        # entière : filtrer par ligne puis regarder les corridors de cette
        # ligne est la question que la page doit répondre.
        #
        # Les paires sont triées par `trier_paires` et non par `_trier` : un
        # relevé n'est pas une paire, et les deux ont des colonnes
        # différentes. Passer les lignes déjà triées ici n'aurait pas
        # d'effet — `par_paire` perd l'ordre — mais le laisser croire en
        # donnerait l'illusion que les deux tris coopèrent.
        paires = trier_paires(par_paire(rows), tri, sens)
        # La vue par paire se pagine au même seuil que la liste. J'avais
        # écrit le contraire, en arguant qu'il y a au plus autant de
        # paires que de relevés : la mesure le refute. Sur 6 000 relevés
        # répartis sur 90 × 37 gares, la vue par paire rendait 0,60 Mo et
        # 148 ms — six fois le poids d'une page de liste. Elle est donc
        # coupée comme elle, au même seuil.
        tranche = _couper(paires, page)
        contenu = _paires_table(tranche, tri, sens, filtres)
        if not tranche:
            contenu = _filtre_vide(filtres) or contenu
        pagination = _pagination(filtres, tri, sens, "paire", page, len(paires))
        resume = _resume_paires(paires, tranche)
    else:
        tries = _trier(rows, tri, sens)
        tranche = _tranche(tries, page)
        contenu = (
            f"<div class='cartes'>{_reading_cards(tranche)}</div>"
            f"{_reading_table(tranche, tri, sens, filtres)}"
        )
        if not tranche:
            contenu = _filtre_vide(filtres) or contenu
        pagination = _pagination(filtres, tri, sens, vue, page, total)
        resume = _resume(tries, tranche, total)

    corps = (
        f"{entete}"
        f"{bascule}"
        f"{resume}"
        f"{contenu}"
        f"{pagination}"
        f"<p><a class='bouton' href='/api/export.csv'>Télécharger le CSV</a></p>"
    )
    return chrome(
        "Comptages",
        corps,
        actif="/comptages",
        extra_css="""
  .pastilles { margin: 0.3rem 0 0.4rem; }
  .pastille { display: inline-block; background: #f0ece2; border: 1px solid var(--bord);
              border-radius: 1rem; padding: 0.1rem 0.55rem; margin: 0.15rem 0.3rem 0.15rem 0;
              font-size: 0.82rem; color: var(--gris); }
  .commentaire { border-left: 3px solid var(--bord); padding-left: 0.6rem; margin: 0.5rem 0;
                 font-size: 0.95rem; }
  /* Les filtres sur une colonne, comme le formulaire de saisie : c'est une
     page qu'on consulte, pas un formulaire qu'on remplit sur un quai.
     `align-items: end` alignait les *cellules* par le bas, donc un `label`
     au-dessus d'un `input` se retrouvait décalé d'une ligne par rapport à
     son voisin ; l'alignement se fait dans la boîte, pas dans la grille. */
  form.filtres { display: grid; gap: 0.6rem 0.5rem; background: #fff; border-radius: 0.8rem;
                 padding: 0.8rem; margin: 0 0 1rem; align-items: start; }
  /* `margin: 0` : un `<p>` apporte un espacement vertical que la grille
     ne demande pas, et l'écart vertical venait de là. */
  form.filtres p.champ { margin: 0; display: flex; flex-direction: column; gap: 0.2rem; }
  form.filtres label { font-size: 0.85rem; color: var(--gris); }
  form.filtres input, form.filtres select { font: inherit; padding: 0.5rem; width: 100%;
                                           box-sizing: border-box; border-radius: 0.5rem;
                                           border: 1px solid var(--bord); background: #fff; }
  form.filtres .filtres-actions { display: flex; gap: 0.8rem; align-items: center;
                                 grid-column: 1 / -1; margin-top: 0.3rem; }
  form.filtres button { font: inherit; padding: 0.6rem 1rem; border: 0; border-radius: 0.6rem;
                        background: var(--encre); color: #fff; }
  form.filtres a.retirer { font-size: 0.9rem; }
  .chip { display: inline-block; background: #f0ece2; border: 1px solid var(--bord);
          border-radius: 1rem; padding: 0.1rem 0.6rem; font-size: 0.85rem; }
  /* La rangée des chips est une seule cellule, sur toute la largeur. */
  form.filtres p.chips { margin: 0; grid-column: 1 / -1; display: flex;
                         flex-wrap: wrap; gap: 0.3rem; }
  /* La table par paire est visible sur téléphone, contrairement à `.tableau`,
     et déborde donc au lieu de se comprimer en colonnes illisibles. */
  .tableau.paires { display: table; font-size: 0.9rem; }
  /* Les erreurs de filtre vont dans le formulaire, pas dans une carte
     d'erreur à part : elles concernent ce qu'on vient de taper. */
  ul.erreurs { margin: 0.3rem 0; padding-left: 1.2rem; font-size: 0.9rem; color: var(--gris);
               grid-column: 1 / -1; }
  nav.pagination { display: flex; gap: 1rem; align-items: center; margin: 1rem 0;
                   justify-content: space-between; }
  @media (min-width: 48rem) {
    /* Les quatre filtres sur une rangée, pas quatre lignes empilées dans
       72 rem de largeur. Quatre colonnes et non cinq : le cinquième enfant
       est le `input type="hidden"`, qui ne prend pas de place, et le
       cinquième *champ* — le bouton — est posé plus bas par
       `.filtres-actions`. */
    form.filtres { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    /* Le tableau remplace les cartes, il ne s'ajoute pas à elles : sans
       cette ligne, un lecteur sur grand écran verrait la liste deux fois,
       une fois en cartes et une fois en tableau. */
    .cartes { display: none; }
  }
""",
    )


def _method_page() -> str:
    """La méthode, en clair, à côté des chiffres.

    Cette page est celle que le plan rend obligatoire avant toute
    estimation. Tant qu'elle n'existe pas, on ne publie que du brut, et
    c'est délibéré : un effectif saisi par un voyageur n'est pas une
    fréquentation, et un échantillon de passionnés n'est pas un sondage.

    Le texte est une chaîne constante et le reste vient du chrome commun :
    une page de lecture qui recopie sa propre mise en page diverge, et
    celle-ci serait la première à diverger puisque c'est la plus longue.
    """
    corps = """<p class="mode">Ce que l'outil fait, et comment lire un comptage.</p>

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

<p class="status">Cette page décrit ce que l'outil fait aujourd'hui. Elle sera mise
à jour chaque fois qu'une règle change.</p>
"""
    return chrome(
        "Méthode",
        corps,
        actif="/methode",
        mention="",
        extra_css="""
  h2 { margin-top: 2rem; }
  h3 { margin-top: 1.4rem; font-size: 1rem; }
  .mode { color: var(--gris); margin: 0 0 1rem; }
  .lead { font-weight: 600; }
  ul { padding-left: 1.2rem; }
  li { margin: 0.35rem 0; }
  .note { background: #fff; border-radius: 0.8rem; padding: 0.8rem 1rem; margin: 1rem 0; }
  @media (min-width: 48rem) {
    /* Un texte de méthode se lit en 72 rem sans effort, mais deux colonnes
       le rendent pénible : on garde la colonne de lecture et on élargit
       seulement la gouttière. La largeur utile reste celle d'un livre. */
    main { max-width: 46rem; }
  }
""",
    )


def _export_csv(rows: list[dict]) -> str:
    """Le CSV des comptages.

    Délégué à `comptagefer.publish`, qui rend aussi le fichier publié sur
    data.gouv : une seule fonction pour le téléchargement et l'export
    automatique, sinon les deux divergent sans qu'on le voie.
    """
    return render_csv(rows)
