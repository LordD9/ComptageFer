"""Publication automatique du CSV des comptages sur data.gouv.fr.

Le plan est verrouillé sur un seul processus Python : la publication est un
fil, comme le poller GTFS-RT, pas un second service et pas un conteneur
`cron`. Un service de plus dans le compose, c'est une base, une file et un
worker à surveiller ; ici un `threading.Thread` suffit.

Le module ne connaît pas la base : il reçoit un `csv_provider`, un appelable
qui rend le texte du CSV. Le rendu lui-même reste dans l'application, seule
fonction à connaître la table `saisie`. Un export automatique qui duplique la
requête SQL du CSV ne publie jamais exactement le fichier que télécharge
l'utilisateur.

L'API data.gouv.fr est en `POST` multipart sur
`/datasets/{dataset}/upload/` (crée une ressource) et
`/datasets/{dataset}/resources/{rid}/upload/` (remplace le fichier d'une
ressource existante). Reprendre la même ressource chaque nuit garde
l'historique des versions côté data.gouv ; en créer une nouvelle par nuit
emplirait le jeu de données de ressources identiques. L'id de la ressource est
donc mémorisé dans `publish.json` à côté des bases.
"""

import csv
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger("comptagefer.publish")
API = "https://www.data.gouv.fr/api/1"
PARIS = ZoneInfo("Europe/Paris")
CSV_NAME = "comptages-ter.csv"
CSV_MIME = "text/csv; charset=utf-8"
STATE_NAME = "publish.json"


def render_csv(rows: list[dict]) -> str:
    """Le CSV des comptages, licence comprise en première ligne.

    C'est le fichier que donne `/api/export.csv`, et c'est celui qui part sur
    data.gouv. Une seule fonction pour les deux : si le robot les sépare, le
    fichier publié n'est plus celui qu'on télécharge.
    """
    buffer = StringIO()
    buffer.write("# Licence Ouverte 2.0\n")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "created_at",
            "origin",
            "destination",
            "passengers",
            "reliability",
            "pseudo",
            "comment",
            "standing",
            "seats_free",
            "imbalance",
            "materiel",
            "composition",
            "perimetre",
            "precedent",
            "courant",
            "suivant",
            "kind",
            "legs",
            "trip_id",
            "trajet",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["created_at"],
                row["origin_name"] or "",
                row["destination_name"] or "",
                row["passengers"] if row["passengers"] is not None else "",
                row["reliability"] if row["reliability"] is not None else "",
                row["pseudo"] or "",
                row["comment"] or "",
                row["standing"] if row["standing"] is not None else "",
                row["seats_free"] if row["seats_free"] is not None else "",
                row["imbalance"] if row["imbalance"] is not None else "",
                row.get("materiel") or "",
                row.get("composition") or "",
                row.get("perimetre") or "",
                _photo_status(row["snapshot"], "precedent"),
                _photo_status(row["snapshot"], "courant"),
                _photo_status(row["snapshot"], "suivant"),
                row["kind"] or "",
                json.dumps(row["legs"], ensure_ascii=False) if row.get("legs") else "",
                row.get("trip_id") or "",
                _trajet_csv(row.get("trajet")),
            ]
        )
    return buffer.getvalue()


def _trajet_csv(trajet: object) -> str:
    """Le trajet du train, en une colonne lisible par un tableur.

    JSON pour être exact, comme `legs`. C'est moins lisible qu'une liste, mais
    mettre les arrêts dans des colonnes supposerait que le lecteur sait déjà de
    combien d'arrêts il s'agit — un train peut en avoir 3 ou 60, et ça varie
    d'un comptage à l'autre. Le tableur ne peut donc pas avoir de colonnes
    fixes sans perdre l'information, et l'information est justement ce qu'on
    veut garder. Le format long est le seul qui tienne.
    """
    if not isinstance(trajet, dict):
        return ""
    arrets = trajet.get("arrets")
    if not isinstance(arrets, list) or not arrets:
        return ""
    return json.dumps(
        [
            [item.get("name") or item.get("stop_id"), item.get("depart_sec")]
            for item in arrets
            if isinstance(item, dict)
        ],
        ensure_ascii=False,
    )


def _photo_status(snapshot: object, key: str) -> str:
    if not isinstance(snapshot, dict):
        return ""
    item = snapshot.get(key) or {}
    if not isinstance(item, dict):
        return ""
    return str(item.get("status") or "")


