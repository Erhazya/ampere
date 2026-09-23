# ADR 013 : développement sur le VPS, une fois sécurisé

- **Statut** : acceptée ; remplace l'ADR 012
- **Date** : 23 septembre 2026

## Contexte

L'ADR 012 prévoyait de développer sous Windows. L'auteur préfère finalement ne rien installer sur son PC et poursuivre dans une session Claude Code connectée au VPS, où tournent déjà d'autres services. Le brief, lui, prévoyait un développement sur l'ordinateur de l'auteur, le VPS ne servant qu'aux démos.

## Options envisagées

### 1. Sur le VPS, une fois sécurisé

Le projet Socle commence par sécuriser l'accès : clés SSH, mot de passe désactivé, pare-feu, protection contre la force brute, swap et utilisateur non root. Le développement se fait ensuite avec cet utilisateur.

- **Avantages** : Linux identique à la production ; Docker disponible ; rien à installer sur le PC.
- **Inconvénients** :
  - il faut attendre la fin de la sécurisation ;
  - le développement, les démos et les autres services se partagent 8 Go de mémoire ;
  - les calculs lourds tournent sur un serveur partagé.

### 2. Sur le VPS tout de suite

- **Avantages** : on avance sans attendre le Socle.
- **Inconvénients** : développer avec un compte administrateur sur un serveur pas encore durci expose le code et les autres services.

### 3. En local, sous Windows (ADR 012)

- **Avantages** : conforme au brief ; le VPS ne sert qu'aux démos.
- **Inconvénients** : outils à installer sur le PC ; Docker seulement en CI.

## Décision

Option 1.

## Conséquences

- **Ordre de travail** : l'étape « accès sécurisé » du Socle passe avant la suite d'Ampère.
- **Transfert** : le dossier du projet est copié sur le VPS. Le développement s'y poursuit sous un utilisateur non root, dans une session Claude Code ouverte par l'auteur.
- **Mémoire** : le swap, les limites par conteneur et la règle d'un seul calcul lourd à la fois protègent les autres services. La mémoire réelle sera suivie.
- **Calculs lourds** (renforcement, banc d'essai de l'assistant) : ils sont lancés hors du traitement quotidien, avec des limites de mémoire et de processeur, et seront réévalués si le serveur ne suffit pas.
- **Docker** est disponible sur le VPS : les images peuvent y être testées avant la CI.
- **CI** : elle reste sur GitHub Actions, sous Linux.
