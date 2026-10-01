# ComptagesFer

Outil collaboratif de comptage de la fréquentation des TER en France.

Le besoin, l'architecture et le plan de développement sont dans [docs/projet.md](docs/projet.md).
Pour proposer un changement : [CONTRIBUTING.md](CONTRIBUTING.md). Pour ce qu'un
changement doit respecter : [docs/regles.md](docs/regles.md).

Licence du code : GPL-3.0. Les comptages publiés sont en Licence Ouverte 2.0.

Les données entrées ne sont pas toutes sous la même licence, et la confusion
serait facile : le **GTFS national** vient de la SNCF en Licence Ouverte 2.0,
le **réseau ferré** qui fait le tracé de la carte vient du Cerema en Licence
Etalab 2.0. `/methode` cite les deux à leur page.

## Déployer

Il faut Docker. Rien d'autre.

```bash
cp .env.example .env
# Définir ADMIN_TOKEN dans .env. Ne pas commiter ce fichier.
docker compose up -d
```

Chaque lancement reprend `ghcr.io/lordd9/comptagefer:latest`. L'image est publiée à chaque mise à jour de `main`, pour amd64 et arm64. Pas de build local.

L'application répond sur http://localhost:8000/. Après le train, on choisit un seul compte ou un serpent de charge. Les comptages se lisent sur http://localhost:8000/comptages, leur tracé sur http://localhost:8000/carte, une ligne ou une gare sur http://localhost:8000/rechercher, la méthode sur http://localhost:8000/methode, et le CSV sur http://localhost:8000/api/export.csv. Le cache temps réel est sur http://localhost:8000/api/rt. Les bases restent dans `./data` après un redémarrage. Au premier lancement, le conteneur charge le GTFS national, pas seulement les noms de gares.

La fenêtre d'admin est sur http://localhost:8000/admin. Elle s'ouvre avec `ADMIN_TOKEN`. Ce jeton n'est pas dans l'image.

### Le compose, ligne par ligne

`compose.yaml` est versionné, et c'est tout le déploiement : un service, un
volume, pas de base séparée, pas de worker. Le voici tel qu'il est dans le
dépôt, commentaires compris.

```yaml
services:
  app:
    image: ghcr.io/lordd9/comptagefer:latest
    pull_policy: always
    ports:
      - "8000:8000"
    environment:
      COMPTAGEFER_DATA: /data
      # Jeton d'administration. Sans valeur, /admin refuse toute connexion.
      ADMIN_TOKEN: ${ADMIN_TOKEN:-}
      # 1 derrière un proxy HTTPS ; vide ou 0 pour les essais locaux HTTP.
      COMPTAGEFER_HTTPS: ${COMPTAGEFER_HTTPS:-0}
      # Publication du CSV sur data.gouv.fr. Les deux premières variables
      # activent la fonctionnalité ; sans elles, l'application démarre quand
      # même et le dit sur /api/publish. Les valeurs vivent dans .env, jamais
      # dans ce fichier.
      DATAGOUV_API_KEY: ${DATAGOUV_API_KEY:-}
      DATAGOUV_DATASET_ID: ${DATAGOUV_DATASET_ID:-}
      # Optionnel : la ressource à remplacer chaque nuit. Sans elle, la
      # première publication crée la ressource et son identifiant est
      # mémorisé dans ./data/publish.json.
      DATAGOUV_RESOURCE_ID: ${DATAGOUV_RESOURCE_ID:-}
      # Heure de publication, heure de Paris. 0 = minuit.
      DATAGOUV_PUBLISH_HOUR: ${DATAGOUV_PUBLISH_HOUR:-0}
    volumes:
      - ./data:/data
    restart: unless-stopped
```

Chaque mot compte :

- `image` + `pull_policy: always` — Compose ne construit rien, il va chercher
  l'image publiée. Il n'existe pas de tag `latest` reconstruit sur votre
  machine, et c'est voulu.
- `ports` — le port de l'hôte, puis celui du conteneur. `8000:8000` rend le
  service sur le réseau local ; changez le membre de gauche si le port 8000
  est déjà pris.
