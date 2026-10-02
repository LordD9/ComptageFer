# Méthode proposée pour estimer la fréquentation annuelle

## Objet et portée

Ce document décrit une méthode à valider pour produire, par segment ferroviaire orienté, une estimation de passages de voyageurs sur une année civile. Il ne décrit ni un voyageur unique, ni un total de voyageurs distincts sur une ligne. Un même voyageur peut être compté sur plusieurs segments de son parcours ; les valeurs de segments ne doivent donc pas être additionnées et présentées comme un total de ligne.

La mesure porte sur les passages observés ou estimés sur un segment, dans un sens donné, selon un service et une période définis. Le périmètre de publication doit préciser le sens, le type de service et l'année concernée.

Les résultats doivent distinguer clairement une mesure observée d'une estimation extrapolée. La méthode n'autorise pas à transformer une couverture incomplète en total annuel présenté comme complet.

## Définitions et unité de mesure

- **Observation** : relevé brut d'une charge de voyageurs associée à une circulation, une date de service et un ou plusieurs segments.
- **Circulation-date de service-segment** : unité de déduplication et d'analyse. Plusieurs relevés rattachés à la même circulation ne constituent pas plusieurs circulations indépendantes. Cette unité ne garantit pas l'indépendance statistique entre trains d'une même journée.
- **Segment orienté** : portion entre deux gares normalisées, dans un sens déterminé. La segmentation est commune aux trains comparés, indépendamment de leurs arrêts commerciaux : si l'itinéraire est établi, un express traversant B sur A–B–C contribue aux mêmes segments A–B et B–C qu'un omnibus.
- **Charge** : valeur de fréquentation relevée ou estimée pour une circulation sur un segment. Elle ne constitue pas un décompte de personnes uniques.
- **Offre** : circulations prévues ou réalisées susceptibles de parcourir le segment pendant une strate temporelle. Son origine, sa période de validité et sa complétude doivent être connues pour calculer une couverture.
- **Année civile** : période du 1er janvier au 31 décembre, selon la date effective de service dans le fuseau `Europe/Paris`.

Les identifiants et vocabulaires déjà employés par le projet incluent `perimetre` (`voiture` ou `um`) et `composition` (`US`, `UM2`, `UM3`). « Voiture » peut désigner une caisse, et non une rame complète. Le sens exact de la valeur de charge et son unité doivent être établis avant de lui appliquer un coefficient ou de publier un total.

## Données brutes et normalisation

Les données brutes sont conservées sans modification. Toute valeur corrigée, transformée ou extrapolée est stockée et présentée séparément, avec au minimum : valeur brute, valeur normalisée, coefficient appliqué le cas échéant, version de méthode et qualificatif indiquant qu'il s'agit d'une estimation.

Les valeurs brutes ne sont pas arrondies prématurément. L'arrondi d'affichage est une étape distincte, qui ne doit pas réinjecter une valeur arrondie dans les calculs.

Une composition ou un périmètre inconnu n'implique pas un coefficient de 1. La valeur reste non normalisée tant que l'unité de mesure n'est pas établie.

### Coefficients de composition proposés

Les propositions suivantes sont des hypothèses de travail, pas des coefficients calibrés ni des faits validés :

- comptage portant sur une seule voiture d'une UM2 : multiplier par **1,75** ;
- comptage portant sur une seule voiture d'une UM3 : multiplier par **2,7**.

Si ces coefficients sont retenus après validation, leur application exige un périmètre de comptage non ambigu et une composition connue. Ils ne doivent pas être multipliés une seconde fois par le nombre de caisses. Une UM entière déjà comptée dans le périmètre `um` n'est pas remultipliée.

**Blocage préalable à l'implémentation et à la publication :** il faut lever l'ambiguïté sur l'unité du comptage. Il faut notamment confirmer, selon les sources et les écrans de saisie, si « une voiture » désigne une caisse, une rame complète, une UM complète ou un autre périmètre. Sans cette définition, aucune normalisation automatique ne doit être appliquée.

