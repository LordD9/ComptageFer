# Règles de code

Ce document dit ce qu'on attend d'un changement, et pourquoi. Il est court
par choix : une règle qu'on ne retient pas n'est pas une règle.

Chaque section vient d'un problème que le projet a réellement eu. Quand la
règle dit « ça n'allait pas », elle renvoie vers le test ou le commit qui le
prouve, pour qu'on puisse vérifier au lieu de croire.

Ce document accompagne [CONTRIBUTING.md](../CONTRIBUTING.md), qui explique le
processus. Ici, c'est quoi.

---

## 1. Ce qui vient de l'utilisateur, c'est hostile par défaut

**Toute donnée qui vient du client est échappée avant d'atteindre une page.**
Sans exception, y compris les champs qui paraissent inoffensifs parce qu'ils
n'ont pas de nom lisible.

> `/carte` échappait `pseudo`, `origine`, `destination` et `stops`. Elle
> n'échappait pas `client_id`, qui partait dans le même `<script>` que le reste
> par `json.dumps`. Un `</script>` dans la valeur fermait la balise et la suite
> était exécutée par le navigateur. Donnée persistée, page publique, faille
> exploitable par quiconque poste un relevé.
> `tests/test_carte.py::test_a_client_id_cannot_break_out_of_the_page_script`

Deux règles qui en découlent :

- **`escape()` sur tout champ interpolé dans du HTML.** Y compris ceux qui
  « ne sont que des identifiants ». L'identifiant du navigateur est un
  identifiant que le navigateur choisit.
- **Un test d'injection par champ, pas par page.** La page `/carte` avait un
  test pour `pseudo`. Il passait. Il ne couvrait rien des cinq autres champs.

**Une donnée du client ne décide pas seule d'un sort.** Le `client_id` est une
clé d'idempotence, pas une autorité : il désigne un relevé existant, il ne
l'autorise pas.

## 2. Une réponse qui perd une donnée ne doit pas être silencieuse

**Une opération doit soit écrire, soit dire qu'elle n'a pas rien écrit.** Le
pire des cas est un 200 qui ne fait rien : l'écran dit « c'est noté », le
contributeur believes, la ligne n'existe pas.

> Le contrôle d'idempotence portait sur `client_id` seul, qui était aussi la
> clé primaire de `saisie`. Signaler un train manquant consommait le jeton du
> navigateur ; le comptage réel qui suivait repartait en `{"stored": false}`
> sans rien écrire. Le voyageur croyait avoir compté.
> `tests/test_audit_regressions.py::test_reporting_a_missing_train_does_not_swallow_the_next_count`

**Un `stored: false` doit avoir une raison que l'appelant peut comprendre.** Un
doublon qui n'est pas un doublon est une perte de données habillée en
succès. Le correctif a été de comparer aussi le genre, pas de changer le code
de retour : les deux genres sont légitimes, et les deux doivent pouvoir
coexister pour un même jeton.

**Une clé primaire qui identifie un objet métier doit porter ce qu'il
identifie.** `client_id` identifiait un relevé, mais une seule ligne. Quand un
même navigateur peut produire deux genres de lignes, la clé doit dire
`(client_id, kind)`.

## 3. Un bug corrigé sans test revient

**Chaque correction ajoute un test**, dans `tests/` ou dans le fichier de
tests qui couvre la zone. Le test échoue avant la correction. Il ne se contente
pas de vérifier que ça marche maintenant : il dit ce qui n'allait pas, dans son
docstring, pour qu'on ne puisse pas défaire la correction sans lire pourquoi.

`tests/test_audit_regressions.py` existe pour ça : un test par constat
d'audit, daté, référencé à `docs/audit-2026-09.md`. Ce fichier est le modèle.

**Un test qui échoue pour une autre raison que celle qu'il cible est un test
qui n'a pas fait son travail.** Pendant la correction d'`store_trip_updates`,
le test du `trip_id` dupliqué a échoué alors que le `trip_update` était déjà
corrigé : les `stop_update` de la seconde entity butaient encore. C'est le
test qui a trouvé le second trou, pas la relecture.

