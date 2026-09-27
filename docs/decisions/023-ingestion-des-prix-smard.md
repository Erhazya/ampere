# ADR 023 : ingestion des prix spot de SMARD

- **Statut** : acceptée ; applique l'ADR 003 et l'ADR 022
- **Date** : 27 septembre 2026

## Contexte

SMARD republie sous licence CC BY 4.0 le prix du marché de la veille pour la zone France (filtre `254`, conception, section 6.2). Son API JSON n'a pas de documentation officielle. Observée le 27 septembre 2026 :

- un index par pas de temps (`index_hour.json`, `index_quarterhour.json`) liste le début de chaque semaine, du 4 janvier 2015 à la semaine en cours : 612 semaines, les mêmes dans les deux index ;
- une semaine commence le lundi à minuit, heure de Paris, et son fichier donne une série de paires (instant en millisecondes, prix en €/MWh), à pas constant en UTC. Les semaines des changements d'heure comptent 668 et 676 quarts d'heure ;
- la semaine en cours contient déjà les prix du jour, publiés la veille. Ceux du lendemain arrivent vers 13 h, heure de Paris. Le dimanche, l'index annonce la semaine suivante dès que les prix du lundi sont là : le 27 septembre 2026, entre 13 h 27 et 13 h 43 ;
- le marché fixe un prix par quart d'heure depuis le 1er octobre 2025, et un prix par heure avant. Pour les semaines antérieures, le fichier au quart d'heure répète quatre fois chaque prix horaire ;
- chaque fichier porte l'heure de sa génération (`meta_data.created`) : une semaine régénérée donne donc une nouvelle réponse, même si les prix n'ont pas changé ;
- d'après cette heure, la plupart des fichiers au quart d'heure ont été générés pour la dernière fois le samedi de leur semaine, vers 13 h 30, avec les prix du dimanche. Six des 52 l'ont été après la fin de leur semaine, de quelques heures à 37 jours plus tard, et les 119 fichiers horaires l'ont tous été le 7 octobre 2025. Rien ne dit si leurs prix ont changé.

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

1. **Chaque semaine jusqu'à 14 jours après sa fin, puis celles que le brut n'a pas entières**
   - **Avantages** : après une panne, même longue, le passage suivant complète ce qui manque ; une réponse abîmée ou d'une forme inconnue est redemandée ; cinq des six régénérations observées tombent dans ces 14 jours. Quatre requêtes par jour, cinq le dimanche.
   - **Inconvénients** : chaque passage relit les réponses gardées pour savoir si elles sont entières ; une révision plus tardive passe inaperçue, sauf lors d'un passage complet.
2. **Les deux dernières semaines de l'index, plus toute semaine absente du brut**
   - **Avantages** : trois requêtes par jour ; un premier passage rattrape tout.
   - **Inconvénients** : une semaine archivée incomplète avant plus de huit jours sans passage, ou avec une réponse illisible, n'est plus jamais redemandée, et chaque passage échoue ensuite. Une semaine cesse aussi d'être relue environ six jours après sa fin : les régénérations à 7,5 et 7,7 jours auraient été manquées.
3. **Toutes les semaines à chaque passage**
   - **Avantages** : aucune révision ne peut échapper.
   - **Inconvénients** : 170 requêtes par jour pour des semaines qui ne bougent plus.

### Contrôles de qualité

1. **Une erreur pour ce qui manque au passé, un avertissement pour le lendemain**
   - **Avantages** : un trou ou un prix aberrant arrête le traitement, avec un code de sortie non nul ; les prix du lendemain en retard n'arrêtent rien, puisque le passage suivant les rattrape.
   - **Inconvénients** : un trou réel chez SMARD fait échouer chaque passage tant qu'il dure.
2. **Tout en avertissement** : le traitement ne s'arrête jamais, mais un trou peut passer inaperçu.

### Publication du nettoyé quand les contrôles trouvent des problèmes

1. **Publier, sauf si des lignes sont invalides**
   - **Avantages** : le nettoyé respecte toujours ses règles ; un trou durable chez SMARD ne l'empêche pas de recevoir les prix nouveaux, et ce trou y reste visible.
   - **Inconvénients** : les contrôles doivent séparer deux sortes de problèmes.
2. **Publier seulement si tous les contrôles passent**
   - **Avantages** : un fichier publié a passé tous les contrôles.
   - **Inconvénients** : un trou durable chez SMARD fige le nettoyé, sans prix nouveaux tant qu'il dure.
3. **Toujours publier**
   - **Avantages** : le plus simple.
   - **Inconvénients** : un passage en échec a déjà publié les lignes qu'il rejette, et rien ne distingue un fichier contrôlé d'un fichier rejeté.

## Décision

