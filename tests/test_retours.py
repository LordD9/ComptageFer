import sqlite3

import pytest
from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer import retours as retours_module


def test_formulaire_public_et_validation_prg_et_stockage(tmp_path):
    client = TestClient(create_app(tmp_path, admin_token="secret"))
    page = client.get("/retours")
    assert page.status_code == 200
    assert "no-store" in page.headers["cache-control"]
    assert "<form" in page.text
    assert '<input id="email" name="email" type="email" maxlength="254" autocomplete="email" value="">' in page.text
    assert "required" not in page.text.split('name="email"', 1)[1].split(">", 1)[0]
    assert 'name="pseudo"' not in page.text.lower()
    reprise = retours_module.formulaire(email='"><img src=x>')
    assert 'value="&quot;&gt;&lt;img src=x&gt;"' in reprise

    assert client.post("/retours", data={"message": "  \n  "}).status_code == 400
    assert client.post("/retours", data={"message": "x" * 5001}).status_code == 400
    message = "  Service <script>alert(1)</script>\nÀ améliorer  "
    submitted = client.post("/retours", data={"message": message}, follow_redirects=False)
    assert submitted.status_code == 303
    assert submitted.headers["location"] == "/retours?envoye=1"
    confirmation = client.get(submitted.headers["location"])
    assert "Votre retour a bien été reçu" in confirmation.text
    assert "no-store" in confirmation.headers["cache-control"]

    with sqlite3.connect(tmp_path / "app.db") as db:
        row = db.execute("SELECT id, message, created_at, traite, email FROM retour").fetchone()
    assert row[0] == 1
    assert row[1] == "Service <script>alert(1)</script>\nÀ améliorer"
    assert row[2] and row[2].endswith("+00:00") and row[3] == 0
    assert row[4] is None


def test_table_retour_migree_idempotemment_sans_alterer_saisie_existante(tmp_path):
    database = tmp_path / "app.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE saisie (client_id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO saisie VALUES ('existant')")
        db.execute(
            """CREATE TABLE retour (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                traite INTEGER NOT NULL DEFAULT 0 CHECK (traite IN (0, 1))
            )"""
        )
        db.execute("INSERT INTO retour VALUES (7, 'retour existant', '2026-10-14T00:00:00+00:00', 0)")
    app = create_app(tmp_path)
    create_app(tmp_path)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT client_id FROM saisie").fetchone() == ("existant",)
        columns = {row[1]: row for row in db.execute("PRAGMA table_info(retour)")}
        assert columns["id"][2].upper() == "INTEGER"
        assert {"message", "created_at", "traite", "email"} <= columns.keys()
        assert db.execute("SELECT id, message, created_at, traite, email FROM retour").fetchone() == (
            7,
            "retour existant",
            "2026-10-14T00:00:00+00:00",
            0,
            None,
        )


def test_admin_retours_authentifie_et_actions_et_pas_de_fuite_publique(tmp_path):
    client = TestClient(create_app(tmp_path, admin_token="secret"))
    client.post("/retours", data={"message": "privé <img src=x onerror=alert(1)>"})
    assert "privé" not in client.get("/comptages").text
    assert "privé" not in client.get("/api/export.csv").text
    assert "privé" not in client.get("/api/sessions").text
    assert "privé" not in client.get("/api/publish").text

    assert client.get("/admin").status_code == 200
    assert "privé" not in client.get("/admin").text
    assert client.post("/admin/login", data={"token": "secret"}).status_code == 200
    panel = client.get("/admin")
    assert "privé &lt;img" in panel.text
    assert "<img" not in panel.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in panel.text
    assert 'white-space:pre-wrap' in panel.text
    refused = client.post(
        "/admin/retours/1/traiter", headers={"Origin": "https://evil.example"}
    )
    assert refused.status_code == 403
    assert "À traiter" in client.get("/admin").text
    assert client.post("/admin/retours/invalide/traiter").status_code == 404
    assert client.post("/admin/retours/99999999999999999999/traiter").status_code == 404
    assert client.post("/admin/retours/1/traiter", follow_redirects=False).status_code == 303
    panel = client.get("/admin")
    assert "Traité" in panel.text
    assert client.post("/admin/retours/1/supprimer", follow_redirects=False).status_code == 303
    assert client.post("/admin/retours/1/supprimer").status_code == 404
    assert client.post("/admin/retours/999/traiter").status_code == 404
    assert "privé" not in client.get("/api/export.csv").text


