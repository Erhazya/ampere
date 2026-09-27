# ADR 027 : ingestion des calendriers

- **Statut** : acceptée ; applique l'ADR 022, avec une table par jour au lieu du pas de 15 min
- **Date** : 27 septembre 2026

## Contexte

La prévision du lendemain, émise la veille à 11 h, utilise le calendrier : jours fériés et vacances scolaires (conception, section 7.2). Lyon est en zone A. Observé le 27 septembre 2026 :

- **les jours fériés** : l'API d'Etalab (`https://calendrier.api.gouv.fr/jours-feries/metropole.json`) donne en un objet JSON de 8 Ko les jours fériés de métropole, de 2006 à 2031, chacun avec son nom : 11 par an, 10 quand deux tombent le même jour, comme l'Ascension et le 1er mai en 2008. Le fichier est régénéré chaque jour, avec les mêmes octets ;
- **les vacances scolaires** : le jeu « Le calendrier scolaire » du ministère de l'Éducation nationale, sur data.education.gouv.fr, a 2 587 lignes, une par période, académie et population : le nom de la période, la population, le début et la fin en UTC, l'académie, la zone et l'année scolaire. Son export Parquet pèse 18 Ko, avec des dates typées, et deux requêtes donnent les mêmes octets. Dernière modification : le 18 septembre 2026 ;
- **la zone A** : huit académies, dont Lyon, avec les mêmes 75 périodes, de 2017-2018 à 2027-2028 ;
- **le sens des dates** : le début d'une période est le minuit de Paris de son premier jour sans cours, souvent un samedi, et sa fin le minuit du jour de la reprise. Une période d'un seul jour a un début égal à sa fin : le pont de l'Ascension 2027 est le seul vendredi 7 mai. L'été a deux lignes, car les élèves reprennent un jour après les enseignants. La dernière année n'a pas encore de fin d'été : une ligne « Début des Vacances d'Été » ne donne que son premier jour.

## Options envisagées

### Périmètre

1. **Les jours fériés et les vacances des élèves de la zone A**
   - **Avantages** : ce dont la prévision du quartier a besoin.
   - **Inconvénients** : un modèle national demanderait aussi les zones B et C.
2. **Les trois zones** : la consommation de la France varie avec les vacances décalées des zones, mais aucun modèle ne s'en sert encore.
3. **Les jours fériés seulement** : les vacances attendraient l'étape 5, qui en aura besoin.

### Format du calendrier scolaire

1. **L'export Parquet complet**
   - **Avantages** : une requête, des dates typées, et le fichier national gardé tel quel dans le brut.
   - **Inconvénients** : 2 500 lignes pour en garder 75.
2. **Les lignes JSON de Lyon** : 75 lignes, mais des pages de 100 lignes au plus, et un filtre fait par le serveur.
3. **L'export CSV complet** : des dates en texte à interpréter.

### Rythme

1. **À chaque passage**
   - **Avantages** : une nouvelle année scolaire arrive le lendemain de sa publication ; le brut ne garde une réponse que si elle change.
   - **Inconvénients** : deux requêtes par jour pour des fichiers qui changent quelques fois par an.
2. **Une fois par semaine** : une règle de plus à tester, et jusqu'à 7 jours de retard.
3. **Seulement avec `--full`** : une mise à jour oubliée ne se verrait pas.

### Forme du nettoyé

1. **Une table par jour**
   - **Avantages** : la prévision la joint par la date de Paris de chaque pas.
   - **Inconvénients** : une exception de plus au pas de 15 min de l'ADR 022.
2. **Deux tables de périodes** : les données telles que publiées, mais la prévision devrait les transformer en jours.
3. **Au quart d'heure** : 96 lignes identiques par jour.

## Décision

Option 1 dans les quatre cas.

- **Requêtes** : à chaque passage, les jours fériés de métropole en JSON, puis l'export Parquet du calendrier scolaire (`/api/explore/v2.1/catalog/datasets/fr-en-calendrier-scolaire/exports/parquet`), de 1 Mo au plus chacun, séparés d'une pause de 0,5 s. Dans le brut, la source est `calendars`, les jeux `public_holidays` et `school_holidays`. `--full` n'y change rien.
- **Schéma** :
  - les jours fériés sont un objet dont chaque clé est une date et chaque valeur un nom non vide ;
  - le calendrier scolaire est un Parquet lisible, borné par son pied à 10 000 lignes et 20 colonnes, avec les colonnes attendues et leurs types. Ampère garde les lignes de l'académie de Lyon pour les élèves (population « Élèves » ou « - ») : si les zones changent un jour, le calendrier suit Lyon. Chaque période commence et finit à un minuit de Paris, ne finit pas avant de commencer, et n'en chevauche aucune autre.
  - Une réponse fautive est une erreur du rapport ; le nettoyé garde alors sa version précédente.
- **Nettoyé** : `clean/calendars/days.parquet`, une ligne par jour de Paris, du 1er juillet 2023 au dernier jour que les deux calendriers connaissent : le 31 décembre de la dernière année des jours fériés, ou le dernier jour de la dernière période scolaire s'il vient avant. C'est aujourd'hui le 4 juillet 2028, premier jour de l'été 2028 :
  - `day` : la date de Paris ;
  - `public_holiday` : le nom du jour férié, vide sinon ;
  - `school_holidays` : le nom des vacances, vide sinon. Une période couvre les jours de son début à la veille de sa fin, ou son seul premier jour quand début et fin sont égaux ;
  - `public_holiday_received_at` et `school_holidays_received_at` : l'heure de réception de chaque réponse utilisée.
- **Contrôles** :
  - erreur : une réponse fautive ; un nettoyé qui s'arrête avant le lendemain du passage, le jour que la prévision doit couvrir ; un problème signalé par `verify()` ;
  - avertissement : un calendrier scolaire qui s'arrête dans moins d'un an, quand une année de plus devrait être publiée ; une année scolaire, depuis 2023-2024, sans ses vacances de la Toussaint, de Noël, d'hiver, de printemps ou d'été.
- **Commande** : `ampere ingest calendars`.

## Conséquences

- Deux requêtes par passage, 26 Ko ; le brut ne grossit qu'aux mises à jour des calendriers.
- L'historique de 2023 à 2026 utilise des calendriers reçus en 2026. Ils sont fixés plusieurs années à l'avance, par arrêté pour les vacances : la prévision n'y apprend rien qu'elle n'aurait pu savoir la veille.
- Le lundi de Pentecôte est un jour férié pour l'API, mais beaucoup y travaillent : la prévision en jugera l'effet sur les données.
- Les autres zones restent dans le brut : un modèle national pourra les lire sans nouvelle requête.
