# ADR 010 : tableau de bord en React et ECharts

- **Statut** : acceptée
- **Date** : 23 septembre 2026

## Contexte

Le brief demande un tableau de bord en TypeScript, capable d'afficher de longues séries, soit des dizaines de milliers de points, grâce à un sous-échantillonnage de type LTTB. Il doit aussi se charger vite, car un recruteur n'attendra pas.

## Options envisagées

### 1. React + ECharts, construits avec Vite

- **Avantages** : React est le framework le plus demandé par les recruteurs, et ECharts sait sous-échantillonner les longues séries (LTTB) nativement.
- **Inconvénients** : React demande plus de code répétitif, et ECharts pèse lourd s'il n'est pas importé module par module.

### 2. Svelte + uPlot

- **Avantages** : application très légère et rapide ; uPlot est ultra-rapide pour les séries temporelles.
- **Inconvénients** : marché de l'emploi plus étroit ; uPlot est minimaliste, donc plus d'interactions à coder soi-même.

### 3. Vue + ECharts

- **Avantages** : courbe d'apprentissage douce, bonne présence en France.
- **Inconvénients** : moins demandé que React dans les offres d'emploi.

Un tableau de bord en Python (Streamlit, Dash) a été écarté d'emblée, car le brief demande TypeScript.

## Décision

React et TypeScript, construits avec Vite, avec ECharts importé module par module.

## Conséquences

- Le tableau de bord vit dans `web/`. Son formatage, son analyse statique et ses tests entrent dans la CI ; les outils seront choisis à l'étape 1.
- Les longues séries utilisent le sous-échantillonnage LTTB d'ECharts.
- Les textes de l'interface sont regroupés dès le départ : anglais d'abord, français ensuite.
