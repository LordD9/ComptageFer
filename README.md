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

L'application répond sur http://localhost:8000/. Le formulaire est sur cette page. Les comptages se lisent sur http://localhost:8000/comptages, et le CSV sur http://localhost:8000/api/export.csv. Le cache temps réel est sur http://localhost:8000/api/rt. Les bases restent dans `./data` après un redémarrage. Au premier lancement, le conteneur charge les noms de gares, pas l'horaire théorique.

`ADMIN_TOKEN` est déclaré par Compose. Sa valeur vient du `.env` local. Elle n'est pas dans l'image.
