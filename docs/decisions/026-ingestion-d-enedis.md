# ADR 026 : ingestion des courbes d'Enedis

- **Statut** : acceptée ; applique les ADR 001, 005 et 022, avec une exception au pas de 15 min du nettoyé
- **Date** : 27 septembre 2026

## Contexte

Enedis publie, au pas de la demi-heure, la consommation des sites de 36 kVA au plus et l'injection des producteurs, par région et par segment, sous Licence Ouverte 2.0 (ADR 001, conception section 6.2). Observé le 27 septembre 2026 :

- **la plateforme** : le portail d'Enedis a changé de logiciel. L'API d'Opendatasoft, celle de la conception, survit comme « couche de compatibilité » : elle sert les lignes et les exports, mais répond 410 pour les métadonnées. L'API native donne les métadonnées d'un jeu, dont la date de sa dernière publication (`dataUpdatedAt`, le 30 juillet 2026 pour les deux jeux) et un rythme trimestriel ; elle exporte en JSON ou en CSV, par pages de 10 000 lignes, mais pas en Parquet ;
- **la consommation** (`conso-inf36-region`) : en Auvergne-Rhône-Alpes, 82 segments, un profil et une plage de puissance souscrite, dont 39 résidentiels, de RES1 à RES4 avec leurs totaux. Chacun a 52 608 demi-heures, du 30 juin 2023 à 22 h UTC, début de la journée de Paris du 1er juillet, au 30 juin 2026 à 21 h 30 UTC. Les instants sont en UTC et la série est régulière : aux changements d'heure, ni heure fantôme ni passage manquant ;
- **l'injection solaire** (`prod-region`) : la même structure, par filière et plage de puissance d'injection. Le solaire de 0 à 3 kW et celui de 3 à 9 kW, les toits de maisons, comptent chacun environ 92 000 sites ;
- **les formats** : un mois de consommation résidentielle pèse 30 Mo en JSON, reçu en 57 s, et 805 Ko en Parquet, reçu en 10 s ; un mois de solaire des toits, 43 Ko. Un même mois demandé deux fois donne les mêmes octets. Un mois pas encore publié, ou plus publié, revient vide : un Parquet de zéro ligne. Les instants du Parquet n'ont pas de fuseau, mais ce sont ceux du JSON, en UTC ;
- **les champs**, d'après la note d'Enedis jointe au jeu :
  - le nombre de sites, le même pour les 48 demi-heures d'une journée ;
  - l'énergie totale, modélisée à partir d'un profil ajusté à la température pour les sites sans courbe relevée ;
  - trois courbes moyennes, en Wh par demi-heure, des seuls sites à compteur communicant. Les courbes n° 1 et n° 2 partagent ces sites en deux moitiés, selon la part de leur consommation entre 8 h et 20 h, ou le coefficient de variation de leur production ; la courbe n° 1 + n° 2 les réunit ;
  - pour chaque courbe, un indice : la part des sites du segment qu'elle représente, en pour cent, ou « < 1 » pour une moitié de courbe d'un très grand segment ;
- **le secret statistique** : sous 5 000 sites relevés sur un trimestre, une courbe n'est publiée à la demi-heure que la semaine du pic du mois, et sous 500 que le jour du pic. Le reste du temps, c'est la moyenne de la journée, recopiée sur ses 48 demi-heures. Sous 100 sites, la valeur est masquée, avec l'indice « S ». En janvier 2026, 21 segments résidentiels sur 39 ont une vraie courbe tout le mois, 11 seulement la semaine du pic, 6 le jour du pic, et un est masqué ; le solaire des toits n'a que quelques demi-heures masquées. Le secret touche aussi l'énergie totale, plus rarement : sur trois ans, 439 demi-heures du solaire des toits la masquent.

## Options envisagées

### Périmètre

1. **La consommation résidentielle et le solaire des toits**
   - **Avantages** : ce que consomment les foyers du quartier, et de quoi vérifier le calcul solaire de l'étape 3.
   - **Inconvénients** : deux jeux à suivre au lieu d'un.
2. **La consommation résidentielle seulement** : la production attend l'étape 3.
3. **Toute la consommation** : 4,3 millions de demi-heures, professionnels et entreprises compris, dont la moitié sans usage prévu.

### Voie et format

1. **Parquet, par la couche de compatibilité**
   - **Avantages** : 37 fois plus léger que le JSON, six fois plus rapide, typé, et les mêmes octets pour les mêmes données : les doublons s'écartent sans empreinte.
   - **Inconvénients** : Enedis garde cette couche pour son ancienne API. Si elle disparaît, l'ingestion s'arrête en erreur, et il faudra passer à l'API native.
2. **CSV par l'API native** : la voie la plus durable, mais six pages par mois, et 7 Mo de texte à lire.
3. **JSON, comme les autres sources** : 1,1 Go et plus d'une demi-heure pour l'historique.

### Rythme

1. **À chaque publication**
   - **Avantages** : une requête légère par passage et par jeu, et tous les mois redemandés quand la publication change : le brut garde chaque publication, un mois identique n'ajoutant rien.
   - **Inconvénients** : six minutes de requêtes les jours qui suivent une publication.
2. **Toute la fenêtre en une requête** : un seul fichier par publication, mais 30 Mo de plus dans le brut à chaque trimestre.
3. **Tous les mois à chaque passage** : 36 requêtes par jour, pour des données qui changent quatre fois par an.

### Forme du nettoyé

