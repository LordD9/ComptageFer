"""Régressions de l'audit des comptes : secret, origine, cookies et sessions."""

import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from comptagefer import compte
from comptagefer.app import create_app


def test_creation_ne_divulgue_le_secret_que_dans_le_corps(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.post(
        "/compte/creer", data={"pseudo": "voyageur"}, follow_redirects=False
    )
    assert response.status_code == 200
    match = re.search(r'<code id="secret-texte">([^<]+)</code>', response.text)
    assert match is not None
    secret = match[1]
    assert secret not in str(response.headers)
    assert "secret-neuf" not in str(response.url)
    assert client.cookies.get(compte.COOKIE_COMPTE)
    assert secret not in client.get("/compte").text
    assert "secret-texte" not in client.get("/compte?secret-neuf=" + secret).text
    client.post("/compte/deconnecter")
    client.post("/compte/se-connecter", data={"secret": secret})
    assert client.cookies.get(compte.COOKIE_COMPTE)


@pytest.mark.parametrize("path", ["/compte", "/compte/inexistant", "/admin"])
def test_pages_privees_non_cacheables_et_non_encadrables(tmp_path, path):
    response = TestClient(create_app(tmp_path)).get(path)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


@pytest.mark.parametrize(
    "path,data",
    [
        ("/compte/creer", {"pseudo": "intrus"}),
        ("/compte/se-connecter", {"secret": "intrus"}),
        ("/compte/deconnecter", {}),
        (
            "/compte/signaler",
            {"client_id": "public", "kind": "count", "motif": "intrus"},
        ),
        ("/admin/login", {"token": "test-admin"}),
        ("/admin/supprimer", {"client_id": "public", "kind": "count"}),
        ("/admin/publier", {}),
    ],
)
@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Referer": "http://testserver.evil.example/form"},
        {"Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_post_inter_origine_refuse_avant_toute_mutation(tmp_path, path, data, headers):
    client = TestClient(create_app(tmp_path, admin_token="test-admin"))
    identifiant, secret = compte.creer_compte(tmp_path / "app.db", "victime")
    jeton, _ = compte.ouvrir_session(tmp_path / "app.db", identifiant)
    client.cookies.set(compte.COOKIE_COMPTE, jeton)
    response = client.post(path, data=data, headers=headers, follow_redirects=False)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert compte.compte_de_session(tmp_path / "app.db", jeton) == identifiant
    assert compte.compte_de_secret(tmp_path / "app.db", secret) == identifiant
    with sqlite3.connect(tmp_path / "app.db") as db:
        assert db.execute("SELECT COUNT(*) FROM compte").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM signalement").fetchone()[0] == 0


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
        {"Referer": "http://testserver/compte"},
        {},  # Les clients non-navigateur restent utilisables.
    ],
)
def test_creation_meme_origine_et_clients_sans_entetes(tmp_path, headers):
    response = TestClient(create_app(tmp_path)).post(
        "/compte/creer",
        data={"pseudo": "voyageur"},
        headers=headers,
    )
    assert response.status_code == 200


@pytest.mark.parametrize("value,secure", [("", False), ("0", False), ("1", True)])
def test_https_applique_au_cookie_compte_et_admin(tmp_path, monkeypatch, value, secure):
    monkeypatch.setenv("COMPTAGEFER_HTTPS", value)
    client = TestClient(
        create_app(tmp_path, admin_token="test-admin"), base_url="https://testserver"
    )
    for path, data in [
        ("/compte/creer", {"pseudo": "voyageur"}),
        ("/admin/login", {"token": "test-admin"}),
    ]:
        response = client.post(path, data=data, follow_redirects=False)
        cookie = response.headers["set-cookie"]
        assert ("; Secure" in cookie) is secure
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    response = client.post("/compte/deconnecter", follow_redirects=False)
    assert ("; Secure" in response.headers["set-cookie"]) is secure
    assert not client.cookies.get(compte.COOKIE_COMPTE)


