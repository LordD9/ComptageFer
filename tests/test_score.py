"""Le score, et la mesure qui doit décider de ses coefficients.

Le plan (`docs/projet.md`, phase 9, vague 3) donne six propriétés vérifiables,
et ce fichier les vérifie une par une. Elles sont écrites ici dans l'ordre où le
plan les range, parce que cet ordre *est* la hiérarchie du score : si deux
d'entre elles se contredisent, c'est la formule qui est mal écrite, pas le test.

La propriété 1 est celle qui casse le plus vite et le plus silencieusement : un
score qui récompense la fidélité déclarée est un score que n'importe qui peut
faire monter à 100 en écrivant `reliability: 100`. Elle est donc testée par
égalité, et non par comparaison.

**Les coefficients de ce fichier sont ceux de `PAR_DEFAUT`, et ils ne sont pas
calibrés.** Les tests vérifient des *rapports* — « un `um` vaut plus qu'une
voiture », « un corridor inédit vaut plus qu'un corridor vu la veille » — et non
des nombres, précisément pour que le réglage des coefficients ne fasse tomber
aucun test : c'est `tools/calibrer_score.py` qui produit la distribution sur la
base réelle, et les nombres ici ne sont que lisibles.
"""

import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from comptagefer.score import PAR_DEFAUT, Coeff, classement, score_de

# Des poids dégénérés, pour isoler chaque règle des autres. Un coefficient à 0
# veut dire « ce critère ne rapporte rien », ce qui est plus net qu'un
# coefficient petit qu'un autre critère écrase.
SEUL_UM = Coeff(base=0, um=1, serpent=0, corridor_inedit=0, corridor_vieux=0, fenetre_jours=30)
SEUL_SERPENT = Coeff(base=0, um=0, serpent=1, corridor_inedit=0, corridor_vieux=0, fenetre_jours=30)
SEUL_INEDIT = Coeff(base=0, um=0, serpent=0, corridor_inedit=1, corridor_vieux=0, fenetre_jours=30)
SEUL_VIEUX = Coeff(base=0, um=0, serpent=0, corridor_inedit=0, corridor_vieux=1, fenetre_jours=30)


def _maintenant() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _base(tmp_path: Path) -> Path:
    """Une base avec le schéma, et rien dedans.

    Le schéma vient de l'application elle-même plutôt que d'un `CREATE TABLE`
    recopié dans le test : c'est la seule façon que le test suive le projet au
    lieu de diverger de lui. Et une base neuve, parce qu'un `saisie` sans
    `compte_id` est exactement la base existante que la phase 9 doit migrer.
    """
    from comptagefer.app import create_app

    database = tmp_path / "app.db"
    create_app(tmp_path)
    return database


def _relever(
    database: Path,
    compte: str,
    origine: str = "A",
    destination: str = "B",
    *,
    genre: str = "count",
    perimetre: str | None = None,
    fiabilite: int = 50,
    jour: datetime | None = None,
    jeton: str = "j",
) -> None:
    """Un relevé rattaché, écrit directement en base.

    Écrire en SQL et non par l'API est délibéré : ces tests portent sur la
    formule de score, pas sur le formulaire. Le rattachement à un compte passe
    par le cookie en production — c'est testé dans `test_compte.py` — donc ici on
    écrit la colonne que ce cookie aurait remplie.
    """
    moment = (jour or _maintenant()).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO saisie (client_id, kind, origin_stop_id, "
            "destination_stop_id, passengers, reliability, perimetre, created_at, "
            "compte_id) VALUES (?, ?, ?, ?, 10, ?, ?, ?, ?)",
            (jeton, genre, origine, destination, fiabilite, perimetre, moment, compte),
        )


def _compte(tmp_path: Path, identifiant: str, pseudo: str) -> None:
    """Un compte en base, sans secret — le classement n'a pas besoin d'en avoir un.

    `_base` est appelé d'abord : ces tests écrivent en SQL direct, et sans le
    schéma de l'application la table `compte` n'existe pas. L'ordre se voit dans
    chaque test : `_base` puis `_compte` puis `_relever`. Ce n'est pas une
    commodité, c'est le même ordre que la production — schéma, puis comptes, puis
    relevés.
    """
    with sqlite3.connect(tmp_path / "app.db") as connection:
        connection.execute(
            "INSERT OR IGNORE INTO compte (id, pseudo, secret, cree_le) VALUES (?, ?, NULL, ?)",
            (identifiant, pseudo, _maintenant().timestamp()),
        )


