# Ampère

Le jumeau numérique énergétique d'un quartier de 200 maisons, alimenté par de vraies données publiques françaises.

> **Statut : en construction.** La conception et les fondations sont terminées. La collecte des données est en cours : chaque jour à 14 h, heure de Paris, le serveur récupère et contrôle cinq sources, et garde chaque réponse brute telle que reçue. Un premier écran est en ligne sur <https://ampere.146-19-168-222.sslip.io> : les sept derniers jours et le lendemain du prix spot, de la consommation et de la prévision de RTE, de l'intensité CO₂, du solaire régional et de la température à Lyon, mis à jour chaque jour.

[English version](README.md)

## Ce qu'il fera

Ampère simule un quartier fictif de 200 maisons près de Lyon. Tout ce qui l'entoure est réel : la météo, les prix spot de l'électricité, l'intensité carbone du réseau et la consommation moyenne des foyers de la région. Chaque jour, il :

- prévoira la consommation du quartier pour le lendemain ;
- comparera des stratégies de pilotage des batteries sur la facture et les émissions de CO₂ : sans batterie, avec une règle simple, et par optimisation mathématique (MILP) ;
- montrera les résultats dans un tableau de bord interactif, avec des scénarios.

Tous les résultats seront mesurés sur une année complète de test, de juillet 2025 à juin 2026, jamais utilisée pendant le développement.

## Choix clés

| Choix | Pourquoi |
|---|---|
| Vraies courbes de consommation résidentielle, en open data Enedis | Les prévisions sont notées sur de vraies mesures, pas sur des données synthétiques |
| Tarif dynamique au prix spot, publié par SMARD | Avec un prix fixe, une règle simple est déjà presque optimale : l'optimisation n'aurait rien à montrer |
| Simulation au pas de 15 min, prévisions notées sur des sommes de 30 min | Suivre le marché sans noter de détail inventé |
| Une année de test, mise sous clé | Une évaluation honnête, sur toutes les saisons |

Le raisonnement complet est dans la [conception](docs/conception.md), les [décisions](docs/decisions/) et le [glossaire](docs/glossaire.md).

## Feuille de route

| Étape | Contenu | Statut |
|---|---|---|
| 0 | Conception | Terminée |
| 1 | Fondations : dépôt, CI, squelette déployé | Terminée |
| 2 | Données : ingestion quotidienne et contrôles de qualité | En cours |
| 3 | Simulation : maisons, panneaux solaires, batteries | À faire |
| 4 | Pilotage : règle simple contre MILP | À faire |
| 5 | Prévisions : références naïves contre LightGBM | À faire |
| 6 | Tableau de bord et première version publique | À faire |

## Sources de données

| Source | Usage | Licence |
|---|---|---|
| RTE éCO2mix | Intensité carbone du réseau, production solaire régionale | Licence Ouverte 2.0 |
| Enedis, open data | Consommation résidentielle, petites installations solaires | Licence Ouverte 2.0 |
| Weather data by Open-Meteo.com | Météo observée et prévue | CC BY 4.0 |
| PVGIS, Commission européenne (JRC) | Calage du modèle solaire | Réutilisation autorisée en citant la source |
| Bundesnetzagentur, SMARD.de | Prix spot de la zone France | CC BY 4.0 |
| Etalab, ministère de l'Éducation nationale | Jours fériés, vacances scolaires | Licence Ouverte |

## Licence

Le code est publié sous licence MIT. Les données restent sous leurs propres licences, indiquées ci-dessus.
