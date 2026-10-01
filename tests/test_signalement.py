"""Le signalement d'un relevé : ce que la vague 2 promet et ce qu'elle refuse de promettre.

Le plan est net, et il faut le citer parce que chaque test ci-dessous en est la
traduction : « un signalement n'efface rien et ne modifie rien : il crée une ligne
que l'admin voit dans `/admin` » et « être connecté ouvre le droit de dire « ce
relevé n'est pas le mien », pas le droit de le retirer ».

Cinq propriétés, dans cet ordre — la sécurité d'abord, parce qu'un signalement
qui fuit est pire que pas de signalement :

1. **On ne signale que ses propres relevés.** Un `client_id` deviné — et il est
   dans le CSV — ne doit pas suffire à faire modérer le relevé de quelqu'un
   d'autre.
2. **Un signalement n'efface rien.** Le relevé est là après, et son score aussi.
3. **Sans session, rien ne s'écrit.** Le formulaire est protégé par le cookie,
   mais la route est le contrôle qui compte.
4. **Un doublon ne fait pas de bruit.** Le même relevé signalé deux fois par la
   même personne donne une ligne, pas deux.
5. **`/admin` montre le relevé, pas son identifiant.** Un signalement sans le
   trajet qu'il conteste n'est pas modérable.
"""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from comptagefer.app import create_app
from comptagefer.compte import (
    COOKIE_COMPTE,
    LONGUEUR_MOTIF,
    creer_compte,
    deja_signales,
    ouvrir_session,
    signaler,
    signalements,
)


def _client(tmp_path: Path, **kwargs) -> TestClient:
    return TestClient(create_app(tmp_path, **kwargs))


def _session(tmp_path: Path, pseudo: str = "romain") -> str:
    """Un compte créé et une session ouverte, comme dans les autres tests."""
    identifiant, _secret = creer_compte(tmp_path / "app.db", pseudo)
    jeton, _expire = ouvrir_session(tmp_path / "app.db", identifiant)
    return jeton


def _compte_id(tmp_path: Path, client_id: str = "jeton") -> str:
    with sqlite3.connect(tmp_path / "app.db") as connection:
        row = connection.execute(
            "SELECT compte_id FROM saisie WHERE client_id = ?", (client_id,)
        ).fetchone()
    return "" if row is None else str(row[0])


def _signaler(client: TestClient, motif: str = "compte deux fois", **extra) -> None:
    corps = {"client_id": "jeton", "kind": "count", "motif": motif}
    corps.update(extra)
    reponse = client.post("/compte/signaler", data=corps, follow_redirects=False)
    # `follow_redirects=False` : TestClient suit le 303 et renvoie la page
    # d'arrivée en 200. C'est utile pour lire la page, inutile ici — ce qu'on
    # vérifie c'est que la route répond un renvoi, pas une page.
    assert reponse.status_code == 303
    assert reponse.headers["location"] == "/compte"


# --- 1. On ne signale que ses propres relevés --------------------------------


def test_un_releve_d_un_autre_compte_ne_se_signale_pas(tmp_path):
    """Le contrôle est dans l'écriture, pas dans la route.

    Le `client_id` et le `kind` viennent d'un formulaire : les deux sont donc
    controlables. Une route qui vérifie seulement « y a-t-il une session » laisse
    n'importe qui signaler le relevé de quelqu'un d'autre en devinant son
    `client_id` — et le `client_id` est dans le CSV exporté.

    Le test ouvre une session réelle et signale un `client_id` qui appartient à
    personne : la ligne ne doit pas apparaître.
    """
    client = _client(tmp_path)
    client.cookies.set(COOKIE_COMPTE, _session(tmp_path))

    _signaler(client, client_id="releve-de-quelqu-un")

    assert signalements(tmp_path / "app.db") == [], (
        "un relevé qui n'appartient pas au compte ne doit pas être signalable : "
        "c'est la porte d'entrée de la modération par les autres"
    )


