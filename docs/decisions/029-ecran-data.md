# ADR 029 : l'écran « Data »

- **Statut** : acceptée ; précise l'étape « export pour l'API » de l'ADR 011, et applique les ADR 010 et 018
- **Date** : 28 septembre 2026

## Contexte

Le 27 septembre 2026, l'auteur a choisi de montrer des données réelles avant l'étape 6 : un premier écran « Data », juste après le traitement quotidien en ligne, avec les derniers jours de prix, d'intensité CO₂, de solaire régional et de météo, mis à jour chaque jour. Au 28 septembre :

- le traitement quotidien tourne en ligne chaque jour à 14 h, heure de Paris, et écrit le nettoyé dans un dossier que l'API monte en lecture seule (ADR 028) ;
- l'API occupe 35 Mo, dans un conteneur limité à 256 Mo. La conception y prévoit DuckDB pour les résultats et l'assistant, avec 400 Mo au plus (section 8.5) ;
- le tableau de bord, en React avec ECharts importé module par module (ADR 010), n'a qu'un écran : l'état de l'API ;
- la Licence Ouverte demande de citer la source et la date de sa dernière mise à jour. ODRÉ et data.education.gouv.fr publient cette date dans leur catalogue (`data_processed`), et data.gouv.fr publie celle de chaque ressource des jours fériés d'Etalab. L'ADR 027 a annoncé que l'écran citerait la date publiée, et non l'heure de réception ;
- les noms des jours fériés et des vacances sont du texte de tiers (relecture de sécurité de la PR n° 23).

## Options envisagées

### D'où l'API tient les données

1. **Un export écrit par le traitement quotidien** : après l'ingestion, un fichier JSON avec les séries de l'écran, que l'API sert.
   - **Avantages** : l'API reste légère ; le contenu de l'écran se prépare et se teste en un seul endroit ; c'est l'étape « export pour l'API » de l'ADR 011.
   - **Inconvénients** : la période est fixée au moment de l'export.
2. **Polars dans l'API** : l'API lit le nettoyé à chaque requête. La période peut varier, mais l'API prend plusieurs dizaines de Mo de plus, et lit des tables pendant que le traitement les écrit.
3. **DuckDB dans l'API** : des requêtes SQL sur le nettoyé, comme la conception le prévoit plus tard. Une dépendance et de la mémoire de plus, pour un écran au contenu fixe.

### Période

1. **Les 7 derniers jours, plus le lendemain**
   - **Avantages** : une semaine montre les jours ouvrés et le week-end ; le lendemain montre les prix, connus vers 13 h, et les prévisions.
   - **Inconvénients** : au quart d'heure, environ 900 points par série.
2. **3 jours, plus le lendemain** : plus lisible, mais le week-end n'apparaît qu'une fois sur deux.
3. **30 jours** : la tendance du mois, en moyennes horaires ou journalières, sans le détail du quart d'heure.

### Contenu

Les quatre séries choisies le 27 septembre, et deux ajouts possibles :

- **la consommation de la France et la prévision de la veille par RTE**, déjà dans le nettoyé d'éCO2mix : on voit ce que vaut une prévision professionnelle, avant celles de l'étape 5 ;
- **les jours fériés et les vacances de Lyon**, qui changent la consommation.

### Mise en page

1. **Des graphiques empilés sur un axe du temps commun**, avec un curseur partagé
   - **Avantages** : on lit d'un coup d'œil ce qui se passe à la même heure, par exemple des prix hauts quand le solaire baisse.
   - **Inconvénients** : un écran haut, qu'on fait défiler sur un téléphone.
2. **Une grille de 2 × 2** : plus compacte, mais les heures ne s'alignent plus d'un graphique à l'autre.
3. **Un onglet par série** : la série la plus lisible, mais aucune comparaison.

### Date de dernière mise à jour

1. **La date que publie chaque source**
   - **Avantages** : ce que demande la licence, et ce qu'annonçait l'ADR 027.
   - **Inconvénients** : trois requêtes de plus par passage, et un peu de brut chaque jour.
2. **La date de la dernière valeur**, avec l'heure de réception : aucune requête, mais pour les calendriers, la réception n'est pas une mise à jour.

## Décision

L'auteur a choisi l'option 1 pour la source des données, la période, la mise en page et la date, et les deux ajouts au contenu.

