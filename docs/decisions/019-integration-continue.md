# ADR 019 : intégration continue, un workflow et deux jobs

- **Statut** : acceptée ; complète les ADR 009, 014 et 015
- **Date** : 26 septembre 2026

## Contexte

D'après le `CLAUDE.md`, la CI lance les mêmes contrôles qu'en développement. Le projet en a deux séries :

- le code Python, géré avec uv (ADR 009) : `ruff format --check`, `ruff check`, `mypy` en mode strict et `pytest` ;
- le tableau de bord, géré avec npm (ADR 015) : la mise en forme avec Prettier, l'analyse avec ESLint, les tests avec Vitest, puis la vérification des types et le build.

Un premier workflow, `secrets.yml`, cherche déjà des secrets avec gitleaks. Il fixe des conventions : déclenchement sur chaque pull request et chaque push vers `main`, permissions en lecture seule, image `ubuntu-24.04`, actions désignées par l'empreinte de leur commit.

Le dépôt reste privé jusqu'à la fin de l'étape 1, sur un compte GitHub gratuit. Tous les dépôts privés du compte se partagent 2 000 minutes de CI par mois, et un contrôle ne peut être rendu obligatoire avant une fusion que sur un dépôt public. Un dépôt public ne consomme pas ces minutes.

En développement, uv 0.12.18 installe Python 3.13, et fnm fournit Node 24 par son alias par défaut (ADR 014). Dependabot met à jour `uv.lock` avec sa propre version de uv : 0.12.15 le 25 septembre 2026.

## Options envisagées

### Organisation

1. **Un workflow, deux jobs en parallèle**
   - **Avantages** : le code Python et le tableau de bord ont chacun leur statut sur la pull request, et un échec d'un côté ne cache pas l'autre. Les deux jobs tournent en même temps.
   - **Inconvénients** : deux machines démarrent à chaque fois, et GitHub compte chaque job à la minute entamée.
2. **Un seul job**
   - **Avantages** : le fichier le plus court, et une seule machine, donc moins de minutes décomptées.
   - **Inconvénients** : tout tourne à la suite, et les deux parties n'ont qu'un statut à elles deux.
3. **Deux workflows, chacun filtré sur son dossier**
   - **Avantages** : seuls les contrôles utiles tournent, ce qui économise des minutes.
   - **Inconvénients** : un contrôle obligatoire sauté par un filtre reste « en attente » et bloque la fusion.

### Versions des outils

1. **Des plages lues par les outils** : `required-version` de uv dans `pyproject.toml`, Node dans `web/.node-version`
   - **Avantages** : pas de version dans le workflow.
   - **Inconvénients** : uv refuse de tourner hors de `required-version`, y compris celui de Dependabot, ce qui bloque ses mises à jour de `uv.lock`. Une plage laisse la CI prendre la dernière version corrective : uv 0.12.19 à la première exécution, contre 0.12.18 en développement. Et fnm, dans le compte de développement, ne lit pas `.node-version`.
2. **uv exact dans la CI, Node et Python par version**
   - **Avantages** : l'outil qui change le plus souvent, uv, est le même qu'en développement et vérifié par sa somme SHA-256. Dependabot n'est pas gêné.
   - **Inconvénients** : la version de uv s'écrit dans le workflow, et sa mise à jour se fait à la main. Les versions correctives de Node et de Python peuvent différer entre le développement et la CI.
3. **Tout en versions exactes**, avec fnm réglé pour lire `.node-version` dans le compte de développement
   - **Avantages** : le développement et la CI deviennent identiques.
   - **Inconvénients** : chaque mise à jour se fait à la main, et le profil du compte de développement change.

## Décision

Option 1 pour l'organisation, option 2 pour les versions.

- Le workflow `.github/workflows/ci.yml` a deux jobs, « Python » et « Dashboard ». Ils tournent en parallèle sur `ubuntu-24.04`, pour chaque pull request et chaque push vers `main`.
- Ils lancent les commandes du `CLAUDE.md` après une installation exacte : `uv sync --locked` échoue si `uv.lock` n'est plus à jour, et `npm ci` installe exactement `package-lock.json`.
- Chaque contrôle est une étape distincte du job. Tous tournent, même quand un contrôle précédent a échoué, sauf si l'installation a échoué ; le job échoue dès qu'un contrôle échoue.
- La CI installe uv 0.12.18, la version du développement, et vérifie sa somme SHA-256 publiée avec la version. Python vient de `.python-version` (3.13), que uv lit en développement comme en CI. Node vient de `web/.node-version` (24), que la CI lit ; en développement, fnm fournit la même version majeure.
- Comme `secrets.yml` : permissions en lecture seule, actions désignées par l'empreinte de leur commit, avec la version en commentaire, et mises à jour par Dependabot. En plus, `persist-credentials: false` : la copie du dépôt ne laisse pas le jeton de GitHub dans la configuration de Git, puisque aucune étape suivante n'en a besoin. Ce jeton ne donne de toute façon qu'un accès en lecture au dépôt.
- Chaque pull request a son groupe d'exécutions : un nouveau push annule l'exécution qu'il rend inutile. Chaque push vers `main` a son propre groupe, pour qu'aucune exécution n'y soit annulée.
- Un job qui dépasse 10 minutes est arrêté.
- Pas de tests de bout en bout pour l'instant : ils viendront avec Playwright.

## Conséquences

- Chaque pull request affiche trois statuts : « Python », « Dashboard » et celui de gitleaks. Les pull requests de Dependabot passent les mêmes contrôles.
- Mettre uv à jour demande deux changements : la version et la somme SHA-256 dans `ci.yml`, et `uv self update` dans le compte de développement.
- Changer de version majeure de Node touche plusieurs endroits : `web/.node-version`, `engines` et `@types/node` dans `web/package.json`, et l'alias par défaut de fnm.
- La deuxième exécution a duré 18 secondes pour le job Python et 29 secondes pour le tableau de bord. GitHub compte chaque job à la minute entamée : une exécution coûte 3 minutes avec gitleaks, parfois 4 quand une machine tarde à démarrer. À la première exécution, le job du tableau de bord a attendu 37 secondes avant sa première étape.
- Quand le dépôt deviendra public, à la fin de l'étape 1, les trois statuts deviendront des contrôles obligatoires avant toute fusion sur `main`.
