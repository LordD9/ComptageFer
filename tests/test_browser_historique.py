"""Parcours Chromium d'une recherche historique jusqu'à la lecture publique."""

import sqlite3
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from tests.conftest import TER

PARIS = ZoneInfo("Europe/Paris")


def _depart_historique(tmp_path):
    """Ajoute une date de circulation antérieure au scénario GTFS du site."""
    timetable = tmp_path / "data" / "timetable.db"
    jour = (datetime.now(PARIS).date() - timedelta(days=4)).isoformat()
    with sqlite3.connect(timetable) as connection:
        service_id = connection.execute(
            "SELECT service_id FROM circulation WHERE trip_id = ?", (TER,)
        ).fetchone()[0]
        depart_sec = connection.execute(
            "SELECT depart_sec FROM passage WHERE trip_id = ? AND stop_id = ?",
            (TER, "StopPoint:LyonA"),
        ).fetchone()[0]
        connection.execute(
            "INSERT OR REPLACE INTO service_day (service_id, day) VALUES (?, ?)",
            (service_id, jour),
        )
    return datetime.combine(datetime.fromisoformat(jour).date(), time.min, PARIS) + timedelta(
        seconds=depart_sec
    )


def _selectionner_train_historique(page, site, tmp_path):
    depart = _depart_historique(tmp_path)
    page.goto(site)
    page.fill("#origin-q", "Lyon")
    page.click("#origin-list button:has-text('Lyon Part-Dieu')")
    page.fill("#destination-q", "Valence")
    page.click("#destination-list button:has-text('Valence')")
    page.wait_for_selector("#train-step:not(.hidden)")

    page.locator("#historical-picker summary").click()
    valeur_locale = page.evaluate(
        """iso => {
          const date = new Date(iso);
          return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
            .toISOString().slice(0, 16);
        }""",
        depart.isoformat(),
    )
    page.fill("#historical-at", valeur_locale)
    page.click("#historical-search")
    page.wait_for_selector("#historical-trains button")
    train = page.locator("#historical-trains button:has-text('TER')")
    assert train.count() == 1
    assert "temps réel inconnu" in train.text_content().lower()
    train.click()
    return depart


def test_un_train_historique_se_compte_sans_temps_reel_et_se_relit(page, site, tmp_path):
    depart = _selectionner_train_historique(page, site, tmp_path)
    assert "temps réel inconnu" in page.text_content("#train-chip").lower()

    page.click("#compo-none")
    page.click("#materiel-next")
    page.wait_for_selector("#comptage-step:not(.hidden)")
    page.click("#mode-unique")
    page.fill("#passengers", "42")
    page.dispatch_event("#passengers", "input")
    page.click("#count-next")
    page.wait_for_selector("#form-step:not(.hidden)")
    page.fill("#reliability", "80")
    page.click("#send")
    page.wait_for_selector("#done-step:not(.hidden)")

    rows = page.request.get(site + "/api/sessions").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["trip_id"] == TER
    assert row["is_historical"] is True
    assert datetime.fromisoformat(row["observed_at"]).astimezone(PARIS) == depart
    assert datetime.fromisoformat(row["created_at"]) > datetime.fromisoformat(row["observed_at"])

    detail = page.request.get(
        site + "/releve?client_id=" + row["client_id"] + "&kind=count"
    ).text()
    assert "Train observé" in detail
    assert "Saisie effectuée le" in detail
    assert "Temps réel inconnu" in detail
    assert page.errors == []


def test_reprise_du_serpent_historique_conserve_la_date_jusqua_la_sauvegarde(
    page, site, tmp_path
):
    depart = _selectionner_train_historique(page, site, tmp_path)
    page.click("#compo-none")
    page.click("#materiel-next")
    page.click("#mode-snake")
    page.wait_for_selector("#snake-onboard")
    page.fill("#snake-onboard", "42")
    page.click("#snake-next")
    page.wait_for_selector("#snake-boarded")

    draft = page.evaluate("JSON.parse(localStorage.getItem('comptagefer-serpent'))")
    assert draft["is_historical"] is True
    assert datetime.fromisoformat(draft["observed_at"]).astimezone(PARIS) == depart

    page.reload()
    page.wait_for_selector("#snake-step:not(.hidden)")
    assert page.locator("#snake-onboard").count() == 0
    assert page.evaluate(
        "JSON.parse(localStorage.getItem('comptagefer-serpent')).is_historical"
    ) is True

    while page.locator("#snake-reliability").count() == 0:
        page.fill("#snake-boarded", "0")
        if page.locator("#snake-alighted").count():
            page.fill("#snake-alighted", "0")
        page.click("#snake-next")
    page.fill("#snake-boarded", "0")
    if page.locator("#snake-alighted").count():
        page.fill("#snake-alighted", "0")
    page.fill("#snake-reliability", "80")
    page.click("#snake-next")
    page.wait_for_selector("#done-step:not(.hidden)")

    rows = page.request.get(site + "/api/sessions").json()
    assert len(rows) == 1
    assert rows[0]["kind"] == "serpent"
    assert rows[0]["is_historical"] is True
    assert datetime.fromisoformat(rows[0]["observed_at"]).astimezone(PARIS) == depart
    assert page.errors == []
