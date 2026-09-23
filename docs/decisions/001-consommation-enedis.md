# ADR 001 : la consommation des maisons vient des courbes réelles d'Enedis

- **Statut** : acceptée
- **Date** : 23 septembre 2026

## Contexte

Le quartier est fictif, mais sa consommation doit être réaliste, maison par maison. Elle sert aussi de « réalité » pour évaluer les prévisions : si cette réalité est inventée, la prévision ne prouve rien. Les données de compteurs individuels sont hors périmètre.

## Options envisagées

### 1. Courbes moyennes réelles d'Enedis, avec une variabilité par foyer

Enedis publie en open data la consommation moyenne d'un foyer par demi-heure, par région, par profil et par tranche de puissance souscrite.

- **Avantages** : forme résidentielle réelle, sensible à la température ; la prévision est notée sur une vraie mesure.
- **Inconvénients** :
  - publication trimestrielle, donc les jours récents doivent être estimés ;
  - courbe moyenne lisse, à laquelle il faut ajouter une variabilité par foyer ;
  - maisons et appartements mélangés ;
  - source absente du brief initial.

### 2. Consommation nationale de RTE, ramenée à 200 foyers

- **Avantages** : donnée réelle, publiée en temps réel. RTE publie aussi sa propre prévision de la veille, ce qui permettrait de se comparer à l'opérateur du réseau.
- **Inconvénients** : la courbe inclut l'industrie et le tertiaire. Sa forme n'est pas résidentielle, ce qui fausserait la valeur des batteries.

### 3. Profils synthétiques générés foyer par foyer

- **Avantages** : 200 foyers tous différents, avec des pics réalistes ; contrôle total.
- **Inconvénients** : prévoir des données que le projet a lui-même générées convainc peu, et un modèle peut simplement réapprendre le générateur.

## Décision

Option 1 : les courbes réelles d'Enedis (jeu de données `conso-inf36-region`, région Auvergne-Rhône-Alpes), avec une variabilité propre à chaque foyer.

## Conséquences

- Enedis rejoint les sources du projet (Licence Ouverte 2.0). Chaque publication est archivée, car Enedis ne garde en ligne qu'une fenêtre glissante.
- Un modèle estime la consommation des jours pas encore publiés. Ces jours sont marqués « estimés » et n'entrent jamais dans une évaluation.
- La méthode de variabilité sera définie à l'étape 3, avec un test qui vérifie que la somme des 200 maisons reste fidèle à la courbe réelle.
- La consommation nationale de RTE reste disponible pour un banc d'essai facultatif de la méthode de prévision.
