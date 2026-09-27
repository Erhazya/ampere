# ADR 024 : ingestion d'éCO2mix, de RTE

- **Statut** : acceptée ; applique l'ADR 022 et la section 6.2 de la conception
- **Date** : 27 septembre 2026

## Contexte

RTE publie éCO2mix sous Licence Ouverte 2.0, par la plateforme ODRÉ (Open Data Réseaux Énergies) : une API sans clé, avec un quota de 50 000 appels par mois. Ampère y prend l'intensité CO₂, la consommation et la prévision J-1 de RTE pour la France, et le solaire d'Auvergne-Rhône-Alpes, pour valider plus tard son calcul de production. Observé le 27 septembre 2026 :

- **quatre jeux** : `eco2mix-national-tr` et `eco2mix-regional-tr` en temps réel, sur les 90 derniers jours, soit du 1er juillet 2026 à aujourd'hui (et demain pour la prévision nationale) ; `eco2mix-national-cons-def` et `eco2mix-regional-cons-def`, avec des données définitives de 2012 à fin 2024 et consolidées de janvier 2025 à fin juin 2026. Le champ `nature` donne la version de chaque ligne ;
- **des versions par paquets** : le jeu consolidé-définitif a été modifié pour la dernière fois le 30 juillet 2026, jusqu'au 30 juin, et le temps réel commence là où il s'arrête. Comme le temps réel ne garde que 90 jours, un mois peut perdre son début avant d'être consolidé ;
- **le national** : une ligne par quart d'heure. En consolidé et en définitif, la consommation et l'intensité CO₂ ne sont remplies qu'à l'heure pile et à la demi-heure, alors que la prévision J-1 l'est à chaque quart d'heure. En temps réel, tout est au quart d'heure, avec environ une heure de retard, et les lignes de demain ne portent que la prévision J-1 ;
- **la région** (code 84) : une ligne par demi-heure en consolidé et en définitif, par quart d'heure en temps réel, avec la production solaire (`solaire`, en MW) et son taux de charge (`tch_solaire`, en %) ;
- **le temps** : `date_heure` est en UTC ; `date` et `heure` sont à l'heure de Paris, et ODRÉ range ses lignes selon elles. Le jour du passage à l'heure d'été, il donne aussi les quarts d'heure de 02:00 à 02:45, qui n'existent pas, aux instants UTC de 03:00 à 03:45 : les mesures sont les mêmes, la prévision répète la dernière valeur. Le jour du retour à l'heure d'hiver, il ne donne qu'une fois l'heure de 02:00, vécue deux fois : le second passage, de 01:00 à 01:59 UTC. Le premier, de 00:00 à 00:59 UTC, manque en 2023, 2024 et 2025, au national comme en région. Ces deux pièges sont apparus au premier vrai passage ;
- **le sens des demi-heures** : pour la région, le centre de gravité de la production solaire tombe au même moment en consolidé ou en définitif qu'en temps réel, à 0,1 minute près sur 88 jours appariés de juillet à septembre. Pour le national, il tombe 7 à 17 minutes plus tard, et la consommation s'aligne sur la prévision J-1 avec 12 à 20 minutes d'écart de plus qu'en temps réel. Une demi-heure nationale ne semble donc pas porter sur la demi-heure qui commence à son instant ;
- **depuis juillet 2023** : aucune valeur ne manque, hors des quarts d'heure :15 et :45 du consolidé-définitif national (26 304 de chaque). La consommation va de 30 à 90 GW, l'intensité de 5 à 71 g/kWh, le solaire régional jusqu'à 3 254 MW pour un taux de charge de 96,8 % ;
- **l'export d'un mois** (`/exports/json`, tous les champs) pèse 2,6 Mo pour le national (166 Ko en gzip) et 1,1 Mo pour la région (101 Ko), en moins d'une seconde. Deux exports identiques rendent les mêmes octets, dans un ordre qui n'est pas celui des dates, même avec `order_by`.

## Options envisagées

### Périmètre

1. **Le national et Auvergne-Rhône-Alpes**
   - **Avantages** : le même code sert aux deux, et l'étape des données couvre tout ce qu'Ampère prend à éCO2mix.
   - **Inconvénients** : environ 20 % de travail en plus dans cet incrément.
