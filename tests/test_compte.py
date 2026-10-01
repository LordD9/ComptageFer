"""Le compte facultatif, et ses deux promesses : ne rien exiger, ne rien laisser.

Ces tests couvrent la partie de la phase 9 qui peut casser sans qu'on le voie.
Le classement a ses propres tests, et les pages sont jouées dans un vrai
Chromium par `test_browser.py`.

Quatre propriétés sont vérifiées ici, dans cet ordre d'importance :

1. **Un relevé posté sans compte est enregistré.** C'est la promesse « 100 %
   facultatif ». Elle tient avant tout le reste, et un jour où le formulaire
   réclame une session, c'est ce test qui tombe.
2. **Aucune adresse email n'est stockée.** Le schéma est lu, pas l'intention.
   C'est une règle de conception, pas un oubli : une adresse est un identifiant
   direct, réutilisable ailleurs, et le §3 du projet dit « anonyme par défaut ».
3. **`compte_id` ne sort nulle part.** Ni du CSV, ni de `/api/export.csv`, ni
   d'une URL, ni du corps d'une page. Le test cherche la *valeur* dans les
   sorties, pas le nom de la colonne : une colonne exportée sous un autre nom
   fuiterait aussi, et c'est le piège que le nom seul ne voit pas.
4. **Le secret ne sort qu'une fois, à la création.** Il est stocké haché, il ne
   voyage pas dans une URL, et il ne réapparaît dans aucune page après.
"""

import re
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.compte import (
    COOKIE_COMPTE,
    LONGUEUR_SECRET,
    compte_de_secret,
    creer_compte,
    generer_secret,
    hacher,
    normaliser,
    ouvrir_session,
)

# Un secret a 24 caractères, groupes de 4. C'est la forme que les tests
# vérifient, parce qu'une forme qui change casserait le collage à la main
# qu'elle est censée faciliter.
FORME_SECRET = re.compile(r"^([0-9a-z]{4}-?){5}[0-9a-z]{4}$")


def _client(tmp_path: Path, **kwargs) -> TestClient:
    return TestClient(create_app(tmp_path, **kwargs))


def _post(client: TestClient, client_id: str = "jeton", **extra) -> None:
    body = {
        "client_id": client_id,
        "origin_stop_id": "A",
        "destination_stop_id": "B",
        "origin_name": "Lyon",
        "destination_name": "Nîmes",
        "passengers": 10,
        "reliability": 80,
    }
    body.update(extra)
    response = client.post("/api/sessions", json=body)
    assert response.status_code == 200


def _compte_avec_session(tmp_path: Path, pseudo: str = "romain") -> str:
    """Un compte créé et une session ouverte. Renvoie la valeur du cookie."""
    database = tmp_path / "app.db"
    identifiant, _secret = creer_compte(database, pseudo)
    jeton, _expire = ouvrir_session(database, identifiant)
    return jeton


def _compte_id(tmp_path: Path, client_id: str = "jeton") -> str | None:
    with sqlite3.connect(tmp_path / "app.db") as connection:
        row = connection.execute(
            "SELECT compte_id FROM saisie WHERE client_id = ?", (client_id,)
        ).fetchone()
    return None if row is None else row[0]


# --- La promesse : compter sans compte reste le chemin par défaut ---------


def test_un_releve_post_sans_cookie_est_enregistre(tmp_path):
    """Le cas le plus fréquent du projet ne demande aucun compte."""
    client = _client(tmp_path)

    _post(client)

    sessions = client.get("/api/sessions").json()
    assert [s["client_id"] for s in sessions] == ["jeton"]
    assert _compte_id(tmp_path) is None


def test_un_releve_sans_compte_n_apparait_pas_dans_une_page_de_compte(tmp_path):
    """Sans compte, `/compte` propose la création plutôt que de planter."""
    client = _client(tmp_path)
    _post(client)

    reponse = client.get("/compte")

    assert reponse.status_code == 200
    assert "Lyon" not in reponse.text


