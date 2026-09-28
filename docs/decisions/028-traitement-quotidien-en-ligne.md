# ADR 028 : le traitement quotidien en ligne

- **Statut** : acceptée ; précise l'ADR 011 pour la plateforme des démos du socle (ADR 021 d'Ampère, ADR 021 et 022 du socle)
- **Date** : 28 septembre 2026
- **Choix** : fait par Claude, l'auteur lui ayant délégué le 27 septembre 2026 les choix qu'il aurait recommandés, jusqu'aux réglages du serveur

## Contexte

L'ADR 011 a choisi un minuteur systemd qui lance chaque jour, à 14 h, heure de Paris, un conteneur de traitement, avec l'option `Persistent`. Le 28 septembre 2026, les cinq sources s'ingèrent chacune par `ampere ingest <source>`, mais seulement dans le compte de développement, sur `data/`. La démo, elle, tourne sous le compte `demos` du socle, en Docker rootless : `demo-deploy@ampere` la déploie chaque minute depuis GHCR, avec `/etc/demos/ampere/compose.yaml` et son `.env`, et garde l'empreinte de l'image déployée.

Il reste à fixer :

- où tourne le traitement, et avec quelle image ;
- où vivent les données, qu'il écrit et que l'API lira pour l'écran « Data » ;
- comment le brut du serveur part de celui du développement, avant la publication d'Enedis de fin octobre 2026, qui fera sortir juillet à septembre 2023 de sa fenêtre (ADR 026) ;
- la mémoire, les sauvegardes et la supervision.

L'image tourne sous l'utilisateur 10001, avec un système de fichiers en lecture seule. En Docker rootless, l'utilisateur 0 d'un conteneur est le compte `demos` de l'hôte, et l'utilisateur 10001 un identifiant subordonné de ce compte.

## Options envisagées

### Où tourne le traitement

1. **Un conteneur ponctuel de l'image déployée** : un service Compose `daily`, que le minuteur lance par `docker compose run --rm daily`, avec l'empreinte de l'image que `demo-deploy` a déployée.
   - **Avantages** : le traitement a exactement le code et les versions de l'API en ligne ; sa limite de mémoire est celle d'un conteneur ; il ne garde rien en mémoire entre deux jours.
   - **Inconvénients** : un script de plus dans le socle.
2. **Une tâche planifiée dans le conteneur de l'API** : un seul conteneur, mais un redémarrage de l'API tue le traitement, et leurs limites de mémoire se confondent.
3. **Le compte de développement** : le code de la branche en cours, pas celui de la démo.

### Où vivent les données

1. **Un dossier de l'hôte, monté dans les conteneurs** : `~demos/data/ampere`, en lecture et écriture pour le traitement, en lecture seule pour l'API.
   - **Avantages** : on le lit, le copie et le sauvegarde avec les outils de l'hôte.
   - **Inconvénients** : son propriétaire doit être l'utilisateur 10001 de l'image, ce qu'un `chown` fait une fois, depuis un conteneur.
2. **Un volume nommé de Docker** : Docker gère le dossier, mais il est enfoui dans le compte de `demos`, plus difficile à amorcer et à sauvegarder.

### Sauvegardes

1. **Une copie compressée du brut chaque jour, sur le serveur, gardée 14 jours**
   - **Avantages** : un brut abîmé ou effacé se restaure en une commande ; 34 Mo par archive le 28 septembre 2026.
   - **Inconvénients** : un disque perdu emporte les copies ; une copie hors du serveur reste à organiser.
2. **Aucune** : le brut ne se reconstruit pas, puisque des sources effacent leur passé (éCO2mix en temps réel, la fenêtre d'Enedis).

## Décision

Option 1 dans les trois cas.

- **Commande** : `ampere daily` ingère les cinq sources à la suite (SMARD, éCO2mix, Open-Meteo, Enedis, calendriers), chacune dans un processus à part, comme `ampere ingest`, sans `--full`. Une source qui s'arrête, sur une exception, une réponse inattendue, une panique de Polars ou un arrêt par le noyau faute de mémoire, laisse donc leur tour aux suivantes. L'ADR 026 demandait de prévoir ce dernier cas : un mois d'Enedis qui déplierait plus que la mémoire du conteneur n'arrête plus que sa source. Le code de sortie vaut 1 dès qu'une source a échoué, ou a rendu une ligne invalide ou une erreur. Une interruption à la main arrête tout.
- **Conteneur** : le service `daily` de `deploy/compose.yaml`, dans le profil `jobs`, que `demo-deploy` ne démarre donc jamais. Il reprend les restrictions de l'API, avec 1 Go de mémoire sans swap (conception, section 8.5), et `AMPERE_DATA=/data`. Le contrôle de santé de l'image, qui interroge l'API, y est désactivé.
- **Données** : `AMPERE_DATA_DIR`, dans le `.env` de la démo, nomme le dossier de l'hôte, monté sur `/data` : en écriture pour `daily`, en lecture seule pour `api`. Le chemin reste hors du dépôt.
- **Lancement** : dans le socle, `demo-job <démo> <service>` lance `docker compose run --rm` sur l'image déployée ; `demo-daily@ampere.timer` le fait chaque jour à 14 h, heure de Paris, avec `Persistent=true` (ADR 022 du socle).
- **Amorçage** : le brut du serveur part d'une copie de celui du développement, faite le 28 septembre 2026, avant la publication de fin octobre d'Enedis.
- **Sauvegardes** : chaque jour à 15 h 30, heure de Paris, après le traitement, le socle archive le brut dans un dossier que la démo ne peut ni lire ni modifier, et garde les 14 dernières archives. La restauration est une procédure de secours du socle (ADR 022 du socle).
- **Supervision** : un traitement en échec laisse `demo-daily@ampere` en échec dans `systemctl --failed`, avec son journal dans celui de systemd. Aucune alerte ne prévient encore : elles viendront avec l'étape 4 du socle, et d'ici là, il faut aller lire cet état.

## Conséquences

- Le traitement du jour tourne sur l'image que la démo sert : une fusion sur `main` change les deux ensemble.
- L'ADR 011 plaçait le minuteur dans `deploy/` et comptait sur la supervision du socle : le minuteur est dans le socle, et la supervision reste à venir.
- Une copie hors du serveur reste à organiser, avec une destination que l'auteur choisira.
- L'écran « Data » pourra lire le nettoyé depuis l'API, sans autre copie.
- Tant que le développement se fait sur le VPS, ses calculs lourds ne doivent pas tourner à 14 h (conception, section 8.5).