## Affectation d'une observation aux segments

Un **comptage unique** sur un trajet connu s'applique à chaque segment orienté qu'il englobe, de son origine à sa destination, sans dépasser les limites de l'origine-destination. Une même valeur est attribuée à chacun de ces segments ; elle n'est pas répartie entre eux. C'est la convention retenue, pas la preuve d'une charge mesurée à chaque arrêt intermédiaire.

Par exemple, une valeur relevée sur A–B–C alimente A–B et B–C avec cette même valeur. Cela ne signifie pas que le résultat A–B plus le résultat B–C représente des voyageurs distincts sur A–C.

La reconstruction du trajet doit s'arrêter dès qu'une donnée est absente, incohérente ou ambiguë. Aucun segment manquant ne reçoit implicitement la valeur zéro et aucune extrapolation de l'itinéraire au-delà de l'origine-destination n'est permise. Les itinéraires ambigus sont exclus du calcul jusqu'à résolution.

Le segment doit reposer sur des gares normalisées et conserver, lorsqu'ils sont connus, la voie et le sens. Un arrêt et un passage sans arrêt ne doivent pas être confondus.

Un **serpent de charge** conserve au contraire ses variations : charge au départ, puis charge précédente + montées − descentes à chaque arrêt. La charge après un arrêt alimente les segments jusqu'à l'arrêt suivant. Une descente ou une montée inconnue, ou une charge reconstruite négative, rend la suite inconnue ; les segments antérieurs correctement observés restent exploitables. L'absence de comptage à la descente finale ne retire pas la charge connue sur le dernier segment parcouru. Aucune charge n'est prolongée au-delà du trajet effectivement observé.

## Date de service et rattachement à l'année

L'année est déterminée par la date effective de l'observation ou du service, et non par la seule date de réception (`created_at`). Une observation hors ligne peut être reçue un autre jour ou une autre année sans que son service change d'année.

Les dates et heures sont interprétées dans `Europe/Paris`. Les heures GTFS supérieures à 24 heures doivent être rattachées au jour de service correspondant, et non ramenées mécaniquement au jour civil précédent ou suivant. Les règles exactes de rattachement doivent être cohérentes avec la date de service de la circulation.

Une observation sans date de service exploitable ne peut pas être attribuée à une année. Elle est mise en anomalie ou exclue, pas rattachée par défaut à la date de réception.

## Doublons et conflits d'observations

Une observation répétée pour la même circulation, date de service et segment ne doit pas augmenter le nombre d'unités indépendantes. Les répétitions sont dédupliquées selon une identité stable de circulation et d'observation, à définir à l'implémentation.

Des valeurs contradictoires pour une même unité ne sont pas moyennées aveuglément. Une règle de fusion doit déterminer la priorité ou signaler le conflit, en conservant les valeurs sources et leur provenance. Tant que cette règle n'est pas définie, les conflits non résolus ne peuvent pas alimenter une publication stricte.

Le score de fiabilité saisi par un utilisateur (0–100) est un jugement de fiabilité, pas une probabilité calibrée. Le score du classement des comptes n'est pas un poids statistique. Le nombre de comptes ayant observé une circulation ne doit pas être assimilé à plusieurs observations indépendantes.

## Strates temporelles et représentativité

L'extrapolation est faite dans des strates définies avant l'examen des charges, sans regroupement opportuniste après observation des résultats. Les dimensions proposées sont :

- segment et sens ;
- type de service ;
- type de jour : ouvré, samedi, dimanche ou jour férié ;
- période scolaire ou de vacances, selon le calendrier applicable à la région concernée ;
- saison ;
- plage horaire.