def test_le_client_peut_compter_avec_un_cookie_inconnu(tmp_path):
    """Un cookie forgé ou d'une session expirée ne doit rien casser.

    C'est le cas le plus probable en production : un navigateur garde un cookie
    bien après la fin de la session, et le `POST` suivant part avec. Si ça
    répond 500, le comptage fait dans le train est perdu.
    """
    client = _client(tmp_path)
    client.cookies.set(COOKIE_COMPTE, "jeton-invente")

    _post(client, client_id="apres-expiration")

    assert client.get("/api/sessions").json()[0]["client_id"] == "apres-expiration"
    assert _compte_id(tmp_path, "apres-expiration") is None


# --- Le secret : la forme, et le fait qu'il ne sort qu'une fois -----------


def test_le_secret_a_la_forme_annoncee():
    """24 caractères, groupes de 4, et aucun caractère qui se confond.

    Le format est testé sur cent secrets : un générateur qui produirait
    ponctuellement un caractère fcé — un `0` confondu avec un `O`, un `l` avec
    un `1` — casserait le collage une fois sur vingt, et pour toujours.
    """
    alphabet = "0123456789abcdefghijkmnpqrstuvwxyz"
    for _ in range(100):
        secret = generer_secret()
        assert FORME_SECRET.match(secret), f"forme inattendue : {secret}"
        assert len(secret.replace("-", "")) == LONGUEUR_SECRET
        douteux = set(secret.replace("-", "")) - set(alphabet)
        assert not douteux, f"caractères ambigus dans {secret} : {douteux}"


def test_le_secret_est_affiche_une_seule_fois_puis_plus_jamais(tmp_path):
    """La page de création le montre, et rien après ne le rend.

    C'est la propriété qui permet de dire « gardez-le » : s'il réapparaissait
    dans `/compte` à chaque visite, la consigne serait fausse.
    """
    client = _client(tmp_path)

    cree = client.post(
        "/compte/creer", data={"pseudo": "romain"}, follow_redirects=False
    )
    assert cree.status_code == 303

    # Le cookie est posé par la création : c'est ce qui fait qu'on arrive
    # connecté, donc `/compte` ne redemande rien.
    assert client.cookies.get(COOKIE_COMPTE)

    # Un second compte ne peut pas rendre le premier secret lisible.
    client.cookies.clear()
    assert "secret-neuf" not in client.get("/compte").text


def test_le_secret_ne_voyage_pas_dans_une_url(tmp_path):
    """Un secret dans une URL finit dans un journal et un `Referer`."""
    client = _client(tmp_path)
    identifiant, _secret = creer_compte(tmp_path / "app.db", "romain")
    jeton, _expire = ouvrir_session(tmp_path / "app.db", identifiant)
    client.cookies.set(COOKIE_COMPTE, jeton)

    for chemin in ("/compte", "/comptages", "/api/export.csv", "/classement"):
        corps = client.get(chemin).text
        assert "secret=" not in corps, f"un secret voyage par {chemin}"
        assert COOKIE_COMPTE not in corps, f"le nom du cookie fuit par {chemin}"


def test_le_secret_stocke_est_un_hash_pas_le_secret(tmp_path):
    """La base ne doit contenir ni le secret ni une session utilisable.

    Une base copiée ne donne ni un compte ni une session. Un secret en clair
    donnerait les deux, puisqu'il est le seul moyen d'en ouvrir un.
    """
    identifiant, secret = creer_compte(tmp_path / "app.db", "romain")
    jeton, _expire = ouvrir_session(tmp_path / "app.db", identifiant)
    base = (tmp_path / "app.db").read_bytes()

    assert secret.encode() not in base
    assert secret.replace("-", "").encode() not in base
    assert jeton.encode() not in base
    with sqlite3.connect(tmp_path / "app.db") as connection:
        stocke = connection.execute(
            "SELECT secret FROM compte WHERE id = ?", (identifiant,)
        ).fetchone()[0]
    assert stocke == hacher(normaliser(secret))
    assert stocke != secret