- **Dates publiées** : à chaque passage, une fois ses données écrites, éCO2mix demande au catalogue d'ODRÉ, en une requête, le `data_processed` de ses quatre jeux. Les calendriers demandent celui du calendrier scolaire au catalogue de data.education.gouv.fr, et, à data.gouv.fr, la fiche du jeu « Jours fériés en France », où ils lisent le `last_modified` du fichier de métropole, `jours_feries_metropole.csv`. L'API que lit Ampère sert les mêmes jours, mais data.gouv.fr date sa page à chaque mise en ligne du site de l'API : l'auteur a choisi la date du fichier, qui ne change qu'avec la liste.
  - Ces réponses vont dans le brut. La fiche de data.gouv.fr, dont les compteurs de visites changent à chaque appel, n'y entre de nouveau que si l'adresse ou la date d'une ressource change.
  - `clean/eco2mix/updates.parquet` et `clean/calendars/updates.parquet` gardent, par jeu, la date publiée et l'heure de la première réception qui l'a donnée.
  - Une date doit tomber entre le 1er janvier 2000 et le lendemain du passage. Une requête qui échoue, ou une réponse fautive, est une erreur du rapport : la date gardée avant reste, et les données sont écrites quand même.
- **Export** : après les cinq sources, `ampere daily` écrit `exports/recent.json` dans `AMPERE_DATA`, en une écriture atomique. `ampere export` fait la même chose à la main. La période va de minuit, heure de Paris, sept jours avant le jour de l'export, jusqu'à la fin du lendemain. Le fichier contient :
  - le prix spot de la zone France (SMARD, €/MWh) ;
  - la consommation de la France et la prévision de RTE (éCO2mix, MW) ;
  - l'intensité CO₂ de la France (éCO2mix, gCO₂/kWh) ;
  - la production solaire d'Auvergne-Rhône-Alpes (éCO2mix, MW) ;
  - la température à Lyon (Open-Meteo, °C), observée, puis prévue par le dernier run après la dernière heure observée ;
  - pour chaque jour de Paris de la période, ses bornes, qui placent les minuits de Paris quel que soit le fuseau du navigateur, son jour férié et les vacances des élèves de Lyon ;
  - pour chaque source, son nom et sa licence, écrits dans le code, la date qu'elle publie quand elle est sous Licence Ouverte, et l'heure où Ampère a reçu ses dernières valeurs.

  Chaque point est un instant UTC en millisecondes et une valeur, dans l'unité du nettoyé. Une source absente ou en retard donne une série plus courte ou vide, jamais une erreur de l'export : l'écran montre jusqu'où va chaque série. Un export qui ne peut pas s'écrire fait échouer le traitement.
- **API** : `GET /api/data/recent` n'ouvre qu'un fichier ordinaire du dossier des données, ni lien ni tube. Elle en contrôle la taille, puis la forme en mode strict : les noms, les licences et les unités sont ceux du code, la période dure dix jours au plus, et les points sont triés dans la période. Elle renvoie ensuite ce qu'elle a lu, avec un ETag. Tant qu'aucun export n'existe, elle répond 503 ; un export refusé aussi, et son journal n'en reprend rien.
- **Écran** : un graphique par grandeur, empilés sur le même axe du temps, en heure de Paris, avec un curseur commun ; les prévisions en pointillés ; les jours fériés et les vacances en bandes sur chaque graphique ; sous les graphiques, chaque source avec sa licence et sa date. Une maquette Figma précède le code, et les textes sont d'abord en anglais (ADR 010).

## Conséquences

- L'API ne charge ni Polars ni DuckDB. DuckDB viendra quand elle devra interroger les résultats et les scénarios.
- L'écran change une fois par jour, après le traitement de 14 h. Le prix du lendemain y apparaît donc à 14 h, une heure après sa publication.
- Trois requêtes de plus par passage. La date d'éCO2mix change à chaque mise à jour d'ODRÉ, donc le brut garde environ 400 octets de plus chaque jour ; la fiche de data.gouv.fr, 16 Ko, n'y entre qu'aux changements de ses ressources.
- Les noms des jours fériés et des vacances s'affichent comme du texte. Dans une infobulle d'ECharts, qui interprète le HTML, ils sont d'abord échappés. L'export borne leur longueur et en retire les caractères de contrôle, et il refuse les nombres non finis ; l'API contrôle de nouveau la forme du fichier. L'infobulle doit fonctionner avec la CSP de la démo, sans `'unsafe-inline'`.
- La période de test (ADR 007) n'est pas concernée : l'écran ne montre que les derniers jours.
