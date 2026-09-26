# ADR 018 : l'API sert ses routes sous /api, et en ligne les fichiers du tableau de bord

- **Statut** : acceptée ; remplace une partie de l'[ADR 016](016-acces-du-tableau-de-bord-a-l-api.md), celle qui fait retirer le préfixe `/api` par un relais
- **Date** : 25 septembre 2026

## Contexte

D'après l'ADR 016, le tableau de bord appelle des chemins sous `/api`, sur sa propre origine. L'API, elle, gardait ses routes à la racine : en développement, le serveur de Vite retirait le préfixe avant de relayer, et en ligne le reverse proxy devait faire de même.

La relecture de la PR n° 7 a relevé deux problèmes :

- En ligne, cette règle revenait à la plateforme du portfolio : `/api/…` vers l'API sans le préfixe, tout le reste vers les fichiers du tableau de bord. Or le contrat d'hébergement de la plateforme ne prévoit aucune règle propre à une démo. Elle fournit un sous-domaine et le HTTPS, et relie ce nom au port de la démo.
- L'API ignorait qu'on la servait sous `/api`. Sa documentation (`/docs`) et son schéma (`/openapi.json`) ne passaient pas par le relais. Et quand FastAPI redirige un chemin terminé par une barre, par exemple `/healthz/` vers `/healthz`, la redirection sortait de `/api`.

## Options envisagées

1. **L'API possède `/api`**
   - **Avantages** : l'API déclare elle-même ses routes sous `/api`, documentation comprise, et en ligne elle sert aussi les fichiers du tableau de bord. Un seul processus sert toute la démo, et la plateforme n'a qu'à relier le sous-domaine au port. Vite relaie sans rien modifier.
   - **Inconvénients** : l'API sert aussi des fichiers et règle leur cache, un travail qu'on confie d'habitude au serveur web. Son image Docker doit contenir le build du tableau de bord. Et si le processus de l'API s'arrête, la page du tableau de bord tombe avec lui : un nouveau visiteur voit la page d'erreur de la plateforme, pas l'état « Unavailable ».
2. **Garder le relais, et lancer l'API avec `root_path=/api`**
   - **Avantages** : la documentation et les redirections restent sous `/api`, sans toucher aux routes.
   - **Inconvénients** : ouverte directement sur le port 8000, la documentation ne trouve plus son schéma. La question des fichiers en ligne reste entière.
3. **Reporter au déploiement**
   - **Avantages** : rien à changer maintenant.
   - **Inconvénients** : l'ADR 016 décrit une règle que la plateforme ne fournira pas, et le code la suit jusqu'à l'incrément du déploiement.

## Décision

Option 1.

- Les routes de l'API sont déclarées sous `/api`, avec un `APIRouter(prefix="/api")`. La documentation suit : `/api/docs`, `/api/redoc` et le schéma `/api/openapi.json`.
- `/healthz` reste à la racine, où la plateforme l'attend pour chaque démo. La même fonction répond sur `/api/healthz`, que le tableau de bord interroge.
- Avec l'option `--dashboard`, par exemple `ampere api --dashboard web/dist`, l'API sert aussi le build du tableau de bord, avec `app.frontend()` de FastAPI. FastAPI ne cherche un fichier que si aucune route ne correspond au chemin. Les chemins de l'API gardent donc leurs réponses : 405 pour une mauvaise méthode, redirection pour une barre finale, 404 en JSON pour un chemin inconnu. Il n'y a pas de page de repli : un chemin inconnu reçoit un 404.
- Au démarrage, `create_app()` refuse un dossier qui n'est pas un build : sans `index.html` lisible dans le dossier même, avec un `package.json` (c'est le dossier source), ou avec une entrée `api`, puisque ce chemin appartient à l'API. En ligne, le build ne change pas pendant que l'API tourne : un chemin sous `/api` n'y est donc jamais pris pour un fichier.
- Les fichiers du build partent avec `Cache-Control: no-cache`, sauf ceux de `assets/` : le navigateur les revérifie à chaque visite, et reçoit un 304 si leur date et leur taille n'ont pas changé. Après un déploiement, il reçoit donc la nouvelle page, avec les nouveaux noms de fichiers. Les fichiers de `assets/`, dont le nom change avec le contenu, gardent le cache par défaut. L'en-tête vient d'une dépendance du routeur des fichiers : les réponses de l'API n'en reçoivent jamais.
- Sans l'option, l'API ne sert que ses routes ; c'est le cas en développement, où Vite sert le tableau de bord.
- En développement, le serveur de Vite relaie `/api/…` vers l'API, sur `127.0.0.1:8000`, sans rien retirer. Il garde l'en-tête `Host` du navigateur, pour que les redirections de l'API restent sur son origine.

## Conséquences

- L'ADR 016 reste valable pour la même origine et l'absence de CORS.
- La plateforme relie le sous-domaine d'Ampère au port de l'API, sans règle propre à Ampère. Elle doit interroger `/healthz` en GET : les routes de FastAPI répondent 405 à HEAD.
- Le code du tableau de bord ne change pas : il appelle toujours `/api/healthz`.
- L'image Docker de l'incrément 5 contiendra le build du tableau de bord et lancera `ampere api --dashboard <dossier>`. Elle devra garder les vraies dates des fichiers : Starlette calcule l'ETag à partir de la date et de la taille, et deux `index.html` de même taille et de même date seraient pris l'un pour l'autre.
- Pour voir en local le tableau de bord comme en ligne : `npm run build` dans `web/`, puis `uv run ampere api --dashboard web/dist` à la racine du dépôt, et http://127.0.0.1:8000.
- Si le tableau de bord gagne un jour plusieurs pages gérées dans le navigateur, il faudra renvoyer `index.html` pour les chemins inconnus hors de `/api`. Le repli `fallback="index.html"` de `app.frontend()` ne suffira pas tel quel : il répondrait aussi à un navigateur qui ouvre un chemin inconnu sous `/api`.
