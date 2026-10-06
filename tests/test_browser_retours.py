def test_retour_anonyme_sans_javascript_et_parcours_admin_mobile(site, navigateur_partage):
    context = navigateur_partage.new_context(
        viewport={"width": 390, "height": 844}, java_script_enabled=False
    )
    page = context.new_page()
    try:
        page.goto(site + "/retours")
        assert page.get_by_role("heading", name="Retours").is_visible()
        page.get_by_label("Votre retour").fill("Message privé depuis mobile")
        page.get_by_role("button", name="Envoyer").click()
        page.wait_for_url("**/retours?envoye=1")
        assert page.get_by_role("status").inner_text() == "Votre retour a bien été reçu."

        page.goto(site + "/admin")
        page.get_by_label("Jeton").fill("jeton-admin-test")
        page.get_by_role("button", name="Ouvrir").click()
        page.get_by_role("heading", name="Retours privés").wait_for()
        assert page.get_by_text("Message privé depuis mobile").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.get_by_role("button", name="Marquer traité").click()
        page.wait_for_url("**/admin")
        assert page.get_by_role("heading", name="Retour 1 — Traité").is_visible()
        page.get_by_role("button", name="Supprimer ce retour").click()
        page.wait_for_url("**/admin")
        assert page.get_by_text("Aucun retour.").is_visible()
    finally:
        context.close()