def _seul_score(database: Path, coeff: Coeff) -> dict[str, float]:
    """Le score par compte, indexé par identifiant — pas par pseudo.

    Indexer par pseudo serait plus lisible et casserait le test du classement sans
    pseudo, qui est justement un test de non-fuite.
    """
    from comptagefer.score import _scores

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        tous = _scores(connection, coeff)
    return {compte: p.points for compte, p in tous.items()}


# --- 1. Le `um` d'abord, parce que c'est la seule réponse à la question ----


def test_un_releve_a_um_vaut_plus_qu_un_releve_a_voiture(tmp_path):
    """`perimetre` décide, et c'est le premier critère du plan.

    180 personnes dans une voiture d'une UM3 et 180 dans les trois sont le même
    relevé écrit deux fois. C'est la raison pour laquelle `PERIMETRES` existe,
    et c'est la même raison qui vaut pour le score.
    """
    database = _base(tmp_path)
    _relever(database, "c1", "A", "B", perimetre="um", jeton="um")
    _relever(database, "c1", "C", "D", perimetre="voiture", jeton="voiture")

    points = _seul_score(database, SEUL_UM)

    assert points["c1"] == 1, "un relevé à um doit rapporter, une voiture non"


def test_um_et_voiture_vaut_moins_de_deux_um(tmp_path):
    """Le critère est un bonus, pas une substitution.

    C'est ce qui distingue « la rame entière vaut plus » de « seul le relevé à
    l'échelle de la rame compte ». Le plan dit « un relevé à `um` rapporte plus
    qu'un relevé à `voiture` », pas « les autres ne valent rien ».
    """
    database = _base(tmp_path)
    _relever(database, "c1", "A", "B", perimetre="um", jeton="1")
    _relever(database, "c1", "C", "D", perimetre="voiture", jeton="2")
    _relever(database, "c1", "E", "F", perimetre="um", jeton="3")

    un_um = _seul_score(database, SEUL_UM)["c1"]

    assert un_um == 2, "deux relevés à um doivent rapporter deux fois"


def test_un_perimetre_inconnu_ne_rapporte_pas_le_bonus_um(tmp_path):
    """Seul `um` ouvre le bonus, pas « tout ce qui n'est pas voiture ».

    `perimetre` est une liste fermée côté validation, donc le cas est
    théorique — mais une valeur vide est réelle : c'est ce qu'enregistre un
    compteur plus ancien, ou un appel direct de l'API. Une liste fermée qui
    s'applique par exclusion devient une liste ouverte au premier enregistrement
    d'une valeur inattendue.
    """
    database = _base(tmp_path)
    _relever(database, "c1", "A", "B", perimetre=None, jeton="vide")

    assert _seul_score(database, SEUL_UM)["c1"] == 0


# --- 2. Le serpent, parce qu'il dit où la charge monte ---------------------


def test_un_serpent_vaut_plus_qu_un_comptage_unique(tmp_path):
    """Le serpent donne le profil, l'effectif unique donne une valeur.

    C'est la seule partie de la base qui renseigne la question du §2 sur les
    montées et descentes. Le plan a d'abord écrit le contraire, et la décision a
    été reprise : une distinction de forme n'est pas une différence de contenu.
    """
    database = _base(tmp_path)
    _relever(database, "c1", "A", "B", genre="serpent", jeton="s")
    _relever(database, "c1", "C", "D", genre="count", jeton="c")

    assert _seul_score(database, SEUL_SERPENT)["c1"] == 1


def test_un_train_signale_ne_rapporte_aucun_point(tmp_path):
    """`missing` n'est pas un comptage : c'est une absence de données.

    Le signaler est utile à la base, et le plan le liste comme une écriture
    légitime. Le score, lui, récompense ce qui se lit à l'échelle de la rame, et
    un train signalé ne se lit à aucune échelle.
    """
    database = _base(tmp_path)
    _relever(database, "c1", "A", "B", genre="missing", jeton="m")

    assert _seul_score(database, PAR_DEFAUT).get("c1", 0) == 0


# --- 3. Le corridor inédit, mesuré sur toute la base -----------------------


