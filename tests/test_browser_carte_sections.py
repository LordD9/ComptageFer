"""Interactions de section exercées dans Chromium avec le vrai Leaflet."""

import json
import urllib.request


def _releve(site: str, jeton: str, origine: str, destination: str, voyageurs: int) -> None:
    corps = {
        "client_id": jeton,
        "origin_stop_id": f"StopArea:{origine}",
        "destination_stop_id": f"StopArea:{destination}",
        "origin_name": origine,
        "destination_name": destination,
        "trip_id": "TER",
        "passengers": voyageurs,
        "reliability": 70,
        "pseudo": jeton,
        "snapshot": {"precedent": None, "courant": {"trip_id": "TER", "status": "SCHEDULED", "delay_seconds": 0}, "suivant": None},
    }
    requete = urllib.request.Request(
        site + "/api/sessions",
        data=json.dumps(corps).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(requete) as response:
        assert response.status == 200


def _ouvre_carte(page, site: str) -> None:
    page.set_default_timeout(15000)
    page.set_default_navigation_timeout(15000)
    page.route("**/*.png", lambda route: route.abort())
    page.goto(site + "/carte", wait_until="domcontentloaded")
    page.wait_for_selector("#carte.leaflet-container")
    page.wait_for_selector("path.section-carte")


def test_section_ouvre_les_quatre_comptages_et_zoom_molette(page, site):
    """Les sens opposés sont regroupés et le panneau ne tronque pas à trois."""
    for index in range(4):
        if index % 2:
            _releve(site, f"section-{index}", "Vienne", "Lyon", 30 + index)
        else:
            _releve(site, f"section-{index}", "Lyon", "Vienne", 30 + index)
    _ouvre_carte(page, site)
    marqueur = page.locator('path[fill-opacity="0.6"]').first
    assert "leaflet-interactive" not in (marqueur.get_attribute("class") or "")

    avant = page.locator("path.section-carte").first.get_attribute("d")
    boite = page.locator("#carte").bounding_box()
    page.mouse.move(boite["x"] + boite["width"] / 2, boite["y"] + boite["height"] / 2)
    page.mouse.wheel(0, -500)
    page.wait_for_timeout(600)
    apres = page.locator("path.section-carte").first.get_attribute("d")
    assert apres != avant, "la molette doit réellement modifier le zoom Leaflet"

    page.locator("path.section-carte").first.hover(force=True)
    page.wait_for_selector(".leaflet-tooltip")
    assert "4 comptages" in page.locator(".leaflet-tooltip").inner_text()
    page.locator("path.section-carte").first.click(force=True)
    page.wait_for_function("() => !document.querySelector('#section-comptages').hidden")
    assert page.locator("#section-comptages li").count() == 4
    assert page.locator("#section-comptages h2").inner_text() == "4 comptages sur cette section"
    assert {"section-0", "section-1", "section-2", "section-3"}.issubset(
        set(page.locator("#section-comptages").inner_text().split())
    )
    assert page.locator(".leaflet-popup").count() == 0, "les marqueurs de gare ne sont pas interactifs"


def test_section_detaille_jusqua_trois_et_echappe_les_noms(page, site):
    _releve(site, "<script>alert(1)</script>", "Lyon", "Vienne", 42)
    _releve(site, "section-deux", "Vienne", "Lyon", 33)
    _releve(site, "section-trois", "Lyon", "Vienne", 27)
    _ouvre_carte(page, site)

    page.locator("path.section-carte").first.hover(force=True)
    page.wait_for_selector(".leaflet-tooltip")
    contenu = page.locator(".leaflet-tooltip").inner_text()
    assert "sans effectif" not in contenu
    assert "Lyon" in contenu and "Vienne" in contenu
    assert page.locator(".leaflet-tooltip script").count() == 0
    page.locator("path.section-carte").first.click(force=True)
    assert page.locator("#section-comptages li").count() == 3
    assert "<script>alert(1)</script>" in page.locator("#section-comptages").inner_text()
    assert page.locator("#section-comptages script").count() == 0


def test_section_liste_tous_les_comptages_meme_si_la_liste_globale_est_absente(page, site):
    for index in range(31):
        origine, destination = ("Vienne", "Lyon") if index % 2 else ("Lyon", "Vienne")
        _releve(site, f"plus-trente-{index}", origine, destination, index + 1)
    page.set_viewport_size({"width": 390, "height": 844})
    _ouvre_carte(page, site)

    assert page.locator(".liste-comptages").count() == 0
    page.locator("path.section-carte").first.click(force=True)
    page.wait_for_function("() => !document.querySelector('#section-comptages').hidden")
    assert page.locator("#section-comptages li").count() == 31
    assert "plus-trente-30" in page.locator("#section-comptages").inner_text()
    carte = page.locator("#carte").bounding_box()
    panneau = page.locator("#section-comptages").bounding_box()
    assert panneau["y"] >= carte["y"] + carte["height"]


def test_section_remplace_la_liste_laterale_et_ouvre_le_releve(page, site):
    """La sélection garde une seule liste à droite, jamais une deuxième ligne sous la carte."""
    _releve(site, "detail-section", "Lyon", "Vienne", 42)
    page.set_viewport_size({"width": 1440, "height": 1000})
    _ouvre_carte(page, site)
    globale = page.locator(".liste-comptages")
    assert globale.is_visible()
    page.locator("path.section-carte").first.click(force=True)
    panneau = page.locator("#section-comptages")
    assert panneau.is_visible()
    assert not globale.is_visible()
    carte = page.locator("#carte").bounding_box()
    boite = panneau.bounding_box()
    assert boite["x"] >= carte["x"] + carte["width"]
    assert abs(boite["y"] - carte["y"]) < 2
    lien = panneau.locator("a[href^='/releve?']")
    assert lien.count() == 1
    with page.expect_navigation():
        lien.click()
    assert "client_id=detail-section" in page.url and "kind=count" in page.url
    assert "42" in page.locator("main").inner_text()
    assert not page.errors