def test_origine_https_avec_proxy_http_sans_confiance_en_forwarded(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COMPTAGEFER_HTTPS", "1")
    client = TestClient(create_app(tmp_path), base_url="http://comptagesfer.fr")
    assert (
        client.post(
            "/compte/creer",
            data={"pseudo": "voyageur"},
            headers={"Origin": "https://comptagesfer.fr"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/compte/creer",
            data={"pseudo": "intrus"},
            headers={
                "Origin": "https://evil.example",
                "X-Forwarded-Host": "evil.example",
            },
        ).status_code
        == 403
    )


def test_changer_de_session_revoque_l_ancien_cookie(tmp_path):
    client = TestClient(create_app(tmp_path))
    client.post("/compte/creer", data={"pseudo": "voyageur"})
    ancien = client.cookies.get(compte.COOKIE_COMPTE)
    assert ancien is not None
    _, secret = compte.creer_compte(tmp_path / "app.db", "autre")
    client.post("/compte/se-connecter", data={"secret": secret})
    assert compte.compte_de_session(tmp_path / "app.db", ancien) is None
    ancien = client.cookies.get(compte.COOKIE_COMPTE)
    assert ancien is not None
    client.post("/compte/creer", data={"pseudo": "troisieme"})
    assert compte.compte_de_session(tmp_path / "app.db", ancien) is None


def test_expiration_exacte_et_compte_absent_invalident_session(tmp_path, monkeypatch):
    create_app(tmp_path)
    database = tmp_path / "app.db"
    identifiant, _ = compte.creer_compte(database, "voyageur")
    jeton, expire = compte.ouvrir_session(database, identifiant)
    monkeypatch.setattr(compte.time, "time", lambda: expire)
    assert compte.compte_de_session(database, jeton) is None
    jeton, _ = compte.ouvrir_session(database, identifiant)
    with sqlite3.connect(database) as db:
        db.execute("DELETE FROM compte WHERE id = ?", (identifiant,))
    assert compte.compte_de_session(database, jeton) is None


def test_connexion_purge_expirees_et_borne_sessions(tmp_path, monkeypatch):
    create_app(tmp_path)
    database = tmp_path / "app.db"
    identifiant, _ = compte.creer_compte(database, "voyageur")
    ancien, expire = compte.ouvrir_session(database, identifiant)
    monkeypatch.setattr(compte.time, "time", lambda: expire + 1)
    for _ in range(15):
        compte.ouvrir_session(database, identifiant)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 10
        assert (
            db.execute(
                "SELECT COUNT(*) FROM session WHERE jeton = ?", (compte.hacher(ancien),)
            ).fetchone()[0]
            == 0
        )


def test_quota_creation_persistant_atomique_et_connexion_encore_possible(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(compte, "CREATIONS_PAR_HEURE", 2)
    create_app(tmp_path)
    _, secret = compte.creer_compte(tmp_path / "app.db", "voyageur")
    navigateurs = [TestClient(create_app(tmp_path)) for _ in range(4)]

    def creer(navigateur):
        return navigateur.post(
            "/compte/creer", data={"pseudo": "nouveau"}, follow_redirects=False
        ).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sorted(pool.map(creer, navigateurs)) == [200, 429, 429, 429]
    # Le quota survit au rechargement du serveur mais ne bloque pas le retour.
    client = TestClient(create_app(tmp_path))
    assert client.post("/compte/creer", data={"pseudo": "encore"}).status_code == 429
    client.post("/compte/se-connecter", data={"secret": secret})
    assert client.cookies.get(compte.COOKIE_COMPTE)


def test_erreur_base_connexion_dit_indisponible(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path))

    def indisponible(*args):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(compte, "compte_de_secret", indisponible)
    assert (
        client.post(
            "/compte/se-connecter", data={"secret": "test"}, follow_redirects=False
        ).status_code
        == 503
    )


@pytest.mark.parametrize("valeur", ["true", "yes", "2", " 1"])
def test_https_invalide_refuse_le_demarrage(tmp_path, monkeypatch, valeur):
    monkeypatch.setenv("COMPTAGEFER_HTTPS", valeur)
    with pytest.raises(ValueError, match="COMPTAGEFER_HTTPS"):
        create_app(tmp_path)


def test_quota_creation_est_une_fenetre_glissante(tmp_path, monkeypatch):
    monkeypatch.setattr(compte, "CREATIONS_PAR_HEURE", 1)
    monkeypatch.setattr(compte.time, "time", lambda: 10000)
    client = TestClient(create_app(tmp_path))
    assert client.post("/compte/creer", data={"pseudo": "premier"}).status_code == 200
    response = client.post("/compte/creer", data={"pseudo": "refuse"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "3600"
    monkeypatch.setattr(compte.time, "time", lambda: 13600)
    assert client.post("/compte/creer", data={"pseudo": "suivant"}).status_code == 200


def test_indexes_installes_sur_base_existante_et_utilises(tmp_path):
    create_app(tmp_path)
    identifiant, secret = compte.creer_compte(tmp_path / "app.db", "voyageur")
    with sqlite3.connect(tmp_path / "app.db") as db:
        for nom in (
            "compte_secret",
            "compte_creation",
            "session_expire",
            "session_compte",
        ):
            db.execute(f"DROP INDEX {nom}")
    create_app(tmp_path)
    assert compte.compte_de_secret(tmp_path / "app.db", secret) == identifiant
    with sqlite3.connect(tmp_path / "app.db") as db:
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM compte WHERE secret = ? LIMIT 1",
            (compte.hacher(compte.normaliser(secret)),),
        ).fetchall()
        assert any("USING INDEX compte_secret" in row[3] for row in plan)


def test_sessions_concurrentes_ne_depassent_pas_le_plafond(tmp_path):
    create_app(tmp_path)
    database = tmp_path / "app.db"
    identifiant, _ = compte.creer_compte(database, "voyageur")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(
            pool.map(lambda _: compte.ouvrir_session(database, identifiant), range(14))
        )
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 10


@pytest.mark.parametrize(
    "chemin,table,operation",
    [
        ("/compte/creer", "compte", "INSERT"),
        ("/compte/creer", "session", "INSERT"),
        ("/compte/creer", "session", "DELETE"),
        ("/compte/se-connecter", "session", "INSERT"),
        ("/compte/se-connecter", "session", "DELETE"),
    ],
)
def test_creation_et_rotation_echouent_sans_ecritures_partielles(
    tmp_path, chemin, table, operation
):
    client = TestClient(create_app(tmp_path), raise_server_exceptions=False)
    database = tmp_path / "app.db"
    identifiant, secret = compte.creer_compte(database, "existant")
    ancien, _ = compte.ouvrir_session(database, identifiant)
    client.cookies.set(compte.COOKIE_COMPTE, ancien)
    with sqlite3.connect(database) as db:
        comptes = db.execute("SELECT * FROM compte").fetchall()
        sessions = db.execute("SELECT * FROM session").fetchall()
        db.execute(
            f"CREATE TRIGGER indisponible BEFORE {operation} ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'indisponible pour le test'); END"
        )
    response = client.post(
        chemin, data={"pseudo": "nouveau", "secret": secret}, follow_redirects=False
    )
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT * FROM compte").fetchall() == comptes
        assert db.execute("SELECT * FROM session").fetchall() == sessions
    assert compte.compte_de_session(database, ancien) == identifiant


def test_remplacer_au_plafond_ne_deconnecte_pas_un_autre_appareil(tmp_path):
    client = TestClient(create_app(tmp_path))
    database = tmp_path / "app.db"
    identifiant, secret = compte.creer_compte(database, "voyageur")
    jetons = [compte.ouvrir_session(database, identifiant)[0] for _ in range(10)]
    client.cookies.set(compte.COOKIE_COMPTE, jetons[-1])
    assert (
        client.post(
            "/compte/se-connecter", data={"secret": secret}, follow_redirects=False
        ).status_code
        == 303
    )
    assert compte.compte_de_session(database, jetons[-1]) is None
    assert all(
        compte.compte_de_session(database, j) == identifiant for j in jetons[:-1]
    )
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 10


def test_erreur_interne_privee_garde_les_entetes(tmp_path):
    app = create_app(tmp_path)

    @app.get("/compte/erreur")
    def erreur():
        raise RuntimeError("échec de test")

    response = TestClient(app, raise_server_exceptions=False).get("/compte/erreur")
    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert "échec de test" not in response.text
