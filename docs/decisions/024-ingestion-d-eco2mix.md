# ADR 024 : ingestion d'éCO2mix, de RTE

- **Statut** : acceptée ; applique l'ADR 022 et la section 6.2 de la conception
- **Date** : 27 septembre 2026

## Contexte

RTE publie éCO2mix sous Licence Ouverte 2.0, par la plateforme ODRÉ (Open Data Réseaux Énergies) : une API sans clé, avec un quota de 50 000 appels par mois. Ampère y prend l'intensité CO₂, la consommation et la prévision J-1 de RTE pour la France, et le solaire d'Auvergne-Rhône-Alpes, pour valider plus tard son calcul de production. Observé le 27 septembre 2026 :

- **quatre jeux** : `eco2mix-national-tr` et `eco2mix-regional-tr` en temps réel, sur les 90 derniers jours, soit du 1er juillet 2026 à aujourd'hui (et demain pour la prévision nationale) ; `eco2mix-national-cons-def` et `eco2mix-regional-cons-def`, avec des données définitives de 2012 à fin 2024 et consolidées de janvier 2025 à fin juin 2026. Le champ `nature` donne la version de chaque ligne ;
- **des versions par paquets** : le jeu consolidé-définitif a été modifié pour la dernière fois le 30 juillet 2026, jusqu'au 30 juin, et le temps réel commence là où il s'arrête ;
- **le national** : une ligne par quart d'heure. En consolidé et en définitif, la consommation et l'intensité CO₂ ne sont remplies qu'à l'heure pile et à la demi-heure, alors que la prévision J-1 l'est à chaque quart d'heure. En temps réel, tout est au quart d'heure, avec environ une heure de retard, et les lignes de demain ne portent que la prévision J-1 ;
- **la région** (code 84) : une ligne par demi-heure en consolidé et en définitif, par quart d'heure en temps réel, avec la production solaire (`solaire`, en MW) et son taux de charge (`tch_solaire`, en %) ;
- **le temps** : `date_heure` est en UTC ; `date` et `heure` sont à l'heure de Paris ;
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

## Décision

Option 1 dans les quatre cas.

- **Requêtes** :
  - à chaque passage, une requête par jeu consolidé-définitif, national puis régional, demande à ODRÉ la période de chaque version (`nature`, avec son premier et son dernier instant). Ces réponses vont dans le brut comme les autres ;
  - puis chaque mois de Paris depuis le 1er juillet 2023, exporté avec tous ses champs (filtré sur la région 84 pour le régional) : depuis le jeu consolidé-définitif s'il couvre le mois entier, sinon depuis le temps réel ;
  - un mois est redemandé à chaque passage jusqu'à 14 jours après sa fin, comme une semaine de SMARD (ADR 023). Au-delà, il ne l'est que si ODRÉ en offre une version plus avancée que la réponse gardée (temps réel, puis consolidée, puis définitive), ou si cette réponse ne se lit plus ou n'a pas toutes ses valeurs ;
  - `ampere ingest eco2mix --full` redemande tous les mois ;
  - chaque réponse passe par `get()` (ADR 022) : du JSON, de 20 Mo au plus. Une pause de 0,5 s sépare deux requêtes. Dans le brut, la source est `eco2mix`, et les jeux `coverage`, `national-tr`, `national-cons-def`, `regional-tr` et `regional-cons-def`.
- **Schéma** : une réponse est une liste d'objets. Chaque ligne a un `date_heure` en UTC, une `nature` connue et des mesures numériques ou nulles ; une ligne régionale a le code 84. Sinon, le passage s'arrête avec l'adresse fautive, la réponse reste dans le brut, et le passage suivant la redemande.
- **Nettoyé** : `clean/eco2mix/measures.parquet`, reconstruit en entier à chaque passage depuis la dernière réponse de chaque requête, avec une ligne par zone, mesure et quart d'heure :
  - `start` : le début du quart d'heure, en UTC ;
  - `area` : `FR` ou `ARA` ;
  - `measure` : `consumption_mw`, `co2_g_per_kwh` et `rte_forecast_mw` pour la France, `solar_mw` et `solar_load_factor_pct` pour la région ;
  - `value`, `step_minutes` (30 ou 15, le pas publié), `version` (`real_time`, `consolidated` ou `definitive`) et `received_at`.

  Une table longue plutôt qu'une colonne par mesure : chaque mesure a son pas et sa version, et une mesure de plus ne change pas le schéma. Une valeur à la demi-heure vaut pour ses deux quarts d'heure. On suppose qu'elle porte sur la demi-heure qui commence à son instant : le consolidé de juillet à septembre 2026, comparé au temps réel gardé dans le brut, dira si c'est juste. Pour chaque quart d'heure, la version la plus avancée l'emporte, puis la réponse reçue en dernier. Une valeur nulle reste absente, et le fichier n'est remplacé que si ses lignes sont valides, d'un seul coup (`write_atomically`, ADR 022).
- **Contrôles**, pour chaque zone et chaque mesure, du 1er juillet 2023 à la veille :
  - ligne invalide : un quart d'heure en double, un instant hors de la grille, une valeur hors de ses bornes, ou une mesure datée après le passage (après la fin du lendemain pour la prévision). Les bornes : de 10 000 à 150 000 MW pour la consommation et la prévision, de 0 à 500 g/kWh pour l'intensité, de 0 à 20 000 MW pour le solaire, de 0 à 150 % pour le taux de charge. Le nettoyé garde alors sa version précédente ;
  - erreur : des quarts d'heure manquants (en comptant 92 et 100 aux changements d'heure), ou un problème signalé par `verify()` sur la couche brute ;
  - avertissement : une dernière consommation en temps réel vieille de plus de 3 heures.

  Le jour même n'est pas contrôlé, puisque le temps réel a environ une heure de retard. Une ligne invalide ou une erreur donne le code de sortie 1.

## Conséquences

- Le premier passage fait environ 80 requêtes ; les suivants, 2 pour les périodes des versions, puis le mois en cours et, jusqu'au 14, le précédent, pour chaque zone.
- À la publication d'un trimestre consolidé, ses trois mois sont redemandés une fois ; à celle d'une année définitive, ses douze mois.
- Le taux de charge peut dépasser 100 % si la puissance installée connue de RTE est en retard sur le parc : il est accepté jusqu'à 150 %.
- Une révision d'un mois consolidé qui ne change pas sa version passe inaperçue, sauf avec `--full`.
- Le code commun aux sources (le rapport des contrôles, l'écriture d'un fichier nettoyé, les jours de Paris) sort de `smard.py` pour servir aux deux.
