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

L'application répond sur http://localhost:8000/. Après le train, on choisit un seul compte ou un serpent de charge. Les comptages se lisent sur http://localhost:8000/comptages, et le CSV sur http://localhost:8000/api/export.csv. Le cache temps réel est sur http://localhost:8000/api/rt. Les bases restent dans `./data` après un redémarrage. Au premier lancement, le conteneur charge les noms de gares, pas l'horaire théorique.

La fenêtre d'admin est sur http://localhost:8000/admin. Elle s'ouvre avec `ADMIN_TOKEN`. Ce jeton n'est pas dans l'image.