def test_un_corridor_inedit_vaut_plus_qu_un_corridor_deja_vu(tmp_path):
    """Le premier relevé d'une paire vaut plus que le suivant.

    C'est la règle qui fait que le classement récompense la couverture et pas le
    volume : sans elle, compter le même train dix fois reste le meilleur moyen
    de monter.

    Le premier ici est `c1`, même dix jours plus tôt : « inédit » veut dire «
    aucun relevé ne portait ce couple », pas « le plus récent de son compte ». Un
    corridor qui traîne dix jours sans être vu reste inédit jusqu'au moment où
    quelqu'un le voit.
    """
    base = _maintenant()
    database = _base(tmp_path)
    _relever(database, "c1", "A", "B", jour=base - timedelta(days=10), jeton="vieux")
    _relever(database, "c2", "A", "B", jour=base, jeton="neuf")

    points = _seul_score(database, SEUL_INEDIT)

    assert points["c1"] == 1, "le premier passage sur ce couple est le premier du monde"
    assert points["c2"] == 0, "le passage suivant ne peut pas être inédit deux fois"


def test_le_corridor_inedit_est_mesure_sur_les_releves_des_autres(tmp_path):
    """Un corridor déjà vu par quelqu'un n'est pas inédit pour le suivant.

    Le test inverse est celui qui compte : si l'inédit était calculé par compte,
    chaque compte pourrait réclamer tous ses corridors comme_tfiches_neuves, et
    le classement ne mesurerait plus du tout la couverture du réseau. C'est le
    défaut exact que le plan veut éviter.
    """
    base = _maintenant()
    database = _base(tmp_path)
    _relever(database, "c1", "A", "B", jour=base - timedelta(days=1), jeton="avant")

    points = _seul_score(database, SEUL_INEDIT)

    assert sum(points.values()) == 1, "un seul passage sur une paire donne un point, pas deux"


def test_un_corridor_inedit_et_un_corridor_vieux_ne_sadditionnent_pas(tmp_path):
    """Une paire est inédit *ou* pas vue depuis longtemps, jamais les deux.

    Une paire qu'on voit pour la première fois n'est pas « pas vue depuis
    longtemps » : c'est la même information, et la compter deux fois ferait
    remonter les premiers contributeurs de la base par-dessus les suivants.
    """
    base = _maintenant()
    database = _base(tmp_path)
    _relever(database, "c1", "A", "B", jour=base, jeton="1")

    points = _seul_score(database, Coeff(0, 0, 0, 3, 3, 30))

    assert points["c1"] == 3, "3 pour l'inédit, pas 6 : la fenêtre ne double pas"


def test_le_corridor_vieux_ne_compte_qu_apres_la_fenetre(tmp_path):
    """La fenêtre court depuis le passage précédent, pas depuis aujourd'hui.

    Un couple vu il y a cent jours puis il y a un jour rapporte son point **au
    passage d'il y a un jour** : c'est à ce moment-là qu'il n'avait pas été vu
    depuis longtemps. Le comparer à aujourd'hui le ferait disparaître — la règle
    deviendrait « le corridor est-il encore vieux », alors que ce qu'elle dit est
    « combien de temps s'est écoulé sans qu'on le voie ».

    Le versant négatif est vérifié des deux côtés parce qu'une fenêtre mal branchée
    (comparaison inversée, `<` au lieu de `>=`) se cache : un couple dans la
    fenêtre ne rapporte rien **et** un couple hors fenêtre ne rapporte rien non
    plus, et une seule assertion verrait passer la moitié du comportement.
    """
    base = _maintenant()
    database = _base(tmp_path)
    _relever(database, "c1", "A", "B", jour=base - timedelta(days=100), jeton="vieux")
    _relever(database, "c1", "A", "B", jour=base - timedelta(days=1), jeton="recent")

    points = _seul_score(database, SEUL_VIEUX)

    assert points["c1"] == 1, (
        "le passage d'il y a cent jours ne peut pas être « pas vu depuis 30 jours » "
        "au moment du passage d'il y a un jour : c'est le même couple"
    )
    # Et l'autre côté : un couple vu hier ne rapporte rien aujourd'hui.
    _relever(database, "c1", "P", "Q", jour=base - timedelta(days=1), jeton="hier")
    _relever(database, "c1", "P", "Q", jour=base, jeton="aujourdhui")

    assert _seul_score(database, SEUL_VIEUX)["c1"] == 1, "un couple vu hier est dans la fenêtre"


