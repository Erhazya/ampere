# ADR 007 : un an de test, jamais vu pendant la mise au point

- **Statut** : acceptée
- **Date** : 23 septembre 2026

## Contexte

Pour évaluer honnêtement, il faut deux choses :

- une période de test qui ne sert jamais à régler les modèles ;
- une prévision qui n'utilise que l'information disponible au moment où elle est émise.

Deux contraintes limitent les périodes possibles. La consommation réelle couvre juillet 2023 à juin 2026. Les prévisions météo archivées, sous forme de runs complets d'ECMWF IFS, commencent le 14 mars 2024.

## Options envisagées

### 1. Un an de test

- **Avantages** : toutes les saisons figurent dans le test ; protocole honnête.
- **Inconvénients** : 16 mois seulement pour la mise au point.

### 2. Six mois de test (janvier à juin 2026)

- **Avantages** : plus de données pour la mise au point.
- **Inconvénients** : ni été ni automne dans le test, donc des résultats saisonniers incomplets.

### 3. Une seule validation glissante sur toute la période

- **Avantages** : plus de points d'évaluation.
- **Inconvénients** : on règle le modèle sur les données mêmes qui servent à le juger, donc les scores sont optimistes.

## Décision

- **Test** : du 1er juillet 2025 au 30 juin 2026 (heure de Paris). Il n'est regardé qu'une fois, à la fin.
- **Mise au point** : du 15 mars 2024 au 30 juin 2025, en validation glissante.
- **Avant le 15 mars 2024** : les données servent seulement au calage du quartier et aux variables de retard.
- **Émission de la prévision** : la veille à 11 h. Elle utilise le run ECMWF IFS de 0 h UTC de la veille, et la consommation connue jusqu'à l'avant-veille.
- **Évaluation finale** : mois par mois. Au début de chaque mois du test, le modèle est réentraîné avec toutes les données antérieures.

## Conséquences

- Un garde-fou dans le code interdit de lire la période de test hors de l'évaluation finale.
- Les résultats du test sont publiés tels quels, avec le détail par saison.
- La même période sert à comparer les stratégies de pilotage.
- De juillet à septembre 2025, les prix sont encore horaires (voir l'ADR 005).
