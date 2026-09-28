# Contribuer

Le besoin, l'architecture et le plan sont dans [docs/projet.md](docs/projet.md).
Ce document explique comment proposer un changement, et les règles qu'un
changement doit suivre sont dans [docs/regles.md](docs/regles.md).

Merci d'avoir lu `docs/regles.md` avant d'écrire. Il n'est pas long, et il
contient la moitié des révisions qu'on vous demandera de faire.

## Ce que le projet accepte et refuse

Le projet mesure la fréquentation des trains régionaux français avec des gens
qui comptent à main levée, dans un train, souvent sans réseau. Tout ce qui
suit en découle.

**Accepté, sans discussion** — un test qui échoue avant et passe après ; une
erreur qui perd une donnée de contributeur ; un affichage qui rend la page
illisible sur un téléphone ; une requête qui parcourt 36 000 lignes pour
afficher huit gares ; un texte d'interface qu'une personne seule peut mal
comprendre.

**Refusé, et il faut une raison** — une dépendance qui n'est pas déjà là ;
un mode de déploiement qui n'est pas `docker compose up` ; un service managé,
une base séparée, un worker ; un champ qui identifie le contributeur ; une
fonctionnalité qui promet ce que la méthode ne permet pas de mesurer.

Le critère, dans les deux sens : est-ce que ça sert quelqu'un qui compte dans
un train ? Si la réponse demande une nuance, c'est probablement non.

## Poser le problème avant le code

Une issue qui explique **le cas d'usage et la fréquence** vaut mieux qu'une
qui explique une solution. « Un train.foo ne passe pas à 48° N » est discutable
avec des coordonnées à l'appui. « Remplace le haversine par un equirectangulaire
pour aller plus vite » est une conversation sur la performance avant d'avoir
parlé du cas.

Quand la réponse existe déjà quelque part dans le dépôt, la question est
supposée y être. `docs/projet.md` est la source de vérité : quand le code et le
plan divergent, c'est le plan qui a tort, et on corrige dans le changement qui
révèle l'écart — pas dans un changement séparé trois mois plus tard.

## Travailler

```bash
git clone https://github.com/LordD9/ComptageFer
cd ComptageFer
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
python -m playwright install chromium   # pour les tests navigateur
```

Lancer les tests :

```bash
pytest -q
```

La suite inclut des tests navigateur dans un vrai Chromium, pas une émulation.
Ils rattrapent les régressions JavaScript qu'aucun test Python ne voit, et ils
sont l'une des raisons pour lesquelles ce projet attrape ses propres bugs de
front. Ils ne valent rien sous QEMU, d'où la restriction à `amd64` en CI.

Avant de pousser, les trois qui comptent :

```bash
pytest -q                              # la suite entière
ruff check .                           # le lint
ruff format --diff                     # le formatage
```

Si `ruff` signale une erreur, ce n'est pas un obstacle à franchir en
`--fix` aveugle : l'avertissement dit souvent où le code ment sur lui-même. Un
`F401` signale un import que plus rien n'utilise, c'est-à-dire du code mort
qui a survécu à la chose qu'il servait.

## Une pull request

**Le titre dit ce que ça change, pas comment.** « `fix: un `</script>` dans un
`client_id` cassait la page carte » plutôt que « refactor carte ». Le corps
répond à deux questions : qu'est-ce qui n'allait pas, et par quoi c'est
remplacé. Si la description du plan change, dites-le et corrigez
`docs/projet.md` dans la même PR.

**La CI doit être verte avant le merge.** Quatre jobs tournent : les tests, le
JavaScript hors ligne, la construction de l'image, et l'audit de
dépendance. Le dernier échoue sur une vulnérabilité connue dans une
dépendance de production, et il n'est pas contournable par un commentaire.

**La branche porte un préfixe** : `fix/`, `feat/`, `docs/`, `perf/`, `refactor/`.

## Ce qu'on attend d'une review

Une review here n'est pas un examen de style. Quelqu'un va essayer de casser
votre changement par un cas que vous n'avez pas prévu, et c'est le but. Les
questions utiles sont du genre : « qu'est-ce qui se passe si ce champ est
vide ? », « cette requête est-elle indexée ? », « est-ce que la page reste
lisible sur un téléphone ? ».

Deux règles qui viennent de l'audit de septembre 2026 et qui valent pour les
changements à venir :

- **Un bug corrigé sans test revient.** Le test dit ce qui n'allait pas, pour
  qu'on ne puisse pas défaire la correction sans lire pourquoi. Voir
  `tests/test_audit_regressions.py`.
- **Une donnée client non échappée dans une page est une faille.** Un seul
  champ oublié suffit, et c'est ce qui est arrivé sur `/carte` : cinq champs
  étaient échappés, le sixième ne l'était pas. Voir `docs/regles.md`.

## Signaler un bug

Un bug qui perd un comptage est prioritaire sur tout le reste : c'est une
donnée de quelqu'un qui n'existe plus et qui ne se reverra pas. Ce qui aide
le plus, dans l'ordre : ce que vous avez fait, ce que vous avez vu, le
`client_id` concerné, la sortie de `/api/rt`, et l'horodatage.

Si vous pouvez reproduire sur le conteneur réel, c'est mieux qu'un rapport
vague. `docker compose up -d`, le comptage, puis l'URL qui casse.

## Licence

GPL-3.0 pour le code. En contributing, vous acceptez que votre contribution
soit publiée sous cette licence.

Les données collectées sont en Licence Ouverte 2.0 et le resteront : c'est ce
qui rend les comptages réutilisables par une collectivité. Aucune contribution
ne change cette ligne.
