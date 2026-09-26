# ADR 020 : image Docker de la démo, construite par la CI et publiée sur GHCR

- **Statut** : acceptée ; complète les ADR 014, 018 et 019. Complétée par l'[ADR 021](021-demo-en-ligne.md) : le paquet est public depuis le 26 septembre 2026, avant le dépôt, et la commande de l'image laisse de côté les pages de documentation (`--no-docs`)
- **Date** : 26 septembre 2026

## Contexte

Le contrat d'hébergement de la plateforme du portfolio demande à chaque démo :

- des images publiées sur GHCR, construites par sa CI, avec une étiquette immuable par commit ;
- des processus qui ne tournent pas en root, un point de santé HTTP et des journaux sur la sortie standard.

L'API sert aussi le build du tableau de bord (ADR 018) : une seule image suffit pour la démo en ligne.

Trois images de base ont été examinées le 26 septembre 2026, aux versions du développement et de la CI (ADR 019) :

- `python:3.13-slim` : Debian 13, Python 3.13.15, 168 Mo ;
- `node:24-slim` : Node 24.21.0 ;
- `ghcr.io/astral-sh/uv:0.12.18`, qui ne contient que uv.

Deux détails de `python:3.13-slim` comptent. L'image n'a pas de `/etc/mime.types`, si bien que Python n'y connaît pas le type des polices `.woff2`, qui partiraient en `application/octet-stream`. Et sa bibliothèque standard n'est pas précompilée : un utilisateur sans droits la recompile à chaque lancement de Python, soit 0,8 seconde de calcul pour chaque contrôle de santé.

Sur le VPS, le Docker rootless du compte de développement (ADR 014) n'a pas l'extension buildx. Il construit avec l'ancien constructeur, qui ne lit que le `.dockerignore` placé à la racine du contexte. La CI, elle, construit avec BuildKit.

## Options envisagées

### Image de base pour Python

1. **`python:3.13-slim`**
   - **Avantages** : l'image officielle, sur Debian, avec glibc. Les bibliothèques prévues aux étapes suivantes du projet (DuckDB, LightGBM, pandas, pvlib) y trouvent des paquets précompilés, et un shell aide au débogage.
   - **Inconvénients** : 168 Mo avant l'ajout du projet.
2. **`python:3.13-alpine`**
   - **Avantages** : une image plus petite.
   - **Inconvénients** : musl au lieu de glibc, si bien que certaines bibliothèques scientifiques n'ont pas de paquet précompilé et se compileraient pendant la construction.
3. **Distroless** (`gcr.io/distroless/python3-debian13`)
   - **Avantages** : ni shell ni gestionnaire de paquets, donc moins de surface d'attaque.
   - **Inconvénients** : son Python est celui de Debian, 3.13.5 au lieu de 3.13.15, et l'environnement doit être construit pour cet interpréteur. Sans shell, l'essai de fumée ne peut pas inspecter le conteneur.

### Construction et publication dans la CI

1. **`docker build` et `docker push`**, les commandes du runner
   - **Avantages** : aucune action de plus, donc aucune action tierce ne reçoit le droit d'écrire dans GHCR. Chaque ligne est une commande Docker ordinaire.
   - **Inconvénients** : pas encore d'attestation de provenance ni d'image pour d'autres processeurs.
2. **Les actions Docker officielles** : setup-buildx, login, metadata et build-push
   - **Avantages** : la provenance, plusieurs processeurs et un cache.
   - **Inconvénients** : quatre actions de plus à épingler, avec le droit d'écrire dans GHCR.

### Moment de la construction

1. **Sur chaque pull request, et publication sur `main`**
   - **Avantages** : un Dockerfile cassé se voit avant la fusion.
   - **Inconvénients** : un job de plus par pull request : 34 secondes sur GitHub, une minute décomptée.
2. **Seulement sur `main`**
   - **Avantages** : moins de minutes consommées.
   - **Inconvénients** : une image cassée ne se voit qu'après la fusion.

## Décision

Option 1 dans les trois cas.

