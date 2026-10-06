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
    page.click("#compo-UM2")
    page.click('.rame-box[data-rame="1"]')
    page.click('.rame-box[data-rame="2"]')
    page.click("#materiel-ouvrir")
    page.fill("#materiel-q", "TER 2N 2")
    page.click("#materiel-list button:has-text('TER 2N 2 voitures')")
    assert "2 voitures" in page.text_content("#formation-resume")
    page.click("#materiel-compter")
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
    page.click("#compo-UM3")
    page.click("#rames-tout")
    page.click("#materiel-ouvrir")
    page.fill("#materiel-q", "TER 2N 2")
    page.click("#materiel-list button:has-text('TER 2N 2 voitures')")
    assert pas_de_debordement() == 0, "le bloc matériel déborde"
    page.click("#materiel-compter")
    assert pas_de_debordement() == 0, "le schéma des voitures déborde"

    # Et le comptage sans matériel, avec sa liste de formations dépliée.
    page.click("#change-train-count")
    page.wait_for_selector("#train-step:not(.hidden)")
    page.click("#trains button:has-text('TER')")
    page.click("#materiel-clear")
    assert pas_de_debordement() == 0, "la liste des formations déborde"
    page.click("#compo-none")
    page.click("#materiel-next")
    page.click("#mode-unique")
    assert pas_de_debordement() == 0, "le comptage unique déborde"


def test_rame_mode_is_hidden_for_a_us(page, site):

    _reach_material(page, site)
    page.click("#compo-US")
    page.click("#materiel-next")
    assert page.locator("#mode-rame").is_hidden()


def _compter_quatre_voitures(page):
    for _ in range(4):
        page.click("#plus10")
        page.click("#voiture-next")


def test_the_mandatory_part_comes_first_and_the_material_is_folded(page, site):
    """La composition et les rames précèdent le matériel, qui reste replié."""
    _reach_material(page, site)
    ordre = page.evaluate(
        """() => {
            const pos = (id) => [...document.querySelectorAll('#materiel-step *')]
                .indexOf(document.getElementById(id));
            const bloc = document.getElementById('materiel-bloc');
            const avant = (id) => !!(document.getElementById(id).compareDocumentPosition(bloc)
                & Node.DOCUMENT_POSITION_FOLLOWING);
            return {composition: avant('composition-choices'), next: avant('materiel-next')};
        }"""
    )
    assert ordre == {"composition": True, "next": True}
    assert page.locator("#materiel-bloc").is_hidden()
    assert page.locator("#materiel-q").is_hidden()
    page.click("#materiel-ouvrir")
    assert page.locator("#materiel-bloc").is_visible()
    assert page.locator("#materiel-q").is_visible()


def test_the_scope_is_never_asked(page, site):
    """On compte toujours une rame entière : la question n'existe plus."""
    _reach_material(page, site)
    page.click("#compo-UM2")
    assert page.locator("#perimetre").count() == 0


def test_a_single_rame_of_a_um_is_sent_as_a_whole_rame(page, site):
    """Une rame d'une UM2, comptée seule : composition UM2, périmètre um."""
    _reach_material(page, site)
    page.click("#compo-UM2")
    page.click('.rame-box[data-rame="1"]')
    page.click("#materiel-next")
    assert page.locator("#mode-rame").is_hidden()
    page.click("#plus20")
    page.click("#count-next")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")
    ligne = _sessions(site)[0]
    assert (ligne["composition"], ligne["perimetre"], ligne["passengers"]) == ("UM2", "um", 20)


def test_a_us_is_sent_as_a_whole_rame(page, site):
    _reach_material(page, site)
    page.click("#compo-US")
    page.click("#materiel-next")
    page.click("#plus10")
    page.click("#count-next")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")
    ligne = _sessions(site)[0]
    assert (ligne["composition"], ligne["perimetre"]) == ("US", "um")


def test_one_can_go_straight_to_counting_without_material(page, site):
    _reach_material(page, site)
    page.click("#compo-UM2")
    page.click('.rame-box[data-rame="1"]')
    page.click('.rame-box[data-rame="2"]')
    page.click("#materiel-next")
    page.wait_for_selector("#comptage-step:not(.hidden)")
    page.click("#mode-rame")
    page.click("#plus20")
    page.click("#count-next")
    page.click("#plus10")
    page.click("#count-next")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")

    ligne = _sessions(site)[0]
    assert ligne["composition"] == "UM2"
    assert ligne["perimetre"] == "um"
    assert ligne["materiel"] in (None, "")
    assert ligne["voitures"] is None
    assert ligne["rames"] == [{"rame": 1, "passengers": 20}, {"rame": 2, "passengers": 10}]


def test_a_composition_without_a_rame_keeps_the_step(page, site):
    _reach_material(page, site)
    page.click("#compo-UM2")
    page.click("#materiel-next")
    assert page.locator("#materiel-step").is_visible()
    assert page.locator("#comptage-step").is_hidden()
    assert page.locator("#rames-error").is_visible()
    assert "rame" in page.text_content("#rames-error")


def test_a_material_without_a_composition_keeps_the_step(page, site):
    _reach_material(page, site)
    page.click("#materiel-ouvrir")
    page.fill("#materiel-q", "TER 2N 2")
    page.click("#materiel-list button:has-text('TER 2N 2 voitures')")
    page.click("#compo-none")
    page.click("#materiel-compter")
    assert page.locator("#comptage-step").is_hidden()
    assert page.locator("#rames-error").is_visible()


