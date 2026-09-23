# ADR 003 : tarif de la v1 au prix spot, publié par SMARD

- **Statut** : acceptée
- **Date** : 23 septembre 2026

## Contexte

Pour calculer les factures, la v1 a besoin d'un tarif. Avec un prix fixe, un kWh stocké vaut la même chose à toute heure. Le seul gain d'une batterie est alors de stocker le surplus solaire au lieu de le revendre bon marché, et la règle simple le fait déjà : l'optimisation (MILP) n'aurait presque rien à montrer.

Une fois le prix spot choisi, la vérification des licences a montré un obstacle. Les prix du marché de la veille ne figurent pas dans la [liste des données d'ENTSO-E librement réutilisables](https://transparencyplatform.zendesk.com/hc/en-us/articles/40921911218961-Legal-Terms-and-Conditions). Selon ses conditions d'utilisation, les republier demanderait l'accord de leur producteur, la bourse EPEX SPOT.

## Options envisagées

### Pour le tarif

1. **Prix spot dynamique** : chaque pas de temps est facturé au prix du marché de la veille, plus l'acheminement et les taxes.
   - Avantages : les prix varient beaucoup (nuit et jour, pics d'hiver, prix négatifs l'été), donc l'optimisation a de vrais gains à trouver.
   - Inconvénients : offre encore rare chez les ménages français.
2. **Heures pleines et heures creuses** : le tarif le plus répandu en France.
   - Avantages : familier et simple à expliquer.
   - Inconvénients : un seul écart de prix par jour, donc des gains modestes ; la règle simple fait presque aussi bien que la MILP.
3. **Tempo** : trois couleurs de jours et deux plages horaires ; les jours rouges d'hiver sont très chers.
   - Avantages : écarts de prix spectaculaires.
   - Inconvénients : calendrier à ingérer ; une vingtaine de jours décisifs par an seulement.

### Pour la source des prix spot

1. **SMARD**, la plateforme de la Bundesnetzagentur (le régulateur allemand de l'énergie). Elle republie les prix d'ENTSO-E, France comprise, sous licence CC BY 4.0, sans clé, chaque jour.
   - Avantages : le tableau de bord peut afficher les prix ; aucun secret à gérer.
   - Inconvénients : source indirecte, donc on s'appuie sur la licence affichée par SMARD ; API documentée seulement par la communauté.
2. **ENTSO-E sans republier** : les prix ne servent qu'aux calculs internes.
   - Avantages : source primaire officielle.
   - Inconvénients : clé d'API à demander ; le tableau de bord ne pourrait pas afficher la courbe des prix.
3. **ENTSO-E avec l'autorisation d'EPEX SPOT**.
   - Avantages : situation propre si l'accord arrive.
   - Inconvénients : réponse incertaine, sans délai garanti ; la démo attend en attendant.
4. **Revenir à Tempo**, dont les prix sont publics.
   - Avantages : aucune question de licence sur les prix.
   - Inconvénients : ceux du tarif Tempo ci-dessus, plus un compte RTE pour obtenir le calendrier.

## Décision

Tarif au prix spot pour la v1. Les prix viennent de SMARD (zone France, filtre `254`), sous licence CC BY 4.0.

## Conséquences

- **Facture d'une maison** : énergie achetée × (prix spot + composantes fixes par kWh), TVA comprise. Le surplus est revendu au tarif d'achat fixe ; l'abonnement est affiché, mais exclu des comparaisons.
- **Mention obligatoire** : « Bundesnetzagentur | SMARD.de ».
- **Plus aucune clé d'API** en v1.
- **Robustesse** : l'API de SMARD n'a pas de documentation officielle, donc son schéma est contrôlé à chaque ingestion. Ember (CC BY 4.0) sert de recoupement pour l'historique.
- **Version complète** : le prix spot sera comparé aux tarifs base, heures pleines et heures creuses, et Tempo.
