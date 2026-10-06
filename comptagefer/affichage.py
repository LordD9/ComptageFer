"""Le chrome commun des pages de lecture.

Sept pages du dépôt recopient chacune leur `<style>`, et sept fois le même
`max-width: 32rem` avec la même typographie. Le mobile y est bon — le
formulaire se fait au pouce, dans un train — mais la version PC de
`/comptages` est cette colonne de 512 px centrée dans un écran large, avec
la moitié de la hauteur de l'écran en vide de chaque côté.

Ce module est l'unique endroit où cette mise en page est écrite. Deux
règles le gouvernent, et elles sont opposées :

- **Rien n'est perdu sur petit écran.** La colonne de 32 rem reste le
  point de départ, parce qu'un téléphone dans un TER n'a pas d'autre
  largeur. Le mobile n'est pas une dégradation, c'est le cas le plus
  contraint.
- **Un écran large reçoit plus, pas la même chose étirée.** Au-delà de
  48 rem, la lecture s'ouvre à 72 rem et `/comptages` propose un tableau
  dense à la place des cartes. Le tableau est dans la même page, pas dans
  une autre : deux URL pour les mêmes données, c'est deux endroits où elles
  divergent.

Pas de framework, pas de dépendance, pas de JavaScript pour cela : la
feuille de style fait le travail, et une media query suffit. Une SPA pour
mettre un tableau en place serait le coût que ce dépôt s'interdit.
"""

from html import escape

NAVIGATION = (
    ("/comptages", "Comptages"),
    ("/carte", "Carte"),
    ("/", "Compter"),
    ("/classement", "Classement"),
    ("/compte", "Compte"),
    ("/methode", "Méthode"),
    ("/retours", "Retours"),
)

NAV_STYLE = """
  header.site { background: #fff; border-bottom: 1px solid var(--bord, #c9c1b4); }
  header.site .barre { max-width: 32rem; margin: 0 auto; padding: 0.7rem 1rem 0.5rem; }
  header.site .marque { font-weight: 700; font-size: 1.05rem; text-decoration: none; }
  header.site nav { display: flex; flex-wrap: wrap; gap: 0.2rem 0.9rem; font-size: 0.92rem; margin-top: 0.3rem; }
  header.site nav a[aria-current="page"] { font-weight: 700; text-decoration: none; border-bottom: 2px solid var(--encre, #1c1915); }
  @media (min-width: 48rem) {
    header.site .barre { max-width: 72rem; display: flex; align-items: baseline; gap: 1.5rem; padding: 0.7rem 1.5rem; }
    header.site nav { margin-top: 0; }
  }
"""


def navigation_html(actif: str) -> str:
    return f'<header class="site"><div class="barre"><a class="marque" href="/">ComptagesFer</a><nav>{_nav(actif)}</nav></div></header>'

