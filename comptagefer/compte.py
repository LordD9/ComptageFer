"""Le compte facultatif : son schéma, son secret, sa session.

Ce module porte ce que la phase 9 ajoute à `app.db`. Il est séparé d'`app.py`
pour une raison de taille — ce fichier est déjà à deux mille lignes — et pour
une raison de règle : **ce qui décide de ce qu'un compte peut devenir tient dans
un seul fichier**, et il est lisible d'un coup.

Trois décisions ne se devinent pas dans le code.

**Aucune adresse email n'est stockée.** Le plan prévoyait une connexion par un
lien envoyé par email ; c'est retiré. Une adresse est un identifiant *direct* :
elle relie toute l'activité future d'une personne à une identité réelle, et elle
sert ailleurs qu'ici — clé d'envoi, identifiant de connexion, moyen de la
croiser avec n'importe quelle fuite. Le §3 du projet dit « anonyme par défaut »
et « pas de trace GPS » ; un email dans la base rendrait les deux faux. Un
secret long ne dit rien de la personne qui le détient. Il n'y a donc pas de
colonne `email`, et il ne doit pas y en avoir une « pour plus tard » : une
colonne vide prévue pour être remplie est une colonne qui se remplira.

**Le secret est affiché une fois.** `/compte` tire 24 caractères, groupes de 4
pour être recopiés sans faute, et ne les rend qu'à la création. En base il n'y
a que son SHA-256, recherché par index. Un secret d'un autre compte n'ouvre
pas celui-ci ; le pseudo ne décide jamais de l'identité.
Perdre le secret, c'est perdre le compte : il n'y a pas d'adresse à qui écrire.
C'est assumé, et écrit dans le plan.

**Le secret n'est pas le mot de passe quotidien.** Le cookie
`comptagefer_compte` tient la session, donc le secret est tapé rarement — et un
secret tapé 47 fois par jour finit noté sur un papier.

Ce module ne décide pas du score : le score est dans `app.py`, avec la requête
qui le lit, parce qu'il n'a pas d'état propre.
"""

import hashlib
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from starlette.responses import Response

from comptagefer.securite import https_actif

# Le nom du cookie qui porte la session. Il est dans ce module et nulle part
# ailleurs : une constante écrite deux fois finit par diverger, et le symptôme
# est une session qui marche sur une page et pas sur l'autre.
COOKIE_COMPTE = "comptagefer_compte"

# La longueur du secret, hors séparateurs. 24 caractères d'un alphabet de 32
# donnent 120 bits, ce qui est très au-dessus de ce qu'exige une base qu'on ne
# peut pas attaquer en ligne : le risque ici est une base copiée, pas une
# énumération.
LONGUEUR_SECRET = 24

# L'alphabet exclut `o`, `l` et `i`, et garde le `0`. Un secret se recopie à la
# main, et un caractère ambigu se corrige de travers en le recopiant : le
# caractère le moins lisible de l'alphabet est moins cher qu'une heure de
# messages sur « c'est un zéro ou un o ».
ALPHABET = "0123456789abcdefghijkmnpqrstuvwxyz"

GROUPE = 4

# Trente jours. C'est long, et c'est voulu : la personne ne tape son secret qu'à
# la création et qu'au changement d'appareil. Une session qui expire vite
# obligerait à le ressaisir, donc à le garder sous la main — ce qu'on évite.
SESSION_SECONDES = 3600 * 24 * 30

_LONGUEUR_JETON = 32

# Plafond global sans conserver d'IP. La transaction rend le quota partagé
# entre les requêtes concurrentes et il survit au redémarrage du serveur.
CREATIONS_PAR_HEURE = 50
# Dix appareils simultanés ; une connexion de plus remplace la plus ancienne.
SESSIONS_PAR_COMPTE = 10


class QuotaCreationAtteint(Exception):
    """Le plafond horaire est atteint ; les comptes existants restent ouverts."""


def hacher(secret: str) -> str:
    """Le SHA-256 d'un secret ou d'un jeton, en hexadécimal.

    Le SHA-256 convient ici pour une raison précise : ce n'est pas un mot de
    passe que quelqu'un choisit et réutilise ailleurs, donc il n'y a pas de
    dictionnaire à construire. C'est un secret aléatoire de 120 bits, et le
    hachage le rend illisible en base sans être lent à calculer — la lenteur
    serait ici un coût payé à chaque `POST /api/sessions`, dans un train, pour
    protéger un secret qu'un attaquant ne peut pas deviner de toute façon.
    """
    return hashlib.sha256(secret.encode()).hexdigest()