**Un test qui fige une phrase d'interface doit être revu quand la phrase
change.** Le pied de `/carte` a cassé deux tests le jour où on a corrigé son
décompte. Les tests voulaient dire « la page dit combien elle n'a pas pu
placer » ; ils disaient aussi « la page dit `2 comptages au total` ». La
deuxième assertion était un accident, la première était l'intention.

**Un test qui compare une coordonnée à l'identique mesure l'arrondi, pas le
code.** Le tracé réseau passe à quelques dizaines de mètres de la gare, pas
dessus : trois tests ont affirmé que le tracé ne passait pas par son arrêt du
milieu, alors qu'il y passait à 28 m. Une comparaison de géométrie se fait
avec une tolérance explicite, en mètres, et le test le dit.

**Une donnée de test inventée de mémoire n'est pas une donnée de test.** Trois
gares du jeu de la carte avaient leur longitude inversée, et une quatrième
était à 280 m de la position réelle : le test échouait pour une raison qui
n'avait rien à voir avec le code, et le premier réflexe — « le réseau est
faux » — était le mauvais. Elles viennent maintenant du GeoJSON SNCF, avec un
commentaire qui dit pourquoi.

## 4. La base ne travaille pas deux fois pour le même résultat

**Toute colonne filtrée est indexée.** Pas « indexée si le plan le montre » :
les requêtes de ce projet sont connues, et le plan se lit en une ligne de
`EXPLAIN QUERY PLAN`.

> `stop.parent` n'était pas indexé, alors que la moitié des requêtes le
> cherchent. Une recherche de gare en faisait 32 `COUNT(*)` en parcours
> complet : 94 ms pour afficher huit gares, sur une base de 36 000 arrêts.
> L'index et un `GROUP BY` l'amènent à 5 ms.

**Un N+1 dans une boucle est un bug de performance, pas un style.** Trente-deux
requêtes dans une boucle Python, c'est trente-deux allers-retours que la base
fait et que l'appelant attend. Une seule requête groupée fait le même travail.

**Une ligne qui charge toute la table est un cache qui manque, pas un index.**
`_coordinates` (carte) et `_station_names` (ligne, serpent) chargent 36 000
lignes à chaque requête, ~80 ms. Signaler le problème est facile ; le corriger
demande de savoir quand le GTFS change. Ne pas le faire au hasard dans un
changement sans rapport.

**Mesurer avant d'optimiser, avec un chiffre.** Les temps de cette section
viennent d'une base synthétique de 36 000 arrêts, l'ordre de grandeur du GTFS
national. Un chiffre sans base de mesure ne prouve rien.

## 5. Le flux GTFS-RT est hostile

Le cache temps réel est la partie du code qui a le plus d'antécédents
d'incident, parce que les règles ci-dessous viennent toutes d'un crash ou d'un
gonflement réel.

**Un flux peut répéter ce que la spec autorise à répéter.** Un même
`StopPointRef` dans un journey SIRI, deux entities avec le même `trip_id`, un
`trip_id` vide. Le code déduplique, ou l'insertion est idempotente, ou les deux.
`INSERT OR IGNORE` sur une clé qui peut entrer en collision vaut mieux qu'un
cycle de collecte qui tombe.

**Une exception dans un cycle ne doit pas annuler le cycle.** Le cache se remplit
et se purge dans le même tour : une `IntegrityError` sur les alerts empilait
silencieusement, parce que l'erreur remontait avant la purge et que la purge
n'avait plus lieu.

> Les deux collisions de clé primaire de septembre 2026 — `store_siri` et
> `store_trip_updates` — faisaient exactement ça : le cycle se terminait sur une
> `IntegrityError`, et la base ne se purgeait plus jamais.
> `tests/test_audit_regressions.py::test_two_entities_with_the_same_trip_id_do_not_break_the_poll`