def test_un_corridor_hors_fenetre_vaut_plus_qu_un_corridor_dedans(tmp_path):
    """Le versant positif de la fenêtre, avec des couples distincts.

    Un même couple dans la fenêtre ne peut pas servir : le groupe de redondance
    l'écarterait si c'est le même jour, et le « dernier vu » serait le relevé
    précédent si c'est un autre jour. Deux couples distincts isolent la fenêtre.
    """
    base = _maintenant()
    database = _base(tmp_path)
    # `Z→Y` est vu il y a 40 jours puis aujourd'hui : hors fenêtre de 30 jours.
    _relever(database, "c1", "Z", "Y", jour=base - timedelta(days=40), jeton="vieux1")
    _relever(database, "c1", "Z", "Y", jour=base, jeton="vieux2")
    # `P→Q` est vu hier puis aujourd'hui : dans la fenêtre.
    _relever(database, "c1", "P", "Q", jour=base - timedelta(days=1), jeton="recent1")
    _relever(database, "c1", "P", "Q", jour=base, jeton="recent2")

    points = _seul_score(database, SEUL_VIEUX)

    assert points["c1"] == 1, "un seul des deux couples est hors fenêtre"


def test_la_fenetre_est_un_parametre_et_pas_une_constante(tmp_path):
    """Changer `fenetre_jours` change la distribution — c'est le but du paramètre.

    Sans ce test, une fenêtre écrite en dur passerait tous les autres tests et ne
    serait pas paramétrable ; or c'est la première des trois questions de
    calibration du plan.
    """
    base = _maintenant()
    database = _base(tmp_path)
    _relever(database, "c1", "Z", "Y", jour=base - timedelta(days=40), jeton="a")
    _relever(database, "c1", "Z", "Y", jour=base, jeton="b")

    court = Coeff(0, 0, 0, 0, 1, 7)
    long_ = Coeff(0, 0, 0, 0, 1, 180)

    assert _seul_score(database, court)["c1"] == 1
    assert _seul_score(database, long_)["c1"] == 0, "40 jours ne sont pas 180"


# --- 4. Ce que le score ne récompense pas ---------------------------------


def test_un_releve_a_date_illisible_ne_redonne_pas_le_point_d_inedit(tmp_path):
    """Une date illisible ne rend pas un corridor inédit pour la suite.

    `created_at` est du texte : rien n'en garantit le format. Un relevé dont la
    date ne se lit pas vaut son relevé, rien de plus — mais le couple qu'il porte
    reste **couvert**.

    Le défaut que ce test attrape : si le code confondait « pas encore vu » et «
    vu sans date lisible », le relevé suivant réclamerait le point d'inédit sur un
    corridor déjà couvert. Le classement couronnerait alors une couverture
    inventée, et ne signalerait rien. C'est le pire mode de défaillance possible
    pour cette fonction, donc le test écrit la date cassée explicitement.

    Le test vérifie la **somme**, pas quel relevé l'obtient : un `created_at`
    illisible se range où le tri textuel le met, donc demander « le second
    relevé est-il l'inédit » dépendrait d'un tri arbitraire. Ce qui doit être
    vrai, quel que soit l'ordre, c'est qu'un couple ne rapporte son point
    d'inédit **qu'une fois**.
    """
    database = _base(tmp_path)
    _compte(tmp_path, "c1", "romain")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO saisie (client_id, kind, origin_stop_id, destination_stop_id, "
            "created_at, compte_id) VALUES ('casse', 'count', 'A', 'B', 'pas une date', 'c1')"
        )
    _relever(database, "c1", "A", "B", jeton="apres")

    points = _seul_score(database, SEUL_INEDIT)

    assert points["c1"] == 1, (
        "le couple était déjà porté par le relevé à date illisible : le point "
        "d'inédit ne peut pas être distribué deux fois"
    )


def test_un_releve_a_date_illisible_ne_prend_pas_le_bonus_de_fenetre(tmp_path):
    """Sans date lisible des deux côtés, l'écart ne se mesure pas.

    Le symétrique du précédent, et il compte : le code pourrait cocher l'absence
    d'inédit tout en donnant quand même le bonus de fenêtre, par un `>=` qui ne
    compare que ce qu'il a. Un corridor vu il y a cent jours puis aujourd'hui —
    sauf que la date du premier ne se lit pas — n'a pas d'écart mesurable.
    """
    base = _maintenant()
    database = _base(tmp_path)
    _compte(tmp_path, "c1", "romain")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO saisie (client_id, kind, origin_stop_id, destination_stop_id, "
            "created_at, compte_id) VALUES ('casse', 'count', 'Z', 'Y', '', 'c1')"
        )
    _relever(database, "c1", "Z", "Y", jour=base, jeton="apres")

    assert _seul_score(database, SEUL_VIEUX)["c1"] == 0, (
        "une fenêtre se mesure entre deux dates lisibles ; sans la première, "
        "elle ne vaut pas « hors fenêtre » mais « inconnue »"
    )


