# ComptageFer

Outil collaboratif de comptage de la fréquentation des TER en France.

Le besoin, l'architecture et le plan de développement sont dans [docs/projet.md](docs/projet.md).

Licence du code : GPL-3.0.

## Lancer

Il faut Docker. Rien d'autre.

```bash
cp .env.example .env
# Définir ADMIN_TOKEN dans .env. Ne pas committer ce fichier.
docker compose up -d
```

Chaque lancement reprend `ghcr.io/lordd9/comptagefer:latest`. L'image est publiée à chaque mise à jour de `main`, pour amd64 et arm64. Pas de build local.

L'application répond sur http://localhost:8000/. Après le train, on choisit un seul compte ou un serpent de charge. Les comptages se lisent sur http://localhost:8000/comptages, la méthode sur http://localhost:8000/methode, et le CSV sur http://localhost:8000/api/export.csv. Le cache temps réel est sur http://localhost:8000/api/rt. Les bases restent dans `./data` après un redémarrage. Au premier lancement, le conteneur charge le GTFS national, pas seulement les noms de gares.

La fenêtre d'admin est sur http://localhost:8000/admin. Elle s'ouvre avec `ADMIN_TOKEN`. Ce jeton n'est pas dans l'image.

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

## Les workflows

| Workflow | Ce qu'il vérifie |
| --- | --- |
| `Tests` | pytest et un vrai Chromium, sur amd64 |
| `Docker test` | démarre l'image construite et lui parle : `/`, `/comptages`, `/methode`, `/offline.js` doivent répondre 200. Les tests JS de la file hors ligne. |
| `Publish image` | construit l'image pour amd64 et arm64, et ne pousse que sur `main` |

`Publish image` construit aussi sur les PR, mais sans pousser — c'est
`Docker test` qui prouve que l'image sert, pas seulement qu'elle se construit.