Les règles de priorité ou d'articulation de ces dimensions, ainsi que les catégories précises, restent à valider. Une strate sans offre ne contribue pas au total. Une strate active, avec offre positive, ne peut pas être réputée couverte par des observations prises dans une autre strate. Un éventuel regroupement de catégories doit être défini avant le calcul, documenté et validé, jamais destiné à masquer une lacune.

Balraj rapporte un ratio de **×3 entre le train moyen et le train le plus chargé**. Cette observation motive la diversité horaire et le contrôle des échantillons concentrés sur la pointe. Ce n'est pas un rapport universel entre heures de pointe et heures creuses, ni une calibration publiée. On ne multiplie pas les relevés creux par trois et on ne divise pas le maximum par trois pour compléter des strates manquantes.

Des relevés nombreux concentrés sur des heures de pointe ne démontrent pas la représentativité annuelle. Il faut considérer le nombre de dates et de circulations indépendantes, mais aussi leur dispersion entre jours, semaines, mois, saisons et périodes scolaires pertinentes. Des journées ou semaines proches peuvent être dépendantes ; elles ne valent pas autant d'échantillons indépendants.

Un rééchantillonnage par blocs temporels (par exemple par journées ou semaines) est une proposition à évaluer pour l'incertitude. Le bloc et la méthode doivent être validés avant usage. Les seuils minimaux de dates, de dispersion temporelle, de couverture d'offre et de précision doivent être calibrés et validés avant publication : aucun seuil numérique n'est fixé ici.

La variation volontaire des moments observés peut améliorer la diversité temporelle, mais ne garantit pas l'absence de biais. Il n'est pas proposé de corriger un biais volontaire par un facteur arbitraire.

## Estimateur et offre annuelle

Pour un segment `s`, une année `Y` et des strates `h`, l'estimateur proposé est :

`F_s,Y = somme_h (N_s,h,Y × moyenne_charge_s,h)`

`N_s,h,Y` est le nombre de circulations de l'offre annuelle retenues dans la strate, dédupliquées et reconstituées ou historisées. Ce n'est ni le nombre de relevés ni le nombre d'utilisateurs ayant contribué. `moyenne_charge_s,h` est calculée à partir des unités d'observation admissibles dans cette même strate, après les contrôles et normalisations validés.

La couverture de l'offre ne peut être calculée que si `N_s,h,Y` est connu et suffisamment complet. L'offre théorique seule ne prouve pas que les trains ont circulé ni qu'ils ont transporté des voyageurs. Une annulation ne vaut pas automatiquement une charge nulle ; aucun report de voyageurs vers les trains voisins ne peut être déduit sans observations correspondantes.

L'offre disponible aujourd'hui peut être jetable ou ne pas conserver l'historique de l'année. Une estimation historique exige donc une source d'offre archivée ou une reconstruction vérifiable, dédupliquée et documentée. Si cette condition n'est pas satisfaite, le total annuel ne peut pas être présenté comme calculé à partir d'une offre complète.

## Règle de publication stricte

Un total annuel n'est publiable que si **toutes les strates actives du périmètre**, c'est-à-dire celles dont l'offre est positive, disposent d'observations admissibles et d'une couverture suffisante selon des seuils préalablement validés, et si l'offre annuelle nécessaire est disponible. Une strate insuffisante bloque le total ; elle n'est pas simplement retirée du périmètre pour déclarer le reste complet. Aucune strate manquante n'est remplacée par zéro, par la moyenne d'une strate voisine ou par une imputation.

Si une ou plusieurs strates ne satisfont pas ces conditions, le système peut présenter une synthèse partielle, à condition de la désigner explicitement comme partielle et non comme un total annuel. Elle doit lister les strates couvertes, les lacunes et les raisons de l'exclusion. L'absence de données ou l'absence d'offre connue ne signifie pas une fréquentation nulle.

