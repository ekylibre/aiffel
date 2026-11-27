---
name: consulter-compta
description: Répondre à une question de comptabilité, de trésorerie, de TVA, de MSA ou de marges de la ferme à partir de Lyvre (exploitant seulement)
---
1. Lyvre n'est ouvert qu'à l'exploitant, et seulement dans les domaines que sa clé lit (« Lyvre (comptabilité) » dans le contexte). Si Lyvre n'est pas connecté, dis-le et indique que l'exploitant le raccorde avec /lyvre. Si l'outil utile manque (domaine non lu), dis quel domaine cocher dans la clé Lyvre ; n'invente aucun chiffre.
2. Si tu ne connais pas encore le dossier (exercices, régimes), appelle d'abord `lyvre_get_context`. Par défaut, la période est l'**exercice en cours** ; si l'utilisateur parle d'une campagne, d'un mois ou d'une année, traduis-la en dates (`date_from` / `date_to`, AAAA-MM-JJ) ou en libellé d'exercice, et répète-la dans la réponse.
3. Choisis l'outil :
   - dépenses, achats d'un fournisseur ou d'une catégorie, factures à payer ou échues : `lyvre_list_purchases` (filtres `supplier`, `status`, période), détail d'une facture avec `lyvre_get_purchase` ;
   - montant par nature de charge ou de produit (plan comptable agricole de Lyvre : engrais 60111, semences 60112, phytosanitaires 60113, aliments du bétail 60114, carburant 60614, fermages 6131, entretien et réparations 615, salaires 641, charges sociales 645, cotisations de l'exploitant 646, ventes 70) : `lyvre_get_balances` avec les préfixes de comptes ;
   - écritures précises (libellé, compte, montant) : `lyvre_search_entries` ;
   - ventes, factures émises, impayés clients : `lyvre_list_sales` ;
   - trésorerie : `lyvre_get_bank_accounts`, puis `lyvre_list_bank_transactions` pour le détail ;
   - échéances à venir (TVA, MSA, emprunts, fermages) : `lyvre_get_agenda` ;
   - TVA : `lyvre_get_vat` ; résultat fiscal : `lyvre_get_tax_result` ; cotisations MSA : `lyvre_get_msa` ;
   - coût de la main-d'œuvre : `lyvre_get_balances` sur « 641 645 » (salariés), « 646 » (exploitant), complété par `lyvre_get_msa` ;
   - marges par activité ou culture : `lyvre_get_margins` ; aides : `lyvre_get_aids` ;
   - un fournisseur ou un client : `lyvre_find_party` ; immobilisations, matériel : `lyvre_list_assets`.
4. Les montants recombinés (somme de plusieurs comptes ou de plusieurs factures) passent par `calc_somme` ; ne calcule pas de tête.
5. Réponds en une ou deux phrases : le montant en euros et la période ; pour un montant soumis à TVA (achats, ventes, charges, marges), dis **HT ou TTC** (les soldes de comptes et les marges sont HT, les factures sont données en TTC), mais pas pour un solde bancaire, des salaires, la TVA due ou les cotisations MSA. Puis le détail utile (trois lignes au plus). Donne le lien Lyvre (`url`) quand l'outil en renvoie un. Termine par « Source : Lyvre (domaine) ».
6. Tu lis seulement : tu ne saisis, ne corriges ni ne valides rien dans Lyvre. Si l'utilisateur demande d'enregistrer une dépense ou une facture, dis-lui que la saisie se fait dans Lyvre pour l'instant.
7. Données sensibles : ne détaille les salaires d'une personne nommée que si l'exploitant le demande explicitement ; sinon, donne des totaux.
