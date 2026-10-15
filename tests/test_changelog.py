from comptagefer.changelog import changelog_page
from comptagefer.app import create_app
from comptagefer import retours
from fastapi.testclient import TestClient


def test_route_changelog_publique_sans_donnees_privees(tmp_path):
    database = tmp_path / "app.db"
    app = create_app(tmp_path, admin_token="test-admin")
    retours.enregistrer(database, "Message confidentiel de test", "prive@example.org")
    with TestClient(app) as client:
        response = client.get("/changelog")
    assert response.status_code == 200
    assert "<h1>Changelog</h1>" in response.text
    assert "Message confidentiel de test" not in response.text
    assert "prive@example.org" not in response.text


def test_page_changelog_est_rendue_avec_le_chrome_commun():
    page = changelog_page()

    assert page.startswith("<!doctype html>")
    assert "<title>Changelog — ComptagesFer</title>" in page
    assert "<h1>Changelog</h1>" in page


def test_navigation_changelog_est_active():
    page = changelog_page()

    assert 'href="/changelog" aria-current="page">Changelog</a>' in page


def test_date_de_livraison_est_machine_lisible():
    page = changelog_page()

    assert "<time datetime='2026-10-15'>15 octobre 2026</time>" in page


def test_les_categories_sont_presentees():
    page = changelog_page()

    assert "<h2>Améliorations</h2>" in page
    assert "<h2>Corrections</h2>" in page


def test_les_changements_et_correctifs_sont_associes_a_leurs_dates():
    page = changelog_page()

    assert "L’administration est organisée en onglets" in page
    assert "Un journal public présente les évolutions du site" in page
    assert "<time datetime='2026-10-14'>14 octobre 2026</time>" in page
    assert "alias de gares explicitement vérifiés" in page
    assert "sens de marche du schéma SVG des rames" in page


def test_le_changelog_public_ne_divulgue_pas_de_contact_ou_retour_prive():
    page = changelog_page()
    contenu = page.split("<main>", 1)[1].split("</main>", 1)[0].casefold()

    assert "contact" not in contenu
    assert "retour" not in contenu
    assert "@" not in contenu
