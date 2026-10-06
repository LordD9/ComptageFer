"""Contrat du catalogue étendu rendu et utilisé dans Chromium."""

import json
import urllib.request

import pytest


FORMATIONS = {
    "Regio 2N 6 caisses": 6,
    "Regio 2N 10 caisses": 10,
    "Omneo Premium 8 caisses": 8,
    "Omneo Premium 10 caisses": 10,
    "Coradia Liner 6 caisses": 6,
    "RER NG 6 voitures": 6,
    "RER NG 7 voitures": 7,
    "Z 5600 4 voitures": 4,
    "Z 5600 6 voitures": 6,
    "MI 2N 5 voitures": 5,
}

RECHERCHES_SERIE = {
    "Z 56300": "Regio 2N 6 caisses",
    "Z 56000": "Regio 2N 10 caisses",
    "Z 56700": "Omneo Premium 8 caisses",
    "Z 56800": "Omneo Premium 10 caisses",
    "B 85000": "Coradia Liner 6 caisses",
    "Z 58000": "RER NG 6 voitures",
    "Z 58500": "RER NG 7 voitures",
    "RER C": "Z 5600 4 voitures",
    "Z 22500": "MI 2N 5 voitures",
}


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


@pytest.mark.parametrize(("label", "count"), FORMATIONS.items())
def test_each_new_formation_is_rendered_with_the_expected_us_case_count(page, site, label, count):
    _reach_material(page, site)
    page.click("#compo-US")
    page.click("#materiel-ouvrir")
    page.fill("#materiel-q", label)
    page.click(f"#materiel-list button:has-text('{label}')")
    expected_summary = f"{count} {'caisse' if count == 1 else 'caisses'}" if "caisse" in label else f"{count} {'voiture' if count == 1 else 'voitures'}"
    assert page.text_content("#formation-resume") == expected_summary
    page.click("#materiel-compter")
    assert page.locator(".voiture-box").count() == count


@pytest.mark.parametrize(("query", "label"), RECHERCHES_SERIE.items())
def test_new_formation_can_be_found_by_its_series(page, site, query, label):
    _reach_material(page, site)
    page.click("#materiel-ouvrir")
    page.fill("#materiel-q", query)
    assert page.locator(f"#materiel-list button:has-text('{label}')").count() == 1


def test_regio_2n_six_caisses_draws_um2_twelve_cases_and_stores_the_full_breakdown(page, site):
    _reach_material(page, site)
    page.click("#compo-UM2")
    page.click('.rame-box[data-rame="1"]')
    page.click('.rame-box[data-rame="2"]')
    page.click("#materiel-ouvrir")
    page.fill("#materiel-q", "Regio 2N 6 caisses")
    page.click("#materiel-list button:has-text('Regio 2N 6 caisses')")
    page.click("#materiel-compter")
    assert page.locator('.voiture-box[data-rame="1"]').count() == 6

    for _ in range(12):
        page.click("#plus10")
        page.click("#voiture-next")
    page.click("#form-step summary")
    page.fill("#pseudo", "catalogue-test")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")

    row = _sessions(site)[0]
    assert row["materiel"] == "Regio 2N 6 caisses"
    assert row["composition"] == "UM2"
    assert row["perimetre"] == "voiture"
    assert row["passengers"] == 120
    assert row["rames"] is None
    assert len(row["voitures"]) == 12
    assert [(item["rame"], item["position"], item["passengers"]) for item in row["voitures"]] == [
        (rame, position, 10) for rame in (1, 2) for position in range(1, 7)
    ]