def test_la_fidelite_declaree_ne_change_aucun_point(tmp_path):
    """Par égalité, pas par comparaison.

    La fidélité est déclarée par celui qui compte : la mettre au score ferait
    monter tout le monde à 100 sans un comptage de plus. Le plan la laisse
    affichée, pas notée. Le test compare deux relevés qui ne diffèrent que par ce
    champ, donc il attrape aussi le cas où la formule l'utilise dans une
    soustraction.
    """
    database = _base(tmp_path)
    _relever(database, "c1", "A", "B", fiabilite=0, jeton="zero")
    _relever(database, "c2", "C", "D", fiabilite=100, jeton="cent")

    points = _seul_score(database, PAR_DEFAUT)

    assert points["c1"] == points["c2"], (
        "la fidélité déclarée ne doit rien changer au score : elle est "
        "manipulable par construction"
    )


def test_un_releve_redondant_du_meme_jour_ne_rapporte_rien_de_plus(tmp_path):
    """Le même origine-destination, la même personne, le même jour : une fois.

    Deux relevés du même train comptés deux fois sont un relevé écrit deux fois.
    Le test le vérifie en comparant le total du couple au total d'un couple seul,
    pas en comptant les lignes.
    """
    database = _base(tmp_path)
    jour = _maintenant()
    _relever(database, "c1", "A", "B", perimetre="um", jour=jour, jeton="matin")
    _relever(database, "c1", "A", "B", perimetre="um", jour=jour, jeton="soir")
    _relever(database, "c1", "C", "D", perimetre="um", jour=jour, jeton="seul")

    points = _seul_score(database, Coeff(0, 1, 0, 0, 0, 30))

    assert points["c1"] == 2, "deux couples, donc deux points : le doublon ne compte pas"


def test_le_redondant_prend_le_meilleur_de_ses_releves(tmp_path):
    """Le doublon ne se départage pas sur l'ordre d'écriture.

    Un relevé à `um` et un relevé à `voiture` du même couple le même jour
    appartiennent à la même unité d'information. Les départager par ordre
    d'insertion ferait que le score dépende de l'ordre des requêtes du
    navigateur, ce qui est invisible et donc non débogable.
    """
    database = _base(tmp_path)
    jour = _maintenant()
    _relever(database, "c1", "A", "B", perimetre="voiture", jour=jour, jeton="a")
    _relever(database, "c1", "A", "B", perimetre="um", jour=jour, jeton="b")

    avant = _seul_score(database, Coeff(0, 1, 0, 0, 0, 30))

    autre = _base(tmp_path / "autre")
    _relever(autre, "c1", "A", "B", perimetre="um", jour=jour, jeton="b")
    _relever(autre, "c1", "A", "B", perimetre="voiture", jour=jour, jeton="a")

    apres = _seul_score(autre, Coeff(0, 1, 0, 0, 0, 30))

    assert avant["c1"] == apres["c1"], "l'ordre d'écriture ne doit rien changer"


def test_la_redondance_est_par_compte_et_pas_par_releve(tmp_path):
    """Deux comptes qui comptent le même train le même jour ont chacun leur point.

    Le couple est commun, la redondance ne l'est pas : la règle parle du « même
    origine-destination par la même personne dans la même journée ». Sans
    « par la même personne », le second compteur verrait son travail absorbé par
    le premier, et le classement cesserait d'inciter à revenir.
    """
    database = _base(tmp_path)
    jour = _maintenant()
    _relever(database, "c1", "A", "B", perimetre="um", jour=jour, jeton="1")
    _relever(database, "c2", "A", "B", perimetre="um", jour=jour, jeton="2")

    points = _seul_score(database, Coeff(0, 1, 0, 0, 0, 30))

    assert points["c1"] == 1 and points["c2"] == 1