def generer_secret() -> str:
    """Un secret neuf, en clair, groupes de 4.

    Il est rendu une fois, à la création, et c'est la seule fois. Ce qui va en
    base est son hash, produit par `hacher`.
    """
    brut = "".join(secrets.choice(ALPHABET) for _ in range(LONGUEUR_SECRET))
    morceaux = [brut[i : i + GROUPE] for i in range(0, len(brut), GROUPE)]
    return "-".join(morceaux)


def jeton_compte() -> str:
    """L'identifiant interne d'un compte, en clair.

    Il est tiré au hasard et non par un compteur : un `AUTOINCREMENT` se devine,
    et un identifiant devinable est un identifiant qui fuite dès qu'il sort par
    une URL ou un journal. Cette fonction est distincte de `generer_secret` —
    l'un s'affiche, l'autre ne quitte jamais le serveur — mais les deux doivent
    être tirés au hasard, donc ils partent du même principe.
    """
    return secrets.token_hex(16)


def creer_schema(connection: sqlite3.Connection) -> None:
    """Les deux tables du compte, créées si elles n'existent pas.

    `compte` et `session` sont dans `app.db` et pas dans un fichier de plus : un
    compte référence ses relevés, et deux fichiers SQLite signifieraient deux
    connexions et une transaction qui ne couvre pas les deux.

    Pas de `NOT NULL` sur `secret` : une base créée avant la phase 9 a déjà des
    lignes, et la contrainte rendrait impossible de leur laisser la colonne
    vide. Un secret absent veut dire un compte antérieur à la connexion, pas un
    compte cassé.
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS compte (
            id TEXT PRIMARY KEY,
            pseudo TEXT NOT NULL,
            secret TEXT,
            cree_le REAL NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS session (
            jeton TEXT PRIMARY KEY,
            compte_id TEXT NOT NULL,
            ouverte REAL NOT NULL,
            expire REAL NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS signalement (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            saisie_client_id TEXT NOT NULL,
            saisie_kind TEXT NOT NULL,
            compte_id TEXT NOT NULL,
            motif TEXT NOT NULL,
            cree_le REAL NOT NULL
        )
        """
    )
    # Un même relevé signalé deux fois par le même compte ne doit pas remplir la
    # file de modération. La contrainte porte sur le triplet, pas sur le relevé
    # seul : deux personnes peuvent signaler le même relevé, chacune son avis.
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS signalement_unique
        ON signalement (saisie_client_id, saisie_kind, compte_id)
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS compte_secret ON compte (secret)")
    connection.execute("CREATE INDEX IF NOT EXISTS compte_creation ON compte (cree_le)")
    connection.execute("CREATE INDEX IF NOT EXISTS session_expire ON session (expire)")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS session_compte ON session (compte_id, ouverte)"
    )


def _ajouter_compte_id(connection: sqlite3.Connection) -> None:
    """La colonne `saisie.compte_id`, ajoutée à une base qui existait déjà.

    `NULL` sur les relevés antérieurs : ils comptent dans les données et pas au
    classement. C'est le seul comportement possible sans deviner à qui
    appartient un relevé écrit avant que le compte existe.
    """
    colonnes = {row[1] for row in connection.execute("PRAGMA table_info(saisie)")}
    if "compte_id" not in colonnes:
        connection.execute("ALTER TABLE saisie ADD COLUMN compte_id TEXT")


def preparer(connection: sqlite3.Connection) -> None:
    """Le point d'entrée appelé au démarrage de l'application.

    Les tables du compte, et la colonne sur `saisie`. `connection` est celle que
    `create_app` a déjà ouverte : la base est créée et migrée en un endroit, et
    c'est le même que pour `trajet`, dont la migration avait perdu une colonne
    parce qu'elle était écrite ailleurs.

    L'ordre avec `_clef_par_genre` n'est pas négociable, et il se lit dans
    `app.py` : cette fonction recrée la table à partir de `SCHEMA_SAISIE`, donc
    `compte_id` doit être ajoutée **après**.
    """
    creer_schema(connection)
    _ajouter_compte_id(connection)


def creer_compte(database: Path, pseudo: str) -> tuple[str, str]:
    """Un compte, son identifiant, et son secret en clair.

    Le secret est renvoyé **une seule fois**, et l'appelant doit l'afficher puis
    l'oublier. Il n'est stocké que haché. Le renvoyer dans le tuple, plutôt que
    de le relire en base, est ce qui rend l'oubli possible : il n'y a rien à
    relire.

    Le hachage porte sur la forme **normalisée**, pas sur le secret affiché avec
    ses tirets. C'est ce qui fait que le secret affiché ouvre le compte quand il
    est renvoyé tel quel : hacher la forme affichée ici et la forme sans tirets à
    la connexion donnerait deux hachages différents du même secret, et le compte
    serait inatteignable. La forme stockée est donc la seule qui compte, et elle
    est calculée par `normaliser` — la même fonction que la connexion.
    """
    with _transaction(database) as connection:
        return _creer_compte(connection, pseudo)


