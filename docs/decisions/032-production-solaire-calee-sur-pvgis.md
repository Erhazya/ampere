# ADR 032 : la production solaire, calculée avec pvlib et calée sur PVGIS

- **Statut** : acceptée ; précise l'ADR 006 et applique l'ADR 031
- **Date** : 28 septembre 2026

## Contexte

L'ADR 006 a choisi de calculer la production de chaque toit avec pvlib, à partir de la météo d'Open-Meteo, de la caler sur PVGIS, puis de la comparer au solaire de la région publié par éCO2mix. L'ADR 031 équipe 60 toits, de 3 ou de 6 kWc. Au 28 septembre 2026 :

- l'API de PVGIS 5.3 donne, heure par heure, la production d'un toit de 1 kWc à Lyon, avec les rayonnements de la base satellitaire SARAH3 et la météo d'ERA5, de 2005 à 2023. Une année pèse environ 1 Mo en JSON ;
- la météo observée d'Open-Meteo commence le 30 juin 2023 : les deux se recouvrent de juillet à décembre 2023. Cette période précède le 15 mars 2024 ; l'ADR 007 la réserve justement au calage du quartier ;
- Open-Meteo date chaque rayonnement de la fin de l'heure dont il est la moyenne (ADR 025) ;
- pour ses toits fixes, PVGIS calcule les pertes par réflexion avec le modèle de Martin et Ruiz, la température des cellules avec le modèle de Faiman, et la puissance des modules au silicium cristallin avec le modèle de Huld. pvlib contient ces trois modèles, mais pas la transposition de Muneer, que PVGIS utilise pour le rayonnement diffus sur un plan incliné ;
- pvlib dépend de pandas, de SciPy et de h5py.

## Options envisagées

### L'orientation des toits équipés

Tous sont inclinés à 30°, la pente courante d'un toit de tuiles.

1. **Sud-est, sud ou sud-ouest, à parts égales**
   - **Avantages** : les pans que les installateurs choisissent surtout ; un peu de diversité décale les pointes de production.
   - **Inconvénients** : une hypothèse, sans statistique publique qui la chiffre.
2. **De l'est à l'ouest, à parts égales**
   - **Avantages** : une production plus étalée dans la journée.
   - **Inconvénients** : une production moyenne plus basse, car un toit à l'est ou à l'ouest produit environ 15 à 20 % de moins qu'au sud.
3. **Tout au sud**
   - **Avantages** : le plus simple.
   - **Inconvénients** : tous les toits produisent au même moment, et la batterie paraîtrait un peu plus utile qu'en réalité.

## Décision

L'auteur a choisi l'option 1 le 28 septembre 2026. Le reste suit l'ADR 006, au plus près de PVGIS.

- **Toits** : inclinés à 30°, orientés au sud-est, au sud ou au sud-ouest, tirés à parts égales avec la graine du quartier.
- **PVGIS** : `ampere ingest pvgis` demande, pour chacune des trois orientations, la production horaire de 2023 d'un toit de 1 kWc à Lyon, avec 14 % de pertes, la valeur par défaut de PVGIS. Les réponses vont dans le brut ; `clean/pvgis/production.parquet` en garde, heure par heure et pour chaque orientation, la puissance par kWc. C'est une archive qui ne change plus : le traitement quotidien ne la demande pas.
- **Calcul** : `ampere.solar` calcule la puissance d'un toit de 1 kWc à partir de la météo observée ou prévue :
  - la position du soleil au milieu de chaque heure, puisque le rayonnement d'Open-Meteo est la moyenne de l'heure qui finit à son instant ;
  - le rayonnement sur le toit par la transposition de Perez, la plus citée, faute de celle de Muneer ;
  - puis les modèles de PVGIS : les pertes par réflexion de Martin et Ruiz, la température des cellules de Faiman, la puissance de Huld, et 14 % de pertes.
  
  Chaque heure donne quatre quarts d'heure de même puissance, ce qui garde l'énergie, comme l'ADR 005 le fait pour Enedis.
- **Calage** : sur juillet à décembre 2023, la production calculée de chaque orientation est comparée à celle de PVGIS, mois par mois. Un seul facteur, le rapport des énergies des six mois, corrige l'écart des rayonnements des deux sources. Il est écrit dans le code, avec la commande qui le recalcule et le détail des mois dans `docs/mesures.md`.
- **Validation** : sur juillet 2023 à juin 2025, par le garde-fou de l'ADR 007, le taux de charge des trois orientations est comparé à celui du solaire de la région, publié par éCO2mix, mois par mois et demi-heure par demi-heure. Les deux ne doivent pas être égaux : la région compte aussi des centrales au sol, d'autres pentes et d'autres ciels.
- **Dépendance** : pvlib, sous licence BSD, entre dans les dépendances de l'image. Seul le traitement quotidien l'importera.

## Conséquences

- PVGIS devient la sixième source du projet, à citer : « PVGIS, Commission européenne (JRC) ».
- L'image grossit de pvlib, pandas, SciPy et h5py. Sa taille est mesurée et reportée dans `docs/mesures.md`.
- Le calage repose sur six mois seulement. Un facteur qui s'écarterait de plus de 10 % de 1 signalerait un défaut du calcul plutôt qu'un écart de rayonnement.
- Le même calcul servira aux prévisions de production du lendemain, avec la météo prévue (étape 5).
