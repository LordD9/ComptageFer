# Comptes : audit de sécurité et option passkeys

## Périmètre et méthode

Audit du code sur `origin/main` (`b95b3ab`), puis correctifs et régressions sur
la branche de PR. Exploration via MCP Codebase Memory : `compte.py`, les routes
`create_app`, leurs appels, les tests de compte/signalement et la saisie hors
ligne. L'index a été rafraîchi après changement de branche.

Ce rapport porte sur l'application. Il ne certifie ni le VPS, ni son proxy,
ni son certificat, ni les anciennes copies de journaux contenant des secrets.
Les passkeys ci-dessous sont une étude, pas une fonctionnalité livrée.

## Défauts corrigés

- **Secret de connexion dans une URL — gravité élevée.** La création renvoyait
  un `303` vers `/compte?secret-neuf=…`. Le secret pouvait rester dans
  l'historique, les journaux d'accès et des référents. Le `noindex` n'apporte
  aucune confidentialité. La réponse au POST rend maintenant le secret sans
  redirection ; un GET ne peut plus afficher de secret fourni en paramètre.
  Les tests lisent le vrai secret du HTML puis se reconnectent avec lui.
- **POST sans contrôle d'origine — gravité moyenne.** Création, connexion,
  déconnexion et administration acceptaient des formulaires étrangers.
  `SameSite=Lax` ne protège pas la connexion forcée dans un compte de l'attaquant,
  et un sous-domaine hostile est « same-site », pas « same-origin ».
  Les POST vérifient Origin, sinon Referer, et refusent les métadonnées
  `Sec-Fetch-Site: cross-site/same-site`, avant toute mutation.
- **Pages privées sans politique de cache ni protection contre l'encadrement —
  gravité moyenne.** Compte et admin, y compris leurs erreurs/redirections,
  portent `Cache-Control: no-store`, `X-Frame-Options: DENY` et
  `Content-Security-Policy: frame-ancestors 'none'`. Le référent est limité à la
  même origine, et supprimé sur la page du secret. Imposer `no-referrer` sur
  les formulaires rendait leur Origin `null` dans Chromium et bloquait les
  POST légitimes : cette interaction est exercée dans un vrai navigateur.
- **Configuration HTTPS trompeuse — gravité élevée pour l'admin public HTTP.**
  Compose ne transmettait pas `COMPTAGEFER_HTTPS`. La chaîne `0` était considérée
  vraie par le cookie compte et le cookie admin n'avait jamais `Secure`.
  Désormais, seul `1` active `Secure` pour les deux ; vide/`0` sont le mode HTTP
  local, les autres valeurs refusent le démarrage. Compose, `.env.example`
  et README concordent. Aucun TLS n'est fourni par cette variable.
- **Création anonyme sans plafond — disponibilité.** La borne annoncée dans
  le plan n'existait pas. Le plafond est maintenant de 50 comptes par heure
  glissante sur l'instance, sans IP stockée. Une transaction SQLite
  `BEGIN IMMEDIATE` empêche deux requêtes concurrentes de dépasser le quota,
  et le compteur persiste via les dates de création. Réponse `429` et
  `Retry-After: 3600`, sans bloquer les reconnexions. Chiffre provisoire.
- **Sessions non bornées et anciennes sessions encore utilisables — gravité
  moyenne.** Les reconnexions empilaient les lignes sans purge. Les expirées
  sont supprimées à l'ouverture ; dix sessions actives par compte au maximum,
  avec éviction de la plus ancienne. Le navigateur qui change de compte ou
  se reconnecte révoque son ancien jeton. L'expiration exacte est invalide,
  et une session sans compte existant ne s'authentifie pas.
- **Écritures partielles à la création ou rotation — bug confirmé en revue.**
  Compte, nouvelle session et ancienne révocation étaient validés séparément.
  Une panne au dernier stade laissait un compte inaccessible sans rendre son
  secret. Ces mutations sont désormais dans une seule transaction avec rollback
  et réponse `503` en cas d'erreur SQLite. Le remplacement intervient avant le
  plafond : il ne déconnecte pas inutilement un second appareil. Des triggers
  de test font échouer chaque étape et vérifient le contenu exact des tables
  ainsi que le maintien de l'ancienne session. Les erreurs inattendues `500`
  gardent aussi les en-têtes de confidentialité via un gestionnaire dédié.
- **Connexion O(N) et verdict trompeur sur erreur SQLite — disponibilité/bug.**
  Le parcours de tous les condensats n'était pas en temps constant, puisqu'il
  s'arrêtait au succès. Une recherche indexée du SHA-256 remplace ce parcours,
  avec validation de la forme canonique du secret. Une erreur de lecture
  SQLite renvoie `503`, pas un faux mauvais secret.
