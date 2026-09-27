# ADR 025 : ingestion de la météo d'Open-Meteo

- **Statut** : acceptée ; applique les ADR 006, 007 et 022, avec une exception au pas de 15 min du nettoyé
- **Date** : 27 septembre 2026

## Contexte

Open-Meteo donne la météo du point de Lyon (45,76° N ; 4,84° E, conception section 5) sous licence CC BY 4.0, sans clé, pour un usage non commercial et à moins de 10 000 appels par jour. Ampère en tire la météo observée, pour le calcul solaire (ADR 006) et l'estimation de la consommation récente, et les prévisions telles qu'elles ont été émises, pour prévoir sans fuite d'information (ADR 007). Observé le 27 septembre 2026, et conforme à la documentation d'Open-Meteo :

- **l'archive** (`archive-api.open-meteo.com/v1/archive`) donne une série par variable, heure par heure, en UTC avec `timezone=GMT`. Avec `models=ecmwf_ifs`, elle prend le point de la grille d'ECMWF IFS à 9 km le plus proche (45,73° N ; 4,83° E). Open-Meteo assemble cette série depuis 2017 à partir des runs de 0, 6, 12 et 18 h UTC, sans délai : la date de fin peut aller jusqu'à aujourd'hui, pas au-delà (réponse 400), et les heures à venir du jour viennent de la prévision. Un mois de six variables pèse 37 Ko, 9 Ko compressé ;
- **les runs** (`single-runs-api.open-meteo.com/v1/forecast`) donnent une prévision entière d'ECMWF IFS, désignée par son heure de lancement. Celui de 0 h UTC est archivé depuis le 14 mars 2024. Pour un run absent, l'API répond 400 : « The requested model run is not available ». Un run arrive environ 6 h 30 après son lancement : d'après les métadonnées du modèle, celui de 6 h UTC, le 27 septembre, est arrivé à 12 h 29 UTC. Limité à deux jours par `forecast_days=2`, un run fait 48 heures, 2,8 Ko, 0,9 Ko compressé, et ses quatre rayonnements sont vides à la première heure. Le modèle à 0,25°, `ecmwf_ifs025`, n'a de runs archivés qu'à partir de 2026 ;
- **le sens des valeurs** : un rayonnement est la moyenne de l'heure qui finit à l'instant donné, la température et le vent sont des valeurs à l'instant. Les données le confirment : les 22 et 23 septembre, par ciel clair, le rayonnement culmine à 12:00 UTC, et 13:00 dépasse 11:00, pour un midi solaire vers 11:33 UTC ;
- **`generationtime_ms`**, la durée de calcul de la réponse, change à chaque appel : deux réponses aux mêmes données n'ont jamais les mêmes octets ;
- **le vent** est en km/h par défaut, en m/s avec `wind_speed_unit=ms` ;
- **le plan incliné**, que la conception comptait parmi les variables utiles, se demande à Open-Meteo pour une inclinaison et une orientation à la fois. pvlib le calcule pour chaque toit à partir du rayonnement global, direct normal et diffus ;
- **des runs manquent** : au premier passage, le 27 septembre 2026, les runs de 0 h UTC des 5, 6, 8 et 9 août 2025 répondent 400. Celui du 7 août 2025 n'a ni température, ni rayonnement global, ni diffus, et celui du 23 juin 2026 n'a que le rayonnement global. Les runs voisins, de 6, 12 et 18 h UTC, manquent eux aussi ou sont incomplets. Le 23 juin 2026, ceux de 6 et 12 h sont entiers, mais ils arrivent après 11 h, l'heure d'émission de la prévision (ADR 007) : s'en servir serait une fuite d'information.

## Options envisagées

### Modèle de la météo observée

1. **ECMWF IFS à 9 km**
   - **Avantages** : le modèle des prévisions, ce qui compte pour les évaluer et pour le calcul solaire ; le point de grille le plus proche de Lyon ; aucun délai.
   - **Inconvénients** : une série faite de prévisions à courte échéance, dont le modèle change à chaque mise à jour d'IFS. Open-Meteo la déconseille pour étudier le climat sur des décennies, ce que ne fait pas Ampère.
2. **ERA5 à 25 km**
   - **Avantages** : la réanalyse de référence, homogène depuis 1940.
   - **Inconvénients** : cinq jours de retard, une grille plus grossière, et un autre modèle que celui des prévisions.
