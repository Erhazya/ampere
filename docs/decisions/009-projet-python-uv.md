# ADR 009 : projet Python géré avec uv

- **Statut** : acceptée
- **Date** : 23 septembre 2026

## Contexte

Il faut un outil pour trois tâches : fixer la version de Python, créer l'environnement virtuel et verrouiller les versions exactes des dépendances. Il doit fonctionner de la même façon sur le PC, en CI et dans Docker, car la reproductibilité en dépend.

## Options envisagées

### 1. uv

Outil très rapide qui gère la version de Python, l'environnement virtuel, les dépendances et le fichier de verrouillage `uv.lock`.

- **Avantages** : une seule commande pour tout ; très rapide ; standard de fait aujourd'hui ; simple en CI et dans Docker.
- **Inconvénients** : plus jeune que Poetry.

### 2. Poetry

- **Avantages** : mature, très répandu en entreprise.
- **Inconvénients** : plus lent ; ne gère pas la version de Python elle-même.

### 3. pip et venv

- **Avantages** : fournis avec Python, sans outil supplémentaire.
- **Inconvénients** : pas de verrouillage exact des versions, donc une reproductibilité plus faible.

## Décision

uv.

## Conséquences

- `pyproject.toml` et `uv.lock` sont versionnés, et la version de Python est fixée dans le projet.
- La CI et les images Docker utilisent uv.
- uv sera installé sur le PC de développement à l'étape 1.
