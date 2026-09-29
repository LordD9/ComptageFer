import json
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.publish import (
    API,
    PARIS,
    Config,
    Publication,
    config_from_env,
    next_run_at,
    render_csv,
)

CLE = "cle-de-test"
JEU = "66a1b2c3d4e5f60718293a4b"
CSV_AVEC_DONNEES = (
    "# Licence Ouverte 2.0\n"
    "created_at,origin,destination,passengers\n"
    '2026-09-27,Lyon Part Dieu,Nimes Pont du Gard,40\n'
)


class Reponse:
    def __init__(self, code: int = 201, corps: bytes = b"") -> None:
        self.status = code
        self._corps = corps

    def read(self) -> bytes:
        return self._corps

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Journal:
    """Un opener qui note les requêtes au lieu d'aller sur data.gouv."""

    def __init__(self, code: int = 201, corps: bytes = b"") -> None:
        self.appels: list = []
        self.code = code
        self.corps = corps

    def __call__(self, request, timeout=None):
        self.appels.append(request)
        return Reponse(self.code, self.corps)


def _compte() -> dict:
    return {
        "client_id": "jeton",
        "origin_stop_id": "A",
        "destination_stop_id": "B",
        "origin_name": "Lyon Part Dieu",
        "destination_name": "Nîmes Pont du Gard",
        "trip_id": "TRIP1",
        "passengers": 40,
        "reliability": 70,
        "snapshot": {"courant": {"status": "SCHEDULED"}},
    }


# --- le CSV reste le même qu'avant ---------------------------------------


def test_le_csv_garde_sa_licence_et_ses_entetes():
    csv = render_csv(
        [
            {
                "created_at": "2026-09-27T10:00:00+00:00",
                "origin_name": "Lyon Part Dieu",
                "destination_name": "Nîmes Pont du Gard",
                "passengers": 40,
                "reliability": 70,
                "pseudo": "railfan",
                "comment": "car de substitution",
                "standing": None,
                "seats_free": None,
                "imbalance": None,
                "snapshot": {"precedent": None, "courant": {"status": "CANCELED"}},
                "kind": "count",
                "legs": None,
                "trip_id": None,
                "trajet": None,
            }
        ]
    )

    lignes = csv.strip().splitlines()
    assert lignes[0] == "# Licence Ouverte 2.0"
    assert lignes[1].startswith("created_at,origin,destination")
    assert "Lyon Part Dieu" in lignes[2]
    assert lignes[2].count("CANCELED") == 1


def test_le_commentaire_parvient_jusqu_au_csv(tmp_path: Path):
    """Le commentaire est la partie libre du comptage : c'est lui qui explique
    une charge atypique. S'il reste dans la base, il n'est publié nulle part et
    la promesse de /methode est vide pour le lecteur du jeu de données.

    Le test passe par le vrai POST puis par le vrai export : une fixture
    écrite à la main prouverait que render_csv sait lire une clé, pas que la
    chaîne complète la garde.
    """
    client = TestClient(create_app(tmp_path))
    client.post("/api/sessions", json={**_compte(), "comment": "car de substitution"})

    telecharge = client.get("/api/export.csv").text
    lignes = telecharge.strip().splitlines()
    entetes = lignes[1].split(",")

    assert "comment" in entetes, f"colonnes : {entetes}"
    colonne = entetes.index("comment")
    # L'ordre des lignes suit created_at, et deux posts peuvent partager la même
    # seconde : on cherche la ligne du bon jeton plutôt que d'indexer.
    lignes_par_jeton = {
        ligne.split(",")[0]: ligne.split(",")[colonne]
        for ligne in lignes[2:]
    }
    assert "car de substitution" in lignes_par_jeton.values(), lignes_par_jeton
    # Le commentaire absent ne devient pas la chaîne « None ».
    client.post("/api/sessions", json={**_compte(), "client_id": "jeton2"})
    apres = client.get("/api/export.csv").text.strip().splitlines()
    valeurs = [l.split(",")[colonne] for l in apres[2:]]
    assert len(valeurs) == 2, f"deux comptages attendus : {apres}"
    assert "" in valeurs, f"commentaire vide rendu : {valeurs}"


def test_le_csv_telecharge_est_celui_du_depot(tmp_path: Path):
    """Une seule fonction rend les deux, sinon l'export automatique dérive."""
    client = TestClient(create_app(tmp_path))
    client.post("/api/sessions", json=_compte())

    telecharge = client.get("/api/export.csv").text
    states = client.get("/api/sessions").json()

    assert telecharge == render_csv(states)


# --- la configuration vient de l'environnement ----------------------------


