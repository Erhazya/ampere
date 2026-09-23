# ADR 008 : le pilotage minimise la facture ; le CO₂ est mesuré

- **Statut** : acceptée
- **Date** : 23 septembre 2026

## Contexte

Le brief vise à la fois une facture et des émissions de CO₂ plus basses. Mais l'intensité CO₂ du lendemain n'est pas publiée à l'avance : pour optimiser le CO₂, il faudrait d'abord la prévoir.

## Options envisagées

### 1. Coût seul, CO₂ mesuré après coup

- **Avantages** : simple et honnête. Savoir si économiser fait aussi baisser le CO₂ est déjà un résultat.
- **Inconvénients** : le climat n'est pas visé directement avant la version complète.

### 2. Coût + prix du carbone

On ajoute à la facture un prix par tonne de CO₂, réglable par un curseur.

- **Avantages** : le tableau de bord peut montrer une courbe de compromis entre coût et CO₂.
- **Inconvénients** : il faut une prévision de l'intensité CO₂ dès la v1.

### 3. CO₂ seul

- **Avantages** : message climat fort.
- **Inconvénients** : la facture peut augmenter, ce qu'un ménage ne veut pas ; il faut aussi une prévision du CO₂.

## Décision

Option 1. Le CO₂ est calculé après coup, avec l'intensité réelle publiée par RTE. L'énergie solaire revendue n'est pas comptée comme des émissions évitées.

## Conséquences

- Pas de prévision du CO₂ en v1.
- Le README dira si l'optimisation du coût réduit aussi les émissions, même si la réponse est non.
- La version complète pourra ajouter un prix du carbone et une prévision de l'intensité CO₂. Cette prévision servira aussi l'API bonus « quand consommer pour émettre le moins de CO₂ ? ».