- `environment` — le seul chemin entre le `.env` et le code.
  `COMPTAGEFER_DATA` est en dur : c'est le point de montage du volume, il ne
  vient pas du `.env`. Toutes les autres lignes sont de la substitution
  `${NOM:-défaut}`.
- `volumes` — `./data:/data`, le seul volume. Le chemin de gauche est sur
  l'hôte, celui de droite est le chemin vu du conteneur.
- `restart: unless-stopped` — le service revient après un reboot, sauf si vous
  l'avez arrêté vous-même.

### Le `.env`, et comment il alimente le compose

Le `.env` est le fichier que vous écrivez ; `compose.yaml` est le fichier que
le dépôt fournit. Le premier est votre, le second est le même pour tout le
monde, c'est pourquoi il ne contient que des noms de variables.

**Qui lit le `.env`.** Docker Compose, et lui seul. Il cherche un fichier
`.env` dans le répertoire du projet — celui qui contient `compose.yaml` — et le
lit au moment où vous lancez la commande. Le conteneur ne le reçoit pas, ne le
voit pas, et l'application ne l'ouvre jamais : elle lit son environnement de
processus, via `os.environ` (`comptagefer/app.py`, `ADMIN_TOKEN` et
`COMPTAGEFER_DATA` ; `comptagefer/publish.py`, `config_from_env`, les quatre
variables data.gouv).

**Ce que veut dire `${ADMIN_TOKEN:-}`.** Compose remplace cette écriture par la
valeur trouvée, *avant* de créer le conteneur. Le `:-` veut dire « si la
variable est absente ou vide, mets la valeur qui suit », ici une chaîne vide.
C'est ce qui rend les variables facultatives : sans `.env`, `ADMIN_TOKEN`
arrive vide dans le conteneur et `/admin` refuse toute connexion au lieu de
planter. `${DATAGOUV_PUBLISH_HOUR:-0}` a une valeur de repli, 0, qui est aussi
le défaut du code.

**La forme du fichier.** `.env` n'est pas un script shell, même si Compose est
indulgent. La forme à écrire, celle de `.env.example`, reste
`NOM=valeur` :

```bash
# .env — copier depuis .env.example
ADMIN_TOKEN=3f9a1c7e5b204d86af13c0e7b4d9528c6e0a1f37b5d4928
DATAGOUV_API_KEY=
DATAGOUV_DATASET_ID=
```

Compose 2.26 tolère `export NOM=valeur`, `NOM = valeur` et `NOM: valeur` : ne
comptez pas sur cette souplesse, un `.env` écrit ainsi n'est plus lisible par
un humain. Ce qu'il ne tolère pas, en revanche, échoue en silence — le
conteneur démarre, la variable est vide, et `/admin` refuse tout :

- **Une ligne sans `=`.** Elle est ignorée.
- **Un `#` non échappé dans la valeur.** `ADMIN_TOKEN=abc #def` donne `abc`.
  `ADMIN_TOKEN="abc #def"` donne bien `abc #def`.
- **Une variable définie deux fois.** La dernière ligne gagne, sans un mot.
- **La casse.** `admin_token` n'est pas `ADMIN_TOKEN`.
- **Un `$` dans la valeur.** `ADMIN_TOKEN=$PATH` est interprété, pas pris au
  mot ; mettez la valeur entre guillemets simples pour la lire tel quel.

Un jeton admin se tire au hasard, en hexadécimal : ni `#` ni `$` à escalier,
donc rien à échapper.

```bash
openssl rand -hex 24
```

**Qui gagne, si la variable est dans les deux endroits.** L'environnement du
shell. `ADMIN_TOKEN=xxx docker compose up -d` l'emporte sur le `.env`. Ne faites
pas cela pour un jeton : il reste dans l'historique du shell. Écrivez-le dans
le `.env`.

**Modifier le `.env` ne suffit pas.** Les variables sont figées à la création du
conteneneur. Après une édition, `docker compose up -d` recrée le service et la
nouvelle valeur arrive ; `docker compose restart` redémarre le même conteneur et
l'ancienne valeur reste en place, sans aucun avertissement. Vérifié sur le
conteneur réel : après `restart`, `os.environ["ADMIN_TOKEN"]` est encore
l'ancien jeton, et le nouveau est refusé à `/admin`. Dans les deux cas
l'import GTFS et les bases ne sont pas refaits : seul l'environnement change.

