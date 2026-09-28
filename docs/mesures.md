# Mesures

Chaque chiffre publié (README, vitrine, vidéo) figure ici avec le moyen de le reproduire. Un chiffre absent de ce document ne doit pas être publié.

## Modèle d'entrée

```markdown
### Nom du chiffre

- **Valeur** :
- **Mesuré le** :
- **Période** : par exemple la période de test, du 1er juillet 2025 au 30 juin 2026
- **Commande** : la commande exacte qui régénère la valeur
- **Version du code** : le commit
- **Données** : les sources et leurs dates de réception
- **Protocole** : comment la valeur est calculée
- **Limites** : ce que la valeur ne dit pas
```

## Chiffres prévus

| Chiffre | Voir la conception | Étape |
|---|---|---|
| Gains en euros et en CO₂ de chaque stratégie (règle simple, MILP) | Section 7.4 | 4 |
| Taux d'autoconsommation et d'autosuffisance | Section 5.9 | 4 |
| Pic au transformateur, dont l'effet rebond | Section 5.7 | 4 |
| Erreur de prévision (MAE) et score de compétence face aux références naïves | Section 7.3 | 5 |
| Mémoire réelle de chaque conteneur sur le VPS | Section 8.5 | 6 |
| Durée du traitement quotidien | Section 8.3 | 6 |
| Décalage entre les demi-heures nationales d'éCO2mix, consolidées, et le temps réel gardé dans le brut, sur juillet à septembre 2026 | ADR 024 | 2, fin octobre 2026 |
| Couverture des intervalles, gains du pilotage sur prévisions et du renforcement, taux de bonnes réponses et temps de réponse de l'assistant | Section 7 | Version complète |

## Mesures

### Calage du modèle solaire sur PVGIS

- **Valeur** : un facteur de 0,954 : sur juillet à décembre 2023, PVGIS donne 4,6 % de moins que notre modèle, pour les trois orientations des toits. Mois par mois, le rapport va de 0,88 (octobre, au sud et au sud-ouest) à 1,00 (juillet, au sud-ouest). Heure par heure, les deux courbes ont une corrélation de 0,94, et s'écartent en moyenne de 72 W par kWc quand PVGIS produit.
- **Mesuré le** : 28 septembre 2026.
- **Période** : de juillet à décembre 2023, en UTC, dans la période que l'ADR 007 réserve au calage du quartier.
- **Commande** : `uv run ampere ingest pvgis`, puis `uv run ampere solar check`, qui échoue quand le facteur écrit dans `ampere.solar` ne correspond plus aux données.
- **Version du code** : l'incrément 3.1.
- **Données** : PVGIS 5.3, rayonnements SARAH3 et météo ERA5, reçu le 28 septembre 2026 ; la météo observée d'Open-Meteo (ADR 025).
- **Protocole** : pour chaque orientation, l'énergie de chaque mois calculée sans calage, face à celle de PVGIS pour le même toit de 1 kWc ; chaque heure compte dans le mois où elle commence. Le facteur est le rapport des énergies des six mois et des trois orientations (ADR 032).
- **Limites** : six mois seulement. PVGIS n'est pas une mesure, mais un calcul fait sur les rayonnements d'un satellite. L'écart d'octobre dit que les rayonnements des deux sources diffèrent aussi selon la saison, ce qu'un seul facteur ne corrige pas.

### Écart entre notre production solaire et le taux de charge régional

- **Valeur** : sur deux ans, le taux de charge moyen de nos trois toits est de 13,7 %, celui du solaire de la région de 14,6 %, soit un rapport de 0,94. Heure par heure, les deux ont une corrélation de 0,93. Mois par mois, le rapport va de 0,76 (août 2023) à 1,30 (décembre 2024) : nos toits produisent de 13 à 24 % de moins de juillet à novembre 2024 et de juillet à août 2023, et jusqu'à 30 % de plus en décembre et en janvier.
- **Mesuré le** : 28 septembre 2026.
- **Période** : de juillet 2023 à juin 2025, lue par le garde-fou de l'ADR 007.
- **Commande** : `uv run ampere solar check`.
- **Version du code** : l'incrément 3.1.
- **Données** : la météo observée d'Open-Meteo à Lyon ; le taux de charge du solaire d'Auvergne-Rhône-Alpes publié par éCO2mix, définitif puis consolidé (ADR 024).
- **Protocole** : la moyenne des trois orientations, calée, donne la puissance d'un toit de 1 kWc, soit un taux de charge ; celui de la région est la moyenne de ses demi-heures dans chaque heure. Les deux sont comparés mois par mois, en heure de Paris, et heure par heure (ADR 032).
- **Limites** : les deux ne doivent pas être égaux. La région compte des centrales au sol, d'autres pentes et d'autres ciels que celui de Lyon ; l'écart de l'été et celui de l'hiver n'ont pas encore d'explication vérifiée.

### Poids de l'image de la démo avec pvlib

- **Valeur** : 931 Mo construite sur le VPS, contre 467 Mo pour l'image que la démo servait le 28 septembre 2026, sans pvlib. pvlib pèse 32 Mo, dont 29 de données ; ses dépendances installent 245 Mo de plus : SciPy 118 Mo, NumPy 61 Mo, pandas 49 Mo, h5py 17 Mo.
- **Mesuré le** : 28 septembre 2026.
- **Commande** : `docker build --file deploy/Dockerfile --tag ampere:pvlib .`, puis `docker image ls` ; les tailles des paquets avec `du -sh` dans `.venv/lib/python3.13/site-packages`.
- **Version du code** : l'incrément 3.1.
- **Protocole** : la taille décompressée que donne Docker, pour une image construite avec le constructeur classique du Docker rootless du développement.
- **Limites** : l'image publiée sur GHCR est construite par BuildKit, et sa taille peut différer un peu. Le poids ne dit rien de la mémoire : seul le traitement quotidien importera pvlib. Si ce poids gêne, les quatre modèles que le projet utilise pourraient être repris de pvlib, sous sa licence BSD, avec NumPy seul (ADR 032).

