# ADR 022 : fondations des données : Polars, httpx2, et une couche brute faite des octets reçus

- **Statut** : acceptée ; précise la conception, section 8.4
- **Date** : 27 septembre 2026

## Contexte

L'étape 2 ingère les sources publiques de la section 6 de la conception : prix spot, éCO2mix, météo, profils Enedis, calendriers. Trois règles viennent de la conception et de `CLAUDE.md` :

- les données brutes sont gardées telles que reçues, avec leur date de réception, et aucun trou n'est comblé en silence ;
- trois couches : brut, nettoyé (UTC, pas de 15 min), résultats, en Parquet interrogé par DuckDB (section 8.4) ;
- toutes les dates sont stockées en UTC, et l'heure de Paris ne sert qu'à l'affichage.

Enedis ne garde en ligne qu'une fenêtre glissante de trois ans : ce qui n'est pas archivé à sa sortie finit par disparaître. La simulation utilisera pvlib, qui travaille sur des tableaux pandas. Le projet a déjà httpx2, pour les tests de l'API. Le processeur du VPS a les instructions AVX2 que demande la version standard de Polars.

## Options envisagées

### Bibliothèque de tableaux

1. **Polars, et pandas aux frontières seulement**
   - **Avantages** : rapide, avec des types stricts (dates avec fuseau, valeurs manquantes explicites), et un échange sans copie avec DuckDB par Arrow.
   - **Inconvénients** : pvlib et quelques outils de modélisation attendent pandas : il faut convertir à ces frontières.
2. **pandas partout**
   - **Avantages** : l'outil le plus répandu, natif pour pvlib et pour la modélisation.
   - **Inconvénients** : plus lent, et des types plus lâches, qui laissent passer des erreurs silencieuses sur les dates et les valeurs manquantes.

### Client HTTP

1. **httpx2**
   - **Avantages** : déjà dans le projet ; un délai d'attente par défaut ; un transport simulé, qui rejoue des réponses enregistrées dans les tests, sans réseau ni bibliothèque de plus.
   - **Inconvénients** : moins connu que requests.
2. **requests**
   - **Avantages** : le client le plus connu.
   - **Inconvénients** : aucun délai d'attente par défaut, et une bibliothèque de plus pour simuler les réponses.

### Couche brute

1. **Les octets reçus, compressés en gzip, avec un manifeste**
   - **Avantages** : on peut tout reconstruire depuis le brut, et prouver ce qu'on a reçu, grâce à l'empreinte de chaque réponse.
   - **Inconvénients** : il faut relire et analyser le brut pour chaque reconstruction.
2. **Converti en Parquet à la réception**
   - **Avantages** : plus compact, et interrogeable tout de suite.
   - **Inconvénients** : ce n'est plus « tel que reçu » : une erreur de conversion déforme l'archive pour toujours.

### Première source

1. **SMARD, les prix spot** : une API JSON simple, une publication par jour, et des prix au pas de 15 min depuis octobre 2025. Toute la chaîne est exercée sans piège de format.
2. **RTE éCO2mix** : la source la plus riche, mais chaque mesure y existe en trois versions successives.
3. **Open-Meteo** : simple aussi, mais distinguer la météo observée des prévisions archivées demande déjà du soin.

## Décision

Option 1 dans les quatre cas.

- **Dossier des données** : `AMPERE_DATA`, par défaut `data/`, que Git ignore. Il contient `raw/`, `clean/` et `results/`.
- **Couche brute** :
  - chaque réponse est gardée telle quelle, compressée en gzip, sous `raw/<source>/<jeu>/<AAAA>/<MM>/<réception>-<empreinte>.<extension>.gz`. L'en-tête gzip ne porte pas d'heure : un même contenu donne toujours le même fichier ;
  - un manifeste par source, `raw/<source>/manifest.jsonl`, reçoit une ligne par fichier : le jeu, la requête, le chemin, la date de réception en UTC, l'empreinte SHA-256 et la taille ;
  - une réponse identique à la dernière reçue pour la même requête n'ajoute rien : relancer l'ingestion ne change pas l'archive ;
  - chaque fichier s'écrit sous un nom temporaire, puis prend son nom définitif : une coupure ne laisse jamais de fichier à moitié écrit ;
  - la relecture vérifie l'empreinte.
- **Couche nettoyée** : du Parquet, des horodatages en UTC, un pas de 15 min et des unités harmonisées. Elle se reconstruit entièrement depuis le brut. Son format précis arrive avec SMARD.
- **Temps** : une journée est une journée de Paris. Elle compte 96 quarts d'heure, 92 le jour du passage à l'heure d'été et 100 le jour du passage à l'heure d'hiver, et ses bornes se calculent en UTC.
- **HTTP** : un client httpx2 avec un délai de 30 s et un en-tête `User-Agent` qui nomme le projet et son dépôt. Après une erreur de réseau, un code 429 ou un code 5xx, il réessaie deux fois, 1 s puis 2 s plus tard. Toute autre erreur l'arrête tout de suite.
- **Tests** : les réponses des sources sont simulées par le transport de httpx2, sans réseau.

## Conséquences

- La couche brute est la seule qui ne se reconstruit pas : sur le serveur, elle vivra dans un volume déclaré à la plateforme (incrément 2.7), que les sauvegardes du socle devront couvrir.
- L'archive grossit à chaque révision d'une source, comme les versions successives d'éCO2mix : c'est voulu, puisqu'une prévision doit pouvoir être rejouée avec les données connues à son heure (section 7.5).
- Passer de Polars à pandas à la frontière de pvlib coûte une copie du tableau : négligeable pour un quartier de 200 maisons.
- L'en-tête `User-Agent` permet aux sources de savoir qui les interroge, et de contacter le projet.