def test_le_client_id_du_compte_ne_vaut_pas_autorisation(tmp_path):
    """Deux comptes, deux relevés, et un seul peut être signalé.

    Le test inverse du précédent : si le contrôle était absent ou mal branché,
    les deux relevés seraient signalables depuis n'importe quelle session. Il est
    écrit parce qu'un test qui vérifie seulement « rien ne s'écrit » passe aussi
    quand la fonction ne fait rien du tout.
    """
    client = _client(tmp_path)
    base = tmp_path / "app.db"

    premier = creer_compte(base, "romain")[0]
    deuxieme = creer_compte(base, "amélie")[0]
    jeton_romain, _ = ouvrir_session(base, premier)
    jeton_amelie, _ = ouvrir_session(base, deuxieme)

    with sqlite3.connect(base) as connection:
        for identifiant, jeton in ((premier, "j-romain"), (deuxieme, "j-amelie")):
            connection.execute(
                "INSERT INTO saisie (client_id, kind, origin_stop_id, "
                "destination_stop_id, created_at, compte_id) "
                "VALUES (?, 'count', 'A', 'B', '2026-09-01T10:00:00+00:00', ?)",
                (jeton, identifiant),
            )

    # La session de romain tente le relevé d'amélie.
    client.cookies.set(COOKIE_COMPTE, jeton_romain)
    _signaler(client, client_id="j-amelie")

    assert signalements(base) == [], "le relevé d'amélie n'est pas signalable par romain"

    # Sa propre session, elle, fonctionne.
    client.cookies.set(COOKIE_COMPTE, jeton_amelie)
    _signaler(client, client_id="j-amelie")

    assert len(signalements(base)) == 1, "amélie peut signaler son propre relevé"


# --- 2. Un signalement n'efface rien ----------------------------------------


def test_un_releve_signale_reste_dans_les_donnees_et_dans_le_score(tmp_path):
    """Le relevé est toujours là, et il compte toujours.

    C'est la promesse centrale de la vague 2, et elle se vérifie par les deux
    côtés : `/comptages` (la donnée) et `/classement` (le score). Un signalement
    qui retirait le relevé du CSV serait une suppression différée — la
    modération que le plan refuse, parce qu'elle donne à une personne le pouvoir
    de faire disparaître un relevé sans qu'aucune trace en reste.
    """
    client = _client(tmp_path)
    client.cookies.set(COOKIE_COMPTE, _session(tmp_path))
    client.post(
        "/api/sessions",
        json={
            "client_id": "jeton",
            "origin_stop_id": "A",
            "destination_stop_id": "B",
            "origin_name": "Lyon",
            "destination_name": "Nîmes",
            "passengers": 42,
            "reliability": 80,
        },
    )

    _signaler(client)

    assert "Lyon" in client.get("/comptages").text, "le relevé signalé a disparu des données"
    assert "42" in client.get("/api/export.csv").text, "le relevé signalé a disparu du CSV"
    assert "romain" in client.get("/classement").text, (
        "le compte du relevé signalé a disparu du classement"
    )
    assert "Lyon" in client.get("/compte").text, "le relevé a disparu de son propre historique"


def test_le_motif_d_un_signalement_ne_se_retrouve_pas_dans_les_donnees_publiques(tmp_path):
    """Le motif est une parole entre la personne et l'admin.

    Il n'a rien à faire dans le CSV, dans `/comptages` ni dans `/classement` : ce
    sont des pages publiques, et un motif écrit au clavier peut contenir ce que la
    personne avait en tête. Le test cherche la **valeur** du motif, pas le nom de
    la colonne, pour le même raison que le test de non-fuite du `compte_id`.
    """
    client = _client(tmp_path)
    client.cookies.set(COOKIE_COMPTE, _session(tmp_path))
    client.post(
        "/api/sessions",
        json={
            "client_id": "jeton",
            "origin_stop_id": "A",
            "destination_stop_id": "B",
            "origin_name": "Lyon",
            "destination_name": "Nîmes",
            "passengers": 42,
            "reliability": 80,
        },
    )
    motif = "mauvaise_prise_de_vue"
    _signaler(client, motif=motif)

    sorties = {
        "/api/export.csv": client.get("/api/export.csv").text,
        "/comptages": client.get("/comptages").text,
        "/classement": client.get("/classement").text,
        "/carte": client.get("/carte").text,
    }
    for chemin, corps in sorties.items():
        assert motif not in corps, f"le motif de signalement fuit par {chemin}"


# --- 3. Sans session, rien ne s'écrit ---------------------------------------