2. **Le national seulement, la région avec la simulation**
   - **Avantages** : un incrément plus court.
   - **Inconvénients** : il faudra revenir sur cette source à l'étape suivante.

### Champs demandés

1. **Tous les champs**
   - **Avantages** : le brut garde les lignes entières, et la production par filière sera là pour le tableau de bord sans rien redemander.
   - **Inconvénients** : environ 270 Ko compressés par mois pour les deux jeux.
2. **Seulement les champs utilisés**
   - **Avantages** : un brut plus petit.
   - **Inconvénients** : un besoin nouveau oblige à redemander l'historique.

### Unité de requête et rattrapage

1. **Le mois, avec montée de version**
   - **Avantages** : une seule unité de requête ; environ 80 requêtes au premier passage, 4 à 6 ensuite ; une version plus avancée est prise dès qu'ODRÉ la publie.
   - **Inconvénients** : le mois en cours change chaque jour, et chaque version reçue reste dans le brut : environ 50 Mo par an.
2. **Le temps réel au jour, le reste au mois**
   - **Avantages** : le brut ne grossit que d'environ 7 Mo par an.
   - **Inconvénients** : environ 250 requêtes au premier passage, et deux unités de requête dans le code.
3. **Tous les mois à chaque passage**
   - **Avantages** : aucune révision ne peut échapper.
   - **Inconvénients** : 80 requêtes et une centaine de mégaoctets téléchargés chaque jour.

### Forme du nettoyé

1. **Au quart d'heure, comme les prix de SMARD** : une valeur à la demi-heure vaut pour ses deux quarts d'heure, avec le pas publié et la version en colonnes.
   - **Avantages** : la simulation lit toutes les sources sur la même grille (ADR 005).
   - **Inconvénients** : une hypothèse sur le sens des valeurs à la demi-heure (voir la décision).
2. **Au pas publié**
   - **Avantages** : aucune hypothèse.
   - **Inconvénients** : chaque lecteur doit faire la conversion.

### Reconstruction du nettoyé

1. **Mois par mois, depuis le jeu choisi**
   - **Avantages** : chaque passage ne lit qu'une fois les réponses qu'il vient de choisir ou de vérifier ; une ligne se contrôle contre son mois et son jeu ; une vieille réponse abîmée ne bloque rien.
   - **Inconvénients** : un trou du consolidé n'est plus bouché par le temps réel ; il reste un trou, signalé.
2. **Quart d'heure par quart d'heure, parmi toutes les réponses**
   - **Avantages** : chaque quart d'heure prend la version la plus avancée qui existe, même quand le consolidé a un trou.
   - **Inconvénients** : la réponse temps réel d'un mois devenu consolidé est relue à chaque passage sans jamais être redemandée. Si elle s'abîme, chaque passage échoue, sans remède.

### Placement des demi-heures nationales

1. **Garder le placement, et le vérifier** au consolidé de juillet à septembre 2026, contre le temps réel gardé dans le brut.
   - **Avantages** : la correction se fera sur une preuve directe ; aucun lecteur n'utilise ces valeurs avant la simulation.
   - **Inconvénients** : d'ici là, le nettoyé garde un placement probablement faux pour le national.
2. **Ne plus étendre les demi-heures nationales d'ici là**
   - **Avantages** : aucune hypothèse.
   - **Inconvénients** : les contrôles changent deux fois.
3. **Centrer les demi-heures dès maintenant**
   - **Avantages** : plus proche des indices.
   - **Inconvénients** : les indices disent 7 à 20 minutes, pas 15 ; au passage au temps réel, le dernier quart d'heure du consolidé n'aurait plus de valeur.

### Réponse mal formée pour un mois

1. **Une erreur, pas un arrêt**
   - **Avantages** : une anomalie d'un mois ne fige ni les autres mois ni l'autre zone.
   - **Inconvénients** : le passage va au bout avec un trou, qui est une erreur du rapport.
2. **Arrêter le passage**
   - **Avantages** : le plus simple et le plus visible.
   - **Inconvénients** : une anomalie en temps réel se répète à chaque passage tant qu'elle reste dans la fenêtre de 90 jours, et fige toute la source jusqu'à trois mois.

### Réponse moins complète qu'une autre réponse gardée