La publication doit préciser au minimum : méthode et version, année civile, segment et sens, type de service, valeur et unité, statut observé ou estimé, nombres d'observations, de circulations et de jours distincts, couverture temporelle et couverture de l'offre si calculable, dispersion/incertitude disponible, ainsi que les limites et lacunes. Les valeurs brutes et normalisées doivent rester distinguables.

La couverture de l'offre par les strates suffisamment documentées peut être exprimée comme leur somme de circulations théoriques divisée par la somme de toutes les circulations théoriques du périmètre. Elle est distincte du taux de trains effectivement comptés et de la dispersion des dates observées : aucune de ces mesures ne remplace les autres. Les noms et seuils de ces indicateurs restent à valider.

## Voyageurs.kilomètres

Un volume de voyageurs.kilomètres se déduit des passages par segment multipliés par la longueur de chaque segment parcouru. Les segments doivent former une partition sans chevauchement et les longueurs doivent suivre la voie, avec une source et une version traçables ; une distance en ligne droite n'est pas un substitut silencieux. Cette somme est un volume de transport, jamais un nombre de voyageurs uniques. Une longueur inconnue bloque le total du périmètre ou donne un résultat explicitement partiel, selon la même règle de publication stricte.

## Export : exigence future, non livrée ici

L'export actuel de publication dans `comptagefer/publish.py` (`render_csv`, lignes 45–104) expose les statuts `precedent`, `courant` et `suivant` via `_photo_status` ; il ne publie pas pour chacun l'identité complète, la date ni le type. Le calcul de trajets dans `comptagefer/timetable.py` (`listed_trips`) et le test `tests/test_timetable.py::test_four_hour_list_keeps_type_delay_and_both_neighbour_pairs` montrent que les deux voisins de même type sont déjà distingués dans les données calculées.

Une évolution future de l'export devra produire cinq rôles distincts : précédent, précédent même type, courant, suivant et suivant même type. Pour chaque rôle, l'exigence de données comprend l'identifiant du trajet, le type, le départ théorique daté, le statut, le retard, la source, la correspondance et l'instant de capture, dans la mesure où ces informations sont figées et disponibles. Les noms exacts de colonnes et leur disponibilité doivent être vérifiés au moment de concevoir l'export ; les termes ci-dessus sont des exigences sémantiques, pas un schéma d'API livré.

Aujourd'hui, `_brief` dans `comptagefer/timetable.py` conserve pour les voisins `trip_id`, `kind`, `departure_time`, `status`, `delay_seconds` et `etat`. On ne prétend donc pas que source, correspondance et instant de capture sont déjà figés pour chaque voisin. Si ces métadonnées sont nécessaires et absentes, leur collecte sera un prérequis distinct ; les anciens relevés ne les inventeront pas. Le CSV téléchargeable et celui publié sur data.gouv devront continuer à partager le même rendu.

L'export doit partir d'un instantané figé, pas d'une relecture du GTFS ou du temps réel courant. Un champ indisponible reste vide ; il ne devient ni zéro ni une valeur normale supposée. Aucun `compte_id` ne doit être exporté. Ce document ne modifie ni l'export, ni l'API, ni la page méthode, ni les tests.

## Petits cas de vérification à prévoir

Ces cas sont des scénarios qualitatifs pour de futurs tests ; ils ne fixent pas de valeurs de fréquentation ni de seuils numériques.

