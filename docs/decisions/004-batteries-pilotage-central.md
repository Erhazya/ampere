# ADR 004 : batteries individuelles, pilotage central

- **Statut** : acceptée
- **Date** : 23 septembre 2026

## Contexte

Le quartier compte des batteries. Il faut décider à qui elles appartiennent, qui les pilote et ce qu'on optimise, tout en gardant une v1 livrable.

## Options envisagées

### 1. Batteries individuelles, pilotage central

Chaque maison équipée a sa batterie et sa facture ; un agrégateur les pilote toutes.

- **Avantages** : réaliste, puisque c'est le métier des centrales électriques virtuelles ; vraiment multi-agents ; progression naturelle vers la coordination.
- **Inconvénients** : la dimension collective n'arrive vraiment qu'après la v1.

### 2. Une batterie de quartier

Une grande batterie partagée, dans le cadre de l'autoconsommation collective.

- **Avantages** : un seul problème d'optimisation, très lisible.
- **Inconvénients** : peu multi-agents ; il faut répartir la facture entre les foyers.

### 3. Batteries individuelles coordonnées dès la v1

Une limite de puissance commune au transformateur oblige à une seule grande optimisation.

- **Avantages** : démonstration forte dès la v1. Sans coordination, toutes les batteries chargeraient au même moment, quand le prix est bas, et créeraient un nouveau pic.
- **Inconvénients** : problème bien plus gros, qui risque de retarder la v1.

## Décision

Option 1. En v1, chaque maison est optimisée séparément, et la puissance totale du quartier au transformateur est mesurée.

## Conséquences

- 200 petits problèmes indépendants, rapides à résoudre et faciles à paralléliser.
- Une facture par maison. Le pic au transformateur devient un indicateur, qui rendra l'effet rebond visible.
- La version complète ajoutera une limite commune au transformateur, qui obligera à coordonner les batteries.