def test_sans_jeton_la_publication_est_inactive(tmp_path: Path):
    publication = Publication(Config(), tmp_path / "publish.json", lambda: "x")

    assert publication.config.enabled is False
    assert publication.config.missing() == ["DATAGOUV_API_KEY", "DATAGOUV_DATASET_ID"]


def test_une_cle_sans_jeu_ne_publie_pas(tmp_path: Path):
    publication = Publication(Config(api_key=CLE), tmp_path / "publish.json", lambda: "x")

    resultat = publication.publish_now(opener=Journal())

    assert resultat["ok"] is False
    assert "DATAGOUV_DATASET_ID" in resultat["error"]


def test_la_configuration_est_lue_dans_l_environnement():
    config = config_from_env(
        {
            "DATAGOUV_API_KEY": CLE,
            "DATAGOUV_DATASET_ID": JEU,
            "DATAGOUV_RESOURCE_ID": "res-1",
            "DATAGOUV_PUBLISH_HOUR": "4",
        }
    )

    assert config.enabled is True
    assert config.hour == 4
    assert config.resource_id == "res-1"


def test_une_heure_absente_ou_absurde_ne_casse_pas_le_demarrage():
    assert config_from_env({}).hour == 0
    assert config_from_env({"DATAGOUV_PUBLISH_HOUR": "minuit"}).hour == 0
    assert config_from_env({"DATAGOUV_PUBLISH_HOUR": "99"}).hour == 23


def test_une_ressource_connue_est_reutilisee_pour_une_seule_publication(tmp_path: Path):
    publication = Publication(
        Config(api_key=CLE, dataset_id=JEU, resource_id="res-1"),
        tmp_path / "publish.json",
        lambda: CSV_AVEC_DONNEES,
    )
    journal = Journal(200)

    publication.publish_now(opener=journal)
    publication.publish_now(opener=journal)

    assert [a.full_url for a in journal.appels] == [
        f"{API}/datasets/{JEU}/resources/res-1/upload/",
        f"{API}/datasets/{JEU}/resources/res-1/upload/",
    ]


def test_sans_ressource_la_premiere_publication_en_cree_une(tmp_path: Path):
    """Une ressource par nuit remplirait le jeu de données de doublons."""
    publication = Publication(
        Config(api_key=CLE, dataset_id=JEU),
        tmp_path / "publish.json",
        lambda: CSV_AVEC_DONNEES,
    )
    journal = Journal(201, json.dumps({"id": "res-creee"}).encode())

    publication.publish_now(opener=journal)
    publication.publish_now(opener=journal)

    assert journal.appels[0].full_url == f"{API}/datasets/{JEU}/upload/"
    assert journal.appels[1].full_url == f"{API}/datasets/{JEU}/resources/res-creee/upload/"


def test_l_etat_est_pose_et_survit_au_redemarrage(tmp_path: Path):
    fichier = tmp_path / "publish.json"
    publication = Publication(
        Config(api_key=CLE, dataset_id=JEU), fichier, lambda: CSV_AVEC_DONNEES
    )

    publication.publish_now(opener=Journal(201, json.dumps({"id": "res-9"}).encode()))

    relue = Publication(Config(api_key=CLE, dataset_id=JEU), fichier, lambda: CSV_AVEC_DONNEES)
    status = relue.status()
    assert status["last_status"] == "ok"
    assert status["resource_id"] == "res-9"
    assert status["last_error"] is None


# --- le corps de la requête -----------------------------------------------


def test_la_cle_part_en_entete_et_le_csv_en_multipart(tmp_path: Path):
    publication = Publication(
        Config(api_key=CLE, dataset_id=JEU), tmp_path / "publish.json", lambda: CSV_AVEC_DONNEES
    )
    journal = Journal()

    publication.publish_now(opener=journal)

    requete = journal.appels[0]
    assert requete.get_header("X-api-key") == CLE
    assert requete.get_method() == "POST"
    assert requete.headers["Content-type"].startswith("multipart/form-data; boundary=")
    corps = requete.data
    assert b'name="file"; filename="comptages-ter.csv"' in corps
    assert corps.endswith(b"--\r\n")


def test_une_cle_refusee_est_remontee_sans_tuer_le_fil(tmp_path: Path):
    import urllib.error

    def opener(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {}, None
        )

    publication = Publication(
        Config(api_key="mauvaise", dataset_id=JEU), tmp_path / "publish.json", lambda: CSV_AVEC_DONNEES
    )

    resultat = publication.publish_now(opener=opener)

    assert resultat["ok"] is False
    assert "401" in resultat["error"]
    assert publication.status()["last_status"] == "erreur"


# --- l'heure de publication -----------------------------------------------