Option 1 dans les cinq cas. Pour le rattrapage, la première version de cette décision retenait l'option 2 ; la relecture de son code, avant la fusion, en a reproduit les blocages.

- **Requêtes** : l'index au quart d'heure, qui liste toutes les semaines, puis, depuis le 1er juillet 2023, les semaines que le brut n'a pas pour de bon. Une semaine est redemandée à chaque passage jusqu'à 14 jours après sa fin. Au-delà, elle ne l'est que si sa dernière réponse manque, ne se lit plus (fichier abîmé, forme inconnue) ou n'a pas tous ses prix. Les 14 jours se comptent à la date du passage, pas à celle de la réception : une réponse identique à la précédente n'ajoute rien au brut (ADR 022), si bien que la dernière réception d'une semaine précède souvent sa fin.
- **Fichiers** : une semaine qui commence avant le 1er octobre 2025 demande son fichier horaire ; une semaine qui finit après demande son fichier au quart d'heure ; la semaine de transition demande les deux. Une semaine finit le lundi suivant à minuit, heure de Paris. Chaque réponse passe par `get()` (ADR 022), qui n'accepte que du JSON, et de 1 Mo au plus (les fichiers de SMARD pèsent 15 Ko), puis va dans la couche brute : la source `smard`, et les jeux `index`, `prices-hour` et `prices-quarterhour`. Une courte pause sépare deux semaines, par politesse envers SMARD.
- **Schéma** : chaque réponse est contrôlée après son archivage. Un index sans liste d'instants, une série qui n'est pas une liste de paires (instant entier, prix numérique ou nul) ou une version de `meta_data` autre que 1 arrêtent tout, avec l'adresse fautive. La réponse reste dans le brut, pour comprendre ce qui a changé, et le passage suivant la redemande.
- **Nettoyé** : `clean/smard/prices.parquet`, reconstruit en entier à chaque passage depuis la dernière réponse de chaque semaine :
  - `start` : le début du quart d'heure, en UTC ;
  - `price_eur_per_mwh` : le prix, tel que publié ;
  - `market_step_minutes` : 60 avant le 1er octobre 2025, 15 après ;
  - `received_at` : l'heure de réception de la réponse d'où vient le prix.

  Un prix nul chez SMARD est un prix absent : la ligne n'existe pas, et aucun trou n'est comblé. Le fichier est remplacé d'un seul coup, comme les fichiers bruts (`write_atomically`, ADR 022), et seulement si ses lignes sont valides.
- **Contrôles**, pour chaque jour de Paris du 1er juillet 2023 à aujourd'hui :
  - ligne invalide : un quart d'heure en double, un instant hors de la grille du quart d'heure, un prix hors de −500 à 5 000 €/MWh (les bornes du marché européen, avec de la marge), ou un prix d'après le lendemain, que le marché n'a pas encore fixé. Le nettoyé garde alors sa version précédente ;
  - erreur : des quarts d'heure manquants (en comptant 92 et 100 aux changements d'heure), ou un problème signalé par `verify()` sur la couche brute. Le nouveau nettoyé est écrit quand même : ses lignes sont justes, et ses trous restent visibles ;
  - avertissement : après 14 h à Paris, des prix du lendemain manquants.

  Une ligne invalide ou une erreur donne le code de sortie 1.
- **Commandes** : `ampere data init` crée la couche brute, une fois ; `ampere ingest smard` récupère, archive, reconstruit et contrôle, et `--full` redemande toutes les semaines. Le début de l'historique ne se change pas en ligne de commande : une date tapée de travers viderait le nettoyé sans erreur. Le journal note le dossier des données (`AMPERE_DATA`, résolu en chemin absolu), chaque réponse nouvelle, puis le nombre de fichiers demandés, de réponses nouvelles et de quarts d'heure.

## Conséquences

- Le premier passage fait environ 170 requêtes, espacées ; les suivants, l'index et trois semaines, quatre le dimanche après-midi.
- Chaque passage lit deux fois toutes les réponses gardées : pour savoir si elles sont entières, puis pour reconstruire le nettoyé. Le 27 septembre 2026, un passage sans réponse nouvelle prend 4,8 s, requêtes et pauses comprises. Ce travail grandit d'une semaine par semaine ; il sera à revoir avant les sources plus lourdes.
- Une révision faite plus de 14 jours après la fin d'une semaine n'est vue qu'avec `--full`, que le traitement quotidien pourra lancer une fois par mois.
- Un trou réel chez SMARD fait échouer chaque passage tant qu'il dure, et sa semaine est redemandée à chaque fois : le trou est rattrapé dès que SMARD le comble.
- Le changement de pas du marché reste visible dans les données : les analyses de la période de test pourront le prendre en compte.
- La source de secours prévue par la conception, Ember, pourra recouper les prix horaires.
