---
name: diagnostiquer-photo
description: Identifier une maladie, un ravageur ou une plante à partir d'une photo envoyée par l'utilisateur (Pl@ntNet)
---
1. Tu ne vois pas la photo. Seuls les outils Pl@ntNet l'analysent : ne décris jamais l'image toi-même et n'invente aucun symptôme.
2. Choisis l'outil selon la question : `plantnet_identifier_maladie` pour une tache, une pourriture, un ravageur ou une carence ; `plantnet_identifier_espece` pour reconnaître une plante (adventice, culture, haie). Sans question précise, commence par la maladie ; si rien n'en ressort, essaie l'espèce.
3. Nuance selon le score :
   - au-dessus de 0,5 : « probablement » ;
   - entre 0,2 et 0,5 : « peut-être », avec les deux ou trois hypothèses les plus probables ;
   - en dessous de 0,2 ou sans résultat : dis que l'identification n'est pas fiable et demande une photo plus nette, de près, de l'organe atteint (feuille, tige, fruit), à la lumière du jour.
4. Rapproche le résultat de ce que tu sais de la ferme (cultures de la campagne avec `ekylibre_productions`) : une maladie de la vigne sur une ferme sans vigne doit éveiller le doute.
5. Ne prescris jamais de traitement à partir de la photo seule. Si l'utilisateur demande quoi faire, propose de vérifier les produits autorisés avec le savoir-faire `rechercher-produit-phyto`, et rappelle qu'un conseiller ou un technicien confirme le diagnostic.
6. Cite toujours « Source : Pl@ntNet » et précise qu'il s'agit d'une identification automatique.
