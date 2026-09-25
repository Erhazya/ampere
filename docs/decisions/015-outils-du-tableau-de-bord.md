# ADR 015 : outils du tableau de bord

- **Statut** : acceptée ; complète l'ADR 010. Note du 25 septembre 2026 : depuis create-vite 9, le gabarit fournit Oxlint et non plus ESLint. ESLint a été gardé, et les règles JSX que vérifiait Oxlint (les clés des listes, par exemple) viennent d'@eslint-react, compatible avec ESLint 10 (PR n° 7).
- **Date** : 25 septembre 2026

## Contexte

L'ADR 010 retient React et TypeScript, construits avec Vite, pour le tableau de bord. Elle laisse à l'étape 1 le choix de ses outils : le gestionnaire de paquets, le formatage, l'analyse statique et les tests.

Ces outils tournent en local comme dans la CI, à chaque push, au même titre que uv, ruff, mypy et pytest côté Python. Node.js 24 est installé par fnm dans le compte de développement (ADR 014).

## Options envisagées

### Gestionnaire de paquets

1. **npm**
   - **Avantages** : livré avec Node.js, rien à installer de plus. `package-lock.json` fixe les versions exactes, et `npm ci` les réinstalle à l'identique en CI.
   - **Inconvénients** : plus lent, et plus permissif sur les dépendances non déclarées.
2. **pnpm**
   - **Avantages** : plus rapide, et plus strict : un paquet ne peut pas utiliser une dépendance qu'il n'a pas déclarée.
   - **Inconvénients** : un outil de plus à installer et à expliquer, pour un seul dossier `web/`.

### Formatage et analyse statique

1. **ESLint et Prettier**
   - **Avantages** : le standard du marché. Le gabarit React + TypeScript de Vite fournit déjà ESLint, avec les règles propres à React. Prettier se charge seul de la mise en forme.
   - **Inconvénients** : deux outils et deux configurations à accorder.
2. **Biome**
   - **Avantages** : un seul outil, très rapide, pour les deux rôles.
   - **Inconvénients** : moins répandu en entreprise, et certaines règles d'ESLint n'y ont pas d'équivalent.

### Tests

1. **Vitest et Testing Library**
   - **Avantages** : Vitest réutilise la configuration de Vite. Testing Library teste les composants comme un utilisateur les voit (textes, rôles, boutons), plutôt que leur fonctionnement interne.
   - **Inconvénients** : des dépendances de plus, et une page simulée (jsdom) à mettre en place.
2. **Aucun test pour l'instant**
   - **Avantages** : rien à installer. La CI ne vérifie que le typage et le build, jusqu'aux premiers vrais composants.
   - **Inconvénients** : les habitudes se prennent au début ; ajouter les tests plus tard coûte davantage.

## Décision

- **npm**, avec `package-lock.json` versionné et `npm ci` en CI.
- **ESLint** pour l'analyse statique et **Prettier** pour le formatage. Les règles de mise en forme d'ESLint sont désactivées, pour que les deux outils ne se contredisent pas.
- **Vitest et Testing Library** pour les tests des composants.

## Conséquences

- Les commandes du tableau de bord se lancent dans `web/` avec `npm run`. Leurs noms exacts seront fixés avec l'incrément du tableau de bord, puis reportés dans `CLAUDE.md`.
- Pour `web/`, la CI enchaîne l'installation (`npm ci`), le formatage, l'analyse statique, les tests et le build.
- Les tests de bout en bout, dans un vrai navigateur, relèvent d'un autre outil, choisi plus tard.