@contextmanager
def _transaction(database: Path):
    """Une écriture sérialisée, validée en entier ou annulée, puis fermée."""
    with closing(sqlite3.connect(database)) as connection, connection:
        creer_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        yield connection


def _creer_compte(connection: sqlite3.Connection, pseudo: str) -> tuple[str, str]:
    identifiant = jeton_compte()
    secret = generer_secret()
    maintenant = time.time()
    nombre = connection.execute(
        "SELECT COUNT(*) FROM compte WHERE cree_le > ?", (maintenant - 3600,)
    ).fetchone()[0]
    if nombre >= CREATIONS_PAR_HEURE:
        raise QuotaCreationAtteint
    connection.execute(
        "INSERT INTO compte (id, pseudo, secret, cree_le) VALUES (?, ?, ?, ?)",
        (identifiant, pseudo[:40], hacher(normaliser(secret)), maintenant),
    )
    return identifiant, secret


def creer_compte_et_session(
    database: Path, pseudo: str, ancien_jeton: str = ""
) -> tuple[str, float, str]:
    """Créer, ouvrir et remplacer dans la même transaction.

    Si l'ouverture ou la révocation échoue, aucun compte inaccessible ne reste
    en base et l'ancienne session reste utilisable.
    """
    with _transaction(database) as connection:
        identifiant, secret = _creer_compte(connection, pseudo)
        jeton, expire = _ouvrir_session(connection, identifiant, ancien_jeton)
    return jeton, expire, secret


def normaliser(secret: str) -> str:
    """Un secret tapé par un humain, ramené à la forme qu'on a stockée.

    Les tirets sont retirés et la casse abaissée : quelqu'un qui colle son
    secret avec les séparateurs, ou en majuscules, doit se connecter quand même.
    C'est la transcription qui varie, pas le secret.

    On ne retire rien d'autre. Un espace autour ne fait pas partie du secret
    affiché, donc l'enlever évite qu'un copier-coller depuis un éditeur refuse
    l'accès à un compte qui existe.
    """
    return secret.strip().replace("-", "").lower()


def compte_de_secret(database: Path, secret: str) -> str | None:
    """Chercher le condensat complet d'un secret aléatoire, pas ses préfixes.

    Le parcours de tous les comptes coûtait O(N) à toute tentative anonyme et
    s'arrêtait au premier succès : il n'était pas en temps constant. L'index
    porte sur le SHA-256, jamais sur le secret en clair.
    """
    if len(secret) > 100:
        return None
    canonique = normaliser(secret)
    if len(canonique) != LONGUEUR_SECRET or any(c not in ALPHABET for c in canonique):
        return None
    with sqlite3.connect(database) as connection:
        creer_schema(connection)
        row = connection.execute(
            "SELECT id FROM compte WHERE secret = ? LIMIT 1", (hacher(canonique),)
        ).fetchone()
    return None if row is None else str(row[0])


def pseudo_de(database: Path, identifiant: str) -> str:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT pseudo FROM compte WHERE id = ?", (identifiant,)
        ).fetchone()
    return "" if row is None else str(row[0])


def ouvrir_session(
    database: Path, identifiant: str, ancien_jeton: str = ""
) -> tuple[str, float]:
    """Une session, et son expiration.

    Renvoie le jeton en clair — il va dans le cookie et nulle part ailleurs — et
    la date d'expiration, pour que l'appelant fixe le `max_age` du cookie sur la
    même valeur que la ligne en base. Les deux venir de deux endroits, c'est la
    session qui expire dans le cookie mais pas en base, ou l'inverse.
    """
    with _transaction(database) as connection:
        return _ouvrir_session(connection, identifiant, ancien_jeton)


