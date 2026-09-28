# Glossaire

Les notions du projet, expliquées simplement. Ce glossaire s'enrichit à chaque étape et sert aussi à préparer les entretiens.

> Dernière mise à jour : 28 septembre 2026 (étape 2, ADR 028).

## Énergie et marché de l'électricité

**Acheminement (TURPE)** : ce que coûte le transport de l'électricité jusqu'à la maison. En France, c'est le tarif d'utilisation des réseaux publics d'électricité (TURPE), fixé par la CRE. Il est payé dans la facture, avec une part fixe et une part par kWh.

**Agrégateur** : acteur qui pilote ensemble de nombreux petits équipements (batteries, voitures, chauffe-eau) pour en tirer plus de valeur que chacun séparément. Un ensemble ainsi piloté s'appelle une centrale électrique virtuelle.

**Autoconsommation (taux d')** : part de la production solaire consommée sur place, directement ou via la batterie. À ne pas confondre avec l'autosuffisance.

**Autosuffisance (taux d')** : part de la consommation couverte par la production locale. Exemple : une maison qui consomme 5 000 kWh par an, dont 1 500 viennent de ses panneaux, est autosuffisante à 30 %.

**Bilan énergétique** : à chaque instant, l'énergie qui entre dans un système est égale à celle qui en sort ou qui y est stockée. Vérifier le bilan à chaque pas de temps permet de détecter la plupart des erreurs de simulation.

**Bourse de l'électricité (EPEX SPOT)** : plateforme où producteurs, fournisseurs et négociants achètent et vendent l'électricité. EPEX SPOT, la principale bourse en France, organise l'enchère du marché de la veille, qui fixe le prix spot. Elle vend ses données de marché, ce qui explique les restrictions sur leur republication.

**Changements d'heure** : deux fois par an, la France passe à l'heure d'été, fin mars, de 2 h à 3 h, puis revient à l'heure d'hiver, fin octobre, de 3 h à 2 h. Ces jours-là comptent 92 et 100 quarts d'heure au lieu de 96. Le projet range tout en UTC, qui ne change jamais, et ne compte en heure de Paris que les jours. ODRÉ, qui range ses lignes en heures de Paris, donne au printemps l'heure qui n'existe pas, et à l'automne un seul des deux passages de l'heure vécue deux fois (ADR 024).

**CRE (Commission de régulation de l'énergie)** : autorité indépendante qui fixe notamment les tarifs de réseau et propose les tarifs réglementés de vente.

**Effet rebond** : quand de nombreux équipements réagissent au même signal, par exemple un prix bas, ils agissent tous au même moment et créent un nouveau pic de consommation.

**Intensité carbone** : quantité de CO₂ émise pour produire 1 kWh d'électricité, en grammes de CO₂ par kWh. L'intensité *moyenne* divise les émissions de toute la production par l'énergie produite ; c'est celle que publie RTE. L'intensité *marginale* est celle de la centrale qu'on allume pour produire un kWh supplémentaire ; c'est elle qui mesure l'effet réel d'un changement de consommation, mais elle n'est pas publiée.

**kW, kWh, kWc** : le kW mesure une puissance (un débit), le kWh une énergie (une quantité). Une batterie de 10 kWh qui débite 5 kW se vide en 2 heures. Le kWc (kilowatt-crête) est la puissance d'un panneau en plein soleil, dans des conditions standard.

**Obligation d'achat** : dispositif public qui garantit aux petites installations solaires le rachat de leur surplus à un prix fixe (le tarif d'achat), pendant 20 ans.

**Pas de temps** : durée d'un « tour » de simulation. Toutes les grandeurs sont calculées une fois par pas : toutes les 15 minutes dans Ampère. Plus le pas est court, plus la simulation est fine, mais plus il y a de calculs.

**Prix spot (marché de la veille)** : prix de l'électricité fixé chaque jour vers 13 h par une enchère européenne, pour chaque quart d'heure du lendemain (pour chaque heure avant octobre 2025). Il varie fortement selon l'heure, la saison et la météo, et peut devenir négatif quand la production dépasse largement la demande.

**Profil et puissance souscrite (Enedis)** : Enedis classe les foyers par profil de consommation (par exemple tarif base, ou heures pleines et heures creuses) et par puissance souscrite, c'est-à-dire la puissance maximale autorisée par le contrat (de 3 à 36 kVA). Une puissance élevée signale souvent un grand logement ou un chauffage électrique. Pour chaque segment, Enedis publie le nombre de sites, leur énergie totale, et la courbe moyenne des seuls sites à compteur communicant, avec la part des sites qu'elle représente (ADR 026).

**Rayonnement solaire (global, direct, diffus, direct normal)** : puissance du soleil reçue par mètre carré, en W/m². Le rayonnement global, reçu par un plan horizontal, est la somme du direct, venu du disque du soleil, et du diffus, renvoyé par le ciel et les nuages. Le direct normal est celui que reçoit un plan tourné face au soleil. En anglais, ce sont GHI, DHI et DNI : à partir des trois, pvlib calcule ce que reçoit un toit selon son inclinaison et son orientation. Chez Open-Meteo, une valeur horaire est la moyenne de l'heure qui finit à l'instant donné (ADR 025).

**Taux de charge** : production d'une filière divisée par sa puissance installée ; il indique quelle part de sa capacité elle utilise. En moyenne sur l'année, le solaire en France tourne autour de 13 à 15 %, car il ne produit rien la nuit et peu par temps couvert.

**Vente du surplus** : mode de raccordement où la maison consomme d'abord sa production solaire et ne vend que le reste. Son injection sur le réseau est donc sa production moins sa consommation.

**Zones de vacances scolaires** : la métropole est partagée en trois zones, A, B et C, dont les vacances d'hiver et de printemps sont décalées d'une ou deux semaines, pour étaler les départs. Lyon est en zone A, avec sept autres académies qui ont les mêmes vacances (ADR 027).

## Prévision et évaluation

**Couverture d'un intervalle** : un intervalle de prévision à 80 % doit contenir la valeur réelle 80 % du temps. La couverture mesurée indique si l'incertitude annoncée est honnête.

**Fuite d'information** : erreur qui consiste à laisser un modèle utiliser, pendant son évaluation, une information qu'il n'aurait pas eue au moment de prévoir, par exemple la météo observée au lieu de la météo prévue. Ses scores paraissent alors meilleurs qu'en conditions réelles.

**MAE (erreur absolue moyenne)** : moyenne des écarts entre prévision et réalité, sans tenir compte du signe, dans l'unité de la donnée. Une MAE de 12 kW signifie que la prévision se trompe en moyenne de 12 kW, vers le haut ou vers le bas.

**MAPE (erreur absolue moyenne en pourcentage)** : la même erreur, divisée par la valeur réelle. Elle est trompeuse quand la réalité approche zéro, par exemple pour le solaire la nuit : une erreur minuscule devient un pourcentage énorme, voire une division par zéro. Le projet ne l'utilise donc pas.

**Période de test** : période réservée, jamais utilisée pour régler les modèles, sur laquelle on les juge une seule fois, à la fin. La regarder pendant la mise au point reviendrait à connaître les questions de l'examen à l'avance.

**Référence naïve** : la prévision la plus simple possible, comme « la même chose que la semaine dernière à la même heure ». Un modèle ne mérite sa place que s'il fait mieux qu'elle.

**Score de compétence** : part de l'erreur de la référence naïve que le modèle élimine, soit 1 − MAE du modèle ÷ MAE de la référence. À 0 %, le modèle ne fait pas mieux que la référence ; à 30 %, il fait 30 % d'erreur en moins ; un score négatif signifie qu'il fait pire.

**Validation glissante** : méthode honnête pour évaluer une prévision dans le temps. On entraîne le modèle sur le passé, on prévoit la période suivante (par exemple un mois) et on mesure l'erreur ; puis on avance d'une période et on recommence. Le modèle ne voit jamais le futur.

## Optimisation et pilotage

**Horizon glissant (MPC)** : on planifie les prochaines heures avec les prévisions disponibles, on n'applique que le début du plan, puis on replanifie avec des prévisions plus récentes. C'est la manière de piloter quand l'avenir est incertain. MPC signifie *Model Predictive Control*, ou commande prédictive.

**Information parfaite** : hypothèse où l'optimiseur connaît l'avenir exactement. Impossible en vrai, elle donne un plafond : aucune stratégie réelle ne peut faire mieux.

**MILP (optimisation linéaire en nombres entiers)** : on décrit un problème par des équations (les contraintes) et un objectif à minimiser ; un solveur trouve la meilleure solution. « En nombres entiers » signifie que certaines variables ne valent que 0 ou 1, pour représenter des choix du type oui ou non, comme charger ou décharger.

**Solveur** : programme qui résout un problème d'optimisation. Le projet utilisera un solveur libre (HiGHS ou OR-Tools).

## Données et logiciel

**ADR (Architecture Decision Record)** : court document qui garde la trace d'une décision : le contexte, les options envisagées, la décision et ses conséquences. Le projet en rédige un par décision importante, dans `docs/decisions/`.

**Apache Arrow** : format de tableau en mémoire, rangé par colonnes, que partagent Polars, DuckDB et pandas. Un tableau passe ainsi de Polars à DuckDB sans être recopié, et vers pandas en une seule copie (ADR 022).

**Attestation de provenance** : document signé qui dit où, quand et à partir de quel code une image a été construite. L'image d'Ampère n'en a pas encore (ADR 020).

**BuildKit** : le moteur actuel de `docker build`, celui de la CI. Le Docker rootless du VPS, sans l'extension buildx, utilise encore l'ancien constructeur, qui ne lit que le `.dockerignore` placé à la racine du contexte (ADR 020).

**Cache du navigateur** : copies des réponses que le navigateur garde pour ne pas les redemander. Avec l'en-tête `Cache-Control: no-cache`, il revérifie la page auprès du serveur à chaque visite, en envoyant l'empreinte de sa copie (l'ETag), et le serveur répond 304 si elle n'a pas changé. Les fichiers de `assets/` du build ont un nom qui change avec leur contenu : le navigateur peut les garder sans risque. Les autres, la page comprise, sont revérifiés à chaque visite (ADR 018).

**CC BY 4.0** : licence Creative Commons qui autorise à copier, republier et modifier des données, y compris à des fins commerciales, à condition de citer la source et d'indiquer les modifications apportées.

**Compose (Docker Compose)** : fichier, `compose.yaml`, qui décrit comment lancer les conteneurs d'une application : l'image, le port publié, les limites, les options de sécurité. `docker compose up` crée les conteneurs, ou recrée seulement ceux dont la description a changé. Celui d'Ampère décrit la démo en ligne : l'API, et le traitement quotidien, rangé dans un profil (ADR 021 et 028).

**Construction en plusieurs étapes** (*multi-stage build*) : un Dockerfile qui enchaîne plusieurs images. Les premières construisent, avec leurs outils (Node, uv), et la dernière reprend le résultat sans ces outils : l'image publiée ne contient ni Node ni uv (ADR 020).

**Conteneur, image Docker** : une image réunit un programme et tout ce dont il a besoin pour tourner, de l'interpréteur Python aux fichiers du tableau de bord. Un conteneur est cette image en train de tourner, isolée du reste du serveur. Un Dockerfile décrit comment construire l'image (ADR 020).

**Conteneur ponctuel** : un conteneur lancé pour une seule tâche, puis supprimé. `docker compose run --rm daily` démarre ainsi le service `daily` de la démo, qui disparaît à la fin du traitement : le traitement tourne sur l'image de la démo, sans occuper de mémoire le reste de la journée (ADR 028).

**Contexte de construction** : les fichiers que `docker build` envoie au moteur de construction, et que le Dockerfile peut copier. Le fichier `.dockerignore` en écarte tout ce qui n'est pas utile, comme les notes locales ou l'environnement virtuel (ADR 020).

**Contrôle obligatoire** : statut de la CI qui doit être vert pour qu'une pull request puisse être fusionnée. Sur un compte GitHub gratuit, seul un dépôt public peut en imposer (ADR 019). Depuis la publication du dépôt, le 27 septembre 2026, `main` en impose quatre : « Python », « Dashboard », « Image » et gitleaks.

**CORS (*Cross-Origin Resource Sharing*)** : autorisation qu'un serveur donne à une page venue d'une autre origine pour lire ses réponses. Sans elle, le navigateur bloque cette lecture. Le tableau de bord n'en a pas besoin, puisqu'il appelle l'API sur sa propre origine (ADR 016).

**Couches de données** : les trois états d'une donnée dans le projet. Le brut garde chaque réponse d'une source telle que reçue ; le nettoyé la met en UTC, au pas de 15 min (la météo et les courbes d'Enedis restent au pas publié, et les calendriers ont une ligne par jour de Paris) et dans des unités communes ; les résultats viennent des simulations et des prévisions. Seul le brut ne se reconstruit pas : il est la seule copie de ce que les sources ont envoyé (ADR 022).

**CSP** (*Content Security Policy*) : en-tête HTTP qui dit au navigateur d'où une page peut charger ses scripts, ses styles, ses polices et ses images. En ligne, le tableau de bord n'a droit qu'aux fichiers de son propre site : un script venu d'ailleurs ne s'exécuterait pas (ADR 021).

**CSS Modules** : fichiers CSS rattachés à un composant, dont l'outil de construction rend les noms de classes uniques. Deux composants peuvent ainsi avoir chacun une classe `.card` sans se gêner (ADR 017).

**Dependabot** : service de GitHub qui propose par des pull requests les mises à jour des dépendances. Ici, il passe chaque semaine sur les paquets Python (uv), ceux du tableau de bord (npm), les actions des workflows et les images de base du Dockerfile (`.github/dependabot.yml`).

**Dépendance (FastAPI)** : fonction que FastAPI exécute avant de répondre, déclarée avec `Depends`. Celle du routeur des fichiers du tableau de bord pose l'en-tête `Cache-Control` sur chaque fichier servi (ADR 018).

**Déploiement tiré** (mode *pull*) : c'est le serveur qui va chercher la nouvelle version, au lieu que la CI la lui envoie. Chaque minute, il regarde si l'image `main` a changé sur GHCR, et la déploie si c'est le cas. GitHub n'a ainsi aucun accès au serveur (ADR 021).

**Distroless** : famille d'images Docker de Google sans shell ni gestionnaire de paquets, réduites au programme et à ses bibliothèques (ADR 020).

**Docker rootless** : Docker dont le démon tourne sous un compte ordinaire, sans droits d'administration. Le compte de développement du VPS s'en sert pour essayer les images avant un push, sans jamais toucher au Docker du système, dont l'accès équivaut à root (ADR 014 et 020). La démo en ligne tourne de la même façon, sous le compte `demos` de la plateforme du portfolio (ADR 021).

**Données temps réel, consolidées, définitives** : trois versions successives d'une même mesure publiée par RTE. Chacune corrige la précédente à mesure que les informations arrivent. D'où la règle du projet : stocker chaque version reçue, avec sa date de réception. Sur ODRÉ, le temps réel couvre les 90 derniers jours, et les versions consolidée et définitive arrivent par paquets de plusieurs mois. Le nettoyé d'Ampère prend chaque mois dans la version la plus avancée qui le couvre en entier (ADR 024).

**DuckDB** : moteur de base de données analytique qui tourne dans le programme lui-même, sans serveur. Il interroge directement des fichiers Parquet en SQL.

**ECMWF IFS** : le modèle de prévision du Centre européen pour les prévisions météorologiques à moyen terme (ECMWF), lancé quatre fois par jour sur une grille de 9 km. Open-Meteo archive ses runs de 0 h UTC depuis mars 2024, et assemble à partir de ses runs successifs une série de météo observée, sans délai. Ampère prend les deux, pour que la météo observée et les prévisions viennent du même modèle (ADR 025).

**Écriture atomique** : écrire un fichier sous un nom temporaire, puis lui donner son nom définitif en une seule opération. Un arrêt pendant l'écriture ne laisse jamais de fichier à moitié écrit sous le vrai nom. Contre une coupure de courant, il faut en plus forcer l'écriture sur le disque (`fsync`) avant le renommage : sinon, le système peut enregistrer le nouveau nom avant les données (ADR 022).

**Empreinte** (SHA) : identifiant calculé à partir du contenu exact d'un objet, comme le commit d'une action GitHub ou une image Docker. Désigner l'action ou l'image par son empreinte plutôt que par une étiquette comme `v7` ou `3.13-slim`, que son auteur peut déplacer, garantit que ce qui tourne ne change pas sans une mise à jour visible, proposée par Dependabot. Les outils qu'une action télécharge ont leur propre version : la CI fixe celle de uv et vérifie sa somme SHA-256 (ADR 019 et 020).

**Empreinte de comparaison** : dans la couche brute, ce qui doit être identique pour qu'une réponse n'ajoute rien à l'archive. C'est d'ordinaire la réponse entière ; pour Open-Meteo, c'est toute la réponse sauf `generationtime_ms`, la durée de son calcul, qui change à chaque appel (ADR 025).

**En-têtes transmis** (`X-Forwarded-For`, `X-Forwarded-Proto`) : en-têtes par lesquels le relais indique à l'API l'adresse du visiteur et le protocole de sa requête. uvicorn ne les croit que s'ils viennent d'une adresse listée dans `FORWARDED_ALLOW_IPS`. Sans eux, ses redirections partiraient en HTTP (ADR 021).

**Essai de fumée** (*smoke test*) : vérification rapide qu'un programme démarre et répond, avant des tests plus poussés. `deploy/smoke-test.sh` démarre un conteneur de l'image et interroge l'API et le tableau de bord (ADR 020).

**Fichiers statiques** : fichiers envoyés tels quels au navigateur, sans calcul côté serveur : le HTML, le JavaScript, les styles, les polices. `npm run build` produit ceux du tableau de bord dans `web/dist/`, et en ligne l'API les sert (ADR 018).

**GHCR** (*GitHub Container Registry*) : le registre d'images Docker de GitHub. La CI y publie l'image de la démo à chaque push vers `main` (ADR 020).

**glibc, musl** : deux bibliothèques C de Linux, sur lesquelles reposent les programmes compilés. Debian utilise glibc, Alpine musl ; certains paquets Python précompilés n'existent que pour glibc (ADR 020).

**Hook (React)** : fonction qui donne à un composant un état, ou qui lui fait lancer un effet : un appel réseau, un minuteur. `useApiHealth` en est un : il interroge l'API, puis de nouveau 30 secondes après chaque réponse, et renvoie ce qu'il sait.

**Idempotent** : se dit d'une opération qu'on peut relancer sans changer le résultat. Par exemple, ingérer deux fois la même journée ne crée pas de doublon.

**Identifiant subordonné** : en Docker rootless, l'utilisateur 0 d'un conteneur est le compte qui fait tourner Docker, et ses autres utilisateurs sont des identifiants réservés à ce compte sur le serveur. L'utilisateur de l'image d'Ampère y a ainsi un numéro sans nom : les fichiers que le traitement écrit lui appartiennent, et seul un conteneur peut les lui attribuer (ADR 028).

**Intégration continue (CI)** : contrôles lancés automatiquement, sur une machine neuve, à chaque modification proposée. Ici, un workflow GitHub Actions, un fichier de `.github/workflows/`, fait tourner, sur chaque pull request et chaque push vers `main`, les mêmes commandes qu'en développement. Il a deux jobs, c'est-à-dire deux suites d'étapes qui tournent chacune sur sa propre machine : un pour le Python, un pour le tableau de bord (ADR 019).

**Jumeau numérique** : modèle informatique d'un système réel, soumis aux mêmes conditions que lui, qui permet de tester des décisions sans toucher au monde réel.

**Licence Ouverte (Etalab 2.0)** : licence de l'État français pour les données publiques. Elle donne les mêmes libertés que CC BY 4.0, à condition de citer la source et la date de dernière mise à jour.

**LTTB (*Largest-Triangle-Three-Buckets*)** : méthode de sous-échantillonnage qui réduit une longue série à quelques centaines de points en gardant sa forme visuelle, pics et creux compris. Elle rend les graphiques rapides sans les déformer.

**Manifeste** : le registre d'une source dans la couche brute, `manifest.jsonl`. Il compte une ligne par fichier reçu : ce qui a été demandé, d'où vient la réponse, où le fichier est rangé, quand il est arrivé, et son empreinte SHA-256 (ADR 022).

**Minuteur systemd** : planificateur intégré à Linux, qui lance une tâche à heure fixe. Avec l'option `Persistent`, il rattrape une exécution manquée pendant un arrêt du serveur. La plateforme du portfolio en a un par démo, qui lance le déploiement chaque minute, et un autre pour le traitement quotidien d'Ampère, chaque jour à 14 h (ADR 021 et 028).

**Montage** : un dossier du serveur rendu visible dans un conteneur. Le dossier des données d'Ampère est monté en écriture dans le traitement quotidien, et en lecture seule dans l'API, qui ne peut donc ni modifier ni effacer ce qu'elle lit (ADR 028).

**ODRÉ (Open Data Réseaux Énergies)** : la plateforme de données ouvertes des gestionnaires de réseaux d'énergie, dont RTE. Elle publie éCO2mix sous Licence Ouverte, par une API sans clé : quatre jeux, national et régional, en temps réel et en consolidé-définitif (ADR 024).

**Origine** : ce qui identifie le site d'une page pour le navigateur : le protocole, le nom et le port, par exemple `https://ampere.exemple:443`. Par sécurité, une page ne peut lire que les réponses de sa propre origine, sauf autorisation CORS.

**Page de repli** (*fallback*) : page qu'un serveur de fichiers renvoie quand le fichier demandé n'existe pas, par exemple `index.html` pour une application dont les pages sont gérées dans le navigateur. L'API n'en a pas : un chemin inconnu reçoit un 404 (ADR 018).

**Parquet** : format de fichier standard de la data, compressé et rangé par colonnes. Lire une seule colonne ne demande pas de lire tout le fichier.

**Point de santé (`/healthz`)** : route qui répond tant que le processus tourne. La plateforme du portfolio l'interroge pour savoir si une démo est en vie. Le tableau de bord interroge la même fonction sous `/api/healthz` pour afficher l'état de l'API, et le contrôle de santé de l'image Docker l'interroge en GET toutes les 30 secondes (ADR 020).

**Polars** : bibliothèque de tableaux de données écrite en Rust, rapide et stricte sur les types, notamment les dates avec leur fuseau et les valeurs manquantes. Le projet l'utilise pour ses données, et ne passe à pandas que pour les bibliothèques qui l'exigent, comme pvlib (ADR 022).

**Profil (Compose)** : une étiquette qui range un service à part dans le fichier Compose. `docker compose up` ne démarre pas un service rangé dans un profil qu'on ne lui a pas nommé, mais `docker compose run` le lance quand on le lui demande. Le traitement quotidien est ainsi dans le profil `jobs` : le déploiement ne le démarre jamais, et le minuteur le lance une fois par jour (ADR 028).

**Rattrapage** : ce que fait l'ingestion quand des données manquent dans l'archive, après une panne ou au premier passage : elle demande ce qui manque, en plus des jours récents, qu'une source peut encore corriger. Pour SMARD, une semaine est redemandée jusqu'à 14 jours après sa fin, puis tant que le brut n'en a pas une réponse lisible et complète (ADR 023). Pour éCO2mix, un mois est redemandé de la même façon, et aussi quand ODRÉ en publie une version plus avancée (ADR 024). Pour Open-Meteo, un mois de météo observée suit la règle de SMARD, et un run n'est redemandé que tant que le brut n'en a pas une réponse lisible et complète, sauf six runs qu'Open-Meteo n'a pas, listés, que seul `--full` redemande (ADR 025). Pour Enedis, qui publie chaque trimestre, tous les mois sont redemandés pendant les 7 jours qui suivent une nouvelle publication ; le reste du temps, un mois l'est tant que le brut n'en a pas de réponse lisible, ou qu'elle est vide alors que le mois est dans les trois ans publiés (ADR 026).

**Réanalyse** : reconstitution de la météo passée par un modèle qui intègre toutes les observations disponibles (stations, satellites, ballons). C'est la « météo observée » la plus complète et la plus homogène ; ERA5 en est l'exemple le plus connu.

**Relais (*reverse proxy*)** : serveur qui reçoit les requêtes à la place d'un autre et les lui transmet. En développement, le serveur de Vite relaie `/api/…` vers l'API, si bien que le navigateur ne voit qu'une origine. En ligne, Caddy joue ce rôle : il tient le certificat HTTPS, ajoute les en-têtes de sécurité et relaie les visiteurs vers le port de l'API, publié sur 127.0.0.1 seulement (ADR 021).

**Retour arrière** (*rollback*) : remettre en ligne une version précédente. Chaque image publiée garde l'étiquette de son commit, `sha-<commit>`, avec l'empreinte complète du commit, et cette étiquette ne bouge jamais : revenir à ce commit, c'est redéployer cette image (ADR 021).

**Run (d'un modèle météo)** : une exécution complète d'un modèle de prévision, lancée à heure fixe (par exemple à 00 h UTC). Chaque run produit une prévision pour les jours suivants ; il n'est disponible que quelques heures après son lancement, environ 6 h 30 pour ECMWF IFS (ADR 025).

**Schéma OpenAPI** : description de toutes les routes d'une API (chemins, paramètres, réponses) dans le format standard OpenAPI. FastAPI le produit à partir du code, sur `/api/openapi.json`, et en tire deux pages de documentation interactives, `/api/docs` et `/api/redoc` (ADR 018). La démo en ligne ne sert que le schéma : ces pages chargent leurs scripts depuis un CDN (ADR 021).

**Secret statistique** : règle qui interdit de publier un agrégat qui trahirait le comportement d'un foyer. Chez Enedis, une courbe moyenne de moins de 5 000 sites relevés n'est publiée à la demi-heure que la semaine ou le jour du pic du mois ; le reste du temps, elle vaut la moyenne de sa journée, recopiée sur 48 demi-heures. Sous 100 sites, elle est masquée. Le nettoyé d'Ampère marque ces valeurs d'un pas de 1 440 minutes (ADR 026).

**Table longue** : une table qui a une ligne par mesure et par instant, avec une colonne qui nomme la mesure, plutôt qu'une colonne par mesure. Chaque ligne porte ainsi son propre pas de temps et sa propre version, et une mesure de plus ne change pas le schéma. Les nettoyés d'éCO2mix, d'Open-Meteo et d'Enedis sont rangés ainsi (ADR 024 à 026).

**UTC** : temps universel, sans changement d'heure. Le projet stocke tous les instants en UTC et ne les convertit en heure de Paris qu'à l'affichage ; seuls les calendriers, qui décrivent des journées de Paris, ont une colonne de dates de Paris (ADR 027). Il évite ainsi les pièges des journées de 23 et de 25 heures.