def test_sans_session_le_signalement_ne_scrit_rien(tmp_path):
    """Le formulaire est protégé par le cookie ; la route est le vrai contrôle.

    Un formulaire peut être posté sans le cookie — avec curl, ou en changeant la
    cible du `action`. Le test poste donc directement, sans cookie du tout, et
    vérifie qu'aucune ligne n'apparaît.
    """
    client = _client(tmp_path)
    client.post(
        "/api/sessions",
        json={
            "client_id": "jeton",
            "origin_stop_id": "A",
            "destination_stop_id": "B",
            "origin_name": "Lyon",
            "destination_name": "Nîmes",
            "passengers": 42,
            "reliability": 80,
        },
    )

    _signaler(client)

    assert signalements(tmp_path / "app.db") == [], (
        "sans session, le signalement doit être refusé silencieusement : un 403 "
        "dirait à un script que la route existe et qu'il faut chercher"
    )


# --- 4. Un doublon ne fait pas de bruit -------------------------------------


def test_signaler_deux_fois_le_meme_releve_ne_cree_qu_une_ligne(tmp_path):
    """La file de modération ne se remplit pas de double-clics.

    Le plan ne dit rien d'un doublon. La contrainte est un index unique sur
    `(client_id, kind, compte_id)` : le même relevé signalé par la même personne
    donne une ligne, et `INSERT OR IGNORE` transforme le second essai en no-op
    propre. Deux personnes peuvent, elles, signaler le même relevé — chacune son
    avis, et c'est à l'admin de trancher.
    """
    client = _client(tmp_path)
    client.cookies.set(COOKIE_COMPTE, _session(tmp_path))
    client.post(
        "/api/sessions",
        json={
            "client_id": "jeton",
            "origin_stop_id": "A",
            "destination_stop_id": "B",
            "origin_name": "Lyon",
            "destination_name": "Nîmes",
            "passengers": 42,
            "reliability": 80,
        },
    )

    _signaler(client, motif="première fois")
    _signaler(client, motif="seconde fois")

    lignes = signalements(tmp_path / "app.db")
    assert len(lignes) == 1
    assert lignes[0]["motif"] == "première fois", (
        "c'est le premier motif qui reste : un doublon ne doit pas réécrire "
        "l'explication que l'admin a déjà lue"
    )


def test_un_releve_appartient_a_un_seul_compte_donc_un_seul_peut_le_signaler(tmp_path):
    """La contrainte unique ne gêne personne, parce que la règle la rend superflue.

    Un relevé est rattaché à **un** compte, et `signaler` ne vise que les relevés de
    celui qui signale. Donc deux personnes ne peuvent pas viser le même relevé par
    le chemin normal — et la colonne `compte_id` de l'index unique ne retire rien
    à personne.

    Le test le vérifie quand même, parce que la propriété utile en découle : si une évolution venait à autoriser un signalement sur le relevé d'un autre —
    une modération ouverte, un cas d'abus —, l'index unique ne bloquerait que le
    doublon d'une **même** personne. Le dire ici évite de découvrir la contrainte
    en débuguant un bug de modération : la règle du doublon est par personne, pas
    par relevé.
    """
    base = tmp_path / "app.db"
    client = _client(tmp_path)
    premier = creer_compte(base, "romain")[0]
    jeton_romain, _ = ouvrir_session(base, premier)

    with sqlite3.connect(base) as connection:
        connection.execute(
            "INSERT INTO saisie (client_id, kind, origin_stop_id, "
            "destination_stop_id, created_at, compte_id) "
            "VALUES ('j-partage', 'count', 'A', 'B', '2026-09-01T10:00:00+00:00', ?)",
            (premier,),
        )

    assert signaler(base, premier, "j-partage", "count", "je n'y étais pas") is True
    assert signaler(base, premier, "j-partage", "count", "seconde tentative") is False, (
        "la même personne ne crée pas deux lignes pour le même relevé"
    )


# --- 5. La page dit ce qu'elle fait -----------------------------------------


