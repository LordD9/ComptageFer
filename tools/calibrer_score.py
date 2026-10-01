"""La distribution du score sur une base réelle, pour calibrer les coefficients.

    python tools/calibrer_score.py data/app.db

Le plan (`docs/projet.md`, phase 9, vague 3) dit que les coefficients « ne sont
pas choisis dans ce document » et qu'une fonction de score unique et paramétrée
« produit une distribution sur la base réelle — et la distribution décide des
coefficients ». C'est cet outil qui produit cette distribution. Il ne choisit rien
et il ne modifie rien : il sort des chiffres, et la décision reste écrite, avec
ses trois questions, dans le plan.

Il refuse de tourner sur une base sans relevé rattaché. Une distribution de zéro
n'est pas une mesure, et un « 0 point, 0 compte » se lit comme une mesure : c'est
le genre de chiffre qui fait choisir des coefficients sur une absence de données.
Sortir en erreur est plus honnête qu'imprimer une table vide que quelqu'un
copiera dans une PR.

Les trois mesures du plan, en clair, parce que ce sont elles qui décident :

- **la part de l'inédit.** Un bon compteur doit tirer un tiers de ses points de
  corridors qu'aucun relevé ne portait. Si la part est proche de zéro, le
  classement récompense le volume, ce qu'il ne doit pas faire ; si elle est de
  100 %, il récompense le hasard d'être arrivé tôt, ce qui revient au même.
- **l'écart au premier.** Si le premier est à mille fois le médian, le classement
  classe une course et pas un réseau.
- **la fenêtre.** Combien de couples ont été vus dans les 30 derniers jours : si
  presque tous, une fenêtre à 30 jours ne distingue rien et il faut la changer.

Rien n'est écrit dans la base : l'outil ouvre `app.db` en lecture et sort.
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from comptagefer.score import PAR_DEFAUT, Coeff, classement  # noqa: E402


def _couverture(connection: sqlite3.Connection) -> tuple[int, int, Counter]:
    """Les couples, leur étendue, et le nombre de passages par couple.

    C'est ce qui répond à la troisième question du plan : si presque tous les
    couples ont été vus récemment, la fenêtre ne distingue plus rien.
    """
    rows = connection.execute(
        "SELECT origin_stop_id || '>' || destination_stop_id AS couple, "
        "MAX(created_at) AS dernier, COUNT(*) AS passages FROM saisie "
        "WHERE kind IN ('count', 'serpent') GROUP BY couple"
    ).fetchall()
    return len(rows), sum(r["passages"] for r in rows), Counter(
        r["passages"] for r in rows
    )


def principal(database: Path, coeff: Coeff = PAR_DEFAUT) -> int:
    """La distribution, imprimée. Renvoie un code de sortie.

    Un code de sortie non nul sur une base non calibrable est le contrat : le
    `test_score.py` s'y accroche, donc « aucune donnée » ne peut pas se déguiser
    en « distribution vide mais valide ».
    """
    if not database.exists():
        print(f"Base absente : {database}", file=sys.stderr)
        return 2

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {"saisie", "compte"} <= tables:
            manquantes = ", ".join(sorted({"saisie", "compte"} - tables))
            print(
                f"Cette base n'a pas encore le schéma du compte ({manquantes}). "
                "Démarrez l'application une fois pour qu'elle le crée, puis "
                "relancez la calibration.",
                file=sys.stderr,
            )
            return 2

        lignes = classement(database, coeff)
        couples, passages, repetition = _couverture(connection)
        vus = _vus_recent(connection, coeff.fenetre_jours)

    if not lignes:
        print(
            "Aucun relevé rattaché à un compte dans cette base. "
            "La distribution ne peut pas être produite : il n'y a rien à "
            "répartir. Les coefficients se calibrent sur la base du VPS.",
            file=sys.stderr,
        )
        return 1

    points = [ligne["points"] for ligne in lignes]
    total_releves = sum(ligne["releves"] for ligne in lignes)
    part_inedit = sum(ligne["inedit"] for ligne in lignes) / max(sum(points), 1)
    median = sorted(points)[len(points) // 2]

    print(f"Base            : {database}")
    print(f"Comptes classés : {len(lignes)}")
    print(f"Relevés retenus : {total_releves}")
    print(f"Couples vues    : {couples} pour {passages} passages")
    print("")
    print("Classement (les 10 premiers, pour situer la distribution)")
    for rang, ligne in enumerate(lignes[:10], start=1):
        print(
            f"  {rang:>2}. {ligne['pseudo'][:24]:<24} {ligne['points']:>7.0f} pts "
            f"sur {ligne['releves']:>4} relevé(s), "
            f"{ligne['inedit']:>5.0f} sur des corridors inédits"
        )
    if len(lignes) > 10:
        print(f"  … et {len(lignes) - 10} autre(s) compte(s)")
    print("")
    print("Distribution des points")
    print(f"  premier  {points[0]:.0f}")
    print(f"  médian   {median:.0f}")
    print(f"  dernier  {points[-1]:.0f}")
    if median:
        print(f"  écart premier/médian : {points[0] / median:.1f}")
    print(f"  part de l'inédit     : {part_inedit * 100:.0f} %")
    print("")
    print("Répartition (combien de comptes à chaque points)")
    tranche = Counter(int(p // 10) * 10 for p in points)
    for debut in sorted(tranche):
        print(f"  {debut:>5}–{debut + 9:<5} {'#' * tranche[debut]} ({tranche[debut]})")
    print("")
    print("Répétition par couple (combien de passages porte un même couple)")
    for fois, nombre in sorted(repetition.items())[:6]:
        print(f"  {fois} passage(s) : {nombre} couple(s)")
    print("")
    print("Les trois questions du plan")
    print(
        f"  1. part de l'inédit : {part_inedit * 100:.0f} % "
        "— viser un tiers pour un bon compteur"
    )
    if median:
        ecart = points[0] / median
        verdict = "ça classe une course, pas un réseau" if ecart > 10 else "acceptable"
        print(f"  2. écart premier/médian : {ecart:.1f} — {verdict} (au-delà de 10, c'est une course)")
    else:
        print("  2. écart premier/médian : indéfinissable, le médian est nul")
    print(
        f"  3. couples vus dans la fenêtre de {coeff.fenetre_jours} jours : "
        f"{vus} / {couples} ({_pourcentage(vus, couples)})"
    )
    if couples and vus / couples > 0.9:
        print(
            "     — presque tous : une fenêtre de "
            f"{coeff.fenetre_jours} jours ne distingue plus rien, il faut l'allonger"
        )
    elif couples and vus / couples < 0.3:
        print(
            "     — presque aucun : la fenêtre est trop large, elle ne classe plus rien"
        )
    print("")
    print("Ces nombres ont été produits avec les coefficients de `PAR_DEFAUT`,")
    print("qui ne sont pas calibrés. Ils disent si la FORME du score tient, pas si")
    print("les poids sont justes : les poids se choisissent en regardant cette")
    print("distribution, et la décision s'écrit dans le plan.")
    return 0


def _pourcentage(part: int, tout: int) -> str:
    """Une part en pourcentage, sans division par zéro.

    Le plan parle de « presque tous les corridors » et de « presque aucun » : ce
    sont deux seuils sur un rapport, donc il faut le rapport. Écrire
    `part / tout` ici planterait sur une base où un seul couple existe, ce qui
    est exactement le cas d'une installation neuve — celle où l'outil doit encore
    parler.
    """
    if not tout:
        return "—"
    return f"{part * 100 / tout:.0f} %"


def _vus_recent(connection: sqlite3.Connection, jours: int) -> int:
    """Combien de couples ont un relevé dans la fenêtre.

    Une requête, pas un `COUNT(*)` par couple : la question est posée sur des
    milliers de lignes, et `docs/regles.md` §4 refuse le parcours complet quand la
    question est une seule.
    """
    return int(
        connection.execute(
            "SELECT COUNT(DISTINCT origin_stop_id || '>' || destination_stop_id) "
            "FROM saisie WHERE kind IN ('count', 'serpent') "
            "AND created_at >= datetime('now', ?)",
            (f"-{jours} days",),
        ).fetchone()[0]
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(principal(Path(sys.argv[1])))
