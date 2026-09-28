# ADR 030 : le notebook d'exploration

- **Statut** : acceptée ; applique l'ADR 007, dont il écrit le garde-fou
- **Date** : 28 septembre 2026

## Contexte

Le plan du projet termine l'étape 2 par un premier notebook d'exploration : un document qui mêle du code, ses résultats (tableaux, graphiques) et le texte qui les lit. Il raconte ce que disent les données avant la simulation. Au 28 septembre 2026 :

- les cinq sources sont ingérées depuis juillet 2023 et mises à jour chaque jour en ligne (ADR 023 à 028). Le nettoyé du développement compte 113 856 prix, 569 080 valeurs d'éCO2mix, 432 779 valeurs météo, 9,8 millions de valeurs de consommation et 524 324 de production solaire d'Enedis, et 1 831 jours de calendrier ;
- la conception laisse deux questions ouvertes pour l'étape 2 : le sens exact des profils résidentiels d'Enedis, et le modèle qui estimera les jours qu'Enedis n'a pas encore publiés (sections 5.2 et 11) ;
- l'ADR 007 réserve la période du 1er juillet 2025 au 30 juin 2026 à l'évaluation finale, et prévoit un garde-fou dans le code. Il n'existe pas encore : l'ingestion et l'export de l'écran « Data » lisent toutes les périodes, mais n'analysent rien ;
- le dossier `notebooks/` est prévu dans l'organisation du dépôt (conception, section 8.6) ;
- la CI n'a pas les données : `data/` reste hors du dépôt.

## Options envisagées

### Outil

1. **Jupyter, avec ses sorties dans le dépôt**
   - **Avantages** : le format standard de la data (`.ipynb`) ; GitHub l'affiche avec ses graphiques, sans rien lancer.
   - **Inconvénients** : un fichier JSON aux diffs bruyants ; un état caché si l'on exécute les cellules dans le désordre.
2. **marimo** : un notebook réactif, enregistré en `.py`.
   - **Avantages** : pas d'état caché, des diffs lisibles, ruff et mypy s'y appliquent.
   - **Inconvénients** : GitHub n'en montre que le code ; il faut exporter une page HTML pour voir les graphiques ; l'outil est moins connu.
3. **Jupyter et jupytext** : un `.ipynb` doublé d'un `.py` synchronisé.
   - **Avantages** : des diffs lisibles sur le `.py`, les graphiques sur le `.ipynb`.
   - **Inconvénients** : deux fichiers par notebook, et un outil de plus à expliquer.

### Période lue

1. **Juillet 2023 à juin 2025, derrière un garde-fou**
   - **Avantages** : aucune donnée de la période de test ; une seule frontière à expliquer.
   - **Inconvénients** : les mois qui suivent la période de test restent de côté.
2. **La même, plus juillet à septembre 2026**
   - **Avantages** : trois mois de plus.
   - **Inconvénients** : Enedis ne les a pas encore publiés ; une frontière de plus.

### Graphiques

1. **matplotlib** : des images fixes, visibles sur GitHub et dans tout export.
2. **Altair ou Plotly** : des graphiques interactifs, mais invisibles sur GitHub sans exécuter le notebook, et une dépendance plus lourde.

## Décision

L'auteur a choisi le 28 septembre 2026 l'option 1 de chaque choix, et les quatre thèmes proposés.

- **Outil** : `notebooks/exploration.ipynb`, avec ses sorties dans le dépôt. Une commande le réexécute de haut en bas avant chaque commit, ce qui écarte l'état caché. Jupyter et matplotlib sont dans un groupe de dépendances à part, `notebooks`, que ni la CI ni l'image de la démo n'installent.
- **Garde-fou** : `ampere.data.periods` définit la période de test en heure de Paris, et `scan()` lit une table du nettoyé sur une plage donnée, après avoir refusé toute plage qui touche la période de test, puis une colonne qui ne tient pas ce que sont les bornes : des jours de Paris sur une colonne d'instants UTC seraient lus à minuit UTC, deux heures après le début du jour. Tout code d'analyse lit le nettoyé par cette fonction ; seule l'évaluation finale, à l'étape 5, aura un accès à part.
- **Période** : du 1er juillet 2023 au 30 juin 2025, heure de Paris.
- **Thèmes** :
  1. la couverture et la qualité de chaque source : dates, pas de temps, versions, trous, changements d'heure ;
  2. les profils d'Enedis : le sens des 39 segments résidentiels, leurs courbes, le secret statistique ;
  3. la consommation et la météo : la consommation des foyers face à la température, au calendrier et à la consommation nationale, pour préparer l'estimation des jours récents ;
  4. les prix et le CO₂ : la forme d'une journée de prix, l'écart entre heures chères et heures creuses, les prix négatifs, l'intensité CO₂ selon l'heure et la saison.
- **Graphiques** : matplotlib, avec une palette validée pour un fond clair.
- **Langue** : le texte en français, comme la documentation ; le code en anglais.

## Conséquences

- La CI ne peut pas exécuter le notebook. Un test vérifie ce qu'il contient : des cellules exécutées dans l'ordre, sans erreur, et aucune lecture du nettoyé hors de `scan()`.
- Les graphiques sont des images dans le fichier : il pèse de l'ordre du mégaoctet, et chaque réexécution change ses sorties.
- Le notebook agrège avec Polars en mode paresseux, pour ne jamais charger les 9,8 millions de valeurs d'Enedis en mémoire.
- Ses réponses aux deux questions ouvertes passent dans la conception.
- Avec lui, l'étape 2 est terminée.
