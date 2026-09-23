# ADR 005 : pas de temps de 15 min, prévisions notées à 30 min

- **Statut** : acceptée
- **Date** : 23 septembre 2026

## Contexte

Le pas de temps fixe la finesse de la simulation, mais les sources n'ont pas toutes le même pas :

- Enedis : 30 min ;
- prix SMARD : 15 min depuis le 1er octobre 2025, 1 h avant ;
- éCO2mix : 15 min en temps réel, 30 min en données consolidées ;
- Open-Meteo : 1 h.

## Options envisagées

### 1. 30 min

- **Avantages** : c'est le pas des vraies courbes Enedis, donc aucune consommation n'est inventée ; 48 pas par jour.
- **Inconvénients** : depuis octobre 2025, on lisse les écarts de prix entre deux quarts d'heure voisins.

### 2. 15 min

- **Avantages** : c'est le pas du marché depuis octobre 2025, qui suit au plus près les prix actuels.
- **Inconvénients** :
  - la consommation est à découper, puisqu'Enedis est à 30 min ;
  - deux fois plus de calculs ;
  - avant octobre 2025, les prix ne sont qu'horaires.

### 3. 1 heure

- **Avantages** : le plus simple et le plus léger.
- **Inconvénients** : on moyenne les vraies courbes et leurs pics, ce qui rend les batteries moins réalistes.

Une seconde question s'est posée ensuite : comment passer de 30 à 15 min pour la consommation ?

- **Découper, noter à 30 min** : chaque demi-heure devient deux quarts d'heure de même puissance, ce qui conserve exactement l'énergie ; les prévisions sont notées sur des sommes de 30 min.
- **Lisser, noter à 15 min** : une interpolation conserve l'énergie, mais les scores portent alors en partie sur des valeurs interpolées.

## Décision

Pas de temps de 15 min. Chaque demi-heure Enedis devient deux quarts d'heure de même puissance, puis la variabilité par foyer ajoute le détail. Les prévisions de consommation sont notées sur des sommes de 30 min.

## Conséquences

- **Journées** : 96 pas par jour, 92 ou 100 les jours de changement d'heure.
- **Prix** : réels au quart d'heure depuis le 1er octobre 2025. Avant, le prix horaire est répété sur quatre quarts d'heure, y compris de juillet à septembre 2025, qui font partie de la période de test.
- **CO₂** : l'intensité consolidée au pas de 30 min est répétée sur deux quarts d'heure.
- **Météo** : les valeurs horaires sont interpolées ; la méthode sera fixée à l'étape 3.
- **Test** : chaque demi-heure doit garder exactement l'énergie mesurée par Enedis.
- **Optimisation** : 96 pas par jour, ce qui reste un petit problème pour un solveur.
