---
name: rechercher-produit-phyto
description: Trouver un produit phytosanitaire autorisé, ses usages, sa dose, son délai avant récolte et sa zone non traitée
---
1. Utilise `lexicon_search_phytosanitary_products` avec au moins un critère : nom du produit, substance, culture (libellé E-Phy : « Blé », « Maïs », « Vigne »…) ou cible (« pucerons », « désherbage », « mildiou »). Avec une culture ou une cible, la réponse inclut les usages (dose, délai avant récolte, ZNT).
2. Si la ferme possède le produit, `ekylibre_produits` (type `Matter`) permet de le confirmer.
3. Donne le nom, le numéro d'autorisation (AMM), puis pour l'usage demandé la dose maximale, le délai avant récolte et la ZNT.
4. Rappelle que l'étiquette et la réglementation font foi, et cite « Source : Lexicon (OSFarm), E-Phy (ANSES) ».