class Config:
    """Ce qu'il faut pour publier, lu dans l'environnement du conteneur.

    Sans clé API, la publication est inactive : c'est le comportement par
    défaut d'un déploiement qui n'a rien mis dans `.env`. Le service démarre
    quand même, il ne publie pas, et il le dit sur `/api/publish`.
    """

    def __init__(
        self,
        api_key: str = "",
        dataset_id: str = "",
        resource_id: str = "",
        hour: int = 0,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.dataset_id = (dataset_id or "").strip()
        self.resource_id = (resource_id or "").strip()
        self.hour = hour

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.dataset_id)

    def missing(self) -> list[str]:
        """Ce qui empêche la publication, à afficher tel quel à l'hébergeur."""
        absent = []
        if not self.api_key:
            absent.append("DATAGOUV_API_KEY")
        if not self.dataset_id:
            absent.append("DATAGOUV_DATASET_ID")
        return absent

    def __repr__(self) -> str:  # pragma: no cover - jamais de clé dans un log
        return (
            f"Config(dataset_id={self.dataset_id!r}, resource_id={self.resource_id!r}, "
            f"hour={self.hour!r}, api_key={'posée' if self.api_key else 'absente'})"
        )


def config_from_env(environ: dict | None = None) -> Config:
    source = os.environ if environ is None else environ
    try:
        hour = int(source.get("DATAGOUV_PUBLISH_HOUR", "0"))
    except ValueError:
        hour = 0
    return Config(
        api_key=source.get("DATAGOUV_API_KEY", ""),
        dataset_id=source.get("DATAGOUV_DATASET_ID", ""),
        resource_id=source.get("DATAGOUV_RESOURCE_ID", ""),
        hour=min(max(hour, 0), 23),
    )


def next_run_at(hour: int, now: datetime) -> datetime:
    """La prochaine occurrence de l'heure de publication, heure de Paris.

    Le conteneur tourne en UTC et sur un Pi qui peut se mettre en veille : la
    cible est recalculée à chaque tour, on ne dort pas une durée calculée une
    fois pour toutes.
    """
    moment = now.astimezone(PARIS)
    cible = moment.replace(hour=hour, minute=0, second=0, microsecond=0)
    if cible <= moment:
        cible += timedelta(days=1)
    return cible


def _multipart(payload: bytes) -> tuple[str, bytes]:
    """Un corps `multipart/form-data` pour un envoi de fichier unique.

    Fait à la main parce que le projet n'a qu'une dépendance HTTP, la
    bibliothèque standard. Le séparateur est tiré au hasard, donc il ne peut
    pas apparaître dans un CSV de comptages.
    """
    boundary = "----ComptageFer" + uuid4().hex
    entete = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{CSV_NAME}"\r\n'
        f"Content-Type: {CSV_MIME}\r\n\r\n"
    ).encode()
    corps = entete + payload + f"\r\n--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", corps