def test_routes_retours_refusent_session_absente_expiree_et_token_vide(tmp_path, monkeypatch):
    app = create_app(tmp_path, admin_token="secret")
    client = TestClient(app)
    assert client.post("/admin/retours/1/traiter").status_code == 401
    client.post("/admin/login", data={"token": "secret"})
    cookie = client.cookies["comptagefer_admin"]
    client.cookies.set("comptagefer_admin", "invalide")
    assert client.post("/admin/retours/1/traiter").status_code == 401
    client.cookies.set("comptagefer_admin", cookie)
    import time

    maintenant = time.time()
    monkeypatch.setattr("comptagefer.app.time.time", lambda: maintenant + 3601)
    assert client.post("/admin/retours/1/traiter").status_code == 401

    closed = TestClient(create_app(tmp_path / "closed", admin_token=""))
    assert closed.post("/admin/login", data={"token": ""}).status_code == 401


def test_post_retours_refuse_origine_hostile_et_affiche_erreur(tmp_path):
    client = TestClient(create_app(tmp_path))
    refused = client.post("/retours", data={"message": "secret"}, headers={"Origin": "https://evil.example"})
    assert refused.status_code == 403
    with sqlite3.connect(tmp_path / "app.db") as db:
        assert db.execute("SELECT COUNT(*) FROM retour").fetchone() == (0,)

    empty = client.post("/retours", data={"message": ""})
    assert empty.status_code == 400
    assert "Veuillez saisir un message" in empty.text
    assert "no-store" in empty.headers["cache-control"]


def test_quota_global_persiste_apres_suppression_et_refuse_sans_stockage(tmp_path):
    database = tmp_path / "app.db"
    app = create_app(tmp_path, admin_token="secret")
    client = TestClient(app)
    for numero in range(100):
        assert client.post("/retours", data={"message": f"message {numero}"}, follow_redirects=False).status_code == 303
    assert retours_module.supprimer(database, 1)

    restarted = TestClient(create_app(tmp_path, admin_token="secret"))
    refused = restarted.post("/retours", data={"message": "<script>message gardé</script>"})
    assert refused.status_code == 429
    assert refused.headers["retry-after"].isdigit()
    assert "no-store" in refused.headers["cache-control"]
    assert "message gardé" in refused.text
    assert "&lt;script&gt;message gardé&lt;/script&gt;" in refused.text
    assert "Votre retour a bien été reçu" not in refused.text
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM retour").fetchone() == (99,)
        assert db.execute("SELECT COUNT(*) FROM retour_admission").fetchone() == (100,)


def test_quota_glissant_expire_a_une_heure(tmp_path, monkeypatch):
    import comptagefer.retours as retours_module

    horloge = [1_000_000.0]
    monkeypatch.setattr(retours_module.time, "time", lambda: horloge[0])
    client = TestClient(create_app(tmp_path))
    for numero in range(100):
        assert client.post("/retours", data={"message": str(numero)}, follow_redirects=False).status_code == 303
    horloge[0] += 3600
    assert client.post("/retours", data={"message": "Après expiration"}, follow_redirects=False).status_code == 303
    with sqlite3.connect(tmp_path / "app.db") as db:
        assert db.execute("SELECT COUNT(*) FROM retour_admission").fetchone() == (1,)


