"""Le journal public des changements de ComptagesFer."""

from html import escape

from comptagefer.affichage import chrome


CHANGEMENTS = (
    (
        "2026-10-15",
        "15 octobre 2026",
        (
            (
                "Améliorations",
                (
                    "L’administration est organisée en onglets pour retrouver ses différents outils.",
                    "Un journal public présente les évolutions du site.",
                ),
            ),
        ),
    ),
    (
        "2026-10-14",
        "14 octobre 2026",
        (
            (
                "Corrections",
                (
                    "Les noms des gares des trajets signalés sont résolus à partir de leurs identifiants.",
                    "La recherche de gares reconnaît des alias de gares explicitement vérifiés sans fusionner les résultats ambigus.",
                    "Le sens de marche du schéma SVG des rames est désormais indiqué correctement.",
                ),
            ),
        ),
    ),
)


def _changements_html() -> str:
    articles = []
    for date, date_affichee, categories in CHANGEMENTS:
        sections = []
        for nom, changements in categories:
            liste = "".join(f"<li>{escape(changement)}</li>" for changement in changements)
            sections.append(f"<section><h2>{escape(nom)}</h2><ul>{liste}</ul></section>")
        contenu = "".join(sections)
        articles.append(
            f"<article><time datetime='{escape(date, quote=True)}'>"
            f"{escape(date_affichee)}</time>{contenu}</article>"
        )
    return "".join(articles)


def changelog_page() -> str:
    """Rendre la page publique du journal dans le chrome commun."""
    return chrome("Changelog", _changements_html(), actif="/changelog")