def test_la_page_du_compte_propose_le_signalement_depuis_le_bouton(tmp_path):
    """Le formulaire est là, et il ne s'affiche pas si le relevé est signalé.

    Le `kind` est dans un champ caché à côté du `client_id` : ce sont les deux
    colonnes de la clé primaire, et les renvoyer ensemble est ce qui évite que
    le formulaire vise le relevé jumeau du même navigateur.
    """
    client = _client(tmp_path)
    client.cookies.set(COOKIE_COMPTE, _session(tmp_path))
    client.post(
        "/api/sessions",
        json={
            "client_id": "jeton",
            "origin_stop_id": "A",
            "destination_stop_id": "B",
            "origin_name": "Lyon",
            "destination_name": "Nîmes",
            "passengers": 42,
            "reliability": 80,
        },
    )

    avant = client.get("/compte").text

    assert "/compte/signaler" in avant, "la page ne propose pas de signaler"
    assert "name='client_id'" in avant and "name='kind'" in avant
    assert "Deja signale" not in avant

    _signaler(client)
    apres = client.get("/compte").text

    assert "Deja signale" in apres, (
        "le bouton doit dire que c'est déjà fait, sinon le formulaire renvoie "
        "sans changement visible et l'action semble n'avoir rien fait"
    )
    assert "/compte/signaler" not in apres, "un relevé déjà signalé ne propose plus son formulaire"


def test_un_motif_vide_n_est_ecrit_ni_refuse_silencieusement(tmp_path):
    """Un motif vide ne crée pas de ligne de modération à traiter.

    Le champ du formulaire est `required`, donc le navigateur empêche l'envoi
    vide. La route reçoit pourtant `motif=""` d'un client qui n'est pas le
    navigateur, donc elle refuse — et sans écrire, sinon l'admin trouve une
    ligne sans rien pour la traiter.
    """
    client = _client(tmp_path)
    client.cookies.set(COOKIE_COMPTE, _session(tmp_path))
    client.post(
        "/api/sessions",
        json={
            "client_id": "jeton",
            "origin_stop_id": "A",
            "destination_stop_id": "B",
            "passengers": 42,
            "reliability": 80,
        },
    )

    _signaler(client, motif="   ")

    assert signalements(tmp_path / "app.db") == [], "un motif vide ne doit rien écrire"


def test_un_motif_tres_long_est_borne(tmp_path):
    """Le motif est tronqué, pas refusé.

    La borne protège la base et l'écran de l'admin, pas l'utilisateur : quelqu'un
    qui écrit trois cents caractères veut signaler, pas se faire bloquer. Refuser
    le ferait sans lui dire pourquoi.
    """
    client = _client(tmp_path)
    client.cookies.set(COOKIE_COMPTE, _session(tmp_path))
    client.post(
        "/api/sessions",
        json={
            "client_id": "jeton",
            "origin_stop_id": "A",
            "destination_stop_id": "B",
            "passengers": 42,
            "reliability": 80,
        },
    )

    _signaler(client, motif="x" * (LONGUEUR_MOTIF * 3))

    lignes = signalements(tmp_path / "app.db")
    assert len(lignes) == 1
    assert len(lignes[0]["motif"]) == LONGUEUR_MOTIF


# --- 6. `/admin` est là où le signalement se lit ----------------------------


def test_admin_voit_le_signalement_avec_le_releve_quil_concerne(tmp_path):
    """Le panneau montre le trajet et le motif, pas un identifiant.

    Un signalement sans le relevé qu'il conteste n'est pas modérable : l'admin
    ne peut pas juger « ce relevé n'est pas le mien » sans savoir lequel.
    """
    base = tmp_path / "app.db"
    client = _client(tmp_path, admin_token="secret-admin")
    client.cookies.set(COOKIE_COMPTE, _session(tmp_path))
    client.post(
        "/api/sessions",
        json={
            "client_id": "jeton",
            "origin_stop_id": "A",
            "destination_stop_id": "B",
            "origin_name": "Lyon",
            "destination_name": "Nîmes",
            "passengers": 42,
            "reliability": 80,
        },
    )
    _signaler(client, motif="compté deux fois")

    client.cookies.clear()
    client.post("/admin/login", data={"token": "secret-admin"})
    corps = client.get("/admin").text

    assert "Signalements" in corps
    assert "Lyon" in corps, "le signalement doit montrer le relevé qu'il concerne"
    assert "compté deux fois" in corps, "le motif doit être lisible par l'admin"
    assert "romain" in corps, "l'admin doit savoir qui a signalé"