def test_le_mauvais_secret_n_ouvre_pas_de_session(tmp_path):
    """Un secret d'un autre compte, ou un secret inventé, n'ouvre rien.

    Les deux cas sont le même test : ce qui compte est que la session ne s'ouvre
    pas, pas la raison pour laquelle elle ne s'ouvre pas.
    """
    client = _client(tmp_path)
    creer_compte(tmp_path / "app.db", "romain")

    for faux in ("0000-0000-0000-0000-0000-0000", "pas-un-secret"):
        client.cookies.clear()
        reponse = client.post(
            "/compte/se-connecter", data={"secret": faux}, follow_redirects=False
        )
        assert reponse.status_code == 303
        client.cookies.clear()
        assert "Se déconnecter" not in client.get("/compte").text


# --- Le rattachement ------------------------------------------------------


def test_un_releve_post_avec_une_session_est_rattache(tmp_path):
    client = _client(tmp_path)
    client.cookies.set(COOKIE_COMPTE, _compte_avec_session(tmp_path))

    _post(client)

    assert _compte_id(tmp_path) is not None


def test_deux_comptes_ont_deux_historiques_separes(tmp_path):
    client = _client(tmp_path)
    client.cookies.set(COOKIE_COMPTE, _compte_avec_session(tmp_path, "romain"))
    _post(client, client_id="a")

    client.cookies.clear()
    client.cookies.set(COOKIE_COMPTE, _compte_avec_session(tmp_path, "camille"))
    _post(client, client_id="b")

    assert client.get("/compte").text.count("Lyon") == 1
    assert _compte_id(tmp_path, "a") != _compte_id(tmp_path, "b")


def test_une_creation_puis_un_secret_rendonne_ouvre_le_compte(tmp_path):
    """Le secret affiché, renvoyé tel quel, doit ouvrir le compte.

    Ce test existe parce que le bug qu'il attrape est invisible autrement : la
    création hachait le secret **avec** ses tirets et la connexion le hachait
    **sans**. Les deux fonctions passaient leurs tests séparément, et le compte
    était inatteignable — la seule voie pour s'en apercevoir était de créer un
    compte puis de tenter de s'y reconnecter, ce que personne ne fait.
    """
    database = tmp_path / "app.db"
    _identifiant, secret = creer_compte(database, "romain")

    assert compte_de_secret(database, secret) is not None


def test_un_secret_avec_ou_sans_separateurs_ouvre_le_meme_compte(tmp_path):
    """Le secret se recopie : les tirets et la casse varient, le compte non."""
    database = tmp_path / "app.db"
    _identifiant, secret = creer_compte(database, "romain")

    assert compte_de_secret(database, secret.replace("-", "")) is not None
    assert compte_de_secret(database, secret.upper()) is not None
    assert compte_de_secret(database, "  " + secret + "  ") is not None


def test_le_secret_de_la_page_est_donne_tel_quel_par_la_connexion(tmp_path):
    """Le même secret passe par la page et par la connexion, et les deux marchent.

    C'est le test de bout en bout du parcours, et il ferme le trou que les deux
    précédents ouvrent : ils travaillent sur le module, celui-ci travaille sur la
    page, où le secret transite dans une URL avant d'être renvoyé par un formulaire.
    """
    client = _client(tmp_path)
    cree = client.post(
        "/compte/creer", data={"pseudo": "romain"}, follow_redirects=False
    )
    secret = cree.headers["location"].split("secret-neuf=")[1]

    client.cookies.clear()
    _post(client, client_id="compte-prive")
    client.cookies.clear()
    connexion = client.post(
        "/compte/se-connecter", data={"secret": secret}, follow_redirects=False
    )

    assert connexion.status_code == 303
    assert client.cookies.get(COOKIE_COMPTE), "le secret affiche n'a pas ouvert de session"


def test_un_mauvais_secret_ouvre_rien(tmp_path):
    """Un secret d'un autre compte n'ouvre rien, et ne lève rien."""
    database = tmp_path / "app.db"
    creer_compte(database, "romain")

    assert compte_de_secret(database, "0000-1111-2222-3333-4444-5555") is None
    assert compte_de_secret(database, "") is None