def test_i_do_not_know_the_composition_goes_straight_to_a_single_count(page, site):
    _reach_material(page, site)
    page.click("#compo-none")
    page.click("#materiel-next")
    page.wait_for_selector("#comptage-step:not(.hidden)")
    page.click("#mode-unique")
    page.click("#plus10")
    page.click("#count-next")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")
    ligne = _sessions(site)[0]
    assert ligne["composition"] is None and ligne["perimetre"] is None


def _queue(page):
    return page.evaluate("() => JSON.parse(localStorage.getItem('comptagefer-queue') || '[]')")


def _attendre_file_vide(page):
    page.wait_for_function(
        "() => JSON.parse(localStorage.getItem('comptagefer-queue') || '[]').length === 0",
        timeout=10000,
    )


def _suivre_le_reseau(page):
    """Les requêtes qui échouent et les erreurs de console, hors /api/sessions."""
    echecs: list[str] = []
    console: list[str] = []
    page.on("requestfailed", lambda req: echecs.append(req.url))
    page.on("console", lambda msg: console.append(msg.text) if msg.type == "error" else None)
    return echecs, console


def _revenir_en_ligne(page):
    page.context.set_offline(False)
    page.evaluate("() => window.dispatchEvent(new Event('online'))")
    _attendre_file_vide(page)


def test_a_full_count_with_material_works_offline_and_is_sent_once_back_online(page, site):
    """Le comptage complet ne demande aucun réseau après le choix du train."""
    _reach_material(page, site)
    echecs, console = _suivre_le_reseau(page)
    page.context.set_offline(True)

    page.click("#compo-UM2")
    page.click('.rame-box[data-rame="1"]')
    page.click('.rame-box[data-rame="2"]')
    page.click("#materiel-ouvrir")
    page.fill("#materiel-q", "TER 2N 2")
    page.click("#materiel-list button:has-text('TER 2N 2 voitures')")
    page.click("#materiel-compter")
    page.wait_for_selector("#comptage-step:not(.hidden)")
    _compter_quatre_voitures(page)
    page.click("#form-step summary")
    page.fill("#pseudo", "horsligne")
    page.fill("#comment", "tunnel")
    page.click("#send")
    page.wait_for_function("() => document.getElementById('error').textContent.includes('Pas de réseau')")

    assert page.locator("#done-step").is_hidden()
    assert "Pas de réseau" in page.text_content("#error")
    file = _queue(page)
    assert len(file) == 1
    corps = file[0]
    assert corps["materiel"] == "TER 2N 2 voitures"
    assert corps["composition"] == "UM2"
    assert corps["perimetre"] == "voiture"
    assert corps["passengers"] == 40
    assert len(corps["voitures"]) == 4
    assert corps["snapshot"]
    assert corps["pseudo"] == "horsligne"
    assert [u for u in echecs if "/api/sessions" not in u] == []
    assert [c for c in console if "/api/sessions" not in c and "ERR_INTERNET_DISCONNECTED" not in c] == []
    assert page.errors == []
    assert _sessions(site) == []

    _revenir_en_ligne(page)
    lignes = _sessions(site)
    assert len(lignes) == 1
    ligne = lignes[0]
    assert ligne["passengers"] == 40
    assert len(ligne["voitures"]) == 4
    assert ligne["materiel"] == "TER 2N 2 voitures"
    assert ligne["composition"] == "UM2"
    assert ligne["pseudo"] == "horsligne"


def test_a_direct_count_without_material_works_offline_and_is_sent_once(page, site):
    _reach_material(page, site)
    echecs, console = _suivre_le_reseau(page)
    page.context.set_offline(True)

    page.click("#compo-UM2")
    page.click("#rames-tout")
    page.click("#materiel-next")
    page.click("#mode-rame")
    page.click("#plus20")
    page.click("#count-next")
    page.click("#plus10")
    page.click("#count-next")
    page.click("#form-step summary")
    page.fill("#pseudo", "direct")
    page.fill("#comment", "sans matériel")
    page.click("#send")
    page.wait_for_function("() => document.getElementById('error').textContent.includes('Pas de réseau')")

    assert page.locator("#done-step").is_hidden()
    file = _queue(page)
    assert len(file) == 1
    corps = file[0]
    assert corps["composition"] == "UM2" and corps["perimetre"] == "um"
    assert corps["materiel"] == ""
    assert corps["passengers"] == 30
    assert corps["rames"] == [{"rame": 1, "passengers": 20}, {"rame": 2, "passengers": 10}]
    assert corps["snapshot"]
    assert [u for u in echecs if "/api/sessions" not in u] == []
    assert [c for c in console if "/api/sessions" not in c and "ERR_INTERNET_DISCONNECTED" not in c] == []
    assert page.errors == []

    _revenir_en_ligne(page)
    lignes = _sessions(site)
    assert len(lignes) == 1
    assert lignes[0]["passengers"] == 30
    assert lignes[0]["composition"] == "UM2" and lignes[0]["perimetre"] == "um"
    assert lignes[0]["rames"] == corps["rames"]
    assert lignes[0]["pseudo"] == "direct"
