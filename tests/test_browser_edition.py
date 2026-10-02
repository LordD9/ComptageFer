import json
import sqlite3

import pytest


def _compte(page, site):
    page.goto(site + "/compte")
    page.fill("#pseudo", "navigatrice")
    with page.expect_navigation():
        page.click("button:has-text('Créer mon compte')")
    page.wait_for_selector("#secret-texte")
    page.goto(site + "/compte")
    page.wait_for_selector("h1:has-text('Vos releves')")


def _count(page, site, client_id="browser-key"):
    response = page.request.post(site + "/api/sessions", data={
        "client_id": client_id, "origin_stop_id": "A", "destination_stop_id": "B",
        "origin_name": "Lyon", "destination_name": "Vienne", "passengers": 10,
        "reliability": 80,
    }, headers={"Content-Type": "application/json"})
    assert response.status == 200


def test_owner_edits_and_explicitly_confirms_deletion_in_browser(page, site, base_du_site):
    _compte(page, site)
    _count(page, site)
    page.reload()
    page.wait_for_selector("a:has-text('Modifier')")
    page.click("a:has-text('Modifier')")
    page.wait_for_selector("form[action='/compte/modifier']")
    assert "contexte figé" in page.content()
    page.fill("input[name='passengers']", "29")
    with page.expect_navigation():
        page.click("button:has-text('Enregistrer')")
    page.wait_for_selector("h1:has-text('Vos releves')")
    with sqlite3.connect(base_du_site) as db:
        assert db.execute("SELECT passengers FROM saisie WHERE client_id='browser-key'").fetchone()[0] == 29
    form = page.locator("form[action='/compte/supprimer']")
    checkbox = form.locator("input[name='confirmation']")
    assert checkbox.is_visible() and checkbox.get_attribute("required") is not None
    checkbox.check()
    with page.expect_navigation():
        form.locator("button[type='submit']").click()
    with sqlite3.connect(base_du_site) as db:
        assert db.execute("SELECT COUNT(*) FROM saisie WHERE client_id='browser-key'").fetchone()[0] == 0


def test_serpent_editor_uses_stop_fields_and_keeps_route_fixed(page, site, base_du_site):
    _compte(page, site)
    response = page.request.post(site + "/api/sessions", data={
        "client_id": "browser-serpent", "kind": "serpent", "origin_stop_id": "A",
        "destination_stop_id": "C", "origin_name": "Lyon", "destination_name": "Vienne",
        "reliability": 70, "legs": [
            {"stop_id": "A", "stop_name": "Lyon", "onboard": 10},
            {"stop_id": "B", "stop_name": "Valence", "boarded": 2, "alighted": 1},
            {"stop_id": "C", "stop_name": "Vienne", "boarded": 0, "alighted": 3},
        ],
    }, headers={"Content-Type": "application/json"})
    assert response.status == 200
    page.reload()
    page.locator("a:has-text('Modifier')").click()
    page.wait_for_selector("form[action='/compte/modifier'] input[name='legs_0_onboard']")
    assert page.locator("textarea[name='legs']").count() == 0
    assert page.locator("text=Valence · arrêt inchangé").is_visible()
    page.fill("input[name='legs_0_onboard']", "14")
    page.fill("input[name='legs_1_boarded']", "5")
    page.fill("input[name='legs_1_alighted']", "2")
    with page.expect_navigation():
        page.click("button:has-text('Enregistrer')")
    with sqlite3.connect(base_du_site) as db:
        row = db.execute("SELECT passengers, legs FROM saisie WHERE client_id='browser-serpent'").fetchone()
    assert row[0] == 14
    legs = json.loads(row[1])
    assert [(leg["stop_id"], leg["stop_name"]) for leg in legs] == [
        ("A", "Lyon"), ("B", "Valence"), ("C", "Vienne")]
    assert legs[1]["boarded"] == 5 and legs[1]["alighted"] == 2


def test_admin_edits_and_can_remove_unowned_entry_in_browser(page, site, base_du_site):
    page.request.post(site + "/api/sessions", data={
        "client_id": "admin-browser-key", "origin_stop_id": "A", "destination_stop_id": "B",
        "origin_name": "Lyon", "destination_name": "Vienne", "passengers": 10,
        "reliability": 80,
    }, headers={"Content-Type": "application/json"})
    page.goto(site + "/admin")
    page.fill("input[name='token']", "jeton-admin-test")
    with page.expect_navigation():
        page.click("button:has-text('Ouvrir')")
    page.wait_for_selector("h1:has-text('Admin')")
    page.click("form[action='/admin/modifier'] button")
    page.wait_for_selector("form[action='/admin/modifier'] input[name='passengers']")
    page.fill("input[name='passengers']", "41")
    with page.expect_navigation():
        page.click("button:has-text('Enregistrer')")
    with sqlite3.connect(base_du_site) as db:
        assert db.execute("SELECT passengers, compte_id FROM saisie WHERE client_id='admin-browser-key'").fetchone() == (41, None)
    page.locator("form[action='/admin/supprimer'] button").click()
    with sqlite3.connect(base_du_site) as db:
        assert db.execute("SELECT COUNT(*) FROM saisie WHERE client_id='admin-browser-key'").fetchone()[0] == 0


@pytest.mark.parametrize("largeur", [390, 1440])
def test_edition_indique_les_pourcentages_et_garde_les_champs_lisibles(page, site, largeur):
    page.set_viewport_size({"width": largeur, "height": 1000})
    _compte(page, site)
    _count(page, site)
    page.reload()
    page.locator("a:has-text('Modifier')").click()
    for name in ("reliability", "standing", "seats_free", "imbalance"):
        champ = page.locator(f"input[name='{name}']")
        assert "%" in champ.locator("..").inner_text()
        assert champ.get_attribute("max") == "100"
    champs = page.locator("form input:not([type='hidden']), form textarea, form select")
    for index in range(champs.count()):
        assert champs.nth(index).bounding_box()["width"] >= min(250, largeur - 80)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not page.errors
