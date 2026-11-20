# Pivot Aiffel / Billing — Lyvre + Fourmy

> Cahier des besoins produit par `/sc:brainstorm` le 2026-11-20.
> Sources : conversation de cadrage du 2026-11-20 ; état des lieux du jour ;
> `docs/requirements.md` (phase 1), `docs/architecture/phase1.md` (ADR-01 à ADR-09),
> mémoires `aiffel-concept`, `aiffel-public-private`, `aiffel-avatars`.
> **Étape suivante** : `/sc:design` pour l'architecture cible des deux dépôts, puis
> `/sc:workflow` pour un plan d'implémentation par tranches.

## 1. Pourquoi ce pivot

Le prototype Aiffel a été bâti autour d'Ekylibre (connecteur REST v2, appairage
`simple-token`, trois endpoints de lecture B1–B4). La stratégie produit change :

- L'écosystème métier cible devient **Fourmy** (gestion agricole : parcelles,
  interventions, intrants) et **Lyvre** (comptabilité et paie). Ekylibre n'est
  plus la plateforme de rattachement.
- Aiffel doit exister sous **deux modes d'usage distincts** : installation libre
  par n'importe quel exploitant ou collectif, et abonnement dans l'infra
  Ekylibre (lavz) aux côtés de Lyvre et Fourmy.
- Le billing `aiffel-billing` grandit : il devient le **service d'abonnement
  commun** des trois produits (Aiffel, Lyvre, Fourmy) et change de nom en
  `ekylibre-billing`. Il n'est plus un sous-module d'Aiffel.
- Le site `ekylibre.com` (dépôt `ekylibre-sas`) devient la vitrine commerciale
  commune des trois produits ; c'est là qu'on souscrit.

## 2. Décisions acquises (ne pas rediscuter sans raison)

1. **Intégration data uniquement** : Aiffel lit/écrit chez Fourmy (agro) et
   Lyvre (compta/paie). Le canal utilisateur reste Telegram (puis web/mobile
   plus tard, cf. phase 2). Aiffel n'est pas absorbé en module de Lyvre/Fourmy.
2. **Suppression complète** du connecteur Ekylibre dans Aiffel (code, tests,
   fixtures). Aucune ferme pilote réelle à migrer : la coupure est franche.
3. **Fourmy neuf** : son API d'intégration reste à définir avec l'équipe Fourmy.
   Aiffel produit la liste des endpoints attendus (équivalent de B1–B4 pour
   Ekylibre) ; l'implémentation côté Fourmy est hors de ce dépôt.
4. **Installation libre** : image Docker publique, auto-hébergée, **sans**
   extension billing, clés LLM fournies par l'utilisateur. Le cœur `aiffel/`
   reste public et libre.
5. **`ekylibre-billing`** : nouveau dépôt commun, autonome, qui ouvre et gère
   les comptes des trois produits dans l'infra Ekylibre.
6. **`ekylibre.com` (ekylibre-sas)** : pages produit et parcours de souscription
   pour Aiffel, Lyvre et Fourmy ; pointent vers `ekylibre-billing`.

## 3. Modes d'usage cibles

### Mode A — Installation libre (public)

- Cloner l'image Docker d'Aiffel (ou `docker compose` de référence) sur sa
  propre machine.
