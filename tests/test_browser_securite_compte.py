"""L'origine et le secret sont testés dans le navigateur, pas seulement en HTTP."""

import sqlite3
from html import escape

from comptagefer.compte import COOKIE_COMPTE


def test_secret_hors_url_et_navigation_sans_referer(page, site):
    page.goto(site + "/compte")
    page.fill('input[name="pseudo"]', "voyageur")
    with page.expect_response(lambda r: r.url.endswith("/compte/creer")) as creation:
        page.click('button:has-text("Créer mon compte")')
    assert creation.value.status == 200, creation.value.text()
    page.wait_for_selector("#secret-texte")
    secret = page.locator("#secret-texte").inner_text()
    assert page.url == site + "/compte/creer"
    assert secret not in page.url
    assert creation.value.headers["cache-control"] == "no-store"
    with page.expect_request(lambda r: r.url == site + "/compte") as navigation:
        page.click('a:has-text("J’ai copié mon secret")')
    assert "referer" not in navigation.value.all_headers()
    assert page.locator("#secret-texte").count() == 0
    assert any(
        c["name"] == COOKIE_COMPTE and c["httpOnly"] for c in page.context.cookies()
    )


def test_formulaire_etranger_ne_cree_pas_de_compte(page, site, base_du_site):
    cible = escape(site + "/compte/creer", quote=True)
    attaque = (
        f'<form method="post" action="{cible}">'
        '<input name="pseudo" value="intrus"><button>Envoyer</button></form>'
    )
    page.route(
        "https://evil.example/**",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body=attaque,
        ),
    )
    page.goto("https://evil.example/attaque")
    with page.expect_response(lambda r: r.url.endswith("/compte/creer")) as reception:
        page.click("button")
    assert reception.value.status == 403
    with sqlite3.connect(base_du_site) as db:
        assert db.execute("SELECT COUNT(*) FROM compte").fetchone()[0] == 0
    assert not any(c["name"] == COOKIE_COMPTE for c in page.context.cookies())