def _erreur(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        detail = ""
        try:
            charge = json.loads(exc.read() or b"{}")
            detail = str(charge.get("message") or "")
        except Exception:  # noqa: BLE001 - la réponse peut être du HTML
            detail = ""
        return f"HTTP {exc.code}" + (f" : {detail}" if detail else "")
    return str(exc)


class Publication:
    """L'état de la publication et le fil qui la déclenche à l'heure.

    `csv_provider` rend le texte du CSV au moment de la publication, pas au
    démarrage : un fil qui capturait le CSV une fois publierait le fichier de
    la veille pour toujours.
    """

    def __init__(self, config: Config, state_path: Path, csv_provider) -> None:
        self.config = config
        self.state_path = Path(state_path)
        self.csv_provider = csv_provider
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def est_vide(self, payload: bytes) -> bool:
        """Le CSV ne porte-t-il que la licence et l'en-tête, sans données ?

        On ne compte pas les virgules : l'en-tête en a déjà, autant que les
        lignes de données. Ce qui distingue les deux, c'est qu'une donnée
        s'ajoute sous l'en-tête. Le fichier est celui de l'export, donc le
        test suit le même format : commentaire, en-tête, puis les lignes.
        """
        lignes = [
            ligne
            for ligne in payload.decode("utf-8").splitlines()
            if ligne.strip() and not ligne.startswith("#")
        ]
        return len(lignes) <= 1

    def read_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def write_state(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporaire = self.state_path.with_suffix(".tmp")
        temporaire.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        temporaire.replace(self.state_path)

    def status(self) -> dict:
        state = self.read_state()
        return {
            "enabled": self.config.enabled,
            "missing": self.config.missing(),
            "dataset_id": self.config.dataset_id,
            "resource_id": state.get("resource_id") or self.config.resource_id,
            "hour": self.config.hour,
            "next_run": next_run_at(self.config.hour, datetime.now(PARIS)).isoformat(),
            "last_run": state.get("last_run"),
            "last_status": state.get("last_status"),
            "last_error": state.get("last_error"),
            "last_bytes": state.get("last_bytes"),
        }

    def publish_now(self, opener=None) -> dict:
        """Publie maintenant et rend le résultat. Ne lève pas.

        L'échec d'une nuit ne doit pas arrêter le fil : le rapport est dans
        l'état et dans le journal, et la nuit suivante recommence.
        """
        if not self.config.enabled:
            return {
                "ok": False,
                "error": "publication inactive : " + ", ".join(self.config.missing()),
            }
        envoi = opener or urllib.request.urlopen
        state = self.read_state()
        resultat: dict = {"ok": False, "error": None, "resource_id": None, "bytes": 0}
        try:
            payload = self.csv_provider().encode("utf-8")
            if self.est_vide(payload):
                # Une base vide ne remplace pas la ressource du jour : publier
                # un en-tête seul ferait croire que les comptages ont disparu.
                return {
                    "ok": True,
                    "error": None,
                    "resource_id": state.get("resource_id"),
                    "bytes": 0,
                    "skipped": True,
                }
            content_type, body = _multipart(payload)
            ressource = state.get("resource_id") or self.config.resource_id
            if ressource:
                url = f"{API}/datasets/{self.config.dataset_id}/resources/{ressource}/upload/"
            else:
                url = f"{API}/datasets/{self.config.dataset_id}/upload/"
            entete = {"X-API-KEY": self.config.api_key, "Content-Type": content_type}
            request = urllib.request.Request(url, data=body, headers=entete, method="POST")
            with envoi(request, timeout=120) as response:
                code = getattr(response, "status", 200) or 200
                brut = response.read()
            if ressource:
                nouveau = ressource
            else:
                charge = json.loads(brut or b"{}")
                nouveau = str(charge.get("id") or (charge.get("resource") or {}).get("id") or "")
            resultat = {
                "ok": 200 <= int(code) < 300,
                "error": None if 200 <= int(code) < 300 else f"HTTP {code}",
                "resource_id": nouveau,
                "bytes": len(payload),
                "code": int(code),
            }
        except Exception as exc:  # noqa: BLE001 - un envoi raté ne tue pas le fil
            resultat["error"] = _erreur(exc)
        etat = {
            "resource_id": resultat.get("resource_id") or state.get("resource_id"),
            "last_run": datetime.now(PARIS).isoformat(),
            "last_status": "ok" if resultat["ok"] else "erreur",
            "last_error": resultat.get("error"),
            "last_bytes": resultat.get("bytes"),
        }
        with self._lock:
            self.write_state(etat)
        if resultat["ok"]:
            LOGGER.info("CSV publié sur data.gouv (%s octets)", resultat["bytes"])
        else:
            LOGGER.warning("publication data.gouv échouée : %s", resultat["error"])
        return resultat

    def start(self) -> None:
        if not self.config.enabled:
            LOGGER.info(
                "publication data.gouv inactive, il manque %s", ", ".join(self.config.missing())
            )
            return
        self._thread = threading.Thread(
            target=self.run_forever, name="datagouv", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self, opener=None) -> None:
        while not self._stop.is_set():
            cible = next_run_at(self.config.hour, datetime.now(PARIS))
            # On dort par tranches courtes : un conteneur arrêté puis redémarré,
            # ou une horloge qui saute, ne doit pas décaler la publication d'un
            # jour entier.
            while not self._stop.is_set():
                reste = (cible - datetime.now(PARIS)).total_seconds()
                if reste <= 0:
                    break
                if self._stop.wait(min(reste, 30.0)):
                    return
            if self._stop.is_set():
                return
            time.sleep(1)
            self.publish_now(opener=opener)
