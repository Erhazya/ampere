# ADR 014 : environnement de développement sur le VPS

- **Statut** : acceptée ; complète l'ADR 013
- **Date** : 23 septembre 2026

## Contexte

L'ADR 013 place le développement sur le VPS, sous un compte non root. Le projet d'hébergement (le socle) a depuis précisé ce cadre :

- les projets vivent dans un **compte de développement sans droits d'administration** ;
- la session Claude Code, qui administre le serveur, y lance tout le code tiers (installations, compilations, tests) sous ce compte, dans une tranche mémoire plafonnée ;
- Node.js 20 est déjà installé pour tout le système, et des applications en production en dépendent ;
- un Docker « classique » vaut un accès root, et les ports qu'il publie contournent le pare-feu.

Il reste à choisir comment installer Python, Node.js et Docker pour Ampère.

## Options envisagées

### Python

uv installe lui-même Python 3.13, dans le compte de développement (ADR 009) : aucune option concurrente n'a été retenue.

### Node.js 24

1. **fnm dans le compte de développement** : un gestionnaire de versions rapide, qui installe Node 24 pour ce compte seulement.
2. **nvm dans le compte de développement** : même principe, plus répandu, plus lent à l'ouverture d'un terminal.
3. **Le dépôt NodeSource, pour tout le système** : remplacerait le Node 20 dont dépendent les applications en production.

### Conteneurs

1. **Docker en mode rootless** : le démon Docker tourne sous le compte de développement, sans droits particuliers ; les commandes restent celles de Docker ; un port publié reste derrière le pare-feu.
2. **Podman sans root** : même isolement, mais un outil différent de celui de la production.
3. **Uniquement dans la CI** : rien à installer, mais aucune image testable avant un push.
4. **Accès au Docker du système** : le plus simple, mais le compte devient équivalent à root.

## Décision

- **Python 3.13 par uv**, dans le compte de développement.
- **Node.js 24 par fnm**, dans le compte de développement.
- **Docker en mode rootless** pour le développement, installé avec l'incrément des images Docker.

## Conséquences

- Aucun outil du projet n'est installé pour tout le système ; le Node.js 20 du système n'est pas touché.
- Les commandes de `CLAUDE.md` supposent l'environnement du compte de développement (uv et fnm dans son `PATH`).
- Les images de production sont toujours construites par la CI et publiées sur GHCR ; Docker rootless sert à les essayer avant un push.
- Le dépôt GitHub est d'abord privé, puis rendu public à la fin de l'étape 1.
