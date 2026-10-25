---
name: enregistrer-intervention
description: Préparer l'enregistrement d'une intervention décrite par l'utilisateur, jusqu'au récapitulatif à valider
---
1. Repère dans le message : ce qui a été fait (procédure), quand (date et heure, durée), où (parcelle ou culture), avec quoi (intrants et doses, matériel), qui (intervenants). Pour une date relative (« ce matin », « hier »), calcule-la à partir de la date du jour du contexte.
2. Procédure : `ekylibre_procedures` donne les noms exacts (par exemple `spraying` pour un traitement phytosanitaire, `fertilizing`, `sowing`, `harvesting`, `plowing`). En cas de doute entre deux procédures, demande.
3. Cibles : avec `ekylibre_productions` (campagne en cours), prends les `target_product_ids` de la culture concernée. Pour un nom de parcelle, `ekylibre_parcelles` aide à identifier la culture. Si plusieurs cibles correspondent, demande laquelle.
   Si l'utilisateur a désigné la parcelle par un **nom usuel** absent d'Ekylibre (« les Grands Champs ») et qu'elle est maintenant identifiée sans ambiguïté, appelle une fois `memoire_proposer_alias` (nom usuel, nom exact de la parcelle) : l'utilisateur confirmera d'un bouton. Ne le fais pas pour un nom déjà retenu (« Noms usuels confirmés » du contexte), ni pour un nom qui est déjà celui d'Ekylibre.
4. Intrants : `ekylibre_produits` avec le type `Matter` et le nom du produit. Garde l'unité dite par l'utilisateur : dose par hectare (`l/ha`, `kg/ha`…) ou quantité totale (`l`, `kg`).
5. Matériel et intervenants (facultatifs) : `ekylibre_produits` avec les types `Equipment` et `Worker`.
6. Pour un traitement phytosanitaire, indique `crop_ephy` avec le libellé E-Phy de la culture (« Blé », « Maïs », « Vigne », « Colza »…) pour que la dose soit contrôlée.
7. Appelle `ekylibre_proposer_intervention`. S'il manque une information indispensable (cible, date, produit), pose **une seule** question courte avant de proposer.
8. Après la proposition, réponds en une phrase : « Voici le récapitulatif, vérifiez-le puis validez. » Ne recopie pas le récapitulatif : il est affiché juste après avec les boutons.