def _ouvrir_session(
    connection: sqlite3.Connection, identifiant: str, ancien_jeton: str
) -> tuple[str, float]:
    jeton = secrets.token_urlsafe(_LONGUEUR_JETON)
    maintenant = time.time()
    expire = maintenant + SESSION_SECONDES
    connection.execute("DELETE FROM session WHERE expire <= ?", (maintenant,))
    # Remplacer avant le plafond : remplacer l'appareil le plus récent ne
    # doit pas déconnecter aussi le plus ancien, ni échouer à moitié.
    if ancien_jeton:
        connection.execute("DELETE FROM session WHERE jeton = ?", (hacher(ancien_jeton),))
    connection.execute(
        "INSERT INTO session (jeton, compte_id, ouverte, expire) VALUES (?, ?, ?, ?)",
        (hacher(jeton), identifiant, maintenant, expire),
    )
    connection.execute(
        "DELETE FROM session WHERE jeton IN ("
        "SELECT jeton FROM session WHERE compte_id = ? "
        "ORDER BY ouverte DESC, rowid DESC LIMIT -1 OFFSET ?)",
        (identifiant, SESSIONS_PAR_COMPTE),
    )
    return jeton, expire


def compte_de_session(database: Path, jeton: str) -> str | None:
    """Le compte d'un cookie de session, ou `None`.

    Une session expirée est supprimée au passage. Elle est déjà morte à cet
    instant, donc la garder ne sert à rien, et une base de sessions ne se purge
    pas toute seule : c'est le défaut que l'audit a relevé sur `admin_sessions`,
    qui est un dictionnaire en mémoire sans borne.
    """
    if not jeton:
        return None
    maintenant = time.time()
    with sqlite3.connect(database) as connection:
        creer_schema(connection)
        row = connection.execute(
            "SELECT session.compte_id, session.expire FROM session "
            "JOIN compte ON compte.id = session.compte_id WHERE session.jeton = ?",
            (hacher(jeton),),
        ).fetchone()
        if row is None:
            return None
        if row[1] <= maintenant:
            connection.execute(
                "DELETE FROM session WHERE jeton = ?", (hacher(jeton),)
            )
            return None
    return str(row[0])


def fermer_session(database: Path, jeton: str) -> None:
    if not jeton:
        return
    with sqlite3.connect(database) as connection:
        creer_schema(connection)
        connection.execute("DELETE FROM session WHERE jeton = ?", (hacher(jeton),))


def poser_cookie(response: Response, jeton: str, expire: float) -> None:
    """Le cookie de session, avec `secure` seulement quand le site est en HTTPS.

    Le `secure` est conditionnel parce qu'un cookie `secure` n'est jamais renvoyé
    sur une installation en `http://10.x` : le poser inconditionnellement ferait
    connecter puis déconnecter la personne à la navigation suivante, sans message.
    C'est le bug de configuration le plus coûteux de la phase, parce qu'il ne se
    voit qu'en production, sur le Pi d'origine et pas sur une installation
    fraîche en local.
    """
    response.set_cookie(
        COOKIE_COMPTE,
        jeton,
        httponly=True,
        samesite="lax",
        secure=https_actif(),
        max_age=int(max(0, expire - time.time())),
        path="/",
    )


def retirer_cookie(response: Response) -> None:
    response.delete_cookie(
        COOKIE_COMPTE, path="/", secure=https_actif(), httponly=True, samesite="lax"
    )


def nombre_de_releves(database: Path, identifiant: str) -> int:
    """Combien de relevés un compte a, tous genres compris.

    Compté à part de `releves_de`, qui est tronqué : déduire le total de la liste
    rendue ferait annoncer « 200 relevés » à quelqu'un qui en a quatre mille.
    """
    with sqlite3.connect(database) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM saisie WHERE compte_id = ?", (identifiant,)
            ).fetchone()[0]
        )


def releves_de(database: Path, identifiant: str, limite: int = 200) -> list[dict]:
    """Les relevés d'un compte, les plus récents d'abord.

    La limite est celle de `/comptages`, pas un hasard : au-delà de 200, la page
    qui les affiche doit dire qu'elle en tronque, donc autant que la requête
    prenne la même décision que l'affichage. Sinon la base et l'écran peuvent
    être d'accord sur un nombre et pas sur l'autre.
    """
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        lignes = connection.execute(
            "SELECT client_id, origin_name, destination_name, passengers, "
            "reliability, kind, created_at FROM saisie WHERE compte_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (identifiant, limite),
        ).fetchall()
    return [dict(ligne) for ligne in lignes]


# Un motif de signalement est du texte libre que l'admin lit, donc il est borné.
# La borne est celle d'un commentaire, pas d'une ligne de base : un signalement
# utile dit « c'est deux fois le même train compté », pas un roman.
LONGUEUR_MOTIF = 300