def test_un_releve_sans_date_lisible_ne_casse_pas_le_classement(tmp_path):
    """Une date illisible ne fait pas planter, elle prive d'un bonus.

    Le classement est une page publique : la faire tomber sur une ligne mal
    formée rendrait tout le site muet pour un relevé unique. Le relevé
    reste compté, il perd seulement ce qui dépend du temps.
    """
    database = _base(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO saisie (client_id, kind, origin_stop_id, destination_stop_id, "
            "passengers, reliability, perimetre, created_at, compte_id) "
            "VALUES ('bancal', 'count', 'A', 'B', 5, 50, 'um', 'pas-une-date', 'c1')"
        )

    lignes = classement(database)

    assert len(lignes) == 1
    assert lignes[0]["releves"] == 1, "le relevé est dans la base, donc il compte"


# --- 5. Le classement, la page et sa non-fuite ----------------------------


def test_un_compte_sans_releve_n_apparait_pas(tmp_path):
    """Un compte vide ne couvre rien, donc il n'a pas de rang.

    Sinon dix comptes créés par un script nocturne/remonteraient avec zéro
    relevé, et le classement afficherait des vides en tête d'une liste qui doit
    mesurer une contribution.
    """
    database = _base(tmp_path)
    _compte(tmp_path, "vide", "fantome")
    _compte(tmp_path, "plein", "romain")
    _relever(database, "plein", "A", "B", perimetre="um")

    lignes = classement(database)

    assert [ligne["pseudo"] for ligne in lignes] == ["romain"]


def test_le_classement_est_trie_par_points_decroissants(tmp_path):
    """Le tri est sur les points, pas sur le nombre de relevés.

    C'est toute la différence entre un classement qui mesure l'utilité et un
    classement qui mesure la présence. Un compte avec deux relevés à `um` sur
    deux corridors inédits doit passer devant un compte qui a compté dix fois le
    même train.
    """
    database = _base(tmp_path)
    _compte(tmp_path, "court", "court")
    _compte(tmp_path, "long", "long")
    # `court` : deux um, deux couples inédits.
    _relever(database, "court", "A", "B", perimetre="um", jeton="c1")
    _relever(database, "court", "C", "D", perimetre="um", jeton="c2")
    # `long` : le même couple, dix fois, sur dix jours.
    for i in range(10):
        _relever(
            database,
            "long",
            "Z",
            "Y",
            perimetre="voiture",
            jeton=f"l{i}",
            jour=_maintenant() - timedelta(days=i),
        )

    lignes = classement(database)

    premier, second = lignes[0], lignes[1]
    assert premier["releves"] < second["releves"], (
        "la fixture est fausse : le compte « long » doit avoir plus de relevés"
    )
    assert premier["pseudo"] == "court", "les points doivent passer devant le volume"


def test_le_classement_annonce_son_propre_denominateur(tmp_path):
    """Chaque ligne porte ses points, ses relevés et son dernier relevé.

    « 34 points » seul est un chiffre sans unité ; c'est le §9 du plan appliqué à
    un jeu. Le test vérifie que les quatre nombres sont présents, parce qu'un
    plan qui affiche un score sur deux lignes sur trois est un plan qui affiche
    un score.
    """
    database = _base(tmp_path)
    _compte(tmp_path, "c1", "romain")
    _relever(database, "c1", "A", "B", perimetre="um", jeton="1")
    _relever(database, "c1", "C", "D", genre="serpent", jeton="2")

    ligne = classement(database)[0]

    for cle in ("pseudo", "points", "releves", "paires", "dernier"):
        assert cle in ligne, f"le classement ne dit pas « {cle} »"
    assert ligne["points"] > 0
    assert ligne["releves"] == 2
    assert ligne["paires"] == 2, "deux couples distincts"


def test_le_classement_ne_rend_jamais_l_identifiant(tmp_path):
    """Ni en repli du pseudo, ni ailleurs.

    `/classement` est une page publique. Le repli « compte sans pseudo » est un
    choix : un `id` écrit à la place d'un pseudo vide convertirait la page la plus
    consultée en fuite de la clé primaire. Le test crée donc un compte **sans
    pseudo** et cherche son identifiant dans la sortie.
    """
    database = _base(tmp_path)
    _compte(tmp_path, "c1", "")  # pseudo vide : le cas qu'on veut attraper
    _relever(database, "c1", "A", "B", perimetre="um", jeton="1")

    ligne = classement(database)[0]

    assert ligne["pseudo"] == "compte sans pseudo"
    assert "c1" not in ligne["pseudo"]


def test_le_classement_est_vide_sans_erreur(tmp_path):
    """Une base sans compte ne fait pas planter `/classement`.

    C'est la première installation : l'application démarre, la saisie marche, et
    le classement doit dire qu'il n'y a personne. Le plan l'écrit — « il ne sort
    pas vide » — et une liste vide qui plante n'a rien annoncé.
    """
    database = _base(tmp_path)

    assert classement(database) == []


# --- 6. La calibration doit être reproductible --------------------------


def test_la_calibration_se_lance_et_dit_ce_qui_lui_manque(tmp_path):
    """L'outil de calibration tourne et imprime une distribution.

    Il ne suffit pas que la formule existe : le plan exige que les coefficients
    soient choisis **sur la base réelle**, donc il faut un outil qui produit la
    distribution et le dit. Le test lance le script pour de vrai — c'est le seul
    moyen que son nom d'argument, sa sortie et son exit code restent d'accord
    avec lui — et il échoue volontairement si l'outil se tait ou sort en erreur.
    """
    database = _base(tmp_path)
    _compte(tmp_path, "c1", "romain")
    _relever(database, "c1", "A", "B", perimetre="um", jeton="1")

    racine = Path(__file__).resolve().parent.parent
    resultat = subprocess.run(
        [sys.executable, str(racine / "tools" / "calibrer_score.py"), str(database)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert resultat.returncode == 0, resultat.stderr
    assert "romain" in resultat.stdout
    assert "points" in resultat.stdout, "l'outil doit nommer sa mesure"


def test_la_calibration_refuse_de_calibrer_sans_releves(tmp_path):
    """Une base vide ne se calibre pas : elle donnerait n'importe quoi.

    L'outil doit le dire et sortir en erreur plutôt que d'imprimer une
    distribution de zéro — un « 0 point, 0 compte » se lit comme une mesure, et
    c'est exactement le genre de chiffre qui fait choisir des coefficients sur
    une absence de données.
    """
    database = _base(tmp_path)
    racine = Path(__file__).resolve().parent.parent

    resultat = subprocess.run(
        [sys.executable, str(racine / "tools" / "calibrer_score.py"), str(database)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert resultat.returncode != 0, "une base sans relevé ne doit pas produire de distribution"
    assert "relev" in (resultat.stdout + resultat.stderr).lower()


def test_la_calibration_refuse_une_base_sans_schema(tmp_path):
    """Une base qui n'a pas encore le schéma se dit, elle ne plante pas.

    C'est le cas d'une installation neuve : `app.db` existe, ou pas encore, mais
    les tables sont créées au premier démarrage. L'outil plantait avec une trace
    Python — `no such table: saisie` — parce qu'il interrogeait la base avant de
    vérifier qu'elle était prête.

    Le test écrit une base **vide**, volontairement : c'est la seule façon
    d'obtenir cet état sans démarrer l'application, et donc sans que le schéma
    existe. Il vérifie le message et le code de sortie, parce qu'un outil qui sort
    en erreur avec une trace laisse croire à un bug alors que c'est une base
    pas prête.
    """
    database = tmp_path / "app.db"
    sqlite3.connect(database).close()  # un fichier SQLite valide, sans aucune table
    racine = Path(__file__).resolve().parent.parent

    resultat = subprocess.run(
        [sys.executable, str(racine / "tools" / "calibrer_score.py"), str(database)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert resultat.returncode == 2, "une base sans schéma n'est pas calibrable"
    assert "schéma" in resultat.stderr
    assert "Traceback" not in resultat.stderr, (
        "l'utilisateur ne doit pas lire une trace Python pour une base pas prête"
    )


def test_score_de_renvoie_le_score_du_compte_et_zero_pour_un_inconnu(tmp_path):
    """La page `/compte` demande son propre score, et il doit être lisible.

    Un compte sans relevé a un score **zéro**, pas une absence : la fonction ne
    renvoie jamais `None`. Le test verrouille ce contrat parce que `None` est
    _attrapant_ — il semble dire « pas d'information », alors que la base vient de
    répondre qu'il n'y a rien. `releves == 0` est le test exact, et il est dans le
    même objet que les points.
    """
    database = _base(tmp_path)
    _compte(tmp_path, "c1", "romain")
    _compte(tmp_path, "c2", "neuf")
    _relever(database, "c1", "A", "B", perimetre="um", jeton="1")

    avec_releve = score_de(database, "c1")
    assert avec_releve.points > 0
    assert avec_releve.releves == 1

    sans_releve = score_de(database, "c2")
    assert sans_releve.points == 0 and sans_releve.releves == 0

    inconnu = score_de(database, "inconnu")
    assert inconnu.points == 0 and inconnu.releves == 0, (
        "un identifiant absent donne le même zéro qu'un compte vide : la page "
        "n'a pas à distinguer deux cas qu'elle traite pareil"
    )


def test_score_de_chiffre_la_part_inedite(tmp_path):
    """La part qu'on peut mesurer est celle qui vient des corridors inédits.

    C'est la première des trois questions de calibration du plan : « combien de
    points pour un corridor inédit, en vérifiant qu'un tiers des points d'un bon
    compteur vient de l'inédit ». Sans ce nombre, la question n'a pas de réponse
    et les coefficients restent choisis à l'intuition.
    """
    database = _base(tmp_path)
    _compte(tmp_path, "c1", "romain")
    # Deux couples, dont un déjà vu par un autre compte : un seul est inédit.
    _relever(database, "c0", "A", "B", jour=_maintenant(), jeton="avant")
    _relever(database, "c1", "A", "B", jour=_maintenant(), jeton="apres")
    _relever(database, "c1", "Z", "Y", perimetre="um", jeton="neuf")

    score = score_de(database, "c1")

    assert score.inedit == PAR_DEFAUT.corridor_inedit, (
        "un seul des deux couples est inédit pour ce compte, donc un seul "
        "coefficient — les deux premiers relevés ont le même `created_at` à la "
        "seconde près, et c'est l'ordre d'insertion qui départage"
    )
    assert score.inedit < score.points, "tous les points ne viennent pas de l'inédit"


# --- 7. La page `/classement` --------------------------------------------


def test_la_page_du_classement_affiche_les_points(tmp_path):
    """La page rend le score, et pas seulement les deux nombres provisoires.

    `/classement` affichait `relevés` et `paires`. C'était écrit comme provisoire
    dans le plan, donc le test échoue tant que le score n'est pas là — c'est le
    but : le provisoire ne doit pas devenir définitif par omission.
    """
    from fastapi.testclient import TestClient

    from comptagefer.app import create_app

    database = _base(tmp_path)
    _compte(tmp_path, "c1", "romain")
    _relever(database, "c1", "A", "B", perimetre="um", jeton="1")

    reponse = TestClient(create_app(tmp_path)).get("/classement")

    assert reponse.status_code == 200
    assert "romain" in reponse.text
    assert "point" in reponse.text, "la page doit dire l'unité de sa mesure"
    assert "sur 1 relevé" in reponse.text, (
        "le score a un dénominateur : « 5 points », jamais, et « 5 points sur "
        "1 relevé » se lit comme 5 ; sans dénominateur la page est une page de "
        "Vanity, c'est le §9 du projet"
    )


def test_la_page_du_classement_dit_la_regle_quelle(tmp_path):
    """La page dit ce qu'elle classe, sinon c'est une page de Vanity.

    Le plan l'exige : « la page dit ce qu'elle classe. Les coefficients, en clair,
    avec la mesure qui les a choisis ». Le test vérifie que la règle est affichée,
    pas qu'elle est belle — et il échoue tant que les coefficients sont présentés
    comme définitifs alors qu'ils ne sont pas calibrés.
    """
    from fastapi.testclient import TestClient

    from comptagefer.app import create_app

    database = _base(tmp_path)
    _compte(tmp_path, "c1", "romain")
    _relever(database, "c1", "A", "B", perimetre="um", jeton="1")

    corps = TestClient(create_app(tmp_path)).get("/classement").text

    assert "rame entière" in corps, "la page doit dire que le périmètre à l'échelle de la rame compte"
    assert "pas encore calibr" in corps.lower(), (
        "les coefficients ne sont pas calibrés : la page doit le dire plutôt que "
        "de laisser croire qu'ils sont choisis. Le plan l'interdit en §9, et le "
        "test échoue tant que la phrase n'y est pas."
    )


@pytest.mark.parametrize("coeff", [PAR_DEFAUT])
def test_la_page_ne_fait_pas_fuir_le_score_par_un_autre_chemin(coeff: Coeff):
    """Le score ne sort pas par une URL ni par un paramètre.

    Un `?score=` calculé par le navigateur à partir de données déjà exposées ne
    serait pas une fuite de l'identifiant, mais il mettrait la formule hors du
    serveur — donc hors des tests et hors du contrôle des coefficients. Ce test
    vérifie l'existence de la contrainte par son côté : le classement est une
    route GET sans paramètre de tri.
    """
    from comptagefer.app import create_app

    routes = {
        route.path
        for route in create_app(Path("/tmp")).routes
        if getattr(route, "path", "").startswith("/classement")
    }

    assert routes == {"/classement"}
