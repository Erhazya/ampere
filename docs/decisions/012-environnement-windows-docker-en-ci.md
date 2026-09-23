# ADR 012 : développement sous Windows, Docker en CI

- **Statut** : remplacée par l'[ADR 013](013-developpement-sur-le-vps.md) le jour même
- **Date** : 23 septembre 2026

## Contexte

Le PC de développement tourne sous Windows 11, sans WSL ni Docker. Le VPS et la CI, eux, tournent sous Ubuntu. Enfin, le dossier de travail initial était synchronisé par OneDrive, qui gère mal un dépôt Git, un environnement Python et des gigaoctets de données.

## Options envisagées

### 1. Windows, Docker seulement en CI

- **Avantages** : démarrage immédiat, sans aucun réglage système. uv s'installe sans droits d'administrateur, et Node est déjà présent.
- **Inconvénients** : sans Docker en local, une erreur de Dockerfile n'apparaît qu'en CI ; il faut surveiller les différences entre Windows et Linux (fins de ligne, chemins).

### 2. WSL2 (Ubuntu)

- **Avantages** : même système que le VPS ; Docker gratuit en local.
- **Inconvénients** : l'auteur doit l'installer lui-même, avec les droits d'administrateur et un redémarrage, ce qui peut être bloqué sur un PC géré.

### 3. Windows + Docker Desktop

- **Avantages** : Docker en local sous Windows.
- **Inconvénients** : exige aussi WSL2 ou Hyper-V ; la licence n'est gratuite que pour un usage personnel ou une petite entreprise.

## Décision

Option 1. Le dépôt est placé hors de tout dossier synchronisé.

## Conséquences

- La CI tourne sous Linux : elle attrape les différences avec Windows et construit les images Docker.
- Un fichier `.gitattributes` impose des fins de ligne LF. Le code manipule les chemins avec `pathlib` et précise l'encodage UTF-8.
- Le minuteur systemd et les conteneurs ne sont testés que sur le VPS. Leur installation est décrite dans un document de transfert, confié à une session Claude Code connectée au VPS.
- Passer à WSL2 reste possible plus tard, si Docker en local devient nécessaire.
