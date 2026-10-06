"""L'autocomplétion des champs gare de /comptages, dans un vrai navigateur."""


def _espionner(page) -> list[str]:
    """Les URL de `/api/stops` demandées par la page, dans l'ordre."""
    demandes: list[str] = []
    page.on(
        "request",
        lambda requete: demandes.append(requete.url) if "/api/stops?" in requete.url else None,
    )
    return demandes


def _options(page, liste: str) -> list[str]:
    return page.eval_on_selector_all(
        f"#{liste} option", "(options) => options.map((o) => o.value)"
    )


def test_le_champ_gare_propose_des_gares_pendant_la_frappe(page, site):
    demandes = _espionner(page)
    page.goto(site + "/comptages")

    page.fill("#gare", "v")
    page.wait_for_timeout(300)
    assert demandes == [], "un seul caractère ne lance aucune recherche"

    page.fill("#gare", "va")
    page.wait_for_function("document.querySelectorAll('#gare-liste option').length > 0")
    assert _options(page, "gare-liste") == ["Valence"]
    assert demandes[-1].endswith("/api/stops?q=va"), demandes[-1]
    assert page.get_attribute("#gare", "list") == "gare-liste"
    assert page.errors == []


def test_a_partir_de_cinq_caracteres_la_liste_complete_est_demandee(page, site):
    page.goto(site + "/comptages")

    with page.expect_request(lambda r: "/api/stops?q=vale" in r.url) as courte:
        page.fill("#gare", "vale")
    assert "tout=1" not in courte.value.url, "quatre caractères : liste courte"

    with page.expect_request(lambda r: "/api/stops?q=valen" in r.url) as complete:
        page.fill("#gare", "valen")
    assert complete.value.url.endswith("/api/stops?q=valen&tout=1")
    page.wait_for_function(
        "document.querySelector('#gare-liste option') && "
        "document.querySelector('#gare-liste option').value === 'Valence'"
    )


def test_la_recherche_est_insensible_aux_accents_et_le_second_champ_aussi(page, site):
    page.goto(site + "/comptages")

    page.fill("#gare2", "LYON PART")
    page.wait_for_function("document.querySelectorAll('#gare2-liste option').length > 0")

    assert _options(page, "gare2-liste") == ["Lyon Part-Dieu"]
    assert _options(page, "gare-liste") == [], "chaque champ a sa propre liste"


def test_la_liste_se_vide_sous_deux_caracteres(page, site):
    page.goto(site + "/comptages")
    page.fill("#gare", "val")
    page.wait_for_function("document.querySelectorAll('#gare-liste option').length > 0")

    page.fill("#gare", "v")

    page.wait_for_function("document.querySelectorAll('#gare-liste option').length === 0")


def test_une_paire_saisie_en_minuscules_filtre_et_affiche_les_noms_canoniques(page, site):
    page.goto(site + "/comptages")
    page.fill("#gare", "lyon part-dieu")
    page.fill("#gare2", "VALENCE")

    page.click("form.filtres button[type='submit']")
    page.wait_for_selector("form.filtres")

    assert "gare2=" in page.url
    chip = page.locator(".chip").first.text_content()
    assert "Lyon Part-Dieu" in chip and "Valence" in chip, chip
    assert page.input_value("#gare") == "Lyon Part-Dieu"
    assert page.input_value("#gare2") == "Valence"

    page.click("a.retirer")
    page.wait_for_selector("form.filtres")
    assert "gare" not in page.url
    assert page.input_value("#gare") == "" and page.input_value("#gare2") == ""


def test_sans_javascript_le_champ_reste_un_texte_libre(navigateur_partage, site):
    contexte = navigateur_partage.new_context(java_script_enabled=False)
    try:
        une_page = contexte.new_page()
        une_page.goto(site + "/comptages")
        une_page.fill("#gare", "valence")
        une_page.click("form.filtres button[type='submit']")
        une_page.wait_for_selector("form.filtres")
        assert "gare=Valence" in une_page.url or "gare=valence" in une_page.url
        assert "gare Valence" in une_page.locator(".chip").first.text_content()
    finally:
        contexte.close()
