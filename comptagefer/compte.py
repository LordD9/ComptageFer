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
a que son SHA-256. La comparaison est en temps constant, donc un secret d'un
autre compte n'ouvre rien et ne se distingue pas par un temps de réponse.
Perdre le secret, c'est perdre le compte : il n'y a pas d'adresse à qui écrire.
C'est assumé, et écrit dans le plan.

**Le secret n'est pas le mot de passe quotidien.** Le cookie
`comptagefer_compte` tient la session, donc le secret est tapé rarement — et un
secret tapé 47 fois par jour finit noté sur un papier.

Ce module ne décide pas du score : le score est dans `app.py`, avec la requête
qui le lit, parce qu'il n'a pas d'état propre.
"""

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from pathlib import Path

from starlette.responses import Response

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
    identifiant = jeton_compte()
    secret = generer_secret()
    with sqlite3.connect(database) as connection:
        creer_schema(connection)
        connection.execute(
            "INSERT INTO compte (id, pseudo, secret, cree_le) VALUES (?, ?, ?, ?)",
            (identifiant, pseudo[:40], hacher(normaliser(secret)), time.time()),
        )
    return identifiant, secret


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
    """Le compte qu'un secret ouvre, ou `None`.

    La lecture ne se fait pas par un `WHERE secret = ?` suivi d'un test de
    nullité : cela compare en base et non en temps constant. Le `SELECT` ramène
    tous les hachages et la comparaison se fait ici, avec `compare_digest`. Sur
    quelques dizaines de comptes le coût est le même ; ce qui est protégé est le
    principe, pas une mesure — et il est écrit là parce qu'un `WHERE` serait plus
    court et passerait les tests sans rien changer.
    """
    if not secret:
        return None
    recherche = hacher(normaliser(secret))
    trouve = None
    with sqlite3.connect(database) as connection:
        creer_schema(connection)
        for identifiant, stocke in connection.execute("SELECT id, secret FROM compte"):
            if stocke is None:
                continue
            if hmac.compare_digest(stocke, recherche):
                trouve = str(identifiant)
                break
    return trouve


def pseudo_de(database: Path, identifiant: str) -> str:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT pseudo FROM compte WHERE id = ?", (identifiant,)
        ).fetchone()
    return "" if row is None else str(row[0])


def ouvrir_session(database: Path, identifiant: str) -> tuple[str, float]:
    """Une session, et son expiration.

    Renvoie le jeton en clair — il va dans le cookie et nulle part ailleurs — et
    la date d'expiration, pour que l'appelant fixe le `max_age` du cookie sur la
    même valeur que la ligne en base. Les deux venir de deux endroits, c'est la
    session qui expire dans le cookie mais pas en base, ou l'inverse.
    """
    jeton = secrets.token_urlsafe(_LONGUEUR_JETON)
    maintenant = time.time()
    expire = maintenant + SESSION_SECONDES
    with sqlite3.connect(database) as connection:
        creer_schema(connection)
        connection.execute(
            "INSERT INTO session (jeton, compte_id, ouverte, expire) VALUES (?, ?, ?, ?)",
            (hacher(jeton), identifiant, maintenant, expire),
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
            "SELECT compte_id, expire FROM session WHERE jeton = ?", (hacher(jeton),)
        ).fetchone()
        if row is None:
            return None
        if row[1] < maintenant:
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
        secure=bool(os.environ.get("COMPTAGEFER_HTTPS")),
        max_age=int(max(0, expire - time.time())),
        path="/",
    )


def retirer_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_COMPTE, path="/")


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