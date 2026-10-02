# Méthode proposée pour estimer la fréquentation annuelle

## Objet et portée

Ce document décrit une méthode à valider pour produire, par segment ferroviaire orienté, une estimation de passages de voyageurs sur une année civile. Il ne décrit ni un voyageur unique, ni un total de voyageurs distincts sur une ligne. Un même voyageur peut être compté sur plusieurs segments de son parcours ; les valeurs de segments ne doivent donc pas être additionnées et présentées comme un total de ligne.

La mesure porte sur les passages observés ou estimés sur un segment, dans un sens donné, selon un service et une période définis. Le périmètre de publication doit préciser le sens, le type de service et l'année concernée.

Les résultats doivent distinguer clairement une mesure observée d'une estimation extrapolée. La méthode n'autorise pas à transformer une couverture incomplète en total annuel présenté comme complet.

## Définitions et unité de mesure

- **Observation** : relevé brut d'une charge de voyageurs associée à une circulation, une date de service et un ou plusieurs segments.
- **Circulation-date de service-segment** : unité de déduplication et d'analyse. Plusieurs relevés rattachés à la même circulation ne constituent pas plusieurs circulations indépendantes. Cette unité ne garantit pas l'indépendance statistique entre trains d'une même journée.
- **Segment orienté** : portion entre deux gares normalisées consécutives, sans aucune gare intermédiaire, dans un sens canonique. Les sens opposés sont distincts. Un comptage unique OD contribue avec sa valeur entière à chaque segment élémentaire couvert, sans être réparti.
- **Charge** : valeur de fréquentation relevée ou estimée pour une circulation sur un segment. Elle ne constitue pas un décompte de personnes uniques.
- **Offre** : circulations prévues ou réalisées susceptibles de parcourir le segment pendant une strate temporelle. Son origine, sa période de validité et sa complétude doivent être connues pour calculer une couverture.
- **Année civile** : période du 1er janvier au 31 décembre, selon la date effective de service dans le fuseau `Europe/Paris`.

Les identifiants historiques du projet incluent `perimetre` (`voiture` ou `um`) et `composition` (`US`, `UM2`, `UM3`). Ici, l'unité de référence est toujours la **rame complète** : US désigne une rame, UM2 deux rames complètes couplées et UM3 trois rames complètes. Une voiture/caisse est un sous-élément d'une rame ; la méthode ne calcule pas à l'échelle de la caisse. Le nom historique `voiture` n'implique donc pas que le vocabulaire de la méthode ou d'une future interface doive le reprendre : le terme à employer à l'avenir est « rame ». Le code existant n'est pas modifié par ce document.

## Données brutes et normalisation

Les données brutes sont conservées sans modification. Toute valeur corrigée, transformée ou extrapolée est stockée et présentée séparément, avec au minimum : valeur brute, valeur normalisée, coefficient appliqué le cas échéant, version de méthode et qualificatif indiquant qu'il s'agit d'une estimation.

Les valeurs brutes ne sont pas arrondies prématurément. L'arrondi d'affichage est une étape distincte, qui ne doit pas réinjecter une valeur arrondie dans les calculs.

Par convention, si le périmètre n'est pas renseigné, le comptage porte sur le train entier : coefficient 1, sans extrapolation. Un périmètre explicitement indiqué prime cette convention. Si le comptage est explicitement limité à une rame mais que la composition est inconnue, aucune extrapolation silencieuse n'est faite : la valeur reste non normalisée pour comparer un train entier.

### Coefficients de composition proposés

Les facteurs suivants sont des **placeholders expérimentaux non sourcés**, choisis pour tester une méthode plutôt que les facteurs ×2 et ×3 correspondant au simple nombre de rames. Ils ne sont ni calibrés ni validés empiriquement ; l'expérimentation ne doit pas attendre une telle validation et les facteurs pourront être amendés :

- comptage portant sur une rame complète d'une UM2 : multiplier par **1,75** ;
- comptage portant sur une rame complète d'une UM3 : multiplier par **2,7**.

