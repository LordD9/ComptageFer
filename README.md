# ComptageFer

Outil collaboratif de comptage de la fréquentation des TER en France.

Le besoin, l'architecture et le plan de développement sont dans [docs/projet.md](docs/projet.md).

Licence du code : GPL-3.0.

## Lancer

Il faut Docker. Rien d'autre.

```bash
cp .env.example .env
# Définir ADMIN_TOKEN dans .env. Ne pas committer ce fichier.
docker compose up --build
```

L'application répond sur http://localhost:8000/health. Les bases restent dans `./data` après un redémarrage.

`ADMIN_TOKEN` est déclaré par Compose. Sa valeur vient du `.env` local. Elle n'est pas dans l'image.