- `deploy/Dockerfile` construit l'image par étapes de construction :
  - une base commune, `python:3.13-slim`, dont la bibliothèque standard est précompilée une fois, pip exclu ;
  - le build du tableau de bord, avec `node:24-slim` ;
  - uv, copié depuis son image officielle ;
  - l'environnement Python, installé sur la base par `uv sync --locked --no-dev` ;
  - l'image finale, sur la base, qui reçoit l'environnement et le build, un utilisateur sans droits, `ampere` (UID et GID 10001), et le dossier de travail `/app`.
- Les images de base sont désignées par leur empreinte. Dependabot propose chaque semaine les nouvelles constructions des mêmes étiquettes, avec leurs correctifs de sécurité. Node et Python gardent les versions de `web/.node-version` et `.python-version`, et uv change à la main.
- L'application déclare elle-même le type `font/woff2` quand elle sert le tableau de bord. L'image n'installe donc aucun paquet, et deux constructions ne dépendent pas de l'index Debian du jour.
- La commande est `ampere api --host 0.0.0.0 --port 8000 --dashboard /app/dashboard` : le conteneur écoute sur toutes ses interfaces, et le déploiement choisira l'adresse de l'hôte où publier le port.
- Le contrôle de santé de l'image fait un GET sur `/healthz`, puisque l'API répond 405 à HEAD. Avec la bibliothèque précompilée, il coûte 0,2 seconde de calcul au lieu de 0,8.
- `.dockerignore`, à la racine, n'autorise que les fichiers utiles et exclut en dernier tous les fichiers `.env*`. Les notes locales, l'environnement virtuel, les dépendances du développement et les fichiers d'environnement n'entrent jamais dans le contexte de construction.
- `deploy/smoke-test.sh` démarre un conteneur sur un port libre de 127.0.0.1, que Docker attribue, et vérifie :
  - l'API ;
  - la page, avec son en-tête de cache ;
  - le type d'une police ;
  - l'utilisateur ;
  - l'état « healthy » du contrôle de santé.

  Après un échec, il affiche les journaux du conteneur, et il le supprime dans tous les cas.
- Le workflow `image.yml` construit l'image et lance cet essai sur chaque pull request, dans le job « Image », sans rien publier. Sur chaque push vers `main`, le job « Publish » fait de même, puis `deploy/publish.sh` publie l'image sur `ghcr.io/erhazya/ampere` :
  - `sha-<commit>` n'est jamais remplacée : si elle existe déjà, par exemple quand le job est relancé, l'image publiée reste ;
  - `main` ne bouge que si le commit est encore le dernier de `main`, et désigne alors la même image que `sha-<commit>`, avec la même empreinte.

  Seul ce job peut écrire dans GHCR, et il n'utilise aucun cache.

## Conséquences

- L'image pèse 218 Mo, dont 17 Mo pour l'environnement Python, 18 Mo pour la bibliothèque précompilée et 384 Ko pour le tableau de bord. Au repos, le conteneur occupe 33 Mo de mémoire.
- Chaque pull request affiche désormais quatre statuts : « Python », « Dashboard », « Image » et celui de gitleaks.
- Le paquet GHCR est privé à sa création. Quand le dépôt deviendra public, à la fin de l'étape 1, il faudra le rendre public à la main, dans ses réglages : sa visibilité ne suit pas celle du dépôt, et ce changement est irréversible.
- Mettre uv à jour touche trois endroits : `ci.yml`, `deploy/Dockerfile` et le compte de développement. Changer de version de Node ou de Python touche aussi les étiquettes du Dockerfile.
- Le traitement quotidien utilisera la même image, avec la commande `ampere daily`, quand elle existera. Son conteneur devra désactiver le contrôle de santé, prévu pour l'API (`--no-healthcheck`).
- Le déploiement réglera le reste dans le fichier Compose : les limites de mémoire, la publication du port sur l'hôte, les options de sécurité du conteneur, et l'adresse dont l'API accepte les en-têtes transmis par le proxy (`FORWARDED_ALLOW_IPS`).