**Un flux qu'on ne peut pas avoir ne se retélécharge pas à chaque tour.** SIRI
est un filet quand GTFS-RT est vide ; le retester toutes les 120 secondes
parce que le flux principal ne répond pas est un téléchargement infini. Une
ressource de secours a une condition d'essai, pas une condition de besoin.

**Un cache qui ne change pas ne réécrit pas.** `_refresh_if_unchanged` rafraîchit
la ligne existante quand le contenu est identique. Cette règle existe pour les
trips et elle vaut pour tout ce qui est stocké avec un `fetched_at` en clé :
50 alertes inchangées donnaient 500 lignes après 10 tours.

## 6. Une migration ne perd rien

**Quand une clé ou un schéma change, la migration se teste sur une base
existante**, pas seulement sur une base neuve. Un `CREATE TABLE IF NOT EXISTS`
ne migre rien : il ne fait rien sur une base déjà là.

> Passer `saisie` de `client_id` à `(client_id, kind)` demande de recréer la
> table, SQLite ne sait pas changer une clé primaire. Le test crée une base
> avec l'ancien schéma, la remplit, lance l'application, et vérifie que la
> ligne est toujours là.
> `tests/test_audit_regressions.py::test_an_existing_database_keeps_its_rows_through_the_migration`

**Une migration est idempotente.** Elle s'exécute à chaque démarrage, donc elle
doit pouvoir s'exécuter deux fois. Vérifier l'état avant de migrer, pas après.

**L'import d'un gros fichier est atomique.** Le timetable est écrit dans
`.importing` puis `replace`d. Un import interrompu ne doit pas laisser une base
à moitié écrite que le prochain démarrage croira complète.

## 7. Le texte dit ce que l'outil fait

**La méthode, le README et les messages d'interface disent ce que le code
fait, pas ce qu'on voudrait qu'il fasse.** C'est la règle la plus facile à
violer et la plus difficile à rattraper, parce qu'un texte optimiste survit à
la fonctionnalité qui n'est pas là.

> « SIRI ET Lite est tenté une fois » dans la méthode, contre une tentative à
> chaque tour dans le code. Le code avait tort.

**Une page ne doit pas se contredire.** Le pied de `/carte` comptait
`{count, serpent}` alors que les trains signalés sont dessinés aussi : la page
pouvait afficher « 0 comptage au total, 1 sur la carte ».

**Le français du code et des commentaires est correct.** Les noms de variables
sont en français, les textes le sont aussi. Un `README` qui explique
l'`.env` ligne par ligne est un bon standard : la même rigueur vaut pour le
reste.

## 8. Pas de code mort

**Une fonction, un import, une variable qui n'est plus utilisé disparaît dans le
changement qui la rend inutile.** Pas dans un passage à la souffle, pas
« plus tard ».

> `ensure_stop_names` et `line_counts` n'étaient appelés nulle part. `sous_titre`
> était calculé puis jeté, et un import de `_has_lines` ne servait à rien.
> `ruff` les a vus en une seconde.

**Une constante qui encode une règle porte son pourquoi.** `SESSION_SECONDS =
3600` sans commentaire est un magic number ; avec la phrase qui explique qu'une
session volée doit expirer, c'est une règle.

**Un état en mémoire expire.** Un dictionnaire qui grossit à chaque connexion
et ne redescend jamais est une fuite, même si le processus est jeté au
redémarrage. C'est ce qui est arrivé à `admin_sessions`.

## 9. Le lint n'est pas une barrière, c'est un miroir

`ruff check .` ne bloque pas la CI aujourd'hui, mais il signale des choses
justes : variables inutilisées, imports morts, `datetime.UTC` partout. Un
avertissement ignoré en attendant d'y revenir n'est jamais repris.

Le code suit les règles de `ruff`, sans configuration ajoutée. Le jour où on en
ajoute une, elle va dans `pyproject.toml` et dans la CI, pas dans la tête de
quelqu'un.
