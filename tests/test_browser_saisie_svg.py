"""Parcours navigateur des rames SVG et du serpent avec matériel."""

import json


def _reach_material(page, site):
    page.goto(site)
    page.fill("#origin-q", "Lyon")
    page.click("#origin-list button:has-text('Lyon Part-Dieu')")
    page.fill("#destination-q", "Valence")
    page.click("#destination-list button:has-text('Valence')")
    page.wait_for_selector("#trains button")
    page.click("#trains button:has-text('TER')")
    page.wait_for_selector("#materiel-step:not(.hidden)")


def _material(page):
    page.click("#materiel-ouvrir")
    page.fill("#materiel-q", "TER 2N 2")
    page.click("#materiel-list button:has-text('TER 2N 2 voitures')")


def test_rame_svg_selection_is_accessible_and_responsive(page, site):
    _reach_material(page, site)
    page.click("#compo-US")
    assert page.locator('.rame-box[data-rame="1"]').get_attribute("aria-pressed") == "true"

    page.click("#compo-UM3")
    middle = page.locator('.rame-box[data-rame="2"]')
    assert middle.get_attribute("data-rame") == "2"
    assert "position centrale" in middle.get_attribute("aria-label")
    assert "milieu du train" in middle.text_content()
    assert middle.locator("svg").count() == 1
    middle.focus()
    page.keyboard.press("Space")
    assert page.locator('.rame-box[data-rame="2"]').get_attribute("aria-pressed") == "true"
    assert page.locator('.rame-box[data-rame="2"]').get_attribute("aria-label").endswith("sélectionnée")
    assert page.locator('.rame-box[data-rame="2"]').evaluate("el => el === document.activeElement")
    page.keyboard.press("Space")
    assert page.locator('.rame-box[data-rame="2"]').get_attribute("aria-pressed") == "false"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.errors == []


def test_material_car_total_prefills_snake_onboard_and_offline_payload(page, site):
    _reach_material(page, site)
    page.click("#compo-UM2")
    page.click('.rame-box[data-rame="1"]')
    page.click('.rame-box[data-rame="2"]')
    _material(page)
    page.click("#materiel-next")
    assert not page.locator("#counter").is_visible()
    page.click("#mode-snake")
    page.wait_for_selector("#voitures-schema:not(.hidden)")
    assert page.locator("#counter").is_visible()
    for count in (10, 20, 30, 40):
        page.fill("#passengers", str(count))
        page.dispatch_event("#passengers", "input")
        page.click("#voiture-next")
    page.wait_for_selector("#snake-onboard")
    assert page.input_value("#snake-onboard") == "100"

    # Les arrêts du trajet sont chargés en ligne avant le parcours hors réseau.
    page.context.set_offline(True)
    page.click("#snake-next")
    page.wait_for_selector("#snake-boarded")
    page.fill("#snake-boarded", "5")
    page.fill("#snake-alighted", "2")
    page.click("#snake-next")
    page.wait_for_function("() => document.getElementById('snake-title').textContent.includes('Enregistrer') || document.getElementById('snake-reliability')")
    if page.locator("#snake-reliability").count() == 0:
        # Les éventuels arrêts intermédiaires conservent les champs montées/descentes.
        while page.locator("#snake-reliability").count() == 0:
            page.fill("#snake-boarded", "0")
            page.fill("#snake-alighted", "0")
            page.click("#snake-next")
    page.fill("#snake-boarded", "0")
    if page.locator("#snake-alighted").count():
        page.fill("#snake-alighted", "0")
    page.click("#snake-next")
    page.wait_for_function("() => document.getElementById('snake-error').textContent.length > 0")
    assert "Pas de réseau" in page.text_content("#snake-error")
    queued = json.loads(page.evaluate("localStorage.getItem('comptagefer-queue')"))
    assert len(queued) == 1
    assert queued[0]["kind"] == "serpent"
    assert queued[0]["passengers"] == 100
    assert queued[0]["legs"][0]["onboard"] == 100
    assert queued[0]["legs"][1]["boarded"] == 5
    client_id = queued[0]["client_id"]
    page.context.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")
    page.wait_for_function("() => JSON.parse(localStorage.getItem('comptagefer-queue') || '[]').length === 0")
    rows = page.request.get(site + "/api/sessions").json()
    stored = [row for row in rows if row["client_id"] == client_id]
    assert len(stored) == 1
    assert stored[0]["passengers"] == 100
    assert stored[0]["legs"][0]["onboard"] == 100
    assert page.errors == []


def test_serpent_without_material_keeps_manual_onboard_and_resume(page, site):
    _reach_material(page, site)
    page.click("#compo-none")
    page.click("#materiel-next")
    page.click("#mode-snake")
    page.wait_for_selector("#snake-onboard")
    page.fill("#snake-onboard", "17")
    page.click("#snake-next")
    page.wait_for_selector("#snake-boarded")

    page.reload()
    page.wait_for_selector("#snake-step:not(.hidden)")
    assert "Reprise sur ce téléphone" in page.text_content("#snake-resume")
    assert page.locator("#snake-onboard").count() == 0
    assert page.locator("#snake-boarded").is_visible()
    assert page.errors == []
