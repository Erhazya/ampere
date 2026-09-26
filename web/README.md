# Tableau de bord d'Ampère

Interface web du jumeau numérique : React, TypeScript et Vite (ADR 010), avec les outils de l'ADR 015.

## Commandes

Dans ce dossier, sous le compte de développement :

- `npm ci` : installe les versions exactes de `package-lock.json`.
- `npm run dev` : serveur de développement sur http://127.0.0.1:5173. Il relaie `/api` vers l'API, qui doit tourner à côté (`uv run ampere api`, à la racine du dépôt ; ADR 016 et 018).
- `npm run test` : les tests (Vitest et Testing Library) ; `npm run test:watch` les relance à chaque modification.
- `npm run lint` : l'analyse statique (ESLint).
- `npm run format` : la mise en forme (Prettier) ; `npm run format:check` vérifie sans rien changer.
- `npm run build` : vérifie les types, puis produit les fichiers statiques dans `dist/`.
- `npm run check` : mise en forme, analyse, tests et build, dans l'ordre de la CI.

La CI prend la version de Node dans `.node-version` (24). En développement, fnm fournit la même version majeure par son alias par défaut (ADR 019).

Pour voir le tableau de bord comme en ligne, servi par l'API : `npm run build`, puis `uv run ampere api --dashboard web/dist` à la racine du dépôt, et http://127.0.0.1:8000 (ADR 018).

## Organisation

- `src/api/` : les appels à l'API, et les hooks React qui les utilisent.
- `src/test/setup.ts` : la préparation commune des tests.
- Styles : CSS Modules, un fichier `Nom.module.css` à côté de chaque composant (ADR 017).