# La feuille de base. Elle est volontairement courte : ce qui est propre à
# une page (le cadre de la carte, le champ de recherche) vient dans
# `extra_css`, appelé par la page qui en a besoin.
STYLE = """
  :root { color-scheme: light; --encre: #1c1915; --pale: #f4f1ea; --bord: #c9c1b4; --gris: #5c564c; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 18px/1.4 system-ui, sans-serif; background: var(--pale); color: var(--encre); }
  a { color: var(--encre); }

  main { max-width: 32rem; margin: 0 auto; padding: 1rem 1rem 3rem; }
  h1 { font-size: 1.6rem; margin: 0 0 0.3rem; }
  h2 { font-size: 1.15rem; margin: 1.8rem 0 0.5rem; }
  .mention { color: var(--gris); font-size: 0.9rem; }
  .card { background: #fff; border-radius: 0.8rem; padding: 0.8rem; margin: 0.6rem 0; }
  .status { font-size: 0.85rem; color: var(--gris); }
  a.bouton, .bouton { display: inline-block; background: var(--encre); color: #fff; text-decoration: none;
                     padding: 0.7rem 1.1rem; border-radius: 0.6rem; font-weight: 600; }
  footer.site { border-top: 1px solid var(--bord); background: #fff; font-size: 0.9rem; }
  footer.site .barre { max-width: 32rem; margin: 0 auto; padding: 1rem; }
  footer.site a { margin-right: 0.7rem; }

  /* Deux lectures de la même liste, une par taille d'écran. Sur un téléphone
     ce sont des cartes empilées ; au-delà de 48 rem c'est un tableau, et les
     cartes sont retirées du rendu. Les deux représentations sont dans la même
     page parce qu'elles sont la même liste : une page qui les montrait
     toutes les deux ferait lire chaque relevé deux fois. */
  .cartes { display: block; }
  .tableau { display: none; }
  .grille { display: grid; grid-template-columns: 1fr; gap: 0.6rem; align-items: start; }

  @media (min-width: 48rem) {
    footer.site .barre, main { max-width: 72rem; }
    main { padding: 1.5rem 1.5rem 4rem; }
    footer.site .barre { padding: 1rem 1.5rem; }

    .grille { grid-template-columns: repeat(2, minmax(0, 1fr)); }

    .tableau { display: table; width: 100%; border-collapse: collapse; background: #fff;
               border-radius: 0.8rem; overflow: hidden; font-size: 0.95rem; }
    .tableau caption { caption-side: top; text-align: left; color: var(--gris); font-size: 0.9rem; padding: 0 0 0.5rem; }
    .tableau th, .tableau td { text-align: left; padding: 0.5rem 0.7rem; border-bottom: 1px solid #e6e0d5;
                               vertical-align: top; }
    .tableau thead th { background: #fbf8f2; font-size: 0.82rem; text-transform: uppercase;
                        letter-spacing: 0.04em; color: var(--gris); white-space: nowrap; }
    .tableau tbody tr:last-child td { border-bottom: 0; }
    .tableau td.nombre { font-variant-numeric: tabular-nums; white-space: nowrap; }
    .tableau .vide { color: var(--gris); }
  }
"""


def _nav(actif: str) -> str:
    morceaux = []
    for href, texte in NAVIGATION:
        # `aria-current` plutôt qu'une classe : la page courante se lit
        # dans l'accessibilité, pas seulement dans le CSS.
        courant = ' aria-current="page"' if href == actif else ""
        morceaux.append(f'<a href="{href}"{courant}>{texte}</a>')
    return "".join(morceaux)


def chrome(
    titre: str,
    corps: str,
    *,
    actif: str = "",
    extra_css: str = "",
    extra_head: str = "",
    extra_script: str = "",
    mention: str = "",
) -> str:
    """Une page de lecture complète : en-tête, navigation, contenu, pied.

    `corps` est le HTML propre à la page. Tout le reste est commun, donc
    écrit une fois. Une page de lecture qui aurait sa propre feuille de
    style passe le complément par `extra_css` : deux blocs, pas deux
    feuilles concurrentes.
    """
    ligne_mention = f'<p class="mention">{mention}</p>' if mention else ""
    # Le pied ne reprend pas la navigation : celle-ci est dans l'en-tête, et
    # deux liens vers la même page dans un même écran font douter le lecteur
    # sur lequel cliquer. Il dit ce que le site est, et c'est tout.
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(titre)} — ComptagesFer</title>
{extra_head}<style>{STYLE}{NAV_STYLE}{extra_css}</style>
</head>
<body>
{navigation_html(actif)}
<main>
  <h1>{titre}</h1>
  {ligne_mention}
  {corps}
</main>
<footer class="site">
  <div class="barre">
    <p>Comptages collaboratifs de fréquentation ferroviaire. Les chiffres viennent
    de gens qui ont compté, dans leur train, à la main. Les données sont en Licence Ouverte 2.0, le logiciel
    en GPL-3.0.
    <a href="/api/export.csv">Télécharger le CSV</a></p>
  </div>
</footer>
{extra_script}
</body>
</html>
"""