- Pointer le connecteur **Fourmy** vers sa propre instance Fourmy (URL et
  jeton saisis dans la mini page web d'appairage).
- Pointer le connecteur **Lyvre** vers sa propre instance Lyvre (facultatif à
  ce stade, voir §4.3).
- Fournir sa clé LLM (Anthropic ou autre fournisseur du catalogue
  `providers/catalog.py`).
- `ekylibre-billing` n'est pas chargé ; aucun quota, aucun paiement ; usage
  simplement journalisé localement (`UsageEvent` conservé mais sans
  conséquence tarifaire).

### Mode B — Abonnement dans l'infra Ekylibre

- Souscription sur `ekylibre.com` (parcours commun aux trois produits).
- `ekylibre-billing` crée la ferme, provisionne les comptes Aiffel + Lyvre +
  Fourmy nécessaires dans l'infra lavz (Dokploy).
- Aiffel reçoit le `pairing_code` vers une instance Fourmy déjà provisionnée
  (plus de saisie de mot de passe par l'utilisateur).
- Clé LLM : clé de la plateforme par défaut, avec règle de crédit portée par
  l'extension billing (comme aujourd'hui pour Anthropic).

## 4. Besoins fonctionnels

### 4.1 Dépôt `aiffel`

- **F-CONN-1** : connecteur **Fourmy** remplaçant `connectors/ekylibre/`. Il
  porte au moins :
  - Appairage (jeton d'accès Fourmy, mini page web réutilisée).
  - Lectures métiers équivalentes aux endpoints Ekylibre B1–B4 :
    parcelles/cultivable_zones, intervention_records, activity_productions,
    input_totals, procedures, products, farm_profiles.
  - Écriture `POST /interventions` avec provider `{vendor: "aiffel"}`.
  - Gestion `401` et état « à reconnecter » déjà en place dans Aiffel.
- **F-CONN-2** : connecteur **Lyvre** (lecture seule à ce stade), pour que
  l'agent sache répondre aux questions compta/paie courantes (dépenses
  phyto sur la campagne, coût du personnel sur un chantier). À détailler avec
  l'équipe Lyvre dans `/sc:design`.
- **F-CORE-1** : suppression de **tout** le code Ekylibre (connecteur,
  `EkylibreAuth`, fixtures, tests d'intégration, références dans
  `CLAUDE.md`, règle non négociable #5 à retirer).
- **F-CORE-2** : généralisation du nommage : `pairing_codes.purpose` ouvert
  à `fourmy` et `lyvre`, `connections.vendor` idem. `farm_profiles` remplacé
  par l'équivalent Fourmy.
- **F-CORE-3** : la mémoire de ferme, la validation hors modèle, les droits
  de validation (F-ECR-4), Pl@ntNet, vocal, mémoire de parcelles restent
  inchangés dans leur logique.
- **F-EXT-1** : `aiffel-billing` **sort** du dépôt Aiffel (il n'est plus un
  sous-module `extensions/aiffel-billing/`). Le point d'extension
  `aiffel.extensions` reste et devient le mécanisme standard d'ajout de
  billing/crédit, mais le cœur ne dépend d'aucune extension.
- **F-INST-1** : image Docker publique d'Aiffel et `compose.libre.yml` de
  référence (sans extension billing). Doc d'installation libre dans
  `docs/ops/install-libre.md`.
- **F-DOC-1** : requirements, ADR et plan workflow mis à jour. ADR-07
  (jeton `simple-token` partagé) devient caduc. Un nouveau ADR documente le
  passage à Fourmy, les deux modes d'usage, la sortie du billing.

### 4.2 Dépôt `ekylibre-billing` (nouveau, à créer)

- **F-BILL-1** : service autonome déployable indépendamment (ni sous-module
  d'Aiffel, ni couplé au cycle de vie des produits). Image Docker propre.
- **F-BILL-2** : parcours de souscription unifié pour les trois produits :
  - choix du produit (Aiffel / Lyvre / Fourmy, ou pack) ;
  - création du compte client (identité, SIRET, adresse de facturation) ;
  - paiement (Stancer, carte ou SEPA ; même PSP qu'aujourd'hui) ;
  - **provisioning** : création automatique dans l'infra lavz des services
    souscrits (ferme Fourmy, compte Lyvre, bot Aiffel appairé).
- **F-BILL-3** : API pour les produits abonnés :
  - vérifier l'état d'un abonnement (actif / en essai / suspendu) ;
  - récupérer le quota et la consommation en cours ;
  - enregistrer les `UsageEvent` reçus (tokens, STT, Pl@ntNet, Fourmy, Lyvre).
- **F-BILL-4** : page de gestion client : factures, moyens de paiement,
  changement d'offre, résiliation, reprise d'un essai expiré.
- **F-BILL-5** : hooks de cycle de vie (`on_subscribe`, `on_cancel`,
  `on_payment_failed`) utilisables par chaque produit pour activer/suspendre
  l'accès.
- **F-BILL-6** : migration douce depuis l'actuel `aiffel-billing` : reprendre
  ses tables (abonnements, essais, usage, règles de crédit), renommer les
  préfixes (`aiffel_` → `billing_`), conserver les historiques.
- **F-BILL-7** : hors scope cette phase : facturation prorata, multi-devise,
  revendeurs. Noter dans un `BACKLOG.md` du nouveau dépôt.

### 4.3 Dépôt `ekylibre-sas` (site `ekylibre.com`)

- **F-SITE-1** : refonte des pages produit — Aiffel, Lyvre, Fourmy mis en
  avant sur la page d'accueil ; une page par produit décrivant son périmètre
  et son tarif.
- **F-SITE-2** : parcours de souscription : bouton « S'abonner » sur chaque
  page produit → redirection vers `ekylibre-billing` (sous-domaine
  `billing.ekylibre.com` ou chemin `/abonnement`, à trancher en /sc:design).
- **F-SITE-3** : page « Comparer les offres » (pack + à l'unité).
- **F-SITE-4** : mentions légales, CGV, informations RGPD mises à jour pour
  les trois produits.
- **F-SITE-5** : lien vers le dépôt public Aiffel pour l'installation libre
  (« auto-hébergez vous-même »), avec une note claire qu'aucun support n'est
  fourni dans ce mode.

## 5. Besoins non fonctionnels

- **NF-SEC-1** : règles non négociables d'Aiffel conservées (isolation par
  ferme, aucune écriture tiers sans validation humaine hors modèle, aucun
  secret vu par le modèle, pas d'outil générique, logs sans PII). À
  retranscrire pour Fourmy et Lyvre dans l'ADR de pivot.
- **NF-SEC-2** : `ekylibre-billing` n'a **jamais** accès au contenu des
  conversations Aiffel. Il ne reçoit que des `UsageEvent` agrégés (tokens,
  secondes STT, appels Pl@ntNet/Fourmy/Lyvre) et des métadonnées de ferme
  (id, offre, date d'expiration).
- **NF-SEC-3** : les clés d'API de la plateforme (Anthropic, Fourmy, Lyvre,
  Stancer, Pl@ntNet) restent dans le coffre `aiffel/vault/` côté Aiffel et
  dans un coffre équivalent côté `ekylibre-billing`.
- **NF-PERF-1** : mode installation libre doit démarrer sur une machine
  modeste (2 Go RAM) avec Postgres local ; les tests `tests/isolation/`
  restent bloquants.
- **NF-OPS-1** : déploiement lavz par Dokploy conservé pour les trois
  services ; `ekylibre-billing` ajouté comme projet Dokploy à part.
- **NF-DOC-1** : documentation en français (cœur, docs, commentaires) et
  identifiants en anglais, convention Aiffel maintenue aussi dans les deux
  autres dépôts.

## 6. Hypothèses à vérifier

1. L'API Fourmy à venir sera **assez proche** de l'API Ekylibre v2 pour que
   le portage du connecteur soit un remplacement et non une réécriture. À
   confirmer avec l'équipe Fourmy.
2. L'infra lavz tient le déploiement additionnel de `ekylibre-billing` et
   des bases Fourmy/Lyvre (RAM, disque, sauvegardes).
3. Le PSP Stancer reste utilisé ; pas de bascule vers un autre.

## 7. Décisions complémentaires (Q1–Q5 tranchées le 2026-11-20)

- **D-ECH** — Fourmy et Lyvre avancent en parallèle. Aiffel traite les deux
  connecteurs dans la même vague d'implémentation ; pas de période de
  « pause fonctionnelle » à planifier entre Ekylibre et Fourmy. Les tranches
  T2 (Fourmy) et T4 (Lyvre) du §8 peuvent se chevaucher.
- **D-DEP** — `ekylibre-billing` est un **dépôt privé** (orga ekylibre) ;
  seuls les tarifs sont publics, exposés par `ekylibre.com` (ekylibre-sas).
  Le code et la logique de provisioning restent fermés. Identique à la
  logique actuelle `aiffel-billing`, étendue aux trois produits.
- **D-MRQ** — La marque **Aiffel** est conservée telle quelle. Pas de
  renommage. Aiffel est présenté aux côtés de Lyvre et Fourmy sur
  `ekylibre.com`, avec sa propre identité visuelle (métaphore tour Eiffel
  conservée, cf. `aiffel-concept`).
- **D-REL** — Pas de release taguée pour l'image Docker publique. L'image
  **suit la branche `main`** du dépôt Aiffel, sans SemVer, sans changelog,
  **sans support**. La documentation d'installation libre doit afficher ce
  cadre sans ambiguïté (aucun engagement de stabilité ni de rétro-compat).
- **D-BYO** — Mode installation libre strictement **BYO-clés**. Aucun essai
  gratuit, aucun quota offert, aucune connexion requise à `ekylibre-billing`.
  Les deux modes (libre et abonné) restent étanches : le cœur Aiffel public
  ne contient **aucun** code de quota, d'essai ou de crédit — ce code vit
  dans l'extension optionnelle chargée en mode abonné.

## 8. Prochaines étapes

1. Réponses aux questions Q1–Q5 ci-dessus (session courte).
2. `/sc:design` : architecture cible des deux dépôts, contrats d'API entre
   Aiffel ↔ `ekylibre-billing` ↔ Fourmy ↔ Lyvre, plan de dépôt
   (création/renommage/sous-modules), ADR-10 « Pivot Fourmy + billing
   commun ».
3. `/sc:workflow` : tranches verticales (ordre type, T2 et T4 en parallèle
   selon D-ECH) :
   - **T1** — Sortir `aiffel-billing` d'Aiffel et renommer en
     `ekylibre-billing` (rupture du sous-module, dépôt privé autonome,
     premier déploiement Dokploy indépendant). Préparatoire aux suivantes.
   - **T2** — Connecteur **Fourmy** (remplaçant d'Ekylibre), bande passante
     équivalente aux endpoints B1–B4, + écriture d'interventions validée.
     Tourne en parallèle de T4.
   - **T3** — Mode **installation libre** (image Docker suivant `main`,
     `compose.libre.yml`, doc `docs/ops/install-libre.md`, tests).
   - **T4** — Connecteur **Lyvre** (lecture compta/paie). En parallèle de T2.
   - **T5** — Vitrine `ekylibre.com` (ekylibre-sas) : pages Aiffel, Lyvre,
     Fourmy, parcours de souscription unifié vers `ekylibre-billing`, tarifs
     publics (D-DEP).
4. `/sc:implement` ensuite, lot par lot, avec des PR courtes (une par
   tranche ou sous-tranche), chacune revue avant fusion.

## 9. Impact sur les mémoires persistantes

À mettre à jour après validation de ce brief :

- `aiffel-project.md` — phase 1 close sur Ekylibre, démarrage phase 1-bis
  « Pivot Fourmy + billing commun ».
- `aiffel-public-private.md` — `aiffel-billing` → `ekylibre-billing`
  (nouveau dépôt), Aiffel 100 % public.
- `aiffel-avatars.md` — Lyvre → Qonto toujours valide ; ajouter Fourmy ;
  retirer la mention « Twenty seul CRM » si elle entre en conflit.
- `aiffel-pending.md` — supprimer l'item sur le jeton Ekylibre partagé
  (ADR-07 caduc), ajouter les suites du pivot.
- `aiffel-infra.md` — supprimer la référence `ekylibre-dev.com` comme cible
  de staging Aiffel, remplacer par la cible Fourmy à venir.
