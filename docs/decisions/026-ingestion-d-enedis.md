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
  - le nombre de sites, le même pour toutes les demi-heures d'une journée de Paris. Il change pourtant parfois en cours de journée, de moins de 0,1 % ;
  - l'énergie totale, modélisée à partir d'un profil ajusté à la température pour les sites sans courbe relevée ;
  - trois courbes moyennes, en Wh par demi-heure, des seuls sites à compteur communicant. Les courbes n° 1 et n° 2 partagent ces sites en deux moitiés, selon la part de leur consommation entre 8 h et 20 h, ou le coefficient de variation de leur production ; la courbe n° 1 + n° 2 les réunit ;
  - pour chaque courbe, un indice : la part des sites du segment qu'elle représente, en pour cent, ou « < 1 » pour une moitié de courbe d'un très grand segment ;
- **le secret statistique** : sous 5 000 sites relevés sur un trimestre, une courbe n'est publiée à la demi-heure que la semaine du pic du mois, et sous 500 que le jour du pic. Le reste du temps, c'est la moyenne de la journée, recopiée sur ses 48 demi-heures. Sous 100 sites, la valeur est masquée, avec l'indice « S ». En janvier 2026, 21 segments résidentiels sur 39 ont une vraie courbe tout le mois, 11 seulement la semaine du pic, 6 le jour du pic, et un est masqué ; le solaire des toits n'a que quelques demi-heures masquées. Le secret touche aussi l'énergie totale, plus rarement : sur trois ans, 439 demi-heures du solaire des toits la masquent, toujours avec les courbes.

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
  - pendant 7 jours de Paris, à partir du jour de la première réception d'une publication qui en remplace une autre, chaque passage redemande tous les mois : un passage interrompu ne laisse pas de mois à la publication précédente. Les jours se comptent à Paris : l'heure du passage et celle d'une réception viennent de deux horloges, et un huitième passage tomberait sinon d'un côté ou de l'autre de la limite, à quelques dixièmes de seconde près. La première publication jamais vue ouvre ces 7 jours à sa propre date : une première installation n'a pas à tout redemander chaque jour. Une réponse de métadonnées fautive coupe la chaîne : on ne sait pas ce qu'Enedis publiait à ce moment-là, et les 7 jours repartent de la réception suivante. Le reste du temps, un mois est redemandé quand il n'a pas de réponse, quand sa dernière réponse ne se lit pas, ou quand elle est vide alors que le mois est dans la fenêtre, les 36 mois qui finissent avec le dernier mois publié ; `--full` les redemande tous ;
  - une pause de 0,5 s sépare deux requêtes. Dans le brut, la source est `enedis`, les jeux `publication`, `consumption` et `solar`, et la requête d'une réponse est le nom du jeu d'Enedis ou le mois (`2026-01`).
- **Schéma** : des métadonnées sont un objet avec une date `dataUpdatedAt`. Un mois est un Parquet lisible, avec les colonnes attendues et leurs types, des nombres entiers ou à virgule, tous finis ; ses instants sont sur la demi-heure et dans le mois ; il n'a que la région 84 et les segments demandés, sans doublon, et sans valeur nulle dans l'instant, la région, le segment, le nombre de sites et les indices ; ses indices sont des nombres entiers, « < 1 » ou « S ». Une courbe manque exactement quand son indice vaut « S », et un total seulement quand les trois courbes sont masquées : c'est le cas de tout le brut du 27 septembre 2026. Sans ces deux règles, une réponse aux courbes vidées par erreur remplacerait les bonnes sans que rien ne le signale. Une réponse fautive est une erreur du rapport, et le passage continue sans elle. C'est aussi le cas d'un Parquet que Polars ne sait pas lire, même quand il panique : gardée dans le brut, une telle réponse arrêterait sinon chaque passage suivant. Avant tout, le pied du Parquet dit le nombre de lignes et les colonnes d'un mois : au-delà de 500 000 lignes ou de 30 colonnes, la réponse est fautive sans être lue, car Parquet se compresse lui-même, et quelques kilo-octets pourraient se déplier en gigaoctets. Un vrai mois de consommation a 58 000 lignes et 15 colonnes, dont seules celles qu'Ampère lit sont chargées.
- **Nettoyé** : `clean/enedis/consumption.parquet` et `clean/enedis/solar.parquet`, reconstruits à chaque passage. Chaque mois vient de sa dernière réponse lisible qui a des lignes : la dernière publication l'emporte, même quand elle a moins de lignes, et ce qui lui manque devient une erreur des contrôles, ou un avertissement à la fin d'un trimestre. Quand la dernière réponse d'un mois est vide ou illisible, la plus récente qui a des lignes sert, avec un avertissement. Seul un mois sorti de la fenêtre revient vide sans rien dire : le nettoyé garde alors la dernière version publiée. La fenêtre, ce sont les 36 mois qui finissent avec le dernier mois publié, car Enedis publie trois ans. C'est une table longue au pas publié de la demi-heure, une exception au pas de 15 min de l'ADR 022, comme pour la météo (ADR 025) :
  - `start` : le début de la demi-heure, en UTC ;
  - `profile` et `power_range`, les libellés d'Enedis ; le solaire n'a que `power_range` ;
  - `measure` : `sites`, `total_w`, `mean_w` pour la courbe n° 1 + n° 2, `mean_1_w` et `mean_2_w` ;
  - `value` : un nombre de sites, ou la puissance moyenne de la demi-heure en W, soit les Wh publiés multipliés par 2 ;
  - `step_minutes` : 30, ou 1 440 pour une valeur égale sur toutes les demi-heures d'un jour de Paris : le plus souvent le nombre de sites, qu'Enedis compte par journée, et une courbe que le secret réduit à la moyenne de sa journée. Une journée à zéro garde le pas de 30 min : rien n'étant négatif, chacune de ses demi-heures vaut zéro ;
  - `received_at` : l'heure de réception de la réponse.

  Une valeur masquée reste absente. La table est rangée mois après mois, puis par segment, mesure et demi-heure : la trier en entier en ferait une copie de plus. La simulation passera au quart d'heure (ADR 005), et choisira ses segments d'après leur pas dans l'historique de calage, sans regarder la période de test (ADR 007).
