# ADR 031 : le quartier simulé

- **Statut** : acceptée ; précise les ADR 001 et 002 et les sections 5.2 et 5.3 de la conception, à partir du notebook d'exploration (ADR 030)
- **Date** : 28 septembre 2026

## Contexte

L'étape 3 construit le quartier : 200 maisons près de Lyon (ADR 002), dont la consommation part des courbes réelles d'Enedis, avec une variabilité propre à chaque foyer (ADR 001). La conception laisse ouvertes, pour cette étape, la méthode de cette variabilité, les valeurs par défaut des équipements et le modèle qui estimera les jours qu'Enedis n'a pas encore publiés (section 11). Le notebook d'exploration a mesuré, sur juillet 2023 à juin 2025 :

- 19 courbes sur 39 sont complètes à la demi-heure : les six totaux de profils, et 13 segments de puissance, de 0 à 18 kVA ;
- les courbes n° 1 et n° 2 d'un segment partagent ses foyers en deux moitiés, selon leur consommation entre 8 h et 20 h les jours ouvrés. Elles sont complètes pour RES11 jusqu'à 12 kVA et pour RES2 jusqu'à 18 kVA, mais le secret statistique les masque les trois quarts du temps pour RES11 de 12 à 15 kVA et pour RES4 ;
- les petits foyers de RES1, au tarif base de 6 kVA ou moins, sont 45 % de la région et consomment moins de 400 W toute l'année ;
- en juin 2025, 173 779 installations solaires de 9 kW au plus injectent sur le réseau de la région, pour 4,29 millions de foyers : 4 %. Elles se partagent presque à égalité entre 3 kW au plus (89 874) et 3 à 9 kW (83 905), et les secondes injectent en moyenne 2,2 fois plus que les premières ;
- une droite brisée de la température, avec un seuil vers 15 °C, explique 93 % des écarts d'un jour à l'autre de la consommation moyenne d'un foyer de la région.

## Options envisagées

### Les segments où l'on tire les foyers

1. **Les segments complets de plus de 6 kVA**, selon leur nombre de foyers, environ 1,5 million.
   - **Avantages** : la puissance souscrite approche la taille du logement (conception, section 5.2) ; c'est l'hypothèse la plus proche d'un lotissement de maisons.
   - **Inconvénients** : une hypothèse, qu'aucune source ne chiffre : des maisons ont 6 kVA ou moins.
2. **Les 13 segments complets**, de 0 à 18 kVA : la région telle qu'elle est, appartements compris ; les petits foyers de RES1 tireraient la consommation vers le bas.
3. **Les six totaux de profils** : toujours complets, mais chaque courbe mélange petits et grands logements.

### La variabilité de chaque maison

1. **Les moitiés réelles, une échelle et un décalage** : chaque maison suit la courbe n° 1 ou n° 2 de son segment, multipliée par un facteur d'échelle et décalée de quelques minutes.
   - **Avantages** : la diversité vient des données.
   - **Inconvénients** : les pointes d'appareils restent lissées.
2. **Une échelle, un décalage et des pointes d'appareils** tirées au hasard, comme la conception le prévoyait.
   - **Avantages** : des pointes réalistes pour une batterie.
   - **Inconvénients** : leurs paramètres sont à inventer et à justifier.
3. **Les deux** : le plus réaliste, et le plus long à écrire, à régler et à tester.

### Le scénario de référence des équipements

1. **30 % des toits équipés, dont la moitié avec une batterie** : assez pour que les batteries comptent ; c'est l'exemple de la conception.
2. **La région aujourd'hui**, 4 % des foyers : 8 maisons sur 200, un effet à peine visible.
3. **Tous les toits équipés** : l'effet le plus fort, loin de la réalité.

### L'estimation des jours récents

1. **La température et le calendrier**, calés sur juillet 2023 à juin 2025.
2. **La même chose, plus la consommation nationale de RTE**, publiée le lendemain : peut-être plus juste, mais une dépendance de plus.

## Décision

L'auteur a choisi le 28 septembre 2026 l'option 1 de chaque choix.

- **Foyers** : chaque maison reçoit un segment, tiré au sort avec une graine fixe, selon le nombre de foyers, parmi les 9 segments complets de plus de 6 kVA : RES11 de 6 à 15 kVA, RES2 de 6 à 18 kVA, et RES4 de 9 à 12 kVA et de 15 à 18 kVA. L'hypothèse, écrite comme telle : un lotissement de maisons se trouve surtout au-dessus de 6 kVA.
- **Variabilité** :
  - chaque maison suit, à pile ou face, la courbe n° 1 ou la courbe n° 2 de son segment, ou sa courbe n° 1 + n° 2 là où le secret statistique masque les moitiés ;
  - un facteur d'échelle, de moyenne 1 pour ne pas changer le total, tiré d'une loi log-normale dont l'écart est fixé et justifié à l'incrément 3.2 ;
  - un décalage d'horaire de quelques quarts d'heure, qui ne change pas l'énergie du jour.
  
  Un test vérifie que la somme des 200 maisons reste proche de la somme des courbes de leurs segments.
- **Équipements**, le scénario de référence que le tableau de bord fera varier :
  - 30 % des toits équipés, soit 60 maisons ;
  - la moitié en 3 kWc, l'autre en 6 kWc. La seconde tranche d'Enedis, de 3 à 9 kW, injecte 2,2 fois plus que la première : 6 kWc en est l'ordre de grandeur ;
  - la moitié de ces 60 maisons ont une batterie, de 5 ou de 10 kWh. Leur puissance, leur rendement et leurs sources sont fixés à l'incrément 3.3.
- **Jours récents** : pour chaque courbe qu'une maison suit, un modèle de la température et du calendrier (la droite brisée, le jour de la semaine, les jours fériés), calé sur juillet 2023 à juin 2025 par le garde-fou de l'ADR 007. Ces jours sont marqués « estimés ». Après l'évaluation finale, le modèle pourra être recalé sur toute la période publiée.
- **Incréments** :
  1. 3.1 : la production solaire, avec pvlib, calée sur PVGIS et comparée au taux de charge régional d'éCO2mix ;
  2. 3.2 : les 200 foyers et leur variabilité ;
  3. 3.3 : les batteries, la règle simple et le bilan énergétique vérifié à chaque pas ;
  4. 3.4 : l'estimation des jours récents ;
  5. 3.5 : le quartier sur juillet 2023 à juin 2025, avec ses mesures.

## Conséquences

- Le quartier consomme plus qu'un foyer moyen de la région, qui compte beaucoup d'appartements : c'est voulu, et le README le dira.
- Les pointes d'appareils manquent en v1. Une batterie y trouverait sans doute un peu plus à faire : c'est une limite connue, à mesurer à la version complète.
- Deux segments sur neuf, RES4 et RES11 de 12 à 15 kVA, n'ont pas de moitiés réelles : leurs maisons ne varient que par l'échelle et le décalage.
- Les chiffres de la région restent la référence : le scénario de 4 % sera l'un des points du tableau de bord.
