# Glossaire

Les notions du projet, expliquées simplement. Ce glossaire s'enrichit à chaque étape et sert aussi à préparer les entretiens.

> Dernière mise à jour : 26 septembre 2026 (étape 1, ADR 019).

## Énergie et marché de l'électricité

**Acheminement (TURPE)** : ce que coûte le transport de l'électricité jusqu'à la maison. En France, c'est le tarif d'utilisation des réseaux publics d'électricité (TURPE), fixé par la CRE. Il est payé dans la facture, avec une part fixe et une part par kWh.

**Agrégateur** : acteur qui pilote ensemble de nombreux petits équipements (batteries, voitures, chauffe-eau) pour en tirer plus de valeur que chacun séparément. Un ensemble ainsi piloté s'appelle une centrale électrique virtuelle.

**Autoconsommation (taux d')** : part de la production solaire consommée sur place, directement ou via la batterie. À ne pas confondre avec l'autosuffisance.

**Autosuffisance (taux d')** : part de la consommation couverte par la production locale. Exemple : une maison qui consomme 5 000 kWh par an, dont 1 500 viennent de ses panneaux, est autosuffisante à 30 %.

**Bilan énergétique** : à chaque instant, l'énergie qui entre dans un système est égale à celle qui en sort ou qui y est stockée. Vérifier le bilan à chaque pas de temps permet de détecter la plupart des erreurs de simulation.

**Bourse de l'électricité (EPEX SPOT)** : plateforme où producteurs, fournisseurs et négociants achètent et vendent l'électricité. EPEX SPOT, la principale bourse en France, organise l'enchère du marché de la veille, qui fixe le prix spot. Elle vend ses données de marché, ce qui explique les restrictions sur leur republication.

**CRE (Commission de régulation de l'énergie)** : autorité indépendante qui fixe notamment les tarifs de réseau et propose les tarifs réglementés de vente.

**Effet rebond** : quand de nombreux équipements réagissent au même signal, par exemple un prix bas, ils agissent tous au même moment et créent un nouveau pic de consommation.

**Intensité carbone** : quantité de CO₂ émise pour produire 1 kWh d'électricité, en grammes de CO₂ par kWh. L'intensité *moyenne* divise les émissions de toute la production par l'énergie produite ; c'est celle que publie RTE. L'intensité *marginale* est celle de la centrale qu'on allume pour produire un kWh supplémentaire ; c'est elle qui mesure l'effet réel d'un changement de consommation, mais elle n'est pas publiée.

**kW, kWh, kWc** : le kW mesure une puissance (un débit), le kWh une énergie (une quantité). Une batterie de 10 kWh qui débite 5 kW se vide en 2 heures. Le kWc (kilowatt-crête) est la puissance d'un panneau en plein soleil, dans des conditions standard.

**Obligation d'achat** : dispositif public qui garantit aux petites installations solaires le rachat de leur surplus à un prix fixe (le tarif d'achat), pendant 20 ans.

**Pas de temps** : durée d'un « tour » de simulation. Toutes les grandeurs sont calculées une fois par pas : toutes les 15 minutes dans Ampère. Plus le pas est court, plus la simulation est fine, mais plus il y a de calculs.

**Prix spot (marché de la veille)** : prix de l'électricité fixé chaque jour vers 13 h par une enchère européenne, pour chaque quart d'heure du lendemain (pour chaque heure avant octobre 2025). Il varie fortement selon l'heure, la saison et la météo, et peut devenir négatif quand la production dépasse largement la demande.

**Profil et puissance souscrite (Enedis)** : Enedis classe les foyers par profil de consommation (par exemple tarif base, ou heures pleines et heures creuses) et par puissance souscrite, c'est-à-dire la puissance maximale autorisée par le contrat (de 3 à 36 kVA). Une puissance élevée signale souvent un grand logement ou un chauffage électrique.

**Taux de charge** : production d'une filière divisée par sa puissance installée ; il indique quelle part de sa capacité elle utilise. En moyenne sur l'année, le solaire en France tourne autour de 13 à 15 %, car il ne produit rien la nuit et peu par temps couvert.

**Vente du surplus** : mode de raccordement où la maison consomme d'abord sa production solaire et ne vend que le reste. Son injection sur le réseau est donc sa production moins sa consommation.

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

**Cache du navigateur** : copies des réponses que le navigateur garde pour ne pas les redemander. Avec l'en-tête `Cache-Control: no-cache`, il revérifie la page auprès du serveur à chaque visite, en envoyant l'empreinte de sa copie (l'ETag), et le serveur répond 304 si elle n'a pas changé. Les fichiers de `assets/` du build ont un nom qui change avec leur contenu : le navigateur peut les garder sans risque. Les autres, la page comprise, sont revérifiés à chaque visite (ADR 018).

**CC BY 4.0** : licence Creative Commons qui autorise à copier, republier et modifier des données, y compris à des fins commerciales, à condition de citer la source et d'indiquer les modifications apportées.

**Contrôle obligatoire** : statut de la CI qui doit être vert pour qu'une pull request puisse être fusionnée. Sur un compte GitHub gratuit, seul un dépôt public peut en imposer (ADR 019).

**CORS (*Cross-Origin Resource Sharing*)** : autorisation qu'un serveur donne à une page venue d'une autre origine pour lire ses réponses. Sans elle, le navigateur bloque cette lecture. Le tableau de bord n'en a pas besoin, puisqu'il appelle l'API sur sa propre origine (ADR 016).

**CSS Modules** : fichiers CSS rattachés à un composant, dont l'outil de construction rend les noms de classes uniques. Deux composants peuvent ainsi avoir chacun une classe `.card` sans se gêner (ADR 017).

**Dependabot** : service de GitHub qui propose par des pull requests les mises à jour des dépendances. Ici, il passe chaque semaine sur les paquets Python (uv), ceux du tableau de bord (npm) et les actions des workflows (`.github/dependabot.yml`).

**Dépendance (FastAPI)** : fonction que FastAPI exécute avant de répondre, déclarée avec `Depends`. Celle du routeur des fichiers du tableau de bord pose l'en-tête `Cache-Control` sur chaque fichier servi (ADR 018).

**Données temps réel, consolidées, définitives** : trois versions successives d'une même mesure publiée par RTE. Chacune corrige la précédente à mesure que les informations arrivent. D'où la règle du projet : stocker chaque version reçue, avec sa date de réception.

**DuckDB** : moteur de base de données analytique qui tourne dans le programme lui-même, sans serveur. Il interroge directement des fichiers Parquet en SQL.

**Empreinte d'une action** : identifiant (SHA) du commit exact d'une action GitHub. Désigner l'action par son empreinte plutôt que par une étiquette comme `v7`, que son auteur peut déplacer, garantit que le code de l'action ne change pas sans une mise à jour visible, proposée par Dependabot. Les outils que l'action télécharge ont leur propre version : la CI fixe celle de uv et vérifie sa somme SHA-256 (ADR 019).

**Fichiers statiques** : fichiers envoyés tels quels au navigateur, sans calcul côté serveur : le HTML, le JavaScript, les styles, les polices. `npm run build` produit ceux du tableau de bord dans `web/dist/`, et en ligne l'API les sert (ADR 018).

**Hook (React)** : fonction qui donne à un composant un état, ou qui lui fait lancer un effet : un appel réseau, un minuteur. `useApiHealth` en est un : il interroge l'API, puis de nouveau 30 secondes après chaque réponse, et renvoie ce qu'il sait.

**Idempotent** : se dit d'une opération qu'on peut relancer sans changer le résultat. Par exemple, ingérer deux fois la même journée ne crée pas de doublon.

**Intégration continue (CI)** : contrôles lancés automatiquement, sur une machine neuve, à chaque modification proposée. Ici, un workflow GitHub Actions, un fichier de `.github/workflows/`, fait tourner, sur chaque pull request et chaque push vers `main`, les mêmes commandes qu'en développement. Il a deux jobs, c'est-à-dire deux suites d'étapes qui tournent chacune sur sa propre machine : un pour le Python, un pour le tableau de bord (ADR 019).

**Jumeau numérique** : modèle informatique d'un système réel, soumis aux mêmes conditions que lui, qui permet de tester des décisions sans toucher au monde réel.

**Licence Ouverte (Etalab 2.0)** : licence de l'État français pour les données publiques. Elle donne les mêmes libertés que CC BY 4.0, à condition de citer la source et la date de dernière mise à jour.

**LTTB (*Largest-Triangle-Three-Buckets*)** : méthode de sous-échantillonnage qui réduit une longue série à quelques centaines de points en gardant sa forme visuelle, pics et creux compris. Elle rend les graphiques rapides sans les déformer.

**Minuteur systemd** : planificateur intégré à Linux, qui lance une tâche à heure fixe. Avec l'option `Persistent`, il rattrape une exécution manquée pendant un arrêt du serveur.

**Origine** : ce qui identifie le site d'une page pour le navigateur : le protocole, le nom et le port, par exemple `https://ampere.exemple:443`. Par sécurité, une page ne peut lire que les réponses de sa propre origine, sauf autorisation CORS.

**Page de repli** (*fallback*) : page qu'un serveur de fichiers renvoie quand le fichier demandé n'existe pas, par exemple `index.html` pour une application dont les pages sont gérées dans le navigateur. L'API n'en a pas : un chemin inconnu reçoit un 404 (ADR 018).

**Parquet** : format de fichier standard de la data, compressé et rangé par colonnes. Lire une seule colonne ne demande pas de lire tout le fichier.

**Point de santé (`/healthz`)** : route qui répond tant que le processus tourne. La plateforme du portfolio l'interroge pour savoir si une démo est en vie. Le tableau de bord interroge la même fonction sous `/api/healthz` pour afficher l'état de l'API.

**Réanalyse** : reconstitution de la météo passée par un modèle qui intègre toutes les observations disponibles (stations, satellites, ballons). C'est la « météo observée » la plus complète et la plus homogène ; ERA5 en est l'exemple le plus connu.

**Relais (*reverse proxy*)** : serveur qui reçoit les requêtes à la place d'un autre et les lui transmet. En développement, le serveur de Vite relaie `/api/…` vers l'API, si bien que le navigateur ne voit qu'une origine.

**Run (d'un modèle météo)** : une exécution complète d'un modèle de prévision, lancée à heure fixe (par exemple à 00 h UTC). Chaque run produit une prévision pour les jours suivants ; il n'est disponible que quelques heures après son lancement.

**Schéma OpenAPI** : description de toutes les routes d'une API (chemins, paramètres, réponses) dans le format standard OpenAPI. FastAPI le produit à partir du code, sur `/api/openapi.json`, et en tire deux pages de documentation interactives, `/api/docs` et `/api/redoc` (ADR 018).

**UTC** : temps universel, sans changement d'heure. Le projet stocke toutes les dates en UTC et ne les convertit en heure de Paris qu'à l'affichage. Il évite ainsi les pièges des journées de 23 et de 25 heures.