def test_la_prochaine_publication_est_la_prochaine_occurrence_de_lheure():
    avant = datetime(2026, 9, 27, 22, 30, tzinfo=PARIS)

    cible = next_run_at(0, avant)

    assert cible.hour == 0
    assert cible.day == 28
    assert cible > avant


def test_apres_lheure_on_repasse_au_lendemain():
    """Un conteneur redémarré à 00:00:05 ne doit pas publier 24 fois."""
    juste_apres = datetime(2026, 9, 28, 0, 0, 5, tzinfo=PARIS)

    cible = next_run_at(0, juste_apres)

    assert cible.day == 29


def test_le_statut_annonce_la_prochaine_publication_sans_la_cle(tmp_path: Path):
    publication = Publication(
        Config(api_key=CLE, dataset_id=JEU), tmp_path / "publish.json", lambda: CSV_AVEC_DONNEES
    )

    status = publication.status()

    assert status["enabled"] is True
    assert CLE not in json.dumps(status)
    assert datetime.fromisoformat(status["next_run"]).tzinfo is not None


def test_un_csv_vide_ne_remplace_pas_la_ressource(tmp_path: Path):
    """Publier un en-tête seul ferait croire que les comptages ont disparu."""
    journal = Journal(200, b"")
    publication = Publication(
        Config(api_key=CLE, dataset_id=JEU),
        tmp_path / "publish.json",
        lambda: "# Licence Ouverte 2.0\ncreated_at,origin\n",
    )

    resultat = publication.publish_now(opener=journal)

    assert resultat["ok"] is True
    assert resultat["skipped"] is True
    assert journal.appels == []


def test_un_csv_avec_des_donnees_part(tmp_path: Path):
    """Le garde-fou ne doit pas confondre une ligne de données et l'en-tête."""
    journal = Journal(200, b"")
    publication = Publication(
        Config(api_key=CLE, dataset_id=JEU),
        tmp_path / "publish.json",
        lambda: "# Licence Ouverte 2.0\ncreated_at,origin\n2026-09-27,Lyon\n",
    )

    resultat = publication.publish_now(opener=journal)

    assert "skipped" not in resultat
    assert len(journal.appels) == 1
    assert b"Lyon" in journal.appels[0].data


# --- l'admin ---------------------------------------------------------------


def test_le_statut_publie_est_lisible_sans_le_secret(tmp_path: Path):
    client = TestClient(create_app(tmp_path, admin_token=CLE))

    status = client.get("/api/publish").json()

    assert status["enabled"] is False
    assert "DATAGOUV_API_KEY" in status["missing"]


def test_publier_immediatement_exige_le_jeton_d_admin(tmp_path: Path):
    client = TestClient(create_app(tmp_path, admin_token=CLE))

    refuse = client.post("/admin/publier")

    assert refuse.status_code == 401


def test_le_bouton_publier_passe_par_le_jeton_d_admin(tmp_path: Path):
    client = TestClient(create_app(tmp_path, admin_token=CLE))
    client.cookies.set("comptagefer_admin", "inexistante")

    refuse = client.post("/admin/publier")

    assert refuse.status_code == 401


def test_le_bouton_publier_appelle_le_service_avec_un_opener_faux(tmp_path: Path):
    """Un test qui n'attrape pas l'appel réseau sortirait sur le réseau en CI."""
    journal = Journal(201, json.dumps({"id": "res-1"}).encode())
    publication = Publication(
        Config(api_key=CLE, dataset_id=JEU), tmp_path / "publish.json", lambda: CSV_AVEC_DONNEES
    )
    client = TestClient(create_app(tmp_path, admin_token=CLE, publication=publication))
    connexion = client.post("/admin/login", data={"token": CLE})
    assert connexion.status_code == 200

    # La route appelle publish_now() sans argument : on lui injecte l'opener
    # plutôt que de laisser urllib sortir sur data.gouv.fr.
    original = publication.publish_now
    publication.publish_now = lambda opener=None: original(opener=opener or journal)
    page = client.post("/admin/publier")

    assert page.status_code == 200
    assert "publié" in page.text
    assert [a.full_url for a in journal.appels] == [f"{API}/datasets/{JEU}/upload/"]


# --- aucune fuite de secret ------------------------------------------------


def test_le_repr_de_la_configuration_ne_contient_pas_la_cle():
    texte = repr(Config(api_key=CLE, dataset_id=JEU))

    assert CLE not in texte
    assert "posée" in texte


def test_le_compose_ne_contient_que_des_variables_vides():
    """Le compose déclare les variables, il ne porte jamais leur valeur."""
    compose = (Path(__file__).parent.parent / "compose.yaml").read_text()

    assert "DATAGOUV_API_KEY: ${DATAGOUV_API_KEY:-}" in compose
    assert CLE not in compose