1. **Garder la plus complète**
   - **Avantages** : un export consolidé encore vide ou partiel, ou une réponse des périodes tronquée, ne fait rien perdre au nettoyé.
   - **Inconvénients** : quelques fichiers de plus à lire, pour les mois qui ont une réponse dans chaque jeu.
2. **Accepter un nettoyé dégradé pendant un passage**
   - **Avantages** : rien à ajouter ; les erreurs sont signalées et le passage suivant répare.
   - **Inconvénients** : le nettoyé perd pour un temps des valeurs que le brut a.

## Décision

Option 1 dans les huit cas. Pour la reconstruction du nettoyé, la première version de cette décision retenait l'option 2 ; la relecture de son code en a reproduit le blocage.

- **Requêtes** :
  - à chaque passage, quatre requêtes demandent à ODRÉ les périodes des versions : pour chaque zone, celles du consolidé et du définitif, puis celle du temps réel. Le définitif doit remonter au 1er juillet 2023, le consolidé le suivre sans trou, et le temps réel exister : sinon, le passage s'arrête ;
  - puis chaque mois de Paris du 1er juillet 2023 au mois du lendemain, pour avoir la prévision du lendemain le dernier jour du mois. Chaque mois est exporté avec tous ses champs (filtré sur la région 84 pour le régional), depuis le jeu consolidé-définitif s'il couvre le mois entier, sinon depuis le temps réel ;
  - un mois est redemandé à chaque passage jusqu'à 14 jours après sa fin, comme une semaine de SMARD (ADR 023). Au-delà, il ne l'est que si ODRÉ en offre une version plus avancée que la réponse gardée (temps réel, puis consolidée, puis définitive), ou si cette réponse ne se lit plus ou n'a pas toutes ses valeurs. Si l'export n'a pas encore la version annoncée, un avertissement le dit ;
  - un mois temps réel dont le début est sorti de la fenêtre de 90 jours n'est plus redemandé, même avec `--full` : sa réponse reviendrait tronquée. La réponse gardée reste, et ses trous restent des erreurs jusqu'au consolidé ;
  - `ampere ingest eco2mix --full` redemande tous les autres mois ;
  - chaque réponse passe par `get()` (ADR 022) : du JSON, de 20 Mo au plus. Une pause de 0,5 s sépare deux requêtes. Dans le brut, la source est `eco2mix`, et les jeux `coverage`, `national-tr`, `national-cons-def`, `regional-tr` et `regional-cons-def`.
- **Schéma** : une réponse est une liste d'objets. Chaque ligne tombe dans le mois demandé, sur la grille du quart d'heure, avec un `date_heure` en UTC, une `nature` propre à son jeu (temps réel dans `tr`, consolidée ou définitive dans `cons-def`) et des mesures numériques ou nulles ; une ligne régionale a le code 84 ; une valeur consolidée ou définitive à la demi-heure tombe sur une demi-heure. Une ligne dont l'heure de Paris n'a jamais existé est écartée ; deux lignes restantes pour un même instant, ou une date ou un nombre que Python ne sait pas représenter, rendent la réponse fautive. Une réponse mensuelle fautive devient une erreur du rapport, avec son adresse : le passage continue avec la réponse gardée de ce mois si elle se lit, sinon sans ce mois. La réponse reste dans le brut, et le passage suivant la redemande. Une réponse de périodes fautive arrête le passage, puisque sans elle aucun mois ne peut choisir son jeu.
- **Nettoyé** : `clean/eco2mix/measures.parquet`, reconstruit en entier à chaque passage, mois par mois : chaque mois vient de la dernière réponse du jeu choisi pour lui, sauf si la dernière réponse gardée de l'autre jeu couvre plus de quarts d'heure ; à couverture égale, la version la plus avancée l'emporte, et un avertissement signale ce repli. Les réponses plus anciennes restent dans le brut, sans être relues. Une ligne par zone, mesure et quart d'heure :
  - `start` : le début du quart d'heure, en UTC ;
  - `area` : `FR` ou `ARA` ;
  - `measure` : `consumption_mw`, `co2_g_per_kwh` et `rte_forecast_mw` pour la France, `solar_mw` et `solar_load_factor_pct` pour la région ;
  - `value`, `step_minutes` (30 ou 15, le pas publié), `version` (`real_time`, `consolidated` ou `definitive`) et `received_at`.

  Une table longue plutôt qu'une colonne par mesure : chaque mesure a son pas et sa version, et une mesure de plus ne change pas le schéma. Une valeur à la demi-heure vaut pour ses deux quarts d'heure : la demi-heure qui commence à son instant. C'est confirmé pour la région ; pour le national, les indices du contexte disent le contraire, et le consolidé de juillet à septembre 2026, comparé au temps réel gardé dans le brut, tranchera fin octobre 2026. Une valeur nulle reste absente, et le fichier n'est remplacé que si ses lignes sont valides, d'un seul coup (`write_atomically`, ADR 022).
