# ADR 020 : image Docker de la démo, construite par la CI et publiée sur GHCR

- **Statut** : acceptée ; complète les ADR 014, 018 et 019
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

`python:3.13-slim` n'a pas de `/etc/mime.types` : Python n'y connaît pas le type des polices `.woff2`, que le serveur de fichiers enverrait en `text/plain`.

Sur le VPS, le Docker rootless du compte de développement (ADR 014) n'a pas l'extension buildx. Il construit avec l'ancien constructeur, qui ne lit que le `.dockerignore` placé à la racine du contexte.

## Options envisagées

### Image de base pour Python

1. **`python:3.13-slim`**
   - **Avantages** : l'image officielle, sur Debian, avec glibc. Les bibliothèques des étapes suivantes (DuckDB, LightGBM, pandas, pvlib) y trouvent des paquets précompilés, et un shell aide au débogage.
   - **Inconvénients** : 168 Mo avant l'ajout du projet.
2. **`python:3.13-alpine`**
   - **Avantages** : une image plus petite.
   - **Inconvénients** : musl au lieu de glibc, si bien que plusieurs bibliothèques scientifiques se compileraient pendant le build.
3. **Distroless**
   - **Avantages** : ni shell ni gestionnaire de paquets, donc moins de surface d'attaque.
   - **Inconvénients** : Python y suit Debian 12, en version 3.11.

### Construction et publication dans la CI

1. **`docker build` et `docker push`**, les commandes du runner
   - **Avantages** : aucune action de plus, donc aucune action tierce ne reçoit le droit d'écrire dans GHCR. Chaque ligne se lit.
   - **Inconvénients** : pas encore d'attestation de provenance ni d'image pour d'autres processeurs.
2. **Les actions Docker officielles** : setup-buildx, login, metadata et build-push
   - **Avantages** : la provenance, plusieurs processeurs et un cache.
   - **Inconvénients** : quatre actions de plus à épingler, et à qui confier l'écriture dans GHCR.

### Moment de la construction

1. **Sur chaque pull request, et publication sur `main`**
   - **Avantages** : un Dockerfile cassé se voit avant la fusion.
   - **Inconvénients** : quelques minutes de plus par exécution.
2. **Seulement sur `main`**
   - **Avantages** : moins de minutes consommées.
   - **Inconvénients** : une image cassée ne se voit qu'après la fusion.

## Décision

Option 1 dans les trois cas.

- `deploy/Dockerfile` construit l'image en quatre étapes :
  - le build du tableau de bord, avec `node:24-slim` ;
  - uv, copié depuis son image officielle ;
  - l'environnement Python, installé par `uv sync --locked --no-dev` ;
  - l'image finale, sur `python:3.13-slim`, qui ne reçoit que l'environnement et le build.
- Les trois images de base sont désignées par leur empreinte. Dependabot propose chaque semaine les nouvelles versions des mêmes étiquettes, avec leurs correctifs de sécurité. Node et Python gardent les versions de `web/.node-version` et `.python-version`, et uv change à la main.
- L'image finale installe le paquet `media-types`, qui fournit `/etc/mime.types` : les polices partent en `font/woff2`.
- Le processus tourne sous un utilisateur sans droits, `ampere` (UID 10001). La commande est `ampere api --host 0.0.0.0 --port 8000 --dashboard /app/dashboard` : le conteneur écoute sur toutes ses interfaces, et la plateforme publiera le port sur 127.0.0.1 seulement.
- Le contrôle de santé de l'image fait un GET sur `/healthz`, puisque l'API répond 405 à HEAD.
- `.dockerignore`, à la racine, n'autorise que les fichiers utiles : les notes locales, l'environnement virtuel et les dépendances du développement n'entrent jamais dans le contexte de construction.
- `deploy/smoke-test.sh` démarre un conteneur et vérifie :
  - l'API ;
  - la page, avec son en-tête de cache ;
  - le type d'une police ;
  - l'utilisateur ;
  - l'état « healthy » du contrôle de santé.
- Le workflow `image.yml` construit l'image et lance cet essai sur chaque pull request, sans rien publier. Sur chaque push vers `main`, un autre job fait de même, puis publie l'image sur `ghcr.io/erhazya/ampere` sous deux étiquettes : `sha-<commit>`, qui ne change jamais, et `main`. Seul ce job peut écrire dans GHCR, et il n'utilise aucun cache.

## Conséquences

- L'image pèse 200 Mo, dont 17 Mo pour l'environnement Python et 384 Ko pour le tableau de bord. Au repos, le conteneur occupe 38 Mo de mémoire, loin des 400 Mo prévus pour l'API (conception, § 8.5).
- Une construction sans cache prend 3 minutes sur le VPS.
- Le paquet GHCR reste privé tant que le dépôt l'est, puisqu'il contient le code. Il deviendra public avec le dépôt, à la fin de l'étape 1.
- Mettre uv à jour touche désormais trois endroits : `ci.yml`, `deploy/Dockerfile` et le compte de développement.
- Le traitement quotidien utilisera la même image, avec la commande `ampere daily`, quand elle existera.
- Le déploiement (incrément 7) réglera le reste : le fichier Compose, les limites de mémoire, la publication du port sur 127.0.0.1, et l'adresse dont l'API accepte les en-têtes transmis par le proxy (`FORWARDED_ALLOW_IPS`).
