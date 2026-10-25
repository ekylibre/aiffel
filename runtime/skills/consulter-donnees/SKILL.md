---
name: consulter-donnees
description: Répondre à une question chiffrée sur la ferme (passages, quantités, azote, surfaces) à partir d'Ekylibre
---
1. Identifie la campagne (année de récolte) ; par défaut, la campagne en cours indiquée dans le contexte. Si la question porte sur une période, utilise `started_after` / `started_before`.
2. Identifie la culture ou la parcelle : `ekylibre_productions` pour la campagne, ou `ekylibre_parcelles` pour un nom de parcelle. Si plusieurs correspondent, demande laquelle.
   Si l'utilisateur a désigné la parcelle par un **nom usuel** absent d'Ekylibre (« les Grands Champs ») et qu'elle est maintenant identifiée sans ambiguïté, appelle une fois `memoire_proposer_alias` (nom usuel, nom exact de la parcelle) : l'utilisateur confirmera d'un bouton. Ne le fais pas pour un nom déjà retenu (« Noms usuels confirmés » du contexte), ni pour un nom qui est déjà celui d'Ekylibre.
3. Choisis l'outil :
   - quantités, passages, azote par production : `ekylibre_totaux_intrants` (filtre `activity_production_id`) ;
   - liste ou détail d'interventions : `ekylibre_interventions` (filtres campagne, procédure, cible `target_id`, intrant).
4. Si un total doit être recombiné (plusieurs productions, unités différentes), utilise `calc_somme` ou `calc_conversion`.
5. Réponds en une ou deux phrases avec le chiffre et son unité, puis le détail utile, puis la ligne « Source : … ». Signale si le résultat est incomplet (`complete: false`) ou si une donnée manque (par exemple l'azote d'un engrais liquide, non calculé).