def signaler(database: Path, identifiant: str, client_id: str, kind: str, motif: str) -> bool:
    """Signaler un de ses relevés. Renvoie `False` si c'est déjà signalé.

    **Un signalement n'efface rien.** Il crée une ligne que l'admin voit dans
    `/admin`. Le plan le dit et le répète : être connecté ouvre le droit de dire
    « ce relevé n'est pas le mien », pas le droit de le retirer. Une suppression
    différée serait une modération par le signalement, et elle n'est pas
    instrumentée.

    La ligne cible est identifiée par `(client_id, kind)` — les deux, parce que la
    clé primaire de `saisie` est ce couple : un même navigateur peut signaler un
    train manquant et compter un train réel, et un `client_id` seul viserait les
    deux.

    Le relevé doit **appartenir au compte** qui signale. C'est vérifié ici, dans le
    `INSERT ... SELECT`, et pas par l'appelant : une route qui oublie le test
    laisserait n'importe qui signaler n'importe quel relevé en devinant un
    `client_id` — et un `client_id` est dans le CSV.

    `INSERT OR IGNORE` sur l'index unique : un second signalement du même relevé
    par la même personne ne crée pas de seconde ligne. Le plan ne dit pas ce que
    vaut un doublon, et une file de modération gonflée par un double-clic est un
    bruit qu'un humain doit trier.
    """
    if not motif.strip():
        return False
    with sqlite3.connect(database) as connection:
        creer_schema(connection)
        curseur = connection.execute(
            "INSERT OR IGNORE INTO signalement "
            "(saisie_client_id, saisie_kind, compte_id, motif, cree_le) "
            "SELECT client_id, kind, ?, ?, ? FROM saisie "
            "WHERE client_id = ? AND kind = ? AND compte_id = ?",
            (
                identifiant,
                motif.strip()[:LONGUEUR_MOTIF],
                time.time(),
                client_id,
                kind,
                identifiant,
            ),
        )
        # `rowcount` à 0 quand le `INSERT` n'a rien écrit, et ça arrive de deux
        # façons : le relevé n'existe pas ou n'est pas à ce compte, ou il est déjà
        # signalé. Les deux rendent `False` pour l'appelant, qui répondra « déjà
        # signalé » — un motif juste mais un relevé inexistant ne mérite pas
        # mieux, et le dire demande un test que `signaler_deja` ne fait pas.
        return curseur.rowcount > 0


def deja_signales(database: Path, identifiant: str) -> set[tuple[str, str]]:
    """Les `(client_id, kind)` déjà signalés par ce compte, en une lecture.

    La page `/compte` affiche jusqu'à 200 relevés. Les prendre un par un serait
    200 requêtes pour une page — le N+1 que `docs/regles.md` §4 interdit. Le
    tableau complet tient en mémoire quelques kilooctets même avec des milliers
    de signalements, donc il est ramené d'un coup et indexé par le couple.

    Cette fonction remplace `signaler_deja`, qui n'aurait servi qu'ici et aurait
    coûté une requête par relevé.
    """
    with sqlite3.connect(database) as connection:
        creer_schema(connection)
        lignes = connection.execute(
            "SELECT saisie_client_id, saisie_kind FROM signalement WHERE compte_id = ?",
            (identifiant,),
        ).fetchall()
    return {(str(client_id), str(kind)) for client_id, kind in lignes}


def signalements(database: Path, limite: int = 100) -> list[dict]:
    """Les signalements, les plus récents d'abord, pour l'admin.

    Joint à `saisie` pour que l'admin voie **le relevé** et pas seulement son
    identifiant : un signalement sans le trajet qu'il conteste n'est pas
    modérable. Les noms de gares sont lus au moment de la lecture, donc un
    signalement qui vise un relevé supprimé depuis reste visible avec une origine
    vide — ce qui est le cas à traiter en priorité.

    La jointure est sur `(client_id, kind)`, la clé primaire de `saisie` : sans le
    `kind`, un même navigateur et deux genres donneraient des lignes croisées, et
    l'admin verrait un signalement pointer vers le mauvais relevé.
    """
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        creer_schema(connection)
        lignes = connection.execute(
            "SELECT g.id, g.saisie_client_id, g.saisie_kind, g.motif, g.cree_le, "
            "c.pseudo AS auteur, s.origin_name, s.destination_name, s.passengers, "
            "s.created_at AS releve_le "
            "FROM signalement g "
            "JOIN compte c ON c.id = g.compte_id "
            "LEFT JOIN saisie s ON s.client_id = g.saisie_client_id "
            "AND s.kind = g.saisie_kind "
            "ORDER BY g.cree_le DESC, g.id DESC LIMIT ?",
            (limite,),
        ).fetchall()
    return [dict(ligne) for ligne in lignes]