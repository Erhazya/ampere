# ADR 021 : démo en ligne, déployée par la plateforme du socle

- **Statut** : acceptée ; complète les ADR 018 et 020
- **Date** : 26 septembre 2026

## Contexte

La CI publie l'image de la démo sur GHCR à chaque push vers `main` (ADR 020). Cette image sert l'API et le tableau de bord (ADR 018), et répond sur `/healthz`.

Le socle a mis en place une plateforme de démos minimale (ADR 021 du socle) :

- un compte `demos`, avec son propre Docker rootless ;
- un minuteur qui, chaque minute, tire l'image `main` de chaque démo. Quand son empreinte change, il recrée les conteneurs, et si le nouveau conteneur ne devient pas sain, il revient à l'image d'avant ;
- Caddy sur l'hôte, qui reçoit les visiteurs en HTTPS.

Son contrat demande à chaque démo une image publique et un fichier Compose. Ce fichier désigne l'image par `${DEMO_IMAGE}`, publie un seul port, sur `127.0.0.1:${DEMO_PORT}`, et fixe les limites de mémoire et de processeur. La plateforme fournit les deux variables.

Au repos, le conteneur occupe 33 Mo (ADR 020). Le budget de l'API sur le VPS est de 400 Mo (conception, § 8.5).

La documentation interactive de FastAPI charge Swagger UI (`/api/docs`) et ReDoc (`/api/redoc`) depuis jsDelivr, dans des versions non figées (`@5` et `@2`), avec un script ou un style en ligne. ReDoc demande aussi les polices de Google, et les deux pages prennent leur icône sur le site de FastAPI.

## Options envisagées

### Adresse de la démo

1. **`ampere.146-19-168-222.sslip.io`**
   - **Avantages** : gratuit, et construit comme l'adresse du trailer du portfolio.
   - **Inconvénients** : un nom long, qui contient l'adresse IP. Tous les noms sslip.io partagent un même quota de certificats chez Let's Encrypt.
2. **Un vrai domaine**
   - **Avantages** : un nom court, avec son propre quota.
   - **Inconvénients** : environ 10 € par an, hors du budget de 0 € (conception, § 4).

### Visibilité du paquet GHCR

1. **Public tout de suite, avant le dépôt**
   - **Avantages** : le serveur tire l'image sans aucun secret, comme le prévoit le contrat.
   - **Inconvénients** : c'est irréversible, et le code se lit dans l'image quelques jours avant l'ouverture du dépôt.
2. **Privé jusqu'à l'ouverture du dépôt**, avec un jeton en lecture sur le serveur
   - **Avantages** : rien n'est public avant la fin de l'étape 1.
   - **Inconvénients** : un jeton classique `read:packages` lit tous les paquets privés du compte, et Docker le garde en clair.

### Documentation interactive en ligne

1. **Hors ligne pour l'instant** : Caddy répond 404 sur `/api/docs` et `/api/redoc`
   - **Avantages** : une CSP stricte sur tout le site. Le schéma `/api/openapi.json` reste public.
   - **Inconvénients** : un visiteur ne peut pas essayer l'API dans son navigateur.
2. **Autoriser jsDelivr sur ces deux pages**
   - **Avantages** : rien à changer dans l'application.
   - **Inconvénients** : du code tiers non figé, sans contrôle d'intégrité, s'exécute sur l'origine de la démo.
3. **Servir Swagger UI depuis l'image**
   - **Avantages** : la documentation en ligne, avec une CSP stricte.
   - **Inconvénients** : un incrément de plus avant la mise en ligne, et une dépendance de plus à suivre.

## Décision

Option 1 dans les trois cas.

- `deploy/compose.yaml` décrit la démo en ligne pour la plateforme du socle :
  - un service, `api`, sur l'image `${DEMO_IMAGE}`, relancé par Docker sauf arrêt volontaire (`unless-stopped`) ;
  - le port 8000 publié sur `127.0.0.1:${DEMO_PORT}` seulement. La plateforme a attribué le port 8101 ;
  - 256 Mo de mémoire, un processeur et 64 processus au plus ;
  - un système de fichiers en lecture seule, avec `/tmp` en mémoire, aucune capacité Linux, et `no-new-privileges` ;
  - `FORWARDED_ALLOW_IPS` à `172.16.0.0/12`, la plage des réseaux Docker. Les connexions de Caddy arrivent dans le conteneur depuis la passerelle de son réseau, `172.18.0.1` au premier déploiement, et uvicorn croit alors leurs en-têtes `X-Forwarded-For` et `X-Forwarded-Proto`.
- La démo est en ligne sur `https://ampere.146-19-168-222.sslip.io` depuis le 26 septembre 2026. Caddy y ajoute une CSP qui n'autorise que les fichiers du site. Dans Chromium, la page s'affiche sans aucune violation.
- Le paquet `ghcr.io/erhazya/ampere` est public depuis le même jour.
- En ligne, `/api/docs` et `/api/redoc` répondent 404. En local, la documentation reste disponible.
- Sur chaque pull request, le job « Image » de la CI démarre aussi l'image avec `deploy/compose.yaml`, et vérifie qu'elle répond. L'essai de fumée lance le conteneur sans ces réglages : cette étape vérifie que l'image tourne avec ceux de la démo en ligne.

## Conséquences

- Chaque fusion dans `main` arrive en ligne sans geste : la CI publie l'image, et le serveur la déploie dans la minute qui suit. Le délai et la coupure mesurés sont dans l'ADR 021 du socle.
- Le contrôle de santé de l'image (ADR 020) devient la condition d'un déploiement : une image qui ne devient pas saine en 120 secondes n'est pas gardée.
- Revenir à un commit : `demos 'demo-deploy ampere sha-<commit>'` sur le serveur, puis `demos 'demo-deploy ampere main'` pour suivre `main` de nouveau.
- Un changement de `deploy/compose.yaml` n'arrive pas seul sur le serveur : root le copie depuis `main`, après relecture.
- L'application ne peut écrire que dans `/tmp`, en mémoire. Le traitement quotidien et ses données demanderont un volume, déclaré dans ce fichier.
- Les limites valent pour l'API d'aujourd'hui. Elles monteront avec DuckDB et les données, dans le budget de 400 Mo.
- Tout programme du serveur qui joint `127.0.0.1:8101` passe par la même passerelle, et peut donc fausser l'adresse du visiteur dans les journaux de l'API. Rien dans l'API ne dépend de cette adresse.
- La documentation interactive reviendra en ligne quand l'image servira Swagger UI elle-même.
