# Ampère — consignes permanentes

Projet de portfolio décrit dans `BRIEF.md`, un fichier local non versionné : cahier des charges, périmètre, plan par étapes. Les décisions et l'avancement sont suivis dans `docs/`.

## Règles de travail

- Réponds toujours en français, même dans les messages courts.
- Pose tes questions avec le widget de questions interactif, jamais sous forme de liste en texte.
- On travaille en binôme : présente les options avec leurs avantages et inconvénients, donne ta recommandation, puis laisse-moi décider. Chaque décision importante devient un ADR dans `docs/decisions/`.
- Avance par petits incréments testés, et explique brièvement chaque notion nouvelle.
- Je dois pouvoir expliquer chaque ligne en entretien : jamais de gros bloc de code sans explication.
- Code, noms, commentaires et messages de commit en anglais ; documentation en français, traduite en anglais à chaque version publiée.
- Rédaction : tout texte destiné à des lecteurs (documentation, README, ADR, commits, textes d'interface) suit le skill `ecriture-naturelle` : des phrases concrètes, sans les tics des textes générés.
- Aucun secret dans le dépôt, qui sera public.

## Outils Claude

Les skills et agents ci-dessous sont installés pour la session Claude Code, dans des versions relues et adaptées à ces règles, qui priment sur les leurs. Un outil « sur appel » se lance avec `/nom` ; Claude lit aussi son `SKILL.md` aux moments prévus ici.

- **Python** : `ecc-tdd-workflow` et `ecc-python-testing` pour les tests, `ecc-fastapi-patterns` pour l'API ; relectures par les agents `ecc-python-reviewer`, `ecc-fastapi-reviewer` et `ecc-type-design-analyzer`.
- **Tableau de bord** : `ecc-vite-patterns` et `ecc-react-testing` ; relectures par `ecc-typescript-reviewer` et `ecc-react-reviewer`. Maquettes dans Figma avant les premiers écrans.
- **Mouvement** : `find-animation-opportunities` et `animate`, qui écartent les animations décoratives sur les graphiques et sur les données qu'on lit ; `review-animations` (sur appel) sur chaque pull request qui touche l'interface.
- `design-taste-frontend` ne sert pas ici : il s'exclut lui-même des tableaux de bord.
- **Tests de bout en bout** : Playwright, avec `ecc-e2e-testing`.
- **Images Docker** : `ecc-docker-patterns`.
- **Décisions** : `ecc-architecture-decision-records`, au format des ADR du projet.
- **Pull requests** : `/ecc-pr` pour la préparer ; avant la fusion, `/code-review`, `/security-review` et l'agent `ecc-pr-test-analyzer` ; l'agent `ecc-silent-failure-hunter` sur le code qui gère des erreurs.

## Commandes et conventions

> Les commandes Python et de qualité sont réelles depuis l'incrément 1 de l'étape 1. Celles marquées « à confirmer » arriveront avec les incréments suivants.

### Installation

- Prérequis, dans le compte de développement du VPS (ADR 013 et 014) : Git, uv (qui installe lui-même Python 3.13), Node.js 24 par fnm, et Docker en mode rootless, démarré à la demande avec `systemctl --user start docker`. La CI reprend ces versions : Python depuis `.python-version`, Node 24 depuis `web/.node-version`, et exactement la version de uv du compte de développement, écrite dans `.github/workflows/ci.yml` et `deploy/Dockerfile` (ADR 019 et 020).
- `uv sync` : crée l'environnement Python et installe les versions exactes de `uv.lock`.
- `npm ci`, dans `web/` : installe les versions exactes de `package-lock.json` pour le tableau de bord (ADR 015).

### Lancement en local (noms à confirmer)

- `uv run ampere data init` : crée la couche brute des données, une seule fois, dans `AMPERE_DATA` (par défaut `data/`) (ADR 022).
- `uv run ampere ingest smard` : récupère les prix spot de SMARD depuis le 1er juillet 2023, les archive, reconstruit `data/clean/smard/prices.parquet` et le contrôle. Le fichier n'est remplacé que si ses lignes sont valides ; une ligne invalide ou une erreur de contrôle donne le code de sortie 1. `--full` redemande toutes les semaines, pas seulement celles qui peuvent encore changer (ADR 023).
- `uv run ampere ingest eco2mix` : récupère éCO2mix sur ODRÉ depuis le 1er juillet 2023, mois par mois, pour la France (intensité CO₂, consommation, prévision J-1 de RTE) et Auvergne-Rhône-Alpes (solaire), garde chaque version reçue et reconstruit `data/clean/eco2mix/measures.parquet` mois par mois, chacun depuis la version la plus avancée qui le couvre en entier ; `--full` redemande les mois qu'ODRÉ peut encore donner en entier (ADR 024).
- `uv run ampere ingest openmeteo` : récupère la météo de Lyon sur Open-Meteo, avec le modèle ECMWF IFS : la météo observée, mois par mois depuis le 1er juillet 2023, et le run de 0 h UTC de chaque jour depuis le 14 mars 2024, sur deux jours. Il garde chaque réponse, sauf celles qui ne diffèrent que par `generationtime_ms`, reconstruit `data/clean/openmeteo/weather.parquet`, heure par heure, et le contrôle. Six runs qu'Open-Meteo n'a pas, en entier ou en partie, sont listés dans `KNOWN_GAPS` : ils ne sont pas des erreurs. `--full` redemande tous les mois et tous les runs, ces six compris (ADR 025).
- `uv run ampere daily` : traitement quotidien complet (ingestion avec rattrapage, contrôles, simulation, prévision, export).
- `uv run ampere api` : lance l'API FastAPI sur `127.0.0.1:8000`, avec ses routes sous `/api`. Avec `--dashboard web/dist`, elle sert aussi le tableau de bord construit, comme en ligne (ADR 018).
- `npm run dev`, dans `web/` : lance le tableau de bord sur `127.0.0.1:5173`, qui relaie `/api` vers l'API (ADR 016 et 018).
- `uv run ampere reproduce` : régénère tous les chiffres du README à partir des données brutes archivées.
- `docker build --file deploy/Dockerfile --tag ampere .`, à la racine, puis `deploy/smoke-test.sh ampere` : construit l'image de la démo et vérifie qu'un conteneur démarre et répond (ADR 020).
- `deploy/compose-test.sh ampere` : démarre cette image avec `deploy/compose.yaml`, les réglages de la démo en ligne, la vérifie, puis l'arrête (ADR 021). Il prend le port 18101 : sur ce serveur, le port 8101 et les suivants appartiennent aux démos en ligne.

### Tests et qualité (les mêmes en CI, sur chaque pull request et chaque push vers `main` ; ADR 019)

- `uv run pytest` : tests Python (bilan énergétique, tarifs, changements d'heure, données, non-régression).
- `uv run ruff format --check .` puis `uv run ruff check .` : formatage et analyse statique.
- `uv run mypy src tests` : vérification des types, en mode strict.
- Tableau de bord, dans `web/` : `npm run format:check` (Prettier), `npm run lint` (ESLint), `npm run test` (Vitest et Testing Library), puis `npm run build` ; `npm run check` enchaîne les quatre (ADR 015).

### Déploiement

- La fusion d'une pull request sur `main` déclenche la CI, qui publie l'image sur GHCR (ADR 020). Le serveur regarde chaque minute si cette image a changé, la déploie avec `deploy/compose.yaml`, et la garde si elle devient saine en 120 secondes : la démo est sur https://ampere.146-19-168-222.sslip.io (ADR 021, et ADR 021 du socle).
- Sur le serveur, en root : `demos 'demo-deploy ampere sha-<commit>'` revient à un commit, avec son empreinte complète (`gh api repos/Erhazya/ampere/commits/<commit> --jq .sha`), et `demos 'demo-deploy ampere main'` fait suivre `main` de nouveau (procédure de retour arrière du socle). Après la fusion d'un changement de `deploy/compose.yaml`, root le copie dans `/etc/demos/ampere/`, puis lance le déploiement à la main et en vérifie le résultat (ADR 021 du socle).
- Sur le VPS, un minuteur systemd lance le traitement quotidien chaque jour à 14 h, heure de Paris, avec `Persistent=true` (ADR 011).
- Le développement se fait sur le VPS, dans un compte de développement sans droits d'administration : toutes les commandes du projet (installations, compilations, tests) s'y exécutent, jamais en root (ADR 013 et 014).

### Conventions du projet

- **Git** : Conventional Commits ; une branche par fonctionnalité, fusionnée par pull request. La copie de travail du VPS a `core.fileMode` à `false` : Git n'y voit pas le bit d'exécution, et un script se rend exécutable avec `git update-index --chmod=+x <fichier>`.
- **Temps** : dates stockées en UTC, pas de 15 min, heure de Paris seulement à l'affichage.
- **Période de test** (1er juillet 2025 au 30 juin 2026) : jamais utilisée hors de l'évaluation finale (ADR 007).
- **Données** : les données brutes sont conservées telles que reçues, avec leur date de réception, dans la couche brute de `AMPERE_DATA` (par défaut `data/`) ; aucun trou n'est comblé en silence (ADR 022).
- **Secrets** : aucun dans le dépôt ; en v1, aucune source ne demande de clé.
- **Sources à citer** dans l'interface et le README : « RTE, éCO2mix », « Enedis, open data », « Weather data by Open-Meteo.com », « PVGIS, Commission européenne (JRC) », « Bundesnetzagentur | SMARD.de ».
- **Documentation** : `docs/conception.md`, `docs/decisions/` (ADR), `docs/mesures.md`, `docs/retour-experience.md` et `docs/glossaire.md`, à compléter à chaque notion nouvelle.
- **Ton des documents publics** : ils parlent du projet, pas du niveau de l'auteur.

### Avancement

- Étape 0 (conception) terminée le 23 septembre 2026.
- Étape 1 (fondations : dépôt, CI, squelette déployé) terminée le 27 septembre 2026, menée sur le VPS depuis le 23 septembre (ADR 013 et 014).
  - Python 3.13 par uv, Node.js 24 par fnm, Docker rootless ; outils de qualité ruff, mypy et pytest.
  - Tableau de bord : npm, ESLint et Prettier, Vitest et Testing Library (ADR 015).
  - Dépôt GitHub public depuis le 27 septembre 2026. `main` est protégée pour tout le monde, administrateur compris : pull request obligatoire, et contrôles « Python », « Dashboard », « Image » et gitleaks verts sur une branche à jour (ADR 019).
  - Le brief, les prompts et `TRANSFERT.md` restent locaux, ignorés par Git.
- Incréments 1 (squelette Python) et 2 (API minimale) terminés.
- Incrément 3 terminé : squelette `web/`, outils, relais `/api` (ADR 016), styles en CSS Modules (ADR 017), couche qui interroge l'API, et écran d'état dessiné d'abord dans Figma.
- Ensuite, l'API déclare ses routes sous `/api` et, en ligne, sert aussi les fichiers du tableau de bord (ADR 018).
- Incrément 4 terminé : CI GitHub Actions, un job Python et un job tableau de bord (ADR 019).
- Incrément 5 terminé : image Docker vérifiée sur chaque pull request, publiée sur GHCR depuis `main` (ADR 020).
- Incrément 7 : démo en ligne depuis le 26 septembre 2026 sur https://ampere.146-19-168-222.sslip.io, déployée par la plateforme du socle ; paquet GHCR public (ADR 021).
- Déploiement automatique et retour arrière essayés le 26 septembre 2026 (mesure 8 du socle) ; la mesure définitive suit la fusion suivante.
- Incrément 6 terminé le 27 septembre 2026 : dépôt public, `main` protégée, analyse des secrets de GitHub, alertes et correctifs Dependabot, actions limitées à une liste et désignées par leur empreinte, workflows des comptes extérieurs soumis à approbation.
- Étape 2 (données) commencée le 27 septembre 2026. Incrément 2.1, les fondations des données (ADR 022) : les journées de Paris bornées en UTC, la couche brute (durable, verrouillée, vérifiable, créée une fois avec `RawStore.create`) avec son manifeste, et l'accès HTTP aux sources, qui ne laisse passer que des réponses attendues.
- Incrément 2.2, les prix de SMARD de bout en bout (ADR 023) : 113 760 quarts d'heure depuis le 1er juillet 2023, au pas du marché de leur époque, contrôlés jour par jour ; le premier passage fait 172 requêtes en 100 s, les suivants 4 (5 le dimanche après-midi), en 5 s. Une semaine est relue jusqu'à 14 jours après sa fin, puis tant que sa réponse gardée est illisible ou incomplète.
- Incrément 2.3, éCO2mix de bout en bout (ADR 024) : 568 700 valeurs depuis le 1er juillet 2023, au pas publié (30 min en consolidé et en définitif, 15 min en temps réel) et dans la version la plus avancée de leur mois ; le premier passage fait 82 requêtes, les suivants 6, en 15 s. Le nettoyé se reconstruit mois par mois, depuis le jeu choisi pour chacun. Aux changements d'heure, ODRÉ donne au printemps une heure qui n'existe pas, écartée, et il lui manque à l'automne 4 quarts d'heure, laissés vides.
- Incrément 2.4, Open-Meteo de bout en bout (ADR 025) : la météo de Lyon d'ECMWF IFS, observée depuis le 30 juin 2023 et prévue par le run de 0 h UTC de chaque jour depuis le 14 mars 2024, soit 432 779 valeurs heure par heure. Le premier passage fait 968 requêtes en 11 minutes, les suivants de une à trois, en 6 s. Une réponse qui ne diffère que par `generationtime_ms` n'ajoute rien au brut. Six runs manquent chez Open-Meteo, en août 2025 et le 23 juin 2026 : l'étape 5 dira comment évaluer les prévisions de ces jours.
- À faire fin octobre 2026, à la publication du consolidé de juillet à septembre : vérifier le placement des demi-heures nationales d'éCO2mix contre le temps réel gardé (ADR 024, `docs/mesures.md`). Le même jour, un `uv run ampere ingest openmeteo --full` dira si Open-Meteo a comblé ses six runs (ADR 025).
- Incrément 2.4 fusionné le 27 septembre 2026 (PR n° 21) ; la démo tourne avec cette version.
- Choix de l'auteur, le 27 septembre 2026 : juste après l'incrément 2.7 (le traitement quotidien en ligne), un premier écran « Data » sur la démo montre les derniers jours de prix, d'intensité CO₂, de solaire régional et de météo, mis à jour chaque jour. Il a sa maquette Figma et son ADR ; le notebook d'exploration (2.8) vient ensuite.
- Prochaine action : l'incrément 2.5, Enedis.