def test_admin_voit_un_signalement_dont_le_releve_a_disparu(tmp_path):
    """Un signalement orphelin s'affiche quand même, marqué comme tel.

    C'est le cas le plus urgent des deux : quelqu'un a signalé, et l'objet du
    signalement a disparu avant d'être traité. Une jointure intérieure l'aurait
    fait disparaître de l'écran, donc de la file de travail — le pire endroit
    pour perdre un signalement.
    """
    base = tmp_path / "app.db"
    client = _client(tmp_path, admin_token="secret-admin")
    client.cookies.set(COOKIE_COMPTE, _session(tmp_path))
    client.post(
        "/api/sessions",
        json={
            "client_id": "jeton",
            "origin_stop_id": "A",
            "destination_stop_id": "B",
            "origin_name": "Lyon",
            "destination_name": "Nîmes",
            "passengers": 42,
            "reliability": 80,
        },
    )
    _signaler(client, motif="compté deux fois")

    # L'admin supprime le relevé signalé.
    client.cookies.clear()
    client.post("/admin/login", data={"token": "secret-admin"})
    client.post("/admin/supprimer", data={"client_id": "jeton", "kind": "count"})

    corps = client.get("/admin").text

    assert "Signalements" in corps
    assert "relevé supprimé" in corps.lower() or "releve supprime" in corps.lower(), (
        "un signalement dont le relevé n'existe plus doit rester visible et le dire"
    )
    assert "compté deux fois" in corps


def test_le_panneau_est_vide_et_announced_sans_signalement(tmp_path):
    """Une file de travail vide s'annonce.

    Un panneau d'absence laisse croire qu'il n'est pas branché. Le plan applique
    la même règle à `/classement` et à `/comptages`.
    """
    client = _client(tmp_path, admin_token="secret-admin")
    client.post("/admin/login", data={"token": "secret-admin"})

    corps = client.get("/admin").text

    assert "Signalements" in corps
    assert "Aucun signalement" in corps


# --- 7. La table elle-même --------------------------------------------------


def test_la_table_signalement_ne_stocke_que_ce_qui_est_dit(tmp_path):
    """Le schéma est lu, pas l'intention.

    Même famille que le test « aucune adresse email » : une colonne vide prévue
    pour être remplie plus tard est une colonne qui se remplira. Le signalement
    doit pouvoir dire qui signale quoi et quand — et rien d'autre.
    """
    client = _client(tmp_path)
    with sqlite3.connect(tmp_path / "app.db") as connection:
        colonnes = {
            row[1]: row[2] for row in connection.execute("PRAGMA table_info(signalement)")
        }

    assert set(colonnes) == {
        "id",
        "saisie_client_id",
        "saisie_kind",
        "compte_id",
        "motif",
        "cree_le",
    }, f"le signalement stocke {sorted(colonnes)} : rien de plus, rien de moins"


def test_deja_signales_renvoie_les_couples_deja_signales(tmp_path):
    """L'index de la page se construit en une lecture, pas une par relevé.

    La fonction renvoie un ensemble de `(client_id, kind)` — les deux colonnes de
    la clé primaire, parce que c'est ce qui identifie un relevé. Les tests de la
    page s'appuient dessus pour écrire « déjà signalé » sur le bon bouton, et le
    mauvais `kind` afficherait cette mention sur le relevé jumeau.
    """
    base = tmp_path / "app.db"
    client = _client(tmp_path)
    jeton = _session(tmp_path)
    client.cookies.set(COOKIE_COMPTE, jeton)
    identifiant = _compte_id(tmp_path)

    with sqlite3.connect(base) as connection:
        connection.execute(
            "INSERT INTO saisie (client_id, kind, origin_stop_id, "
            "destination_stop_id, created_at, compte_id) "
            "VALUES ('j1', 'count', 'A', 'B', '2026-09-01T10:00:00+00:00', ?)",
            (identifiant,),
        )
        connection.execute(
            "INSERT INTO saisie (client_id, kind, origin_stop_id, "
            "destination_stop_id, created_at, compte_id) "
            "VALUES ('j1', 'missing', 'A', 'B', '2026-09-01T10:00:00+00:00', ?)",
            (identifiant,),
        )

    assert signaler(base, identifiant, "j1", "count", "faux") is True
    signaler(base, identifiant, "j1", "missing", "faux aussi")

    deja = deja_signales(base, identifiant)

    assert ("j1", "count") in deja
    assert ("j1", "missing") in deja, (
        "un relevé de train manquant est un relevé : le genre fait partie de "
        "l'identité du relevé, donc de l'index"
    )
    assert ("j2", "count") not in deja