# ADR 016 : accès du tableau de bord à l'API, même origine et préfixe /api

- **Statut** : acceptée ; en partie remplacée par l'[ADR 018](018-routes-de-l-api-sous-api.md). L'API déclare désormais elle-même ses routes sous `/api`, le relais de Vite ne retire plus le préfixe, et en ligne l'API sert aussi les fichiers du tableau de bord.
- **Date** : 25 septembre 2026

## Contexte

Le tableau de bord (React, dans `web/`) lit ses données dans l'API FastAPI (ADR 010). En développement, deux serveurs tournent : celui de Vite pour le tableau de bord et uvicorn pour l'API, sur deux ports différents.

Un navigateur bloque par défaut les requêtes d'une page vers une autre origine, c'est-à-dire un autre domaine, un autre port ou un autre protocole. Le CORS est l'autorisation qu'un serveur donne pour lever ce blocage.

En ligne, la plateforme du portfolio place chaque démo derrière un reverse proxy, sous une seule adresse.

## Options envisagées

1. **Même origine, préfixe `/api`**
   - **Avantages** : aucune configuration CORS ; une seule adresse et un seul certificat. L'API ignore le préfixe, que le relais retire.
   - **Inconvénients** : il faut un relais dans chaque environnement : le serveur de Vite en développement, le reverse proxy en ligne.
2. **CORS dans FastAPI**
   - **Avantages** : le tableau de bord appelle l'API directement, sans relais.
   - **Inconvénients** : une liste d'origines autorisées à tenir à jour pour chaque environnement, et une requête de vérification préalable pour certains appels.
3. **Un sous-domaine pour l'API**
   - **Avantages** : les deux services sont nettement séparés.
   - **Inconvénients** : CORS obligatoire, et deux noms et deux certificats à gérer.

## Décision

Option 1. Le tableau de bord appelle toujours des chemins relatifs sous `/api`, par exemple `/api/healthz`. En développement, le serveur de Vite relaie `/api` vers l'API, sur `127.0.0.1:8000`, et retire le préfixe. En ligne, le reverse proxy fera de même. L'API garde ses routes à la racine.

## Conséquences

- L'API n'a pas de middleware CORS.
- Le relais de développement se règle dans `web/vite.config.ts`.
- Le déploiement devra reproduire la même règle : `/api/…` vers l'API sans le préfixe, tout le reste vers les fichiers du tableau de bord.
- Les tests du tableau de bord simulent les réponses de `/api`, sans serveur.