- **Contrôles**, pour chaque zone et chaque mesure, du 1er juillet 2023 à la veille, une fois passées les 3 premières heures du jour, le temps que le temps réel arrive :
  - ligne invalide : un quart d'heure en double, un instant hors de la grille, une valeur hors de ses bornes, ou une mesure datée après le passage (après la fin du lendemain pour la prévision). Les bornes : de 10 000 à 150 000 MW pour la consommation et la prévision, de 0 à 500 g/kWh pour l'intensité, de 0 à 20 000 MW pour le solaire, de 0 à 150 % pour le taux de charge. Le nettoyé garde alors sa version précédente ;
  - erreur : des quarts d'heure manquants, ou un problème signalé par `verify()` sur la couche brute. Seuls les instants qu'ODRÉ donne sont comptés : 96 par jour, 92 au passage à l'heure d'été, et 96 aussi au retour à l'heure d'hiver, puisqu'ODRÉ ne donne jamais le premier passage de 02:00. Ce trou connu n'est pas comblé : ces quarts d'heure n'ont pas de ligne dans le nettoyé ;
  - avertissement : une dernière valeur temps réel de plus de 3 heures (la consommation pour la France, le solaire pour la région) ; après 14 h, une prévision du lendemain incomplète ; un premier passage de 02:00 qui apparaît, signe qu'ODRÉ a changé.

  Le jour même n'est pas contrôlé. Une ligne invalide ou une erreur donne le code de sortie 1.

## Conséquences

- Le premier passage fait 82 requêtes, en deux minutes environ ; les suivants, 4 pour les périodes des versions, puis le mois en cours et, jusqu'au 14, le précédent, pour chaque zone, et le mois suivant le dernier jour du mois : 6 à 10 requêtes. Le 27 septembre 2026, le brut d'éCO2mix pesait 11 Mo, et le nettoyé 568 700 valeurs en 2,6 Mo.
- Chaque passage lit une fois chaque mois gardé, pour savoir s'il est acquis, et en reconstruit le nettoyé sans le relire : 15 s pour un passage sans réponse nouvelle, le 27 septembre 2026, contre 25 s dans la première version. Une lecture en colonnes deviendra nécessaire avec Enedis, près de dix fois plus volumineux.
- Entre la sortie d'un mois de la fenêtre du temps réel et la publication de son consolidé, ce mois ne change plus chez nous.
- Un fichier brut abîmé que plus aucune requête ne redemande, comme la réponse temps réel d'un mois devenu consolidé, ne bloque pas le nettoyé, mais `verify()` le signale à chaque passage, qui sort en erreur. Seule une restauration depuis une sauvegarde le répare : la procédure viendra avec les sauvegardes des données, en 2.7.
- La simulation devra traiter les 4 quarts d'heure qu'ODRÉ ne donne pas au retour à l'heure d'hiver, par une règle écrite, et non les combler en silence.
- À la publication d'un trimestre consolidé, ses trois mois sont redemandés une fois ; à celle d'une année définitive, ses douze mois.
- Le taux de charge peut dépasser 100 % si la puissance installée connue de RTE est en retard sur le parc : il est accepté jusqu'à 150 %.
- Une révision d'un mois consolidé qui ne change pas sa version passe inaperçue, sauf avec `--full`.
- Le code commun aux sources sort de `smard.py` pour servir aux deux : le rapport des contrôles, l'écriture d'un fichier nettoyé, les jours de Paris, la récupération d'une réponse dans le brut et le contrôle de sa forme.
