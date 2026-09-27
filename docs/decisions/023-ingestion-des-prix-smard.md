# ADR 023 : ingestion des prix spot de SMARD

- **Statut** : acceptée ; applique l'ADR 003 et l'ADR 022
- **Date** : 27 septembre 2026

## Contexte

SMARD republie sous licence CC BY 4.0 le prix du marché de la veille pour la zone France (filtre `254`, conception, section 6.2). Son API JSON n'a pas de documentation officielle. Observée le 27 septembre 2026 :

- un index par pas de temps (`index_hour.json`, `index_quarterhour.json`) liste le début de chaque semaine, du 4 janvier 2015 à la semaine en cours : 612 semaines, les mêmes dans les deux index ;
- une semaine commence le lundi à minuit, heure de Paris, et son fichier donne une série de paires (instant en millisecondes, prix en €/MWh), à pas constant en UTC. Les semaines des changements d'heure comptent 668 et 676 quarts d'heure ;
- la semaine en cours contient déjà les prix du jour, publiés la veille. Ceux du lendemain arrivent vers 13 h, heure de Paris ;
- le marché fixe un prix par quart d'heure depuis le 1er octobre 2025, et un prix par heure avant. Pour les semaines antérieures, le fichier au quart d'heure répète quatre fois chaque prix horaire ;
- chaque fichier porte l'heure de sa génération (`meta_data.created`) : une semaine régénérée donne donc une nouvelle réponse, même si les prix n'ont pas changé.

Enedis, la source de la consommation, couvre juillet 2023 à juin 2026 (section 6.1), et la période de test va de juillet 2025 à juin 2026 (ADR 007).

## Options envisagées

### Fichiers à archiver avant le 1er octobre 2025

1. **Les fichiers horaires avant, ceux au quart d'heure après**
   - **Avantages** : on archive ce que le marché a vraiment publié. Dans le nettoyé, chaque prix horaire vaut pour ses quatre quarts d'heure, et une colonne donne le pas du marché.
   - **Inconvénients** : deux sortes de fichiers, et une semaine de transition qui a besoin des deux.
2. **Les fichiers au quart d'heure partout**
   - **Avantages** : une seule sorte de fichier.
   - **Inconvénients** : le nettoyé ne dit plus quand le marché était horaire, alors que la période de test chevauche le changement.

### Profondeur de l'historique

1. **Depuis le 1er juillet 2023** : trois ans communs à toutes les sources, période de test comprise ; environ 170 fichiers au premier passage.
2. **Depuis 2015** : tout l'historique de SMARD, mais sans consommation en face avant 2023.
3. **Depuis le 1er juillet 2024** : le minimum du brief, avec moins de recul pour les prévisions.

### Rattrapage

1. **La semaine en cours et la précédente à chaque passage, plus toute semaine absente du brut**
   - **Avantages** : deux ou trois requêtes par jour ; les semaines récentes, qui peuvent encore changer, sont relues ; un premier passage rattrape tout.
   - **Inconvénients** : une révision d'une semaine ancienne passerait inaperçue.
2. **Toutes les semaines à chaque passage**
   - **Avantages** : aucune révision ne peut échapper.
   - **Inconvénients** : 170 requêtes par jour pour des semaines qui ne bougent plus.

### Contrôles de qualité

1. **Une erreur pour ce qui manque au passé, un avertissement pour le lendemain**
   - **Avantages** : un trou ou un prix aberrant arrête le traitement, avec un code de sortie non nul ; les prix du lendemain en retard n'arrêtent rien, puisque le passage suivant les rattrape.
   - **Inconvénients** : un trou réel chez SMARD fait échouer chaque passage tant qu'il dure.
2. **Tout en avertissement** : le traitement ne s'arrête jamais, mais un trou peut passer inaperçu.

## Décision

Option 1 dans les quatre cas.

- **Requêtes** : l'index au quart d'heure, qui liste toutes les semaines, puis les semaines nécessaires depuis le 1er juillet 2023, à heure de Paris. Une semaine qui commence avant le 1er octobre 2025 demande son fichier horaire ; une semaine qui finit après demande son fichier au quart d'heure ; la semaine de transition demande les deux. Chaque réponse passe par `get()` (ADR 022), qui n'accepte que du JSON, puis va dans la couche brute : la source `smard`, et les jeux `index`, `prices-hour` et `prices-quarterhour`. Une courte pause sépare deux semaines, par politesse envers SMARD.
- **Schéma** : chaque réponse est contrôlée après son archivage. Un index sans liste d'instants, une série qui n'est pas une liste de paires (instant entier, prix numérique ou nul) ou une version de `meta_data` autre que 1 arrêtent tout, avec l'adresse fautive. La réponse reste dans le brut, pour comprendre ce qui a changé.
- **Nettoyé** : `clean/smard/prices.parquet`, reconstruit en entier à chaque passage depuis la dernière réponse de chaque semaine :
  - `start` : le début du quart d'heure, en UTC ;
  - `price_eur_per_mwh` : le prix, tel que publié ;
  - `market_step_minutes` : 60 avant le 1er octobre 2025, 15 après ;
  - `received_at` : l'heure de réception de la réponse d'où vient le prix.

  Un prix nul chez SMARD est un prix absent : la ligne n'existe pas, et aucun trou n'est comblé.
- **Contrôles**, pour chaque jour de Paris du 1er juillet 2023 à aujourd'hui :
  - erreur : des quarts d'heure manquants (en comptant 92 et 100 aux changements d'heure), un quart d'heure en double, un prix hors de −500 à 5 000 €/MWh (les bornes du marché européen, avec de la marge), ou un problème signalé par `verify()` sur la couche brute ;
  - avertissement : après 14 h à Paris, des prix du lendemain manquants.

  Une erreur donne un code de sortie non nul.
- **Commandes** : `ampere data init` crée la couche brute, une fois ; `ampere ingest smard` récupère, archive, reconstruit et contrôle. Le dossier des données (`AMPERE_DATA`) est résolu en chemin absolu et noté dans le journal.

## Conséquences

- Le premier passage fait environ 170 requêtes, espacées ; les suivants, l'index et deux ou trois semaines.
- Une révision d'une semaine de plus de quinze jours n'est pas vue. Si elle devenait un problème, un passage complet occasionnel suffirait.
- Le changement de pas du marché reste visible dans les données : les analyses de la période de test pourront le prendre en compte.
- La source de secours prévue par la conception, Ember, pourra recouper les prix horaires.