- **Outil d'installation vulnérable dans l'image de base.** L'audit des paquets
  Python a signalé des avis connus sur l'ancien pip, pas sur une dépendance
  métier. Le Dockerfile met maintenant pip à jour (`>=26.2`) avant l'installation
  du projet. Ce n'est pas une nouvelle dépendance d'exécution de l'application.

Les tests HTTP couvrent également les valeurs HTTPS invalides, la fenêtre
glissante du quota, sa concurrence et sa persistance, la concurrence des
sessions et la réinstallation/utilisation de l'index sur une base existante.
Les parcours Chromium vérifient le secret hors URL et le refus d'un formulaire
étranger. L'image Python 3.12 a été construite et le parcours compte/session/
cookie Secure y a été exercé. `pip-audit` ne trouve plus de vulnérabilité connue
dans les environnements Python local et conteneur testés. Le paquet ComptageFer,
absent de PyPI, est hors couverture de ce scanner : son code fait l'objet de
cet audit. Aucun scan de paquets système ou de TLS du VPS n'est revendiqué.

Une sonde supplémentaire a traversé un **vrai proxy TLS local vers le backend
HTTP**, avec Host public préservé et Chromium : création, secret hors URL,
déconnexion, reconnexion et cookie admin Secure. Certificat auto-signé de test,
validation du certificat ignorée uniquement dans ce contexte navigateur jetable.
Cela vérifie le câblage proxy/application, pas le certificat du site en production.

### Garanties déjà présentes, conservées

- Secret et sessions tirés avec `secrets`, stockés uniquement en SHA-256.
  Le secret est aléatoire à forte entropie, pas un mot de passe choisi : le
  compromis SHA-256 ne serait pas adapté à des mots de passe humains.
- Cookie de session `HttpOnly`, `SameSite=Lax`, `Path=/`, durée de 30 jours.
- Le compte rattaché au relevé vient uniquement du cookie validé, jamais du JSON.
- Le signalement impose l'appartenance dans le SQL et vise la clé composite
  `(client_id, kind)`. Il n'efface pas le relevé.
- Aucun email ; aucun `compte_id` dans les exports publics. Le pseudo et le
  classement sont publics par conception. Le pseudo n'est pas une identité
  unique ni une preuve d'authentification.
- Compter sans cookie ou avec une session inconnue/expirée reste possible.
  Les régressions existantes vérifient également la séparation des historiques,
  le hachage et les migrations sur une base existante.

## Limites et exploitation

- **Déployer HTTPS reste obligatoire en production.** Lier le port HTTP à la
  boucle locale, vérifier certificat et redirection, préserver le Host public,
  limiter les requêtes au proxy. Ne pas logger les corps ni les cookies.
  Les en-têtes forwarded ne remplacent pas ces précautions.
- Un quota global peut être consommé volontairement et empêcher temporairement
  la création de vrais comptes. Il borne l'écriture, pas le trafic ni le nombre
  total de comptes à long terme. Il ne remplace pas les limites de fréquence et
  de taille au reverse proxy ; la connexion n'est pas limitée par ce quota.
- Les clients sans Origin/Referer/Fetch Metadata restent acceptés pour préserver
  les usages API. Un navigateur moderne transmet ces informations ; un client
  direct peut les falsifier, mais doit toujours posséder le secret ou le cookie.
  Ce contrôle n'est pas un mécanisme d'authentification ni une protection XSS.
- Un secret copié ou un cookie volé reste utilisable. La déconnexion ne révoque
  que sa session, pas toutes les autres. Il n'y a pas encore de gestion des
  appareils, de rotation du secret ni de récupération de compte.
- `no-store` ne peut pas retirer des secrets déjà enregistrés auparavant dans
  des logs, l'historique ou des captures. Si une ancienne URL a été divulguée,
  le correctif seul ne rend pas le secret divulgué inutilisable.
- Une réponse POST portant le secret évite sa fuite en URL. Actualiser peut
  demander de resoumettre le formulaire et créer un autre compte ; ce n'est
  pas un affichage à usage unique opposable au navigateur ou à l'utilisateur.

## Passkeys — complexité et périmètre

### Conclusion

**Faisable sans email, sans service managé, en gardant FastAPI + SQLite.**
Complexité moyenne à élevée : la signature n'est pas le plus gros travail ;
la migration, la gestion de plusieurs clés, la récupération et les tests
multi-navigateurs le sont. Ordre de grandeur d'ingénierie, pas mesure ni devis :
**une à deux semaines** pour un remplacement exploitable, revu et testé, par
une personne connaissant ce code. Un prototype de cérémonie seule serait
nettement plus court, mais ne satisferait pas le besoin de production.

Une passkey remplace le **secret de connexion**, pas le **jeton de session**.
Après vérification WebAuthn, on garde `ouvrir_session`, le cookie HttpOnly,
les relevés et les contrôles d'appartenance. Aucun changement aux exports.

