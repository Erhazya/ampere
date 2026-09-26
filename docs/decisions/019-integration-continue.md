# ADR 019 : intégration continue, un workflow et deux jobs

- **Statut** : acceptée ; applique les ADR 009 et 015
- **Date** : 26 septembre 2026

## Contexte

D'après le `CLAUDE.md`, la CI lance les mêmes contrôles qu'en développement. Le projet en a deux séries :

- le code Python, géré avec uv (ADR 009) : `ruff format --check`, `ruff check`, `mypy` en mode strict et `pytest` ;
- le tableau de bord, géré avec npm (ADR 015) : la mise en forme avec Prettier, l'analyse avec ESLint, les tests avec Vitest, puis le build de Vite.

Un premier workflow, `secrets.yml`, cherche déjà des secrets avec gitleaks. Il fixe les conventions du dépôt : déclenchement sur chaque pull request et chaque push vers `main`, permissions en lecture seule, image `ubuntu-24.04`, actions désignées par l'empreinte de leur commit.

Le dépôt reste privé jusqu'à la fin de l'étape 1. Avec un compte GitHub gratuit, un dépôt privé dispose de 2 000 minutes de CI par mois, et les contrôles obligatoires avant une fusion ne sont possibles que sur un dépôt public.

## Options envisagées

### Organisation

1. **Un workflow, deux jobs en parallèle**
   - **Avantages** : le code Python et le tableau de bord ont chacun leur statut sur la pull request, et un échec d'un côté ne cache pas l'autre. Les deux jobs tournent en même temps.
   - **Inconvénients** : deux machines démarrent à chaque fois, même quand une seule partie a changé.
2. **Un seul job**
   - **Avantages** : le fichier le plus court.
   - **Inconvénients** : tout tourne à la suite, et le premier échec arrête le reste.
3. **Deux workflows, chacun filtré sur son dossier**
   - **Avantages** : seuls les contrôles utiles tournent, ce qui économise des minutes.
   - **Inconvénients** : une pull request qui ne touche que la documentation n'a aucun statut, ce qui complique les contrôles obligatoires.

### Versions de Python, de Node et de uv

1. **Dans des fichiers du dépôt**, lus en développement comme en CI
   - **Avantages** : chaque version est écrite une seule fois, et le développement et la CI ne peuvent pas s'écarter.
   - **Inconvénients** : les versions sont réparties dans trois fichiers.
2. **Dans le workflow**
   - **Avantages** : tout se lit dans un seul fichier.
   - **Inconvénients** : rien ne signale un écart avec les versions du développement.

## Décision

Option 1 dans les deux cas.

- Le workflow `.github/workflows/ci.yml` a deux jobs, « Python » et « Dashboard ». Ils tournent en parallèle sur `ubuntu-24.04`, pour chaque pull request et chaque push vers `main`.
- Ils lancent les commandes du `CLAUDE.md`, une étape par contrôle, après une installation exacte : `uv sync --locked` échoue si `uv.lock` n'est plus à jour, et `npm ci` installe exactement `package-lock.json`.
- Les versions viennent du dépôt : Python dans `.python-version`, Node dans `web/.node-version` (24), et uv dans `required-version`, dans la section `[tool.uv]` de `pyproject.toml`. En développement, uv et fnm lisent les mêmes fichiers, et uv refuse de tourner hors de sa plage de versions.
- Le workflow reprend les conventions de `secrets.yml` :
  - des permissions en lecture seule ;
  - des actions désignées par l'empreinte de leur commit, avec la version en commentaire, que Dependabot met à jour ;
  - `persist-credentials: false`, pour que le jeton de GitHub ne reste pas sur la machine pendant l'installation des dépendances.
- Un nouveau push sur une pull request annule l'exécution qu'il rend inutile ; sur `main`, toutes les exécutions vont au bout. Chaque job s'arrête au bout de 10 minutes.
- Pas d'essai de bout en bout pour l'instant : il viendra avec Playwright.

## Conséquences

- Chaque pull request affiche trois statuts : « Python », « Dashboard » et celui de gitleaks. Les pull requests de Dependabot passent les mêmes contrôles.
- Changer de version de Node ou de uv se fait dans un seul fichier, suivi par Git.
- Quand le dépôt deviendra public (incrément 6), les deux jobs deviendront des contrôles obligatoires avant toute fusion sur `main`.