**Vérifier sans rien lancer.** `docker compose config` affiche le compose
résolu, les valeurs développées. C'est le moyen de voir si le `.env` est
syntaxiquement correct. Attention : le jeton admin y apparaît en clair. Ne
collez pas cette sortie dans un ticket ou un message.

### Les options, une par une

- `ADMIN_TOKEN` — le jeton qui ouvre `/admin`. Vide, l'admin est fermé et
  rien ne s'y inscrit. Indispensable dès qu'un tiers atteint le conteneur.
- `COMPTAGEFER_HTTPS` — `1` pour une adresse publique HTTPS. Active `Secure`
  sur les cookies de compte **et d'administration**. La déconnexion du compte
  supprime son cookie avec les mêmes attributs. Il n'existe pas de route de
  déconnexion admin : sa session expire au bout d'une heure.
  Vide ou `0` : cookies sans `Secure`, pour les essais HTTP locaux seulement.
  Toute autre valeur fait échouer le démarrage, plutôt que de masquer une faute
  de configuration. La variable ne fournit ni TLS, ni certificat, ni redirection.
- `DATAGOUV_API_KEY` — la clé personnelle data.gouv.fr, sur
  https://www.data.gouv.fr/account/api. Avec `DATAGOUV_DATASET_ID`, elle
  active la publication automatique. Jamais dans l'image, jamais commité.
- `DATAGOUV_DATASET_ID` — l'identifiant du jeu de données, les 24 caractères
  de son URL : `data.gouv.fr/datasets/5c1c1a1b...` prend `5c1c1a1b...`.
- `DATAGOUV_RESOURCE_ID` — facultatif, et en général inutile. Vide, la
  première publication crée la ressource et son identifiant est mémorisé dans
  `./data/publish.json` ; les suivantes réécrivent le même fichier. Renseignez
  la valeur pour remplacer une ressource déjà créée à la main.

Le compose passe ces variables au conteneur avec une valeur vide par défaut.
C'est ce qui permet de déployer sans compte data.gouv : l'application démarre
quand même, elle dit seulement que la publication est inactive.

`./data` est le seul volume : les bases, l'import GTFS et l'état de
publication. Le sauvegarder, c'est copier ce dossier.

### HTTPS et comptes utilisateurs

En production, servir le site avec un reverse proxy HTTPS (Caddy, Nginx…),
hors de ce compose. Il doit rediriger HTTP vers HTTPS, préserver le `Host`
public et ne pas exposer le port HTTP du conteneur à Internet. Si le proxy est
sur le même hôte, remplacer la ligne des ports par
`- "127.0.0.1:8000:8000"`. Limiter aussi la taille et la fréquence des requêtes
au proxy. Ne jamais journaliser les corps des formulaires ni les cookies.

Écrire `COMPTAGEFER_HTTPS=1` dans `.env`, puis `docker compose up -d` pour
recréer le conteneur. `docker compose restart` ne recharge pas l'environnement.
Le proxy peut parler HTTP au conteneur : la variable désigne le transport
**public**, pas ce lien interne. Les en-têtes `X-Forwarded-Host` et
`X-Forwarded-Proto` ne suffisent pas à activer les cookies sécurisés.

Vérifier dans le navigateur, à l'adresse **HTTPS** : après création ou connexion,
le cookie `comptagefer_compte` a `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/` ;
idem pour `comptagefer_admin` après connexion admin. La session du compte doit
tenir à la navigation et disparaître après déconnexion. Si `COMPTAGEFER_HTTPS=1` est posé
sur une adresse HTTP, le navigateur peut refuser de renvoyer le cookie : le
correctif est HTTPS, pas la désactivation de `Secure` en production.

Le compte reste facultatif. La création affiche le secret **dans la réponse au
POST**, jamais dans une URL, et les pages compte/admin portent `Cache-Control:
no-store`, une politique de référent limitée à la même origine (aucun référent
sur la page du secret) et une interdiction d'encadrement.
Les POST d'une origine étrangère sont refusés, y compris depuis un sous-domaine.
Les clients directs sans en-têtes d'origine restent acceptés ; ces contrôles ne
remplacent pas l'authentification ni la limitation au proxy.