### Ce qu'il faudrait ajouter

1. **Domaine stable et HTTPS.** Définir explicitement le RP ID (nom de domaine,
   sans schéma ni port) et l'origine attendue complète. Ne pas les déduire d'un
   Host ou d'un en-tête envoyé par le client. Un changement de domaine peut
   rendre les anciennes clés inutilisables. Les adresses HTTP du LAN ne sont
   pas un mode passkeys de production ; localhost est le cas local particulier.
2. **Bibliothèque de vérification.** Candidat : `webauthn` / py_webauthn.
   Elle fournit génération d'options et vérification d'enregistrement/assertion.
   Ne pas réimplémenter CBOR, COSE ou les signatures. Dépendance nouvelle à
   discuter et à auditer ; elle n'est pas ajoutée par cette PR.
3. **Schéma.** Table de credentials : credential ID unique, compte, clé publique
   COSE, compteur, transports, état sauvegardé/type d'appareil et dates.
   Plusieurs credentials par compte. Le user handle opaque peut dériver de
   l'identifiant existant, sans email, jamais dans les données ouvertes.
   Ces valeurs sont propres à la connexion, mais ne sont pas « aucune donnée ».
4. **Défis serveur.** Défis aléatoires, temporaires, à usage unique, liés au
   navigateur, à la cérémonie et au compte lors d'un ajout. Stockage SQLite
   pour survivre aux workers, expiration courte, consommation atomique,
   purge et nombre borné. Ne pas sauvegarder une nouvelle clé sur un compte
   existant à partir d'un `compte_id` proposé par le client.
5. **Quatre routes de cérémonie.** Options et vérification pour inscription,
   options et vérification pour connexion. Plus routes de liste/ajout/retrait
   pour gérer les clés. Réauthentification fraîche pour ajouter/retirer une
   clé ou supprimer la dernière méthode de récupération.
6. **JavaScript de compte.** `navigator.credentials.create/get`, conversion
   base64url et gestion des refus, annulations et incompatibilités. Une clé
   découvrable (`residentKey: required`) permet de revenir sans pseudo, lequel
   n'est pas unique. Exiger la vérification utilisateur, pas simplement la
   présence ; attestation `none` pour éviter du fingerprinting inutile.
7. **Validation serveur.** Défi attendu, type de cérémonie, origine exacte,
   RP ID, signature, présence/vérification utilisateur, liaison credential /
   compte / userHandle. Les compteurs peuvent rester à zéro ou ne pas être
   strictement croissants avec des clés synchronisées ; gérer ce cas avec la
   bibliothèque et les états de sauvegarde, sans verrouiller arbitrairement
   tous ces utilisateurs.
8. **Migration et récupération.** Les secrets hachés existants ne se convertissent
   pas en clés. Un utilisateur existant s'authentifie puis ajoute et teste sa
   passkey. Encourager une seconde clé ou une passkey synchronisée. Si le secret
   reste un repli, la connexion reste hameçonnable par ce repli : le dire.
   Retirer le secret exige une décision explicite sur la perte de toutes les
   clés, ou des codes de récupération à usage unique, eux aussi hachés.
9. **Tests.** Authentificateur virtuel Chromium via CDP, création puis connexion,
   rejeu du défi, expiration, mauvaise origine/RP, clé inconnue, signature
   invalide, UV absente, compte différent, clés multiples et retrait de dernière
   clé. Tests manuels Safari/iOS et Chrome/Android : la synchronisation réelle
   et les dialogues système ne sont pas validés par un Chromium virtuel.

### Séquencement conseillé, soumis à décision

- D'abord un prototype jetable sur le domaine local autorisé : cérémonie
  complète et authentificateur virtuel, sans modifier les comptes existants.
- Puis passkeys optionnelles pour les comptes authentifiés, avec gestion de
  plusieurs clés et session existante. Ne pas transformer le cookie seul,
  potentiellement volé, en autorisation d'ajouter une méthode permanente.
- Enfin mode passkey principal et suppression du secret par compte après
  confirmation de la méthode de secours. La création anonyme reste possible
  et les quotas restent nécessaires : une passkey ne prouve pas qu'un humain
  ne crée qu'un compte.

Aucun fournisseur d'identité central n'est nécessaire. Une passkey synchronisée
utilise cependant le gestionnaire choisi par l'utilisateur : cela reste une
contrainte d'usage, pas une dépendance serveur du projet.

### Références consultées

- [MDN : Web Authentication API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Authentication_API)
  — contexte sécurisé, défis, origine et credentials découvrables.
- [py_webauthn : inscription](https://duo-labs.github.io/py_webauthn/registration.html)
  — options, `verify_registration_response`, UV et données à stocker.
- [py_webauthn : authentification](https://duo-labs.github.io/py_webauthn/authentication.html)
  — `verify_authentication_response`, userHandle et mise à jour du compteur.
