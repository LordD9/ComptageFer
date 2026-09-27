# ComptageFer

Outil collaboratif de comptage de la fréquentation des TER en France.

Le besoin, l'architecture et le plan de développement sont dans [docs/projet.md](docs/projet.md).

Licence du code : GPL-3.0. Les comptages publiés sont en Licence Ouverte 2.0.

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

### Les options du compose

Le compose n'a qu'un service, `app`. Tout se règle par variables
d'environnement, lues dans `.env` au démarrage.

- `ADMIN_TOKEN` — le jeton qui ouvre `/admin`. Vide, l'admin est fermé et
  rien ne s'y inscrit. Indispensable dès qu'un tiers atteint le conteneur.
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