La création est plafonnée globalement à 50 comptes par heure glissante, sans
stocker d'IP : au-delà, réponse `429` avec `Retry-After`, mais les reconnexions
restent ouvertes. C'est une borne anti-abus provisoire, pas un chiffre calibré.
Une session dure 30 jours ; dix sessions simultanées par compte au maximum,
puis la plus ancienne est fermée. Reconnexion ou nouveau compte dans le même
navigateur révoque son ancienne session. Garder le secret dans un gestionnaire
de mots de passe : sans lui ni session, il n'y a pas de récupération.

L'[audit de sécurité et l'étude passkeys](docs/securite-comptes.md) détaillent
les garanties, les limites et le coût d'un remplacement du secret par WebAuthn.

## Publier les comptages sur data.gouv.fr

Avec `DATAGOUV_API_KEY` et `DATAGOUV_DATASET_ID` renseignés, le conteneur
envoie le CSV des comptages chaque nuit à minuit, heure de Paris. Il n'y a pas
de deuxième service, pas de `cron` : c'est un fil du même processus que le
serveur, comme le poller temps réel.

Le fichier publié est exactement celui de `/api/export.csv` : la même fonction
le rend dans les deux cas.

Trois choses valent d'être connues avant de remplir le `.env` :

- **Le jeu de données doit déjà exister.** L'application ne le crée pas. Il
  faut l'ouvrir une fois sur data.gouv.fr, le remplir à la main si besoin, et
  lui donner une licence — Licence Ouverte 2.0, comme le CSV. La clé API doit
  avoir le droit d'écrire dessus.
- **La première publication crée une ressource, les suivantes la remplacent.**
  Sans `DATAGOUV_RESOURCE_ID`, c'est automatique. Le jeu de données garde une
  ressource `comptages-ter.csv` à jour, pas une par nuit.
- **Rien n'est publié si rien n'a été compté.** Une base vide ne remplace pas
  la ressource du jour, pour ne pas publier un fichier vide.

Pour vérifier la clé sans attendre minuit : bouton « Publier maintenant sur
data.gouv » dans `/admin`, ou `curl -X POST` sur `/admin/publier` avec la
session ouverte. L'état de la dernière tentative est sur
http://localhost:8000/api/publish, en JSON, sans la clé. Le bouton n'existe
que si la publication est active.

## Tester

Le formulaire est du JavaScript écrit à la main dans une chaîne Python. Les
tests pytest ne l'exécutent pas : une page peut casser au chargement et tous
rester verts. `tests/test_browser.py` ouvre donc un vrai Chromium et traverse
le parcours complet — choisir un train, compter, envoyer, lire, et le serpent
de charge — plus la file hors ligne.

```bash
pip install -e . pytest pytest-playwright
python -m playwright install --with-deps chromium
pytest -q
```

Le workflow `Tests` fait la même chose sur chaque PR, sur amd64.

Les tests de la file hors ligne sont en JavaScript, donc pytest ne les voit
pas. Ils se lancent à part :

```bash
node --test tests/offline.test.js
```

`tests/test_publish.py` ne sort jamais sur le réseau : l'appel HTTP est
injecté, et l'API de data.gouv.fr est vérifiée en direct par le `Docker test`
sur le conteneur réel, qui doit déclarer la publication inactive en l'absence
de clé.

## Les workflows

| Workflow | Ce qu'il vérifie |
| --- | --- |
| `Tests` | pytest et un vrai Chromium, sur amd64 |
| `Docker test` | démarre l'image construite et lui parle : `/`, `/comptages`, `/carte`, `/methode`, `/offline.js`, `/api/publish` doivent répondre 200, et la publication doit se dire inactive sans clé. Les tests JS de la file hors ligne. |
| `Publish image` | construit l'image pour amd64 et arm64, et ne pousse que sur `main` |

`Publish image` construit aussi sur les PR, mais sans pousser — c'est
`Docker test` qui prouve que l'image sert, pas seulement qu'elle se construit.
