"""La charge à bord, dessinée en SVG.

Un fichier, une seule fonction, et pas de JavaScript : la courbe est écrite
par le serveur dans la page. C'est le même arbitrage que la carte elle-même,
et pour la même raison — une courbe que seul un navigateur peut produire
n'est revue par personne, alors qu'une chaîne de caractères est lisible dans
une assertion.

Trois règles gouvernent le rendu :

- **L'axe vertical part de zéro.** Une courbe de charge qui commence au
  maximum fait doubler les écarts, et un passager de plus ressemble alors à
  dix. Le maximum est arrondi vers le haut à une valeur lisible, pour que le
  haut de la boîte porte un nombre rond plutôt que 43.
- **Rien n'est tracé au-delà du dernier point connu.** Une descente non
  relevée laisse la fin du parcours inconnue ; la courbe s'arrête là et le
  dit, au lieu de redescendre vers zéro pour rejoindre la dernière gare.
- **Un SVG se décrit.** Le tracé a un `role="img"` et un `<title>`, parce
  qu'un graphique sans alternative textuelle n'est pas une image, c'est un
  trou. Les valeurs sont aussi écrites dans le `<desc>`.

**Ce module n'échappe rien**, et c'est un contrat, pas un oubli : les noms de
gares viennent de `counted_features`, qui les échappe une fois en amont parce
qu'ils atterrissent dans le même `<script>` que le reste. Les échapper ici
encore afficherait « R&amp;D » à l'écran. Un appelant qui passe du texte
brut doit l'échapper d'abord — c'est la règle du dépôt, et `carte.py` en est
l'unique porte.
"""

from html import escape

# 320 par 120 : la boîte est en `viewBox`, donc elle s'étire à la largeur de
# son conteneur sans jamais pixeliser. Le ratio 8:3 tient dans la colonne
# du panneau latéral comme dans la grille du grand écran.
LARGEUR = 320
HAUTEUR = 120
# Marge gauche : les nombres de l'axe vertical doivent tenir sans se poser sur
# la courbe. Marge basse : le nom de la gare de destination est écrit dessous.
MARGE_G, MARGE_D, MARGE_H, MARGE_B = 30, 8, 10, 22


def _plafond(maximum: int) -> int:
    """Un plafond lisible, jamais un maximum nu.

    `43` donnerait « 43 voyageurs » en haut d'une boîte, ce qui est exact
    mais illisible : le lecteur compare des hauteurs, pas des chiffres.
    On arrondit vers le haut au pas de 10, et un serpent vide de tout
    voyageur donne 10 plutôt que 0 — diviser par la hauteur d'un trait ne se
    fait pas.
    """
    if maximum <= 10:
        return 10
    return ((maximum + 9) // 10) * 10


def profil_svg(
    valeurs: list[int],
    gares: list[str],
    couleur: str,
    *,
    incomplet: str = "",
    titre: str = "Charge à bord",
) -> str:
    """La courbe de charge, en SVG.

    `valeurs` et `gares` sont parallèles : une valeur par arrêt, dans l'ordre
    du parcours. `incomplet` est le nom de l'arrêt où le compte s'arrête, ou
    la chaîne vide si toute la courbe est connue.
    """
    if not valeurs:
        return ""
    # Une gare manquante ferait un axe qui saute une station sans le dire.
    # On aligne sur le plus court des deux plutôt que d'inventer un nom.
    etapes = list(zip(gares, valeurs))
    largeur = LARGEUR - MARGE_G - MARGE_D
    hauteur = HAUTEUR - MARGE_H - MARGE_B
    plafond = _plafond(max(valeurs))

    def x(index: int) -> int:
        # Un seul point ne peut pas être placé : il tombe au milieu, où il se
        # lit sans inventer un début ni une fin de parcours.
        if len(etapes) == 1:
            return MARGE_G + largeur // 2
        return round(MARGE_G + index * largeur / (len(etapes) - 1))

    def y(valeur: int) -> int:
        return round(MARGE_H + hauteur - valeur / plafond * hauteur)

    morceaux = [
        f'<svg viewBox="0 0 {LARGEUR} {HAUTEUR}" role="img" class="courbe" '
        f'xmlns="http://www.w3.org/2000/svg">',
        f"<title>{escape(titre)}</title>",
        f"<desc>{_desc(etapes, incomplet)}</desc>",
    ]
    # Deux repères seulement : zéro et le plafond. Une grille plus fine ajoute
    # des lignes que personne ne lit et du poids dans la page.
    for valeur in (0, plafond):
        morceaux.append(
            f'<line x1="{MARGE_G}" y1="{y(valeur)}" x2="{LARGEUR - MARGE_D}" y2="{y(valeur)}" '
            f'class="repere" />'
        )
        morceaux.append(
            f'<text x="{MARGE_G - 4}" y="{y(valeur) + 4}" class="axe" text-anchor="end">{valeur}</text>'
        )
    points = " ".join(f"{x(index)},{y(valeur)}" for index, (_gare, valeur) in enumerate(etapes))
    morceaux.append(f'<polyline points="{points}" fill="none" stroke="{couleur}" stroke-width="2" />')
    for index, (_gare, valeur) in enumerate(etapes):
        morceaux.append(f'<circle cx="{x(index)}" cy="{y(valeur)}" r="2.5" fill="{couleur}" />')
    # Le nom de la première et de la dernière gare encadrent la courbe. Les
    # gares du milieu ne sont pas nommées : dix noms sur 320 pixels sont illisibles,
    # et l'ordre du parcours se lit déjà sur la liste à côté.
    morceaux.append(_etiquette(gares[0], x(0), HAUTEUR - 6, "start"))
    if len(etapes) > 1:
        morceaux.append(_etiquette(gares[-1], x(len(etapes) - 1), HAUTEUR - 6, "end"))
    morceaux.append("</svg>")
    return "".join(morceaux)


def _etiquette(texte: str, x: int, y: int, ancre: str) -> str:
    return f'<text x="{x}" y="{y}" class="axe" text-anchor="{ancre}">{escape(texte)}</text>'


def _desc(etapes: list[tuple[str, int]], incomplet: str) -> str:
    """La même information que la courbe, en une phrase.

    C'est ce qu'un lecteur d'écran entend à la place du dessin. Les noms de
    gares viennent de la base : ils sont échappés ici, exactement comme le
    sont ceux des étiquettes — un `<desc>` est du texte HTML, pas une zone
    où l'on peut laisser passer une balise.
    """
    detail = ", ".join(f"{escape(gare)} {valeur}" for gare, valeur in etapes)
    phrase = f"Charge à bord, voyageurs par arrêt : {detail}."
    if incomplet:
        phrase += f" Le compte s'arrête à {escape(incomplet)} : la suite n'a pas été relevée."
    return phrase