def test_quota_concurrent_ne_depasse_pas_la_derniere_place(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    database = tmp_path / "app.db"
    client = TestClient(create_app(tmp_path))
    for numero in range(99):
        assert client.post("/retours", data={"message": str(numero)}, follow_redirects=False).status_code == 303

    def envoyer(numero):
        return TestClient(create_app(tmp_path)).post(
            "/retours", data={"message": f"concurrent {numero}"}, follow_redirects=False
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        resultats = list(pool.map(envoyer, range(2)))
    assert sorted(resultats) == [303, 429]
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM retour_admission").fetchone() == (100,)
        assert db.execute("SELECT COUNT(*) FROM retour").fetchone() == (100,)


def test_admin_retours_page_par_pages_de_50_et_parametres_toleres(tmp_path):
    client = TestClient(create_app(tmp_path, admin_token="secret"))
    client.post("/admin/login", data={"token": "secret"})
    for numero in range(51):
        client.post("/retours", data={"message": f"retour-{numero}"})

    premiere = client.get("/admin")
    seconde = client.get("/admin?retours_page=2")
    assert premiere.text.count('class="card retour-admin"') == 50
    assert seconde.text.count('class="card retour-admin"') == 1
    assert "retour-50" in premiere.text and "retour-0" not in premiere.text
    assert "retour-0" in seconde.text and "retour-50" not in seconde.text
    assert "retours_page=2" in premiere.text
    assert "retours_page=1" in seconde.text
    for valeur in ("bad", "-1", "999", "999999999999999999999999999999999999999"):
        page = client.get("/admin", params={"retours_page": valeur})
        assert page.status_code == 200
        assert page.text.count('class="card retour-admin"') == 50


def test_email_contact_est_visible_uniquement_dans_l_admin(tmp_path, caplog):
    database = tmp_path / "app.db"
    client = TestClient(create_app(tmp_path, admin_token="secret"))
    email = "contact@example.org"
    assert retours_module.enregistrer(database, "message privé", email=email) is None

    for url in ("/retours", "/comptages", "/api/export.csv", "/api/sessions", "/api/publish"):
        assert email not in client.get(url).text
    assert email not in client.get("/admin").text

    assert client.post("/admin/login", data={"token": "secret"}).status_code == 200
    assert email in client.get("/admin").text
    with sqlite3.connect(database) as db:
        db.execute("UPDATE retour SET email = ?", ("<img src=x onerror=alert(1)>",))
    panel = client.get("/admin").text
    assert "&lt;img src=x onerror=alert(1)&gt;" in panel
    assert "<img src=x onerror=alert(1)>" not in panel
    assert email not in caplog.text


def test_traiter_efface_definitivement_le_contact_email(tmp_path):
    database = tmp_path / "app.db"
    client = TestClient(create_app(tmp_path, admin_token="secret"))
    email = "contact@example.org"
    assert retours_module.enregistrer(database, "message privé", email=email) is None
    client.post("/admin/login", data={"token": "secret"})
    assert email in client.get("/admin").text

    traite = client.post("/admin/retours/1/traiter", follow_redirects=False)
    assert traite.status_code == 303
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT email, traite FROM retour WHERE id = 1").fetchone() == (None, 1)
    assert email not in client.get("/admin").text


def test_email_contact_est_optionnel_normalise_et_valide_avant_admission(tmp_path):
    database = tmp_path / "app.db"
    create_app(tmp_path)
    assert retours_module.normaliser_email(None) is None
    assert retours_module.normaliser_email(" \t ") is None
    assert retours_module.normaliser_email(" contact@example.org ") == "contact@example.org"

    for email in (
        "pas-une-adresse",
        "deux@@example.org",
        "a..b@example.org",
        "x@localhost",
        f"{'a' * 65}@example.org",
    ):
        with pytest.raises(ValueError):
            retours_module.normaliser_email(email)
    with pytest.raises(ValueError):
        retours_module.enregistrer(database, "message", email="invalide")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM retour").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM retour_admission").fetchone() == (0,)


def test_post_retours_transmet_et_valide_email_sans_le_journaliser(tmp_path, caplog):
    database = tmp_path / "app.db"
    client = TestClient(create_app(tmp_path))
    email = "contact@example.org"
    response = client.post(
        "/retours",
        data={"message": "Réponse souhaitée", "email": f" {email} "},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT message, email FROM retour").fetchone() == (
            "Réponse souhaitée",
            email,
        )
    assert email not in caplog.text

    refuse = client.post("/retours", data={"message": "Sans contact", "email": "invalide"})
    assert refuse.status_code == 400
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM retour").fetchone() == (1,)
