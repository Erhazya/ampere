# ADR 006 : production solaire calculée avec pvlib et la météo

- **Statut** : acceptée
- **Date** : 23 septembre 2026

## Contexte

Il faut connaître la production de chaque toit, chaque jour : hier, pour le tableau de bord, et demain, pour la prévision de la version complète. Or PVGIS, prévu par le brief, ne couvre que les années 2005 à 2023.

## Options envisagées

### 1. Modèle physique pvlib, alimenté par Open-Meteo

Le modèle combine l'ensoleillement, observé ou prévu, et la géométrie de chaque toit. Il est calé sur PVGIS et validé sur les données réelles d'éCO2mix et d'Enedis.

- **Avantages** : seule option qui couvre hier et demain ; explicable ; validée sur des données réelles.
- **Inconvénients** : un modèle de plus à comprendre (position du soleil, pertes).

### 2. PVGIS seul

- **Avantages** : très simple, source reconnue.
- **Inconvénients** : ni jours récents ni prévision, donc impossible de montrer hier ou de prévoir demain.

### 3. Taux de charge solaire régional d'éCO2mix × puissance du toit

- **Avantages** : donnée réelle, très simple.
- **Inconvénients** :
  - tous les toits sont identiques, car l'orientation est ignorée ;
  - la moyenne régionale est lissée ;
  - il faudrait quand même un modèle pour prévoir demain.

## Décision

Option 1 : pvlib, alimenté par Open-Meteo.

## Conséquences

- **Dépendance** : pvlib, sous licence BSD.
- **Calage** : nos productions mensuelles et annuelles sont comparées à celles de PVGIS pour les mêmes toits à Lyon.
- **Validation** :
  - d'abord, notre taux de charge est comparé à celui du solaire en Auvergne-Rhône-Alpes, publié par éCO2mix ;
  - ensuite, notre production est comparée aux courbes des petites installations publiées par Enedis. Beaucoup de ces installations ne réinjectent que leur surplus, ce qui déforme leurs courbes.
- **Prévision** : le même modèle servira à prévoir la production du lendemain à partir de la météo prévue.
