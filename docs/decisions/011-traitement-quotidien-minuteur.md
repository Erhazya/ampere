# ADR 011 : traitement quotidien lancé par un minuteur systemd sur le VPS

- **Statut** : acceptée ; précisée par l'[ADR 028](028-traitement-quotidien-en-ligne.md) : le minuteur est celui de la plateforme des démos du socle, et la supervision reste à venir
- **Date** : 23 septembre 2026

## Contexte

Chaque jour, il faut ingérer les données, simuler la veille, prévoir le lendemain et exporter les résultats. Deux contraintes pèsent sur ce choix : le VPS a déjà connu des arrêts brutaux de plusieurs heures, et Ampère n'y dispose que de 2,5 Go de mémoire.

## Options envisagées

### 1. Minuteur systemd sur le VPS

Un minuteur lance chaque jour un conteneur de traitement. Son option `Persistent` relance une exécution manquée dès le redémarrage du serveur.

- **Avantages** : simple ; aucune mémoire occupée entre deux exécutions ; données sur place.
- **Inconvénients** : dépend du VPS, dont la surveillance vient du socle.

### 2. GitHub Actions planifié

Le traitement tourne chez GitHub chaque jour et publie ses résultats, que le VPS récupère.

- **Avantages** : indépendant des pannes du VPS ; journal d'exécution public.
- **Inconvénients** :
  - horaires parfois retardés ;
  - GitHub désactive ces tâches après 60 jours sans activité sur le dépôt ;
  - stockage des données à organiser.

### 3. Dagster ou Prefect

Orchestrateurs de données complets, avec graphe des tâches, reprises et interface web.

- **Avantages** : très appréciés en entreprise.
- **Inconvénients** : plusieurs centaines de Mo de mémoire en permanence, trop avec le modèle de langage prévu ; surdimensionnés pour quelques tâches par jour.

## Décision

Un minuteur systemd lance le conteneur de traitement chaque jour à 14 h, heure de Paris, avec l'option `Persistent`. Ce créneau suit la publication des prix du lendemain, vers 13 h.

## Conséquences

- Le traitement est idempotent et rattrape les jours manquants.
- Son journal et son code de retour sont surveillés par la supervision du socle.
- La définition du minuteur est versionnée dans `deploy/`. Son installation sera alignée sur les conventions du socle.
