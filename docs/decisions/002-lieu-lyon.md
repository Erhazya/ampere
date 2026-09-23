# ADR 002 : un lotissement de 200 maisons près de Lyon

- **Statut** : acceptée
- **Date** : 23 septembre 2026

## Contexte

Le lieu fixe la météo, l'ensoleillement et la région des données d'Enedis et d'éCO2mix. La forme du quartier doit aussi donner un sens au scénario « et si 30 % des toits avaient des panneaux ? ».

## Options envisagées

### 1. Lyon

- **Avantages** : hivers froids et étés ensoleillés, donc des saisons contrastées, utiles plus tard pour les pompes à chaleur. L'ensoleillement moyen donne des résultats représentatifs de la France.
- **Inconvénients** : chiffres solaires moins spectaculaires que dans le Sud.

### 2. Le Sud (Toulouse, Marseille)

- **Avantages** : beaucoup de soleil, donc des chiffres solaires flatteurs.
- **Inconvénients** : hivers doux, donc moins de chauffage ; résultats moins généralisables.

### 3. Le Nord ou l'Île-de-France

- **Avantages** : cas exigeant pour le solaire, représentatif d'une grande partie de la population.
- **Inconvénients** : production solaire faible, donc peu de matière pour le pilotage des batteries.

## Décision

Lyon, avec comme point de référence 45,76° N et 4,84° E, dans la région Auvergne-Rhône-Alpes (code 84). Le quartier est un lotissement de 200 maisons individuelles : chacune a son toit, son compteur et sa facture.

## Conséquences

- Les données régionales sont celles d'Auvergne-Rhône-Alpes, pour Enedis et pour éCO2mix. Les vacances scolaires sont celles de la zone A.
- Limite : les courbes Enedis mélangent maisons et appartements (voir l'ADR 001).