3. **Les deux**, ERA5 pour recouper : deux fois plus de requêtes et de brut.

### Réponses qui ne diffèrent que par `generationtime_ms`

1. **Une empreinte qui l'ignore**, comme l'ADR 022 le prévoyait
   - **Avantages** : le brut garde les octets reçus, une réponse aux mêmes données n'ajoute rien, et le journal ne compte que les vraies nouveautés.
   - **Inconvénients** : un paramètre de plus pour `RawStore.save`, qui sert à toutes les sources.
2. **Tout garder**
   - **Avantages** : aucun changement au brut.
   - **Inconvénients** : une réponse redemandée s'ajoute même quand elle est identique. Le mois précédent, redemandé chaque jour pendant 14 jours, fait environ 1,4 Mo par an, et chaque `--full` 1,2 Mo. Le journal annonce une réponse nouvelle à chaque fois.

### Forme du nettoyé

1. **Heure par heure, comme publié**
   - **Avantages** : aucune hypothèse sur ce qui se passe dans l'heure ; pvlib travaille à l'heure.
   - **Inconvénients** : une exception au pas de 15 min de l'ADR 022 ; la simulation passe elle-même au quart d'heure.
2. **Au quart d'heure, comme éCO2mix**
   - **Avantages** : toutes les sources sur la même grille.
   - **Inconvénients** : répéter le rayonnement sur l'heure écrase la course du soleil.

### Variables

1. **Celles de la conception, plus le vent à 10 m**, dont les modèles de température des cellules de pvlib ont besoin. Le direct normal y remplace le plan incliné.
2. **Celles de la conception seulement**, avec un vent standard dans le calcul solaire.

### Runs qu'Open-Meteo n'a pas

1. **Une liste écrite**
   - **Avantages** : rien n'est caché, puisque la liste est relue dans le dépôt ; le passage quotidien réussit ; un nouveau trou reste une erreur.
   - **Inconvénients** : une liste tenue à la main ; un trou définitif demande une pull request pour ne plus faire échouer le passage.
2. **Tolérer un run absent après 7 jours**
   - **Avantages** : aucune liste.
   - **Inconvénients** : un nouveau trou devient un avertissement sans que personne ne décide ; les avertissements reviennent chaque jour ; les runs sont redemandés à chaque passage.
3. **Garder des erreurs**
   - **Avantages** : aucune règle de plus.
   - **Inconvénients** : le passage échoue tous les jours tant qu'Open-Meteo ne comble pas ses trous, et cette erreur permanente cache toutes les autres.

## Décision

Option 1 dans les cinq cas.

- **Requêtes**, pour le point de Lyon, avec la température à 2 m, le rayonnement global, direct, diffus et direct normal, et le vent à 10 m en m/s :
  - la météo observée, par mois UTC, avec ECMWF IFS. Le premier mois commence le 30 juin 2023, puisque la journée de Paris du 1er juillet commence la veille à 22 h UTC. Le mois en cours s'arrête à aujourd'hui. Un mois est redemandé à chaque passage jusqu'à 14 jours après sa fin, puis seulement si sa dernière réponse ne se lit plus ou n'a pas toutes ses heures ;
  - les prévisions : le run de 0 h UTC de chaque jour, du 14 mars 2024 à aujourd'hui, limité à deux jours. Un run ne change plus une fois publié : il n'est redemandé que si sa dernière réponse ne se lit plus ou n'a pas toutes ses heures. Un 400 qui dit « The requested model run is not available » signifie que le run n'existe pas, ou pas encore : rien ne va au brut, le journal le note avec la raison, et le passage continue. Toute autre erreur de `get()`, même un 400 donné pour une autre raison, arrête tout, comme pour les autres sources (ADR 022) ;
  - `--full` redemande tous les mois et tous les runs, ceux de la liste compris ;
  - chaque réponse passe par `get()` : du JSON, de 1 Mo au plus. Une pause de 0,5 s sépare deux requêtes. Dans le brut, la source est `openmeteo`, les jeux `observed` et `forecast`, et la requête d'une réponse est son mois (`2026-09`) ou son run (`2026-09-27T00:00`). L'URL du mois en cours change en effet chaque jour avec sa date de fin ; le manifeste garde l'URL exacte de chaque réponse.