Ces facteurs ne s'appliquent qu'à une rame complète explicitement comptée dans une UM connue. Un train entier ou une UM complète déjà compté(e) ne reçoit pas un second coefficient, et aucun facteur par caisse n'est appliqué. Toute normalisation conserve la valeur brute immuable ; le caractère expérimental est visible dans le commentaire et dans les métadonnées de méthode.

Le périmètre absent ne bloque donc pas l'expérimentation : la convention est le train entier, coefficient 1. Un périmètre explicite prime ; une limitation explicite à une rame avec composition inconnue ne doit toutefois pas être extrapolée.

## Affectation d'une observation aux segments

Un **comptage unique** sur un trajet connu s'applique à chaque segment orienté élémentaire qu'il englobe, de son origine à sa destination, sans dépasser les limites de l'origine-destination. Une même valeur est attribuée à chacun de ces segments ; elle n'est pas répartie entre eux. C'est la convention retenue, pas la preuve d'une charge mesurée à chaque arrêt intermédiaire.

Par exemple, une valeur relevée sur A–B–C alimente A–B et B–C avec cette même valeur. Cela ne signifie pas que le résultat A–B plus le résultat B–C représente des voyageurs distincts sur A–C.

La séquence d'arrêts desservis du trajet GTFS ne prouve pas toutes les gares traversées par un express : un arrêt non desservi peut être absent du GTFS. Il faut alors compléter le chemin avec un référentiel du réseau indiquant les gares intermédiaires traversées. Tant que ce chemin n'est pas établi, ne pas fabriquer de segments non élémentaires (par exemple A–C s'il existe une gare B intermédiaire) ; aucun segment inconnu ne vaut zéro. Une reconstruction ambiguë est exclue jusqu'à résolution.

Le segment doit reposer sur des gares normalisées et conserver, lorsqu'ils sont connus, la voie et le sens. Un arrêt et un passage sans arrêt ne doivent pas être confondus.

Un **serpent de charge** conserve au contraire ses variations : charge au départ, puis charge précédente + montées − descentes à chaque arrêt. La charge après un arrêt alimente les segments jusqu'à l'arrêt suivant. Une descente ou une montée inconnue, ou une charge reconstruite négative, rend la suite inconnue ; les segments antérieurs correctement observés restent exploitables. L'absence de comptage à la descente finale ne retire pas la charge connue sur le dernier segment parcouru. Aucune charge n'est prolongée au-delà du trajet effectivement observé.

## Date de service et rattachement à l'année

L'année est déterminée par la date effective de l'observation ou du service dans `Europe/Paris`, et non par la seule date de réception (`created_at`). Une observation hors ligne peut être reçue un autre jour ou une autre année sans que son service change d'année. Les dates de service, les catégories calendaires et leur version doivent être sauvegardées avec le comptage afin qu'une évolution ultérieure du calendrier ne le reclasse pas.

Les dates et heures sont interprétées dans `Europe/Paris`. Les heures GTFS supérieures à 24 heures doivent être rattachées au jour de service correspondant, et non ramenées mécaniquement au jour civil précédent ou suivant. Les règles exactes de rattachement doivent être cohérentes avec la date de service de la circulation.

Une observation sans date de service exploitable ne peut pas être attribuée à une année. Elle est mise en anomalie ou exclue, pas rattachée par défaut à la date de réception.

## Doublons et conflits d'observations

Une observation répétée (renvoi de la même observation) est idempotente et doit être dédupliquée avant fusion. La clé de fusion d'une charge est la circulation GTFS, la date de service, le segment et le sens. Les dates et trains différents restent des unités distinctes, sans que cela prouve leur indépendance statistique.

Pour plusieurs personnes ayant compté la même circulation le même jour, fusionner les valeurs **après normalisation cohérente**, par moyenne pondérée par leur score de fiabilité positif : `x_t = somme_i(r_i × x_i) / somme_i(r_i)`, où `r_i` est le score positif (ou ce score divisé par 100). Un score nul a un poids nul ; si tous les scores sont nuls, la circulation n'a pas de valeur exploitable. Ne jamais sommer les charges des observateurs. Les valeurs sources et leur provenance restent conservées.

Le score de fiabilité (0–100) est un jugement, pas une probabilité calibrée. Le score du classement des comptes n'est pas un poids statistique. Seuls les comptages uniques et serpents constituent des entrées de l'estimation ; un signalement `missing` n'est pas une charge. Aucun contributeur ni type de comptage n'a de priorité de principe : la pondération repose sur la fiabilité déclarée. Plusieurs observateurs d'une même circulation ne doivent pas multiplier son poids par leur nombre dans la moyenne annuelle.

## Strates temporelles et représentativité

L'extrapolation est faite dans sept catégories mutuellement exclusives, définies avant l'examen des charges :

- semaine hors vacances, heure de pointe du matin `[07 h, 10 h[` ;
- semaine hors vacances, heure de pointe du soir `[16 h, 19 h[` ;
- semaine hors vacances, heures creuses (toutes les autres heures) ;
- samedi hors vacances ;
- dimanche ou jour férié hors vacances (un jour férié prime sur le jour de semaine ou le samedi) ;
- vacances scolaires hors été, tous jours et toutes heures confondus ;
- vacances d'été, tous jours et toutes heures confondus.

Les vacances remplacent les catégories habituelles : elles ne forment pas un produit cartésien avec les jours et heures. L'été prime sur les autres vacances ; hors vacances, jour férié prime sur le type de jour. Le calendrier (vacances régionales et jours fériés officiels concernés), sa source/version, la région ou gare de référence et l'année de service sont à choisir et à figer dans chaque comptage. La strate horaire proposée dépend de l'heure de départ théorique au début du segment ; cette convention reste à valider et nécessite un horaire disponible pour le segment, notamment pour les gares traversées sans arrêt. Une catégorie sans offre ne contribue pas au total ; une catégorie active ne peut pas être réputée couverte par une autre.

Une source officielle disponible pour automatiser les vacances est le [calendrier scolaire du ministère de l'Éducation nationale](https://data.education.gouv.fr/explore/dataset/fr-en-calendrier-scolaire/). Son [API](https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/fr-en-calendrier-scolaire/records?limit=2), consultée pour ce cadrage, expose `description`, `population`, `start_date`, `end_date`, `location` (académie), `zones` et `annee_scolaire`. L'approche proposée est un import/cache local des périodes, pas un appel réseau à chaque comptage : convertir les bornes en `Europe/Paris`, retenir le calendrier pertinent pour les élèves, puis classifier la date et figer le régime, l'académie/zone et la version de source avec le relevé. Le rattachement géographique et les règles précises des bornes restent à arrêter ; aucun import ni classement automatique n'est livré ici.

Balraj rapporte un ratio de **×3 entre le train moyen et le train le plus chargé**. Cette observation motive la diversité horaire et le contrôle des échantillons concentrés sur la pointe. Ce n'est pas un rapport universel entre heures de pointe et heures creuses, ni une calibration publiée. On ne multiplie pas les relevés creux par trois et on ne divise pas le maximum par trois pour compléter des strates manquantes.

Des relevés nombreux concentrés sur des heures de pointe ne démontrent pas la représentativité annuelle. Il faut considérer le nombre de dates et de circulations indépendantes, mais aussi leur dispersion entre jours, semaines, mois, saisons et périodes scolaires pertinentes. Des journées ou semaines proches peuvent être dépendantes ; elles ne valent pas autant d'échantillons indépendants.

Le seuil pragmatique de démarrage partiel est d'au moins **5 circulations distinctes** après fusion, ayant un poids positif au total et réparties dans chacune des **trois catégories de semaine hors vacances** (au moins une circulation par catégorie). Les cinq peuvent être réparties entre ces trois catégories ; ce n'est pas cinq par catégorie. Favoriser des dates et moments variés, sans seuil supplémentaire de jours distincts. Ce seuil est expérimental et non validé statistiquement. Le bootstrap ou un rééchantillonnage temporel pourra être étudié ultérieurement, mais un intervalle de confiance n'est pas une condition préalable à l'affichage.

La variation volontaire des moments observés peut améliorer la diversité temporelle, mais ne garantit pas l'absence de biais. Il n'est pas proposé de corriger un biais volontaire par un facteur arbitraire.

## Estimateur et offre annuelle

Pour un segment `s`, une année `Y` et des strates `h`, l'estimateur proposé est :

`F_s,Y = somme_h_couvert (N_s,h,Y × moyenne_charge_s,h)`

La somme porte sur les catégories couvertes dont l'offre annuelle est connue. Pour une estimation complète, cet ensemble contient toutes les catégories actives du périmètre. Pour une estimation partielle, les catégories sans charge exploitable ou sans offre annuelle connue sont exclues de la somme et nommées dans le commentaire, jamais complétées par une valeur inventée.

`N_s,h,Y` est le nombre de circulations de l'offre annuelle retenues dans la catégorie, dédupliquées et historisées. Ce n'est ni le nombre de relevés ni le nombre d'utilisateurs ayant contribué. Pour chaque circulation, les comptages sont d'abord fusionnés par la moyenne pondérée définie plus haut. Pour éviter qu'une circulation avec beaucoup d'observateurs pèse plusieurs fois, une proposition à valider consiste à attribuer à chaque circulation une fiabilité représentative égale à la moyenne de ses scores positifs admissibles, puis à calculer la moyenne de catégorie pondérée par ces fiabilités. Cette convention est une proposition, pas une décision scientifique validée.

La couverture de l'offre ne peut être calculée que si `N_s,h,Y` est connu et suffisamment complet. L'offre théorique seule ne prouve pas que les trains ont circulé ni qu'ils ont transporté des voyageurs. Une annulation ne vaut pas automatiquement une charge nulle ; aucun report de voyageurs vers les trains voisins ne peut être déduit sans observations correspondantes.

L'offre théorique SNCF GTFS de l'année civile doit être reconstruite ou archivée. Les changements de versions d'offre ne doivent pas compter plusieurs fois une même circulation/date ; conserver les versions, dates, services et trajets nécessaires à la traçabilité. Une offre théorique ne décrit pas le trafic réellement effectué. Le choix entre archives ZIP et stockage compressé/normalisé reste ouvert ; mesurer les poids avant de décider, sans supposer de volume.

## Règle de publication stricte

Une synthèse complète requiert une offre annuelle connue et au moins une circulation admissible dans chaque catégorie active (offre positive), ainsi que le seuil de démarrage de cinq circulations réparties dans les trois catégories semaine hors vacances lorsqu'elles sont actives. Une catégorie dont l'offre historique est inconnue interdit de déclarer la couverture complète ; elle n'est pas assimilée à une catégorie sans offre. Le seuil de cinq est pragmatique et n'est pas validé statistiquement ; il ne faut pas présenter la méthode comme scientifiquement calibrée.

Une synthèse partielle présente le volume annuel des seules catégories couvertes, sans compléter les week-ends ou vacances manquants et sans l'appeler estimation de l'année pleine. Elle comporte un booléen `partial=true` et un champ `commentaire` expliquant les catégories absentes ou l'offre inconnue ; une synthèse complète porte `partial=false`. Les normalisations expérimentales sont également signalées dans `commentaire`, mais ne rendent pas à elles seules la synthèse partielle. Afficher le nombre de comptages bruts, le nombre de circulations utilisées (notamment si fusion/dédoublonnage), et leur répartition dans les catégories. Masquer les segments sans valeur. Une charge nulle réellement comptée avec une fiabilité positive reste une valeur valide : c'est l'absence de données qui ne devient jamais zéro. Aucun intervalle de confiance n'est obligatoire pour cet affichage expérimental.

Conserver de préférence la version de méthode, l'année, l'unité, le périmètre et les marqueurs expérimentaux. Ces métadonnées améliorent la traçabilité mais ne sont pas un blocage en soi. Les valeurs brutes et normalisées restent distinguables.

La couverture de l'offre par les strates suffisamment documentées peut être exprimée comme leur somme de circulations théoriques divisée par la somme de toutes les circulations théoriques du périmètre. Elle est distincte du taux de trains effectivement comptés et de la dispersion des dates observées : aucune de ces mesures ne remplace les autres. Les noms et seuils de ces indicateurs restent à valider.

## Voyageurs.kilomètres

Un volume de voyageurs.kilomètres se déduit des passages par segment multipliés par la longueur de chaque segment parcouru. Les segments doivent former une partition sans chevauchement et les longueurs doivent suivre la voie, avec une source et une version traçables ; une distance en ligne droite n'est pas un substitut silencieux. Cette somme est un volume de transport, jamais un nombre de voyageurs uniques. Une longueur inconnue bloque le total du périmètre ou donne un résultat explicitement partiel, selon la même règle de publication stricte.

## Export : exigence future, non livrée ici

L'export actuel de publication dans `comptagefer/publish.py` (`render_csv`, lignes 45–104) expose les statuts `precedent`, `courant` et `suivant` via `_photo_status` ; il ne publie pas pour chacun l'identité complète, la date ni le type. Le calcul de trajets dans `comptagefer/timetable.py` (`listed_trips`) et le test `tests/test_timetable.py::test_four_hour_list_keeps_type_delay_and_both_neighbour_pairs` montrent que les deux voisins de même type sont déjà distingués dans les données calculées.

L'exigence future est limitée aux cinq rôles `precedent`, `precedent_meme_type`, `courant`, `suivant` et `suivant_meme_type`, selon les conventions existantes, dont les deux noms ajoutés exactement comme écrits. Elle ne requiert pas de nouvelles colonnes d'identité, type, source, départ ou capture. Aucun changement d'export n'est inclus dans cette proposition documentaire.

Les données de trajets voisines déjà calculées dans `_brief` et le rendu actuel des statuts restent le point d'appui ; ne pas élargir la demande à des métadonnées supplémentaires. Le CSV téléchargeable et celui publié via l'API/data.gouv doivent partager le même rendu.

L'export doit partir d'un instantané figé, pas d'une relecture du GTFS ou du temps réel courant. Un champ indisponible reste vide ; il ne devient ni zéro ni une valeur normale supposée. Aucun `compte_id` ne doit être exporté. Ce document ne modifie ni l'export, ni l'API, ni la page méthode, ni les tests.

## Petits cas de vérification à prévoir

Ces cas sont des scénarios pour de futurs tests de la méthode ; ils ne sont pas des résultats exécutés ni une validation empirique des seuils.

- **Comptage multi-segments** : une valeur unique sur A–B–C est attribuée à A–B et à B–C, sans être présentée comme un total de ligne.
- **Trajet incomplet** : un segment manquant ou incohérent arrête la reconstruction ; aucun trou n'est rempli par zéro.
- **Serpent incomplet** : une descente inconnue ou une charge négative arrête la suite, sans perdre les segments précédents valides ; une descente finale non relevée ne supprime pas le dernier segment observé.
- **Doublon** : deux enregistrements de la même circulation-date-service-segment ne gonflent pas le nombre d'unités indépendantes.
- **Observateurs multiples** : deux charges différentes d'une même circulation sont fusionnées par la moyenne pondérée de leurs fiabilités positives, après normalisation ; elles ne forment qu'une circulation. Un renvoi de la même observation n'est pas un nouvel observateur.
- **Fiabilité nulle** : une observation de score zéro ne contribue pas ; des scores tous nuls ne donnent pas de charge exploitable.
- **Train vide compté** : un effectif réellement nul de fiabilité positive est conservé, pas assimilé à une donnée manquante.
- **Pointe seulement** : des observations nombreuses concentrées sur la pointe ne suffisent pas à publier un total annuel strict si d'autres strates actives restent insuffisamment couvertes.
- **Offre inconnue** : sans nombre d'offre annuel fiable, la couverture correspondante n'est pas déclarée complète et aucun total strict n'est publié.
- **UM entière** : une UM complète n'est pas multipliée une seconde fois ; une rame complète comptée dans une UM connue reçoit seulement le facteur expérimental correspondant.
- **Périmètre absent** : le comptage porte par convention sur le train entier, coefficient 1 ; un périmètre explicite prime.
- **Voisins absents** : si un trajet voisin n'est pas disponible dans l'instantané, les champs de son rôle restent vides, sans valeur de remplacement.

## Décisions et points à trancher

### Décisions retenues pour cette proposition

- Année civile et rattachement au service selon `Europe/Paris`.
- Une synthèse partielle n'est pas une estimation de l'année pleine ; seules les catégories couvertes alimentent son volume annuel.
- Mesure par passages sur segment orienté ; pas de voyageurs uniques ni de somme des segments assimilée à un total de ligne.
- Un comptage unique OD s'applique avec la même valeur à chaque segment qu'il englobe, sans dépasser son OD ; un serpent conserve les variations de charge reconstruites.
- Les données brutes restent immuables ; normalisation et estimation sont séparées et qualifiées.
- Un trou, une annulation, une donnée inconnue ou un conflit ne vaut pas automatiquement zéro.
- La représentativité requiert une variété temporelle ; la seule abondance de comptages en pointe ne suffit pas.
- Le comptage porte par défaut sur le train entier, sauf périmètre explicitement limité à une rame complète. Les facteurs expérimentaux non sourcés 1,75 (rame en UM2) et 2,7 (rame en UM3) sont des valeurs provisoires amendables, sans seconde multiplication pour un train/UM entier.
- Le démarrage partiel exige cinq circulations admissibles au total, avec au moins une dans chaque catégorie de semaine hors vacances. Les vacances remplacent les catégories ordinaires, tous jours et horaires confondus, avec un groupe été distinct.
- Les relevés d'une même circulation sont fusionnés par moyenne pondérée de fiabilité ; un score nul n'apporte aucun poids. L'affichage donne les nombres de comptages et leur répartition, `partial` et `commentaire`, sans segment dépourvu de valeur.
- Le futur export doit distinguer cinq rôles de trajets et s'appuyer sur un instantané figé.

### Questions non tranchées et prérequis d'implémentation

1. Quelle archive historique du GTFS SNCF est disponible et faut-il conserver les ZIP ou extraire/normaliser leur contenu compressé ? Mesurer les volumes et définir comment dédupliquer les circulations entre versions d'offre.
2. Comment importer et versionner le calendrier scolaire officiel proposé, quelle académie/zone sert de référence par segment, notamment entre zones, et quelle source de jours fériés applicable retenir ?
3. Quel référentiel de réseau fournit les gares intermédiaires traversées mais non desservies, et quel horaire théorique attribuer à ces passages pour classer l'heure du segment ? La règle de départ théorique au début du segment reste à valider.
4. La fiabilité représentative proposée pour pondérer les circulations dans leur catégorie (moyenne des scores positifs admissibles) convient-elle ? Il s'agit d'une convention méthodologique à valider, pas d'une décision déjà entérinée.
5. Comment traiter et signaler les identités de circulation ou données contradictoires qui ne peuvent pas être résolues à partir des clés disponibles ?
6. Quelle convention adopter lorsqu'une catégorie active n'a pas d'offre historique connue ? Ne pas l'assimiler à une catégorie sans offre.

Les facteurs de normalisation et le seuil de démarrage sont des choix expérimentaux, pas des paramètres scientifiquement validés. Les questions restantes ci-dessus portent sur les sources et conventions à arrêter ; elles ne rétablissent pas les anciens blocages concernant le périmètre absent, l'attente d'une validation empirique des facteurs, l'intervalle de confiance ou des seuils statistiques non spécifiés.
