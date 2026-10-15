"""Stockage et rendu des retours privés, distincts des relevés."""

import re
import sqlite3
import time
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from comptagefer.affichage import chrome

MAX_MESSAGE = 5000
MAX_EMAIL = 254
QUOTA_MAX = 100
QUOTA_FENETRE = 3600
PAGE_RETOURS = 50
_PARTIE_EMAIL = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_EMAIL = re.compile(
    rf"{_PARTIE_EMAIL}(?:\.{_PARTIE_EMAIL})*@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
    re.ASCII,
)


def normaliser_email(email: str | None) -> str | None:
    """Retourne l'adresse facultative nettoyée, ou refuse une adresse invalide."""
    if email is None:
        return None
    if not isinstance(email, str):
        raise ValueError("Adresse électronique invalide.")
    email = email.strip()
    if not email:
        return None
    if len(email) > MAX_EMAIL or not _EMAIL.fullmatch(email):
        raise ValueError("Adresse électronique invalide.")
    local, _, domaine = email.rpartition("@")
    if len(local) > 64 or len(domaine) > 253:
        raise ValueError("Adresse électronique invalide.")
    return email


def preparer(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS retour (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            email TEXT,
            traite INTEGER NOT NULL DEFAULT 0 CHECK (traite IN (0, 1))
        )"""
    )
    colonnes = {row[1] for row in connection.execute("PRAGMA table_info(retour)")}
    if "email" not in colonnes:
        connection.execute("ALTER TABLE retour ADD COLUMN email TEXT")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS retour_admission (admitted_at REAL NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS retour_admission_temps ON retour_admission (admitted_at)"
    )


def enregistrer(database: Path, message: str, email: str | None = None) -> int | None:
    """Admet atomiquement jusqu'à 100 retours dans toute heure glissante."""
    email = normaliser_email(email)
    maintenant = time.time()
    seuil = maintenant - QUOTA_FENETRE
    with sqlite3.connect(database, timeout=10) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM retour_admission WHERE admitted_at <= ?", (seuil,))
        count, premiere_admission = connection.execute(
            "SELECT COUNT(*), MIN(admitted_at) FROM retour_admission"
        ).fetchone()
        if count >= QUOTA_MAX:
            connection.commit()
            return max(1, int(premiere_admission + QUOTA_FENETRE - maintenant + 0.999))
        connection.execute("INSERT INTO retour_admission (admitted_at) VALUES (?)", (maintenant,))
        connection.execute(
            "INSERT INTO retour (message, created_at, email, traite) VALUES (?, ?, ?, 0)",
            (message, datetime.now(UTC).isoformat(timespec="seconds"), email),
        )
    return None


def lister(database: Path, page: int = 1) -> list[tuple[int, str, str, int, str | None]]:
    offset = (page - 1) * PAGE_RETOURS
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT id, message, created_at, traite, email FROM retour ORDER BY id DESC LIMIT ? OFFSET ?",
            (PAGE_RETOURS, offset),
        ).fetchall()


def compter(database: Path) -> int:
    with sqlite3.connect(database) as connection:
        return connection.execute("SELECT COUNT(*) FROM retour").fetchone()[0]


def traiter(database: Path, identifiant: int) -> bool:
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA secure_delete = ON")
        curseur = connection.execute(
            "UPDATE retour SET traite = 1, email = NULL WHERE id = ?", (identifiant,)
        )
    return curseur.rowcount == 1


def supprimer(database: Path, identifiant: int) -> bool:
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA secure_delete = ON")
        curseur = connection.execute("DELETE FROM retour WHERE id = ?", (identifiant,))
    return curseur.rowcount == 1


def formulaire(
    message: str = "",
    erreur: str = "",
    confirmation: bool = False,
    email: str = "",
) -> str:
    if confirmation:
        corps = '<p role="status">Votre retour a bien été reçu.</p>'
    else:
        erreur_html = f'<p class="erreur" role="alert">{escape(erreur)}</p>' if erreur else ""
        corps = f"""<p>Votre message reste privé. L’adresse électronique est facultative et sera effacée lorsque le retour sera traité.
        N’indiquez pas de pseudo.</p>
        {erreur_html}
        <form method="post" action="/retours">
          <label for="message">Votre retour</label>
          <textarea id="message" name="message" required maxlength="{MAX_MESSAGE}" rows="8">{escape(message)}</textarea>
          <label for="email">Adresse e-mail (facultatif)</label>
          <input id="email" name="email" type="email" maxlength="{MAX_EMAIL}" autocomplete="email" value="{escape(email, quote=True)}">
          <div class="piege" aria-hidden="true"><label for="website">Ne pas remplir</label><input id="website" name="website" tabindex="-1" autocomplete="off"></div>
          <button type="submit">Envoyer</button>
        </form>"""
    css = """textarea, input[type=email] { display:block; width:100%; max-width:100%; box-sizing:border-box; margin:.4rem 0 1rem; padding:.7rem; font:inherit; }
    .erreur { color:#8b1e16; font-weight:600; }
    .piege { display:none; }"""
    return chrome("Retours", corps, actif="/retours", extra_css=css)


def panneau_admin(database: Path, page: int = 1) -> str:
    total = compter(database)
    pages = max(1, (total + PAGE_RETOURS - 1) // PAGE_RETOURS)
    page = min(max(1, page), pages)
    rows = lister(database, page)
    suffixe_page = f"?retours_page={page}" if page > 1 else ""
    if not rows:
        return "<h2>Retours privés</h2><p>Aucun retour.</p>"
    cartes = []
    for identifiant, message, date, traite, email in rows:
        statut = "Traité" if traite else "À traiter"
        contenu = escape(message).replace("\n", "<br>\n")
        contact = f'<p class="contact-retour">Contact : {escape(email)}</p>' if email else ""
        cartes.append(
            f"""<article class="card retour-admin">
              <h3>Retour {identifiant} — {statut}</h3>
              <p><time datetime="{escape(date, quote=True)}">{escape(date)}</time></p>
              <p class="message-retour">{contenu}</p>
              {contact}
              <form method="post" action="/admin/retours/{identifiant}/traiter{suffixe_page}"><button type="submit">Marquer traité</button></form>
              <form method="post" action="/admin/retours/{identifiant}/supprimer{suffixe_page}"><button type="submit">Supprimer ce retour</button></form>
            </article>"""
        )
    navigation = ""
    if pages > 1:
        precedent = f"<a href='/admin?onglet=retours&amp;retours_page={page - 1}'>Page précédente</a>" if page > 1 else ""
        suivant = f"<a href='/admin?onglet=retours&amp;retours_page={page + 1}'>Page suivante</a>" if page < pages else ""
        navigation = f"<nav aria-label='Pages de retours'>{precedent} Page {page} sur {pages} {suivant}</nav>"
    return (
        "<h2>Retours privés</h2><p>Messages confidentiels, visibles uniquement ici.</p>"
        + navigation
        + "".join(cartes)
        + "<style>.message-retour { white-space:pre-wrap; overflow-wrap:anywhere; }</style>"
        + navigation
    )



