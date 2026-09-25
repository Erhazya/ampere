# ADR 017 : styles du tableau de bord en CSS Modules

- **Statut** : acceptée ; complète les ADR 010 et 015
- **Date** : 25 septembre 2026

## Contexte

L'ADR 010 retient React, TypeScript et Vite pour le tableau de bord, et l'ADR 015 ses outils. Aucune ne dit comment écrire les styles.

L'interface doit rester légère et accessible, et suivre l'identité visuelle du portfolio : Ampère en reprend la couleur, l'ambre.

## Options envisagées

1. **CSS Modules**
   - **Avantages** : du CSS standard, un fichier par composant, dont Vite rend les noms de classes uniques. Rien à installer. Couleurs, polices et espacements passent par des variables CSS.
   - **Inconvénients** : plus de fichiers, et pas de catalogue de classes prêtes à l'emploi.
2. **Tailwind CSS**
   - **Avantages** : très répandu et rapide à écrire ; son échelle de valeurs impose une cohérence.
   - **Inconvénients** : une dépendance et une syntaxe de plus, et un JSX chargé de classes.
3. **Une bibliothèque de composants**, Mantine par exemple
   - **Avantages** : formulaires, tableaux et fenêtres prêts à l'emploi, déjà accessibles.
   - **Inconvénients** : lourde et plus difficile à personnaliser ; il reste moins de code propre au projet.

## Décision

Option 1 : CSS Modules, avec des variables CSS globales pour les couleurs, les polices et les espacements.

## Conséquences

- Chaque composant a son fichier `Nom.module.css` à côté de `Nom.tsx`.
- Les variables CSS (`--ground`, `--ink`, `--amber`…) vivent dans une feuille globale, reprise de l'identité visuelle du portfolio.
- Si un composant complexe devient nécessaire (tableau de données, fenêtre modale), une bibliothèque sans style imposé pourra compléter, avec un nouvel ADR.