- **Contrôles** :
  - ligne invalide : un instant hors de la demi-heure ; une valeur hors de ses bornes (un nombre de sites ou une puissance totale négatifs, une courbe moyenne hors de 0 à 36 000 W pour la consommation et de 0 à 9 000 W pour le solaire). Une même demi-heure deux fois est une faute de forme de sa réponse, et deux mois ne se recouvrent jamais : chercher les doubles parmi dix millions de lignes coûterait plus d'un gigaoctet pour rien ;
  - erreur : une demi-heure sans nombre de sites pour un segment, du 1er juillet 2023 à la dernière demi-heure publiée (48 par jour de Paris, 46 et 50 aux changements d'heure). Le nombre de sites dit qu'une ligne existe ; un total ou une courbe absents sont masqués, pas manquants. Seuls les segments attendus sont contrôlés ainsi, demi-heure par demi-heure : les 39 segments résidentiels et les deux plages du solaire des toits, listés dans le code tels qu'Enedis les publiait le 27 septembre 2026. Un segment attendu qui n'a aucune valeur. Un jeu sans aucune valeur, ou dont aucune dernière réponse n'a de lignes, comme quand le filtre ne correspond plus à ce qu'Enedis publie. Et aussi une réponse fautive, ou un problème signalé par `verify()` ;
  - avertissement : une dernière demi-heure publiée vieille de plus de 150 jours, soit un mois de retard sur la publication trimestrielle attendue ; un nettoyé qui s'arrête plus de 60 jours avant la date de la dernière publication, qui couvre d'ordinaire jusqu'à un mois avant sa date. C'est le signe d'une publication arrivée en partie, par exemple après un passage interrompu : `--full` la rattrape, et l'avertissement le conseille, sauf dans un passage qui vient de redemander tous les mois ; un nettoyé qui s'arrête au milieu d'un trimestre, alors qu'Enedis publie des trimestres entiers : un dernier mois revenu vide pendant les 7 jours manquerait sinon sans que rien ne le signale ; un mois pris dans une réponse plus ancienne que la dernière, quand celle-ci est illisible, ou vide alors que le mois est dans la fenêtre ; un mois dont le secret statistique masque plus de 20 % de la courbe globale, quand il en masque au plus 7,7 % pour la consommation et 1,2 % pour le solaire sur les trois ans ; un segment publié qui n'est pas dans la liste.
- **Commande** : `ampere ingest enedis [--full]`.

## Conséquences

- Le premier passage, le 27 septembre 2026, a fait 80 requêtes, les métadonnées des deux jeux et 39 mois pour chacun, en 7 minutes. Il a gardé 23 Mo de brut ; le nettoyé de la consommation compte 9 848 590 valeurs, dans 16 Mo, et celui du solaire 524 324, dans 1,3 Mo. Un passage ordinaire fait deux requêtes, ceux des 7 jours qui suivent une publication 80. Il relit tout le brut d'Enedis pour reconstruire les deux tables, avec un pic de mémoire sous 0,5 Go : le traitement quotidien a droit à 1 Go (conception, section 8.5).
- Sur les trois ans, la courbe globale de 20 segments résidentiels est à la demi-heure de bout en bout. Pour 18 autres, le secret statistique ne garde la demi-heure que la semaine ou le jour du pic de chaque mois, et il masque le dernier. L'étape 3 choisira les segments du quartier d'après leur pas dans l'historique de calage, jamais d'après la période de test (ADR 007, conception section 7.1).
- L'énergie totale est en partie modélisée par Enedis ; seules les courbes moyennes sont des mesures.
- Un mois qu'Enedis ne publie plus, et que le brut n'a pas, manque pour de bon : chaque passage le dit par des erreurs, jusqu'à ce qu'une sauvegarde du brut le rende (incrément 2.7). La publication de fin octobre 2026 devrait faire sortir juillet à septembre 2023 de la fenêtre : le brut du serveur, à l'incrément 2.7, devra partir de celui du développement avant cette date.
- Un passage interrompu, puis 7 jours sans passage complet, laissent des mois à la publication précédente. L'avertissement du décalage le signale quand un trimestre manque ; au milieu de la fenêtre, un mois resté à la publication précédente ne se voit pas, car ses valeurs sont complètes. `--full` les redemande tous.
- Un mois qui revient vide par erreur à la fin de ce qui est publié passe pour un mois pas encore publié, et n'est redemandé qu'à la publication suivante ou avec `--full`. L'avertissement du trimestre le signale ; un trimestre entier manquant, l'avertissement des 60 jours.
- Si Enedis ajoute, renomme ou retire un segment, les contrôles le signalent à chaque passage, jusqu'à ce que la liste du code suive. Après un glissement de la fenêtre, un segment renommé manquerait pourtant dans les mois sortis, gardés sous son ancien nom : la liste devra alors dire depuis quand vaut chaque nom.
- Une publication qui masque davantage de courbes, ou qui en réduit à la moyenne de leur journée, passe tant qu'elle ne dépasse pas 20 % d'un mois, soit 4 à 6 des 39 segments de foyers masqués en entier ; le pas de 1 440 min rend la seconde visible dans le nettoyé. Comparer chaque mois à sa réponse précédente la verrait mieux.
- Une fenêtre qui commencerait en cours de mois garderait son premier mois en partie, et ses demi-heures manquantes resteraient des erreurs, même avec une version complète dans le brut. La fenêtre d'Enedis suit aujourd'hui les trimestres.
- Une publication que `dataUpdatedAt` ne reflète pas n'est jamais demandée, et une couche de compatibilité figée rendrait les anciens octets : seuls les avertissements des 60 et 150 jours le signalent. Si une publication adopte une forme que les règles refusent, chaque mois est redemandé à chaque passage, 80 requêtes par jour, jusqu'à ce que le code suive.
- Les contrôles du trimestre et des 150 jours supposent une publication trimestrielle. Les métadonnées le disent (`"frequency": "quarterly"`), mais le code ne les lit pas encore : un passage à un rythme mensuel ferait avertir deux mois sur trois.
- Le brut écarte un mois identique en comparant ses octets, ce qui suppose qu'Enedis rende ses lignes toujours dans le même ordre, comme le 27 septembre 2026. Sinon, chaque publication garderait de nouveau les 78 mois, soit environ 23 Mo ; un `order_by` dans l'export l'éviterait.
- Les 7 jours redemandent aussi les mois sortis de la fenêtre, qui reviennent vides : les 80 requêtes grandissent de 6 à chaque trimestre. Une suite bornera la redemande à la fenêtre.
- Au-delà de 100 lignes invalides, le rapport ne liste que les 100 premières et compte les autres : une publication fautive sur une colonne en ferait des millions, qui dépasseraient la mémoire du traitement quotidien.
- Comme pour les autres sources, seule une ligne invalide garde le nettoyé tel quel : une table vide, signalée par une erreur, remplace la précédente, que le brut permet de reconstruire.
- Une réponse piégée pourrait encore épuiser la mémoire avec des chaînes géantes, que le pied ne mesure pas. La limite de mémoire du traitement quotidien et la procédure de restauration du brut, prévues pour l'incrément 2.7, devront en tenir compte.
- Si Enedis retire sa couche de compatibilité, les exports passeront par l'API native, en CSV.
- La conception note le changement de plateforme, et le sens des courbes et de l'indice.