- **Runs manquants** : les six runs vus incomplets ou absents le 27 septembre 2026 sont écrits dans le code, dans `KNOWN_GAPS`. Ils ne sont pas des erreurs, et seul `--full` les redemande ; s'il en trouve un entier, un avertissement dit de le retirer de la liste. Un run manquant qui n'est pas dans la liste reste une erreur. Ce qu'un run incomplet contient reste dans le nettoyé.
- **Doublons** : `RawStore.save` accepte une empreinte, qui dit ce qui doit être identique pour qu'une réponse n'ajoute rien. Pour Open-Meteo, c'est le JSON sans `generationtime_ms`, quel que soit l'ordre des clés. La réponse gardée reste la première reçue ; si son fichier ne se lit plus, la nouvelle réponse est gardée à côté.
- **Schéma** : une réponse est un objet ; son point de grille est à moins de 0,1° de Lyon ; son décalage horaire est nul ; ses unités sont celles demandées ; `hourly` porte `time` et une liste par variable, de même longueur ; les heures se suivent une à une depuis le début du mois ou du run, sans en sortir ; les valeurs sont des nombres ou `null`. Une réponse fautive est une erreur du rapport, et le passage continue sans elle, comme pour éCO2mix (ADR 024).
- **Nettoyé** : `clean/openmeteo/weather.parquet`, reconstruit à chaque passage depuis la dernière réponse lisible de chaque mois et de chaque run ; se rabattre sur une réponse plus ancienne que la dernière reçue est un avertissement. C'est une table longue au pas horaire, une ligne par instant, variable et run :
  - `time` : l'instant, en UTC ;
  - `variable` : `temperature_c`, `global_w_m2`, `direct_horizontal_w_m2`, `diffuse_w_m2`, `direct_normal_w_m2` et `wind_speed_m_s` ;
  - `value` ;
  - `kind` : `observed` ou `forecast` ;
  - `run` : l'heure de lancement d'une prévision, vide pour la météo observée ;
  - `received_at` : l'heure de réception de la réponse.

  Un rayonnement est la moyenne de l'heure qui finit à `time` ; la température et le vent, la valeur à `time`. Une valeur observée postérieure à la réception de sa réponse est écartée dès la lecture de la réponse : c'est une prévision, et un mois auquel manquent ces heures n'est pas entier. Une valeur nulle reste absente, et le fichier n'est remplacé que si ses lignes sont valides.
- **Contrôles** :
  - ligne invalide : une même heure deux fois pour une variable, dans la météo observée ou dans un même run ; un instant hors de l'heure pleine ; une valeur hors de ses bornes, de −40 à 50 °C, de 0 à 1 500 W/m² et de 0 à 75 m/s ;
  - erreur : une heure observée manquante pour une variable, du 1er juillet 2023 à la veille (24 heures par jour de Paris, 23 et 25 aux changements d'heure) ; un run manquant ou incomplet, du 14 mars 2024 à la veille, hors de la liste (48 heures par variable, 47 pour les rayonnements, une heure hors de ces plages ne comptant pas) ; une réponse fautive ; un problème signalé par `verify()` ;
  - avertissement : à partir de 12 h UTC (14 h à Paris en été, 13 h en hiver), le run du jour absent ou incomplet ; un run de la liste revenu entier.
- **Commande** : `ampere ingest openmeteo [--full]`.

## Conséquences

- Le premier passage, le 27 septembre 2026, a fait 968 requêtes, 40 mois et 928 runs, en 11 minutes : loin des limites d'Open-Meteo. Il a gardé 964 réponses, 4,7 Mo de brut, et 432 779 valeurs dans un nettoyé de 1,8 Mo. Les passages suivants font de une à trois requêtes, le mois en cours, le précédent pendant 14 jours et le run du jour, en 6 s environ.
- Le premier run archivé, celui du 14 mars 2024, sert la prévision du 15 mars, premier jour de la mise au point de l'ADR 007.
- Les prévisions du 6 au 10 août 2025 et du 24 juin 2026 n'ont pas leur run entier. Ces jours sont tous dans la période de test : l'étape 5 dira comment les évaluer (ADR 007).
- La simulation passera au quart d'heure avec pvlib, qui tient compte de la course du soleil dans l'heure.
- Dans la conception, le direct normal remplace le plan incliné parmi les variables d'Open-Meteo.
