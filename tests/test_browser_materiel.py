"""Parcours navigateur du comptage par matériel."""

import json
import urllib.request


def _reach_material(page, site):
    page.goto(site)
    page.fill("#origin-q", "Lyon")
    page.click("#origin-list button:has-text('Lyon Part-Dieu')")
    page.fill("#destination-q", "Valence")
    page.click("#destination-list button:has-text('Valence')")
    page.wait_for_selector("#trains button")
    page.click("#trains button:has-text('TER')")
    page.wait_for_selector("#materiel-step:not(.hidden)")


def _sessions(site):
    with urllib.request.urlopen(site + "/api/sessions") as response:
        return json.load(response)


def test_material_composition_car_details_and_total_are_sent(page, site):
    _reach_material(page, site)
    page.fill("#materiel-q", "TER 2N 2")
    page.click("#materiel-list button:has-text('TER 2N 2 voitures')")
    assert "2 voitures" in page.text_content("#formation-resume")
    page.click("#compo-UM2")
    page.click('.rame-box[data-rame="1"]')
    page.click('.rame-box[data-rame="2"]')
    page.click("#materiel-next")
    page.wait_for_selector("#comptage-step:not(.hidden)")
    assert page.locator('.voiture-box[data-rame="1"]').count() == 2

    page.click("#plus10")
    page.click("#voiture-next")
    page.click("#plus20")
    page.click("#voiture-prev")
    page.click("#voiture-next")
    page.click("#plus5")
    assert page.text_content("#count-display") == "25"
    assert page.locator("#voiture-next").text_content() == "Rame suivante"
    page.click("#voiture-next")
    assert page.locator('.voiture-box[data-rame="2"]').count() == 2
    page.click("#plus10")
    page.click("#plus10")
    page.click("#voiture-next")
    page.click("#plus10")
    page.click("#plus10")
    page.click("#plus10")
    page.click("#voiture-next")
    page.click("#form-step summary")
    page.fill("#pseudo", "testeur")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")

    row = _sessions(site)[0]
    assert row["materiel"] == "TER 2N 2 voitures"
    assert row["composition"] == "UM2"
    assert row["perimetre"] == "voiture"
    assert row["passengers"] == 85
    assert row["rames"] is None
    assert row["voitures"] == [
        {"rame": 1, "position": 1, "passengers": 10},
        {"rame": 1, "position": 2, "passengers": 25},
        {"rame": 2, "position": 1, "passengers": 20},
        {"rame": 2, "position": 2, "passengers": 30},
    ]


def test_counter_has_twenty_minus_ten_and_free_entry(page, site):
    _reach_material(page, site)
    page.click("#materiel-clear")
    page.click("#compo-none")
    page.click("#materiel-next")
    page.click("#mode-unique")
    page.click("#plus20")
    page.click("#minus10")
    assert page.text_content("#count-display") == "10"
    page.fill("#passengers", "37")
    page.dispatch_event("#passengers", "input")
    assert page.text_content("#count-display") == "37"
    assert page.input_value("#passengers") == "37"
    page.click("#count-next")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")
    assert _sessions(site)[0]["passengers"] == 37


def test_rame_mode_records_each_selected_rame_and_total(page, site):
    _reach_material(page, site)
    page.click("#materiel-clear")
    page.click("#compo-UM2")
    page.click('.rame-box[data-rame="1"]')
    page.click('.rame-box[data-rame="2"]')
    page.click("#materiel-next")
    assert page.locator("#mode-rame").is_visible()
    page.click("#mode-rame")
    page.click("#plus20")
    page.click("#plus20")
    page.click("#plus5")
    page.click("#count-next")
    page.click("#plus20")
    page.click("#count-next")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")

    row = _sessions(site)[0]
    assert row["perimetre"] == "um"
    assert row["passengers"] == 65
    assert row["rames"] == [
        {"rame": 1, "passengers": 45},
        {"rame": 2, "passengers": 20},
    ]
    assert row["voitures"] is None


def test_each_counting_step_fits_a_phone_viewport(page, site):
    """Un écran qui déborde sur téléphone est un défaut, et il ne se voit pas dans le HTML."""
    page.set_viewport_size({"width": 390, "height": 844})

    def pas_de_debordement() -> int:
        return page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )

    _reach_material(page, site)
    assert pas_de_debordement() == 0, "l'étape matériel déborde"

    # Le schéma des voitures : c'est la nouvelle étape, et c'est elle qui a le
    # plus de cases à faire tenir sur 390 px.
    page.fill("#materiel-q", "TER 2N 2")
    page.click("#materiel-list button:has-text('TER 2N 2 voitures')")
    page.click("#compo-UM3")
    page.click("#rames-tout")
    page.click("#materiel-next")
    assert pas_de_debordement() == 0, "le schéma des voitures déborde"

    # Et le comptage sans matériel, avec sa liste de formations dépliée.
    page.click("#change-train-count")
    page.wait_for_selector("#train-step:not(.hidden)")
    page.click("#trains button:has-text('TER')")
    page.click("#materiel-clear")
    assert pas_de_debordement() == 0, "la liste des formations déborde"
    page.click("#materiel-next")
    page.click("#mode-unique")
    assert pas_de_debordement() == 0, "le comptage unique déborde"


def test_rame_mode_is_hidden_for_a_us(page, site):

    _reach_material(page, site)
    page.click("#materiel-clear")
    page.click("#compo-US")
    page.click("#materiel-next")
    assert page.locator("#mode-rame").is_hidden()