- **Comptage multi-segments** : une valeur unique sur A–B–C est attribuée à A–B et à B–C, sans être présentée comme un total de ligne.
- **Trajet incomplet** : un segment manquant ou incohérent arrête la reconstruction ; aucun trou n'est rempli par zéro.
- **Serpent incomplet** : une descente inconnue ou une charge négative arrête la suite, sans perdre les segments précédents valides ; une descente finale non relevée ne supprime pas le dernier segment observé.
- **Doublon** : deux enregistrements de la même circulation-date-service-segment ne gonflent pas le nombre d'unités indépendantes.
- **Conflit** : deux charges contradictoires pour cette unité sont signalées ou traitées par une règle explicite, jamais moyennées implicitement.
- **Pointe seulement** : des observations nombreuses concentrées sur la pointe ne suffisent pas à publier un total annuel strict si d'autres strates actives restent insuffisamment couvertes.
- **Offre inconnue** : sans nombre d'offre annuel fiable, la couverture correspondante n'est pas déclarée complète et aucun total strict n'est publié.
- **UM entière** : une rame complète comptée dans le périmètre `um` ne reçoit pas de coefficient UM2 ou UM3 supplémentaire.
- **Voiture ambiguë** : tant que le sens du périmètre `voiture` n'est pas établi, la normalisation et la publication qui en dépend sont bloquées.
- **Voisins absents** : si un trajet voisin n'est pas disponible dans l'instantané, les champs de son rôle restent vides, sans valeur de remplacement.

## Décisions et points à trancher

### Décisions retenues pour cette proposition

- Année civile et rattachement au service selon `Europe/Paris`.
- Publication stricte : une estimation partielle n'est pas un total annuel.
- Mesure par passages sur segment orienté ; pas de voyageurs uniques ni de somme des segments assimilée à un total de ligne.
- Un comptage unique OD s'applique avec la même valeur à chaque segment qu'il englobe, sans dépasser son OD ; un serpent conserve les variations de charge reconstruites.
- Les données brutes restent immuables ; normalisation et estimation sont séparées et qualifiées.
- Un trou, une annulation, une donnée inconnue ou un conflit ne vaut pas automatiquement zéro.
- La représentativité requiert une variété temporelle ; la seule abondance de comptages en pointe ne suffit pas.
- Les valeurs 1,75 (une voiture en UM2) et 2,7 (une voiture en UM3) sont des propositions non calibrées.
- Le futur export doit distinguer cinq rôles de trajets et s'appuyer sur un instantané figé.

### Questions non tranchées et prérequis d'implémentation

1. Quelle unité chaque source de comptage exprime-t-elle exactement : caisse, voiture, rame, UM complète, voyageurs ou autre ? Comment vérifier `perimetre` et `composition` lorsqu'ils sont absents ou ambigus ? C'est un blocage de normalisation et de publication.
2. Les coefficients proposés 1,75 et 2,7 sont-ils pertinents, avec quelles sources de calibration et quelle validation indépendante ? Ils ne doivent pas être activés avant décision.
3. Quelle identité stable permet de dédupliquer les observations d'une circulation et de distinguer répétitions, corrections et contributions réellement indépendantes ?
4. Quelle règle de résolution des charges contradictoires, de provenance et de priorité des sources appliquer sans moyenne aveugle ?
5. Quelle règle de normalisation des gares, de construction des segments orientés, de voie, de sens et de reconstruction des itinéraires constitue une preuve suffisante ?
6. Quelles catégories exactes de service, jour, vacances régionales, saison et plage horaire définissent les strates, et quelles interactions sont nécessaires ?
7. Quelle source permet de reconstruire ou d'historiser une offre annuelle dédupliquée, et comment traiter les changements d'offre, suppressions et incertitudes de circulation ?
8. Quels seuils de couverture, nombre de dates/circulations, dispersion temporelle, précision et méthode d'incertitude sont validés pour la publication stricte ? Les dépendances entre journées et semaines doivent être prises en compte.
9. Quelle définition de couverture et quel indicateur de dispersion/incertitude sont affichés, et comment distinguer une inconnue d'une valeur nulle ?
10. Quels champs réellement figés sont disponibles pour chacun des cinq rôles d'export, et quelle convention de colonnes sera adoptée ?
11. Quel format de synthèse partielle permet de lister les lacunes sans être confondu avec un total annuel ?

Aucune de ces questions ouvertes ne doit être masquée par une valeur par défaut silencieuse. L'implémentation et la publication doivent attendre les décisions qui conditionnent la validité des résultats.
