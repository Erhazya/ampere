# Ampère — consignes permanentes

Projet de portfolio décrit dans `BRIEF.md`, un fichier local non versionné : cahier des charges, périmètre, plan par étapes. Les décisions et l'avancement sont suivis dans `docs/`.

## Règles de travail

- Réponds toujours en français, même dans les messages courts.
- Pose tes questions avec le widget de questions interactif, jamais sous forme de liste en texte.
- On travaille en binôme : présente les options avec leurs avantages et inconvénients, donne ta recommandation, puis laisse-moi décider. Chaque décision importante devient un ADR dans `docs/decisions/`.
- Avance par petits incréments testés, et explique brièvement chaque notion nouvelle.
- Je dois pouvoir expliquer chaque ligne en entretien : jamais de gros bloc de code sans explication.
- Code, noms, commentaires et messages de commit en anglais ; documentation en français, traduite en anglais à chaque version publiée.
- Aucun secret dans le dépôt, qui sera public.

## Commandes et conventions

> Les commandes Python et de qualité sont réelles depuis l'incrément 1 de l'étape 1. Celles marquées « à confirmer » arriveront avec les incréments suivants.

### Installation

- Prérequis, dans le compte de développement du VPS (ADR 013 et 014) : Git, uv (qui installe lui-même Python 3.13), Node.js 24 par fnm, et Docker en mode rootless.
- `uv sync` : crée l'environnement Python et installe les versions exactes de `uv.lock`.
- `npm ci`, dans `web/` : installe les dépendances du tableau de bord (gestionnaire de paquets à confirmer).

### Lancement en local (noms à confirmer)

- `uv run ampere daily` : traitement quotidien complet (ingestion avec rattrapage, contrôles, simulation, prévision, export).
- `uv run ampere api` : lance l'API FastAPI.
- `npm run dev`, dans `web/` : lance le tableau de bord.
- `uv run ampere reproduce` : régénère tous les chiffres du README à partir des données brutes archivées.

### Tests et qualité (les mêmes en CI, à chaque push)

- `uv run pytest` : tests Python (bilan énergétique, tarifs, changements d'heure, données, non-régression).
- `uv run ruff format --check .` puis `uv run ruff check .` : formatage et analyse statique.
- `uv run mypy src tests` : vérification des types, en mode strict.
- Tableau de bord : formatage, analyse statique et tests, avec des outils à choisir à l'étape 1.

### Déploiement

- La fusion d'une pull request sur `main` déclenche GitHub Actions : CI, images Docker publiées sur GHCR, puis déploiement sur le VPS, selon un mécanisme à aligner sur le socle.
- Sur le VPS, un minuteur systemd lance le traitement quotidien chaque jour à 14 h, heure de Paris, avec `Persistent=true` (ADR 011).
- Le développement se fait sur le VPS, dans un compte de développement sans droits d'administration : toutes les commandes du projet (installations, compilations, tests) s'y exécutent, jamais en root (ADR 013 et 014).

### Conventions du projet

- **Git** : Conventional Commits ; une branche par fonctionnalité, fusionnée par pull request.
- **Temps** : dates stockées en UTC, pas de 15 min, heure de Paris seulement à l'affichage.
- **Période de test** (1er juillet 2025 au 30 juin 2026) : jamais utilisée hors de l'évaluation finale (ADR 007).
- **Données** : les données brutes sont conservées telles que reçues, avec leur date de réception ; aucun trou n'est comblé en silence.
- **Secrets** : aucun dans le dépôt ; en v1, aucune source ne demande de clé.
- **Sources à citer** dans l'interface et le README : « RTE, éCO2mix », « Enedis, open data », « Weather data by Open-Meteo.com », « PVGIS, Commission européenne (JRC) », « Bundesnetzagentur | SMARD.de ».
- **Documentation** : `docs/conception.md`, `docs/decisions/` (ADR), `docs/mesures.md`, `docs/retour-experience.md` et `docs/glossaire.md`, à compléter à chaque notion nouvelle.
- **Ton des documents publics** : ils parlent du projet, pas du niveau de l'auteur.

### Avancement

- Étape 0 (conception) terminée le 23 septembre 2026.
- Étape 1 (fondations : dépôt, CI, squelette déployé) en cours, sur le VPS depuis le 23 septembre 2026 (ADR 013 et 014).
  - Python 3.13 par uv, Node.js 24 par fnm, Docker rootless ; outils de qualité ruff, mypy et pytest.
  - Dépôt GitHub privé jusqu'à la fin de l'étape 1, puis public.
  - Le brief, les prompts et `TRANSFERT.md` restent locaux, ignorés par Git.
- Prochaine action : suivre les incréments de `TRANSFERT.md` (section 7), à partir du squelette Python.
