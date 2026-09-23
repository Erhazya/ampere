# Retour d'expérience

Ce qui a marché, ce qui a coincé et ce qu'il faudrait refaire autrement, étape par étape.

## Étape 0 : conception (23 septembre 2026)

### Ce qui a marché

- **Vérifier chaque source avant de décider**, dans sa documentation officielle puis par un vrai appel à son API. Cela a évité trois erreurs de départ : PVGIS s'arrête en 2023 ; Enedis garde trois ans de données en ligne, et non deux ; les prix spot d'ENTSO-E ne sont pas librement réutilisables.
- **Lire soi-même les textes de licence.** Le résumé automatique d'un document s'est trompé deux fois sur la même liste ; seule la lecture du document original a tranché.
- **Se demander d'abord quelle est la « réalité »** à laquelle on comparera les prévisions. Cette question a mené aux vraies courbes de consommation d'Enedis, puis à la notation des prévisions au pas de 30 min.
- **Trancher les décisions avant d'écrire les sections qui en dépendent.** Les sections sur la rigueur et l'architecture n'ont pas eu à être réécrites.
- **Tenir un glossaire dès le premier jour.** Il sert à la fois de référence et de fiche de révision.

### Ce qui a coincé

- **La source des prix.** Le brief prévoyait ENTSO-E, mais ses conditions de réutilisation ont imposé une autre source, SMARD, qui republie les mêmes prix sous licence libre.
- **Le pas de temps.** Le marché fonctionne au quart d'heure, alors que les vraies courbes de consommation sont à la demi-heure. Le compromis retenu : simuler au quart d'heure, avec une consommation découpée à énergie constante, et noter les prévisions à la demi-heure.
- **Les données récentes.** Enedis publie avec environ un trimestre de retard : les jours récents doivent être estimés, puis marqués comme tels.
- **L'environnement de développement.** Il a changé le jour même : Windows (ADR 012), puis le VPS une fois sécurisé (ADR 013).

### À refaire autrement

- Vérifier les licences des sources dès la rédaction du brief, avant d'y nommer une source.
- Choisir l'environnement de développement avant de préparer quoi que ce soit.