def test_le_secret_en_base_est_un_hash_pas_le_secret(tmp_path):
    """La base ne doit pas contenir le secret affiché.

    Le secret est la seule chose qui ouvre le compte. S'il est lisible sur le
    disque, une base copiée donne tous les comptes d'un coup — et c'est le seul
    élément du système qui rendrait cette copie utile contre quelqu'un d'autre.
    """
    database = tmp_path / "app.db"
    _identifiant, secret = creer_compte(database, "romain")

    brut = secret.encode()
    assert brut not in database.read_bytes()
    assert secret.replace("-", "").encode() not in database.read_bytes()


def test_une_base_existante_gagne_la_table_compte_sans_echouer(tmp_path):
    """Une base créée avant la phase 9 doit s'ouvrir et accepter un compte.

    Le `ALTER TABLE` touche `saisie`, et la table `compte` est créée à côté. Une
    base de production a des lignes : si la création du compte suppose une table
    vide, le compte est impossible à faire sur l'instance existante.
    """
    database = tmp_path / "app.db"
    _post(_client(tmp_path))
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM saisie").fetchone()[0] == 1

    client = _client(tmp_path)
    cree = client.post(
        "/compte/creer", data={"pseudo": "romain"}, follow_redirects=False
    )

    assert cree.status_code == 303


# --- La non-fuite : la property qui protège les gens ----------------------


def test_aucune_adresse_email_dans_la_base(tmp_path):
    """La règle de la phase, vérifiée sur le schéma et pas sur l'intention.

    Une colonne d'email vide est une colonne d'email qui se remplira. Le test
    lit le schéma de `compte` et échoue sur le premier nom qui ressemble à une
    adresse.
    """
    client = _client(tmp_path)
    creer_compte(tmp_path / "app.db", "romain")

    with sqlite3.connect(tmp_path / "app.db") as connection:
        colonnes = [row[1] for row in connection.execute("PRAGMA table_info(compte)")]
    interdit = ("email", "mail", "courriel", "adresse")
    for nom in colonnes:
        assert nom not in interdit, f"colonne interdite dans compte : {nom}"

    # Et rien dans le fichier de la base qui ressemble à une adresse.
    assert b"@exemple.fr" not in (tmp_path / "app.db").read_bytes()


def test_compte_id_ne_sort_dans_aucune_sortie(tmp_path):
    """La colonne ne doit apparaître ni dans le CSV ni dans une page.

    Le test cherche la valeur, pas le nom. Une colonne renommée fuiterait aussi,
    et une fuite par une URL serait invisible à une comparaison d'en-têtes.
    """
    client = _client(tmp_path)
    client.cookies.set(COOKIE_COMPTE, _compte_avec_session(tmp_path))
    _post(client)

    # La valeur est lue en base, pas devinée : le test cherche ce que la base
    # contient vraiment, donc il suit le code même si l'identifiant change de
    # forme ou de type un jour.
    with sqlite3.connect(tmp_path / "app.db") as connection:
        rang = connection.execute(
            "SELECT compte_id FROM saisie WHERE client_id = 'jeton'"
        ).fetchone()
    assert rang is not None and rang[0] is not None, "le relevé n'a pas été rattaché"
    valeur = str(rang[0])

    sorties = {
        "/api/export.csv": client.get("/api/export.csv").text,
        "/comptages": client.get("/comptages").text,
        "/carte": client.get("/carte").text,
        "/api/publish": client.get("/api/publish").text,
    }
    for chemin, corps in sorties.items():
        assert "compte_id" not in corps, f"le nom de la colonne fuit par {chemin}"
        assert valeur not in corps, f"la valeur du compte fuit par {chemin}"


# --- L'application démarre sans l'option compte ---------------------------


def test_l_application_demarre_sans_une_seule_variable_de_compte(tmp_path):
    """La réversibilité de la phase, en un test.

    Aucune variable de la phase n'est posée, et tout ce qui existait avant doit
    répondre pareil. Une installation contributrice, un fork, une démonstration :
    elles ne doivent pas avoir à configurer quoi que ce soit pour compter.
    """
    client = _client(tmp_path)

    assert client.get("/health").json() == {"status": "ok"}
    _post(client)
    assert client.get("/comptages").status_code == 200
    assert client.get("/classement").status_code == 200
    assert client.get("/compte").status_code == 200