1. **À la demi-heure, pas marqué**
   - **Avantages** : les valeurs telles que publiées, et un jour plat reconnaissable à son pas.
   - **Inconvénients** : une exception au pas de 15 min de l'ADR 022 ; la simulation choisit elle-même ses segments et passe au quart d'heure.
2. **Seulement les vraies demi-heures** : plus simple à lire, mais 18 segments sur 39 perdent l'essentiel de leur historique.
3. **Au quart d'heure, comme éCO2mix** : toutes les sources sur la même grille, mais 20 millions de lignes.

## Décision

Option 1 dans les quatre cas.

- **Requêtes** :
  - à chaque passage, les métadonnées de chaque jeu, par l'API native (`/data-fair/api/v1/datasets/<jeu>`), en JSON de 1 Mo au plus. Leur `dataUpdatedAt` date la dernière publication ;
  - les mois de Paris, du 1er juillet 2023 au mois en cours, une requête par mois et par jeu, en Parquet par la couche de compatibilité (`/api/explore/v2.1/catalog/datasets/<jeu>/exports/parquet`), de 20 Mo au plus. Pour la consommation : la région 84 et les profils qui commencent par RES. Pour l'injection : la région 84, la filière « F5 : Solaire » et les plages de 0 à 3 kW et de 3 à 9 kW ;
  - pendant les 7 jours qui suivent la première réception d'une nouvelle publication, chaque passage redemande tous les mois : un passage interrompu ne laisse pas de mois à la publication précédente. Le reste du temps, seul un mois sans réponse lisible est redemandé, et `--full` les redemande tous ;
  - une pause de 0,5 s sépare deux requêtes. Dans le brut, la source est `enedis`, les jeux `publication`, `consumption` et `solar`, et la requête d'une réponse est le nom du jeu d'Enedis ou le mois (`2026-01`).
- **Schéma** : des métadonnées sont un objet avec une date `dataUpdatedAt`. Un mois est un Parquet lisible, avec les colonnes attendues et leurs types ; ses instants sont sur la demi-heure et dans le mois ; il n'a que la région 84 et les segments demandés, sans doublon ; ses indices sont des nombres entiers, « < 1 » ou « S ». Une réponse fautive est une erreur du rapport, et le passage continue sans elle.
- **Nettoyé** : `clean/enedis/consumption.parquet` et `clean/enedis/solar.parquet`, reconstruits à chaque passage. Chaque mois vient de sa réponse lisible la plus complète, la plus récente à nombre de lignes égal : un mois qu'Enedis ne publie plus revient vide, et le nettoyé garde la dernière version publiée. C'est une table longue au pas publié de la demi-heure, une exception au pas de 15 min de l'ADR 022, comme pour la météo (ADR 025) :
  - `start` : le début de la demi-heure, en UTC ;
  - `profile` et `power_range`, les libellés d'Enedis ; le solaire n'a que `power_range` ;
  - `measure` : `sites`, `total_w`, `mean_w` pour la courbe n° 1 + n° 2, `mean_1_w` et `mean_2_w` ;
  - `value` : un nombre de sites, ou la puissance moyenne de la demi-heure en W, soit les Wh publiés multipliés par 2 ;
  - `step_minutes` : 30, ou 1 440 pour une valeur de la journée entière. C'est toujours le cas de `sites`, et celui d'une autre mesure un jour de Paris où toutes ses demi-heures ont la même valeur ;
  - `received_at` : l'heure de réception de la réponse.

  Une valeur masquée reste absente. La simulation passera au quart d'heure (ADR 005), et choisira ses segments d'après leur pas.
- **Contrôles** :
  - ligne invalide : une même demi-heure deux fois pour un segment et une mesure ; un instant hors de la demi-heure ; une valeur hors de ses bornes (un nombre de sites ou une puissance totale négatifs, une courbe moyenne hors de 0 à 36 000 W pour la consommation et de 0 à 9 000 W pour le solaire) ;
  - erreur : une demi-heure sans nombre de sites pour un segment, du 1er juillet 2023 à la dernière demi-heure publiée (48 par jour de Paris, 46 et 50 aux changements d'heure). Le nombre de sites dit qu'une ligne existe ; un total ou une courbe absents sont masqués, pas manquants. Et aussi une réponse fautive, ou un problème signalé par `verify()` ;
  - avertissement : une dernière demi-heure publiée vieille de plus de 150 jours, soit un mois de retard sur la publication trimestrielle attendue.
- **Commande** : `ampere ingest enedis [--full]`.

## Conséquences

- Le premier passage, le 27 septembre 2026, a fait 80 requêtes, les métadonnées des deux jeux et 39 mois pour chacun, en 7 minutes. Il a gardé 23 Mo de brut ; le nettoyé de la consommation compte 9 848 590 valeurs, dans 38 Mo, et celui du solaire 524 324, dans 1,9 Mo. Un passage ordinaire fait deux requêtes, ceux des 7 jours qui suivent une publication 80.
- Sur les trois ans, la courbe globale de 20 segments résidentiels est à la demi-heure de bout en bout. Pour 18 autres, le secret statistique ne garde la demi-heure que la semaine ou le jour du pic de chaque mois, et il masque le dernier : l'étape 3 choisira les segments du quartier en conséquence.
- L'énergie totale est en partie modélisée par Enedis ; seules les courbes moyennes sont des mesures.
- Si Enedis retire sa couche de compatibilité, les exports passeront par l'API native, en CSV.
- La conception note le changement de plateforme, et le sens des courbes et de l'indice.
