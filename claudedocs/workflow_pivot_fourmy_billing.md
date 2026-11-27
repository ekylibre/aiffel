# Workflow — Pivot Fourmy + billing commun

> Produit par `/sc:workflow` le 2026-11-20.
> Sources : `claudedocs/pivot_ekylibre_lyvre_fourmy.md` (cahier des besoins),
> `docs/architecture/pivot_fourmy_billing.md` (architecture cible, ADR-10).
> Stratégie : **systématique, tranches verticales**. Chaque PR livre un
> morceau indépendant, testable, revu avant fusion.
> Étape suivante : `/sc:implement`, lot par lot, dans l'ordre donné ci-dessous.

## État au 2026-11-27

| Tranche | Statut | Détail |
|---|---|---|
| **T1** | ✅ Code livré (PR ouvertes le 2026-11-27) | `ekylibre/ekylibre-billing#9` : service autonome (base `billing`, tables `billing_*`, API `/internal` réelle, pages `/abonnement` par liens à usage unique, ordonnanceur en processus, Dockerfile + `compose.lavz.yml`), paquet `client/` (`ekylibre-billing-client`), reprise `import_legacy` + script de suppression. `ekylibre/aiffel#40` (fusionnée) : sous-module retiré, client installé par `AIFFEL_EXTENSIONS` (git + secret de build). Reste à l'exploitant : projet Dokploy `ekylibre-billing`, DNS `billing.ekylibre.com`, bascule (`docs/exploitation.md` du billing). Écarts au plan : `docs/ARCHITECTURE.md` du billing. |
| **T2** | ⏳ Bloqué | API et MCP Fourmy en cours de développement (2026-11-27). |
| **T3** | ✅ Livré (hors T3-4) | `ekylibre/aiffel#36` (`compose.libre.yml`, `.env.libre.example`, `docs/ops/install-libre.md`, workflow `publish.yml`) + `ekylibre/aiffel#37` (fix workflow qui tentait de cloner le sous-module privé). Image `ghcr.io/ekylibre/aiffel:main` publiée. T3-4 (test E2E contre faux Fourmy) et T3-6 (section appairage Fourmy/Lyvre de la doc) restent à faire avec T2. |
| **T4** | ✅ Code livré (PR ouverte le 2026-11-27) | Connecteur Lyvre en lecture seule : `/lyvre` + page de clé `lyv_…`, 21 outils `lyvre_*` (exploitant, filtrés par domaine), skill `consulter-compta`, test bloquant `test_no_lyvre_write_tool`, évaluation `tests.eval.compta` 10/10. Écarts : contrat réel de Lyvre (clé d'API + outils d'agent) au lieu des endpoints de l'ébauche §4.2 ; outils de Lyvre exposés tels quels au lieu de 4 outils maison ; une PR au lieu de trois. Reste : essai contre `lyvre.org` avec une vraie clé ; règles de rétention et d'affichage à valider avec l'équipe Lyvre. |
| **T5** | ⏳ À démarrer | Accès au dépôt `ekylibre-sas` requis. |

Chantier hors tranche : **Q7** ✅ (`ekylibre/ekylibre-billing#8`) : capital 29 081 €, RCS Saintes 808 534 283 ; les factures en attente seront émises au premier passage de l'ordonnanceur.

## 0. Objectif et critères de réussite

Au terme de ce plan :

1. Aiffel s'installe librement par n'importe qui (`compose.libre.yml`,
   image `main`), sans billing, avec clés LLM à soi (D-BYO).
2. Aiffel communique avec Fourmy (lecture + écriture validée) et Lyvre
   (lecture compta/paie), plus jamais avec Ekylibre.
3. `ekylibre-billing` est un dépôt privé autonome qui ouvre des comptes
   Aiffel + Lyvre + Fourmy en un parcours unifié, depuis `ekylibre.com`.
4. Tous les tests bloquants historiques restent verts (isolation, écriture
   validée), plus un nouveau test d'étanchéité cœur ↔ billing.

| Indicateur | Cible |
|---|---|
| Couverture B1–B4 portée sur Fourmy | 100 % des endpoints (§4.1 archi) |
| Lectures compta/paie Lyvre | 4 endpoints (§4.2 archi) |
| Tests bloquants | isolation, validated_writes, core_vs_billing : verts |
| Dépendance Ekylibre dans Aiffel | 0 ligne de code, 0 mention |
| Fermes abonnées provisionnées de bout en bout | ≥ 1 test d'intégration |

## 1. Vue d'ensemble des tranches

```
T1 — Sortir le billing et le renommer ─┐
                                        │
T2 — Connecteur Fourmy ─────────────┐  │  parallélisables
                                     ├──┤  (D-ECH tranchée)
T4 — Connecteur Lyvre ──────────────┘  │
                                        │
T3 — Mode installation libre ───────────┤  (dépend de T2 pour la pile fourmy)
                                        │
T5 — Vitrine ekylibre.com + souscription ← (dépend de T1 pour les endpoints billing)
```

| Tranche | Contenu | Dépend de | Durée indicative |
|---|---|---|---|
| **T1** | Sortir `aiffel-billing` d'Aiffel, renommage `ekylibre-billing`, dépôt autonome, premier déploiement Dokploy indépendant, paquet client `ekylibre-billing-client` | — | 4 j |
| **T2** | Connecteur **Fourmy** (remplace Ekylibre) : lectures B1–B4 + écriture validée | T1 (pour l'extension abonnée) ; API Fourmy côté équipe Fourmy | 6 j |
| **T3** | Mode **installation libre** : image publique, `compose.libre.yml`, doc, tests | T2 (connecteur fonctionnel) | 2 j |
| **T4** | Connecteur **Lyvre** (lecture compta/paie), outils agent | T1 ; API Lyvre côté équipe Lyvre | 4 j |
| **T5** | Vitrine `ekylibre.com` (ekylibre-sas) + parcours de souscription unifié | T1 (endpoints billing publics) ; T2 ou T4 pour la crédibilité d'affichage | 3 j |

Total estimé : 19 j, avec T2 ∥ T4 en parallèle = 15 j de calendrier si deux
mains.

## 2. Tranche T1 — Sortir le billing et le renommer

### Objectif

Séparer complètement le billing du dépôt Aiffel. `ekylibre-billing` devient
un dépôt autonome, déployable indépendamment. Aucun changement fonctionnel
visible pour l'utilisateur à ce stade (l'offre reste celle d'aujourd'hui,
limitée à Aiffel), seulement une refonte structurelle préparatoire.

### Pré-requis

- Accès admin sur l'orga `ekylibre` (renommage de dépôt).
- Accès Dokploy (création d'un nouveau projet).
- Valider le domaine `billing.ekylibre.com` (§12 pt 3 archi, lot T1-1).

### Étapes

**T1-1 — Décisions figées (0,25 j)**
- Trancher le domaine : `billing.ekylibre.com` (recommandé) vs
  `ekylibre.com/billing`. Noter dans ADR-10.
- Trancher le nom du paquet client : `ekylibre-billing-client`
  (recommandé).

**T1-2 — Renommage GitHub + mirroring (0,5 j)**
- Renommer `ekylibre/aiffel-billing` → `ekylibre/ekylibre-billing` via
  l'UI GitHub (redirection conservée).
- Mettre à jour les webhooks Dokploy.
- Mettre à jour les references dans tout dépôt Aiffel : `.gitmodules`,
  `extensions/README.md`, `CLAUDE.md`, `docs/ops/dokploy.md`,
  `claudedocs/*`, `pyproject.toml`.

**T1-3 — Désactiver le sous-module dans Aiffel (0,5 j)**
- `git submodule deinit extensions/aiffel-billing`
- Supprimer `extensions/aiffel-billing/` du dépôt Aiffel.
- `.gitmodules` nettoyé.
- Les tâches périodiques et hooks Pl@ntNet/vocal/paiement **restent**
  fonctionnels : l'image Aiffel abonnée installera `ekylibre-billing-client`
  par pip, pas par sous-module.

**T1-4 — Scinder le dépôt `ekylibre-billing` en deux paquets (1 j)**
- `pyproject.toml` racine → deux projets :
  - `ekylibre-billing` (service, API, provisioning, web, scheduler).
  - `ekylibre-billing-client` (SDK Python, entry-point `aiffel.extensions`).
- Déplacer `extension.py`, les `tool_schemas` et les helpers d'event vers
  le paquet client.
- Garder le code serveur et le modèle de données côté `ekylibre_billing/`.

**T1-5 — Renommer les tables et migrations (0,75 j)**
- Migration Alembic du billing : `aiffel_*` → `billing_*` (noms de tables,
  index, contraintes). Migration **reversible**.
- Reprise des données historiques : copie vers les nouveaux noms, puis
  drop des anciennes dans une seconde migration (sécurité).

**T1-6 — API `internal/` (0,5 j)**
- Figer les routes `/internal/entitlements/{farm_id}`, `/internal/events`,
  `/internal/pairing_hooks/aiffel` (§4.3 archi).
- Authentification par clé partagée `AIFFEL_BILLING_API_KEY`.

**T1-7 — Déploiement Dokploy dédié (0,5 j)**
- Nouveau projet Dokploy `ekylibre-billing` : `billing-api`,
  `billing-scheduler`, `billing-db`, `billing-migrate`.
- Variables : `BILLING_MASTER_KEY`, `STANCER_KEY`, `DATABASE_URL`,
  `AIFFEL_BILLING_API_KEY` (côté billing).
- Côté Aiffel abonné : ajouter `AIFFEL_BILLING_API_URL=https://billing.ekylibre.com`
  et la même `AIFFEL_BILLING_API_KEY`.

**T1-8 — Tests et documentation (1 j)**
- Tests client (`ekylibre_billing_client`) contre un mock FastAPI.
- Tests serveur : endpoints `internal/` + migration de reprise.
- Nouveau test bloquant `tests/isolation/test_core_vs_billing.py` côté
  Aiffel : analyse statique vérifiant qu'aucun module `aiffel/**` (hors
  `aiffel/extensions.py`) n'importe `ekylibre_billing_client`.
- Documentation `docs/ARCHITECTURE.md` dans `ekylibre-billing`.
- Documenter le déploiement dans Aiffel `docs/ops/dokploy.md`.

### PR cibles (un morceau = une PR)

- PR #T1.a (Aiffel) — Retire le sous-module `aiffel-billing` et met à jour
  `.gitmodules`, `CLAUDE.md`, doc. Build vert.
- PR #T1.b (`ekylibre-billing`) — Scission en deux paquets
  (`ekylibre-billing` + `ekylibre-billing-client`) + API `internal/`.
- PR #T1.c (`ekylibre-billing`) — Migration `aiffel_*` → `billing_*` (deux
  étapes : copie, puis drop).
- PR #T1.d (Aiffel) — Nouveau test `test_core_vs_billing.py`, bloquant CI,
  et extension abonnée tirant `ekylibre-billing-client` depuis PyPI (ou
  GitHub).

### Critères de validation

- `aiffel/` ne contient plus aucune référence à `aiffel-billing` /
  `extensions/aiffel-billing`.
- `ekylibre-billing` se déploie seul sur Dokploy, accepte un `POST /internal/events`
  venant d'Aiffel et renvoie des entitlements.
- `test_core_vs_billing.py` échouerait si on réintroduisait un import.
- Les offres actuelles (uniquement Aiffel) continuent à fonctionner
  bout-en-bout en environnement de test.

### Risques

- Perte d'historique si la migration `aiffel_*` → `billing_*` est mal faite.
  Mitigation : migration en deux étapes, snapshot pg_dump avant chaque
  étape.
- Lien cassé entre Aiffel abonné et billing pendant la bascule.
  Mitigation : exécuter T1 sur un environnement de staging avant lavz.

## 3. Tranche T2 — Connecteur Fourmy

### Objectif

Aiffel lit et écrit dans Fourmy à la place d'Ekylibre. Tous les tests
bloquants historiques sont portés à l'identique. Le prototype UC-1 et UC-2
redevient fonctionnel sur une instance Fourmy de test.

### Pré-requis

- API Fourmy de test disponible, endpoints §4.1 archi implémentés côté
  équipe Fourmy (D-ECH).
- Un jeton de test Fourmy pour une ferme `demo` dans l'infra de staging.
- T1 fusionnée (pour le paquet client en mode abonné).

### Étapes

**T2-1 — Contrat API Fourmy figé (0,25 j)**
- Rédiger un fichier `connectors/fourmy/tool_schemas.json` (modèle du
  cache de prompt utilisé pour Lexicon aujourd'hui).
- Aligner les champs avec l'équipe Fourmy (procédures, intrants, matériel).

**T2-2 — Client REST Fourmy (1 j)**
- `connectors/fourmy/client.py` : httpx, timeouts, retries sur lectures.
- `connectors/fourmy/auth.py` : `FourmyAuth` (interface) +
  `BearerTokenAuth` (impl). Jamais de `DELETE /tokens`.
- Gestion `401` : passage en état « à reconnecter », logique déjà présente
  dans Aiffel (`ctx.ekylibre_refused` → à renommer `ctx.fourmy_refused`).

**T2-3 — Appairage Fourmy (0,5 j)**
- Adapter `aiffel/pairing.py` : vendor `fourmy`.
- Mini page web : URL Fourmy + email + MdP ; côté mode abonné, le
  pairing est préchargé par le provisioning billing.

**T2-4 — Outils de lecture (1 j)**
- Remplacer le catalogue `tools/catalog.py` : outils
  `fourmy_lire_parcelles`, `fourmy_lire_interventions`,
  `fourmy_lire_productions`, `fourmy_totaux_intrants`, `fourmy_profil_ferme`.
- Même signatures que les outils Ekylibre d'avant (seul le nom change),
  pour minimiser l'impact sur les skills ECC.

**T2-5 — Écriture validée (1,5 j)**
- Outil `fourmy_proposer_intervention` (`access="propose"`), `PendingAction`
  identique.
- Hook `pre_write` : parcelle/produit/matériel présents dans Fourmy,
  dose vs dose homologuée (Lexicon inchangé), DAR/ZNT, campagne.
- Callback Telegram : appelle `POST /api/v1/interventions` sur Fourmy
  avec `provider = {vendor: "aiffel", …}`. Idempotence par
  `idempotency_key` reprise du `PendingAction.id`.

**T2-6 — Migration de données (0,25 j)**
- `0006_pivot_fourmy.py` : nettoyage des références `ekylibre` dans
  `connections`, `secrets`, `source_map`, `pending_actions` (aucune donnée
  réelle à conserver, cf. cahier §2.2).

**T2-7 — Skills ECC (0,25 j)**
- `runtime/skills/enregistrer-intervention/SKILL.md` : remplacer les
  mentions « Ekylibre » par « Fourmy ». Sémantique inchangée.
- `runtime/skills/consulter-donnees/SKILL.md` : idem.

**T2-8 — Tests bloquants portés (0,75 j)**
- `tests/integration/test_validated_writes.py::test_no_code_path_from_the_agent_to_a_fourmy_write` :
  réécrit, identique dans l'esprit.
- `tests/integration/test_fourmy_connector.py` : nouvelle couverture
  (401, timeouts, écriture avec `idempotency_key`).
- Mettre à jour les fixtures `tests/isolation/` pour utiliser les tables
  `fourmy_*` plutôt que `ekylibre_*`.

**T2-9 — Suppression du code Ekylibre (0,5 j)**
- Supprimer `aiffel/connectors/ekylibre/`, les tests
  `test_ekylibre_connector.py`, les mentions dans `CLAUDE.md`
  (règle non négociable #5), `docs/requirements.md`,
  `docs/architecture/phase1.md` (ajouter un bandeau « historique »).
- Mettre à jour `aiffel-pending.md` (mémoire) pour retirer les items
  Ekylibre devenus caducs.

### PR cibles

- PR #T2.a (Aiffel) — Connecteur Fourmy (client + auth + appairage) ; les
  outils Ekylibre restent le temps de la PR suivante.
- PR #T2.b (Aiffel) — Outils de lecture Fourmy, migration des skills.
- PR #T2.c (Aiffel) — Écriture validée Fourmy + tests bloquants portés.
- PR #T2.d (Aiffel) — Suppression définitive du code Ekylibre + mise à
  jour `CLAUDE.md`/`docs`.

### Critères de validation (M-Fourmy)

- 20 questions de référence sur la ferme `demo` (reprises de M2 historique)
  répondues avec sources citées.
- 10 interventions types (phyto, semis, fertilisation, travail du sol,
  récolte) proposées, validées et retrouvées dans Fourmy.
- Les tests bloquants portés sont verts.
- Aucune mention `ekylibre` dans `aiffel/` (hors historisation
  explicite dans `docs/architecture/phase1.md`).

### Risques

- L'API Fourmy diverge de la shape B1–B4 → nécessité de réécrire les
  mappers Duke (`duke/src/duke/integration/ekylibre/mappers.py`). À
  détecter en T2-1.
- Fourmy n'expose pas `/users/me` → impossible de confirmer le `401`.
  Mitigation : demander à l'équipe Fourmy un endpoint équivalent dès
  T2-1.

## 4. Tranche T3 — Mode installation libre

### Objectif

Permettre à un exploitant tiers de lancer Aiffel chez lui. BYO strict. Pas
de billing. Pas de support.

### Pré-requis

- T2 fusionnée (connecteur Fourmy fonctionnel : il n'y a pas d'install
  libre crédible sans un connecteur métier).
- Un `docker compose` minimal pour Fourmy à tester (fourni par l'équipe
  Fourmy ou documenté).

### Étapes

**T3-1 — `compose.libre.yml` (0,5 j)**
- Trois services : `aiffel-db`, `aiffel-migrate` (one-shot),
  `aiffel-app`, `aiffel-worker`.
- Variables minimales : `TELEGRAM_BOT_TOKEN`, `AIFFEL_MASTER_KEY`,
  `ANTHROPIC_API_KEY` (BYO), `DATABASE_URL`.
- Pas de service `billing`, pas de `ekylibre-billing-client` installé.

**T3-2 — Doc `docs/ops/install-libre.md` (0,5 j)**
- Procédure complète : cloner, `docker compose up`, créer un bot
  Telegram, obtenir une clé Anthropic, saisir ses URLs Fourmy/Lyvre.
- **Bandeau clair D-REL** : image `main`, pas de versionnage, pas de
  support, pas de rétro-compat garantie.

**T3-3 — Image publique (0,25 j)**
- CI GitHub Actions : à chaque push sur `main`, build + push vers
  `ghcr.io/ekylibre/aiffel:main`.
- README public mis à jour pour pointer l'image.

**T3-4 — Tests (0,5 j)**
- Test d'intégration bout-en-bout : lance `compose.libre.yml` dans CI,
  vérifie qu'un appairage simulé contre un faux Fourmy fonctionne.
- Garantir que l'install libre ne charge aucune extension billing
  (`test_core_vs_billing.py` déjà en place).

**T3-5 — Mention ekylibre.com (0,25 j)**
- Ajouter une courte section dans la vitrine (préparation de T5) pour
  pointer vers le dépôt public.

### PR cibles

- PR #T3.a (Aiffel) — `compose.libre.yml` + doc + bandeau D-REL.
- PR #T3.b (Aiffel) — CI image publique + test d'intégration.

### Critères de validation

- `docker compose -f compose.libre.yml up` démarre sur une machine
  propre (2 Go RAM), avec une base locale vierge.
- La doc `install-libre.md` explique clairement BYO et absence de support.
- CI construit et publie l'image à chaque merge sur `main`.

### Risques

- Utilisateurs qui ouvrent des issues de support sans contrat →
  mitigation : `GitHub issue template` qui rappelle D-REL.

## 5. Tranche T4 — Connecteur Lyvre

### Objectif

Aiffel répond aux questions compta/paie de l'exploitant à partir de Lyvre.
Pas d'écriture Lyvre dans cette phase.

### Pré-requis

- API Lyvre de test disponible (§4.2 archi).
- T1 fusionnée.

Peut démarrer en parallèle de T2 (D-ECH).

### Étapes

**T4-1 — Contrat API Lyvre figé (0,25 j)**
- `connectors/lyvre/tool_schemas.json`.

**T4-2 — Client REST Lyvre (0,75 j)**
- `client.py`, `auth.py`, retries, logs sans PII.

**T4-3 — Appairage Lyvre (0,5 j)**
- `aiffel/pairing.py` : vendor `lyvre`.
- Mini page web : URL Lyvre + email + MdP, OU en mode abonné, préchargé
  par provisioning.

**T4-4 — Outils de lecture compta (0,75 j)**
- `lyvre_depenses_periode`, `lyvre_recettes_periode`, `lyvre_couts_paie`,
  `lyvre_lire_factures`. Mêmes conventions d'erreurs qu'Ekylibre
  historique (ToolFailure, jamais interruption du tour).

**T4-5 — Skill ECC (0,25 j)**
- `runtime/skills/consulter-compta/SKILL.md` (nouveau) : routage des
  questions compta vers Lyvre, format de réponse (euros HT ou TTC,
  période explicite, sources citées).

**T4-6 — Tests (0,5 j)**
- `tests/integration/test_lyvre_connector.py` (401, timeouts, données
  sensibles masquées dans les logs).
- `tests/eval/` étendu avec 10 questions compta de référence.

### PR cibles

- PR #T4.a (Aiffel) — Client Lyvre + auth + appairage.
- PR #T4.b (Aiffel) — Outils compta + skill.
- PR #T4.c (Aiffel) — Tests + évaluation compta.

### Critères de validation (M-Lyvre)

- 10 questions compta de référence répondues avec sources.
- Masquage effectif des PII dans les logs d'erreur.
- Aucune écriture Lyvre possible depuis l'agent (test bloquant).

### Risques

- Les données compta sont plus sensibles ; vérifier avec l'équipe Lyvre
  les règles de rétention et d'export avant de les afficher dans Telegram.

## 6. Tranche T5 — Vitrine `ekylibre.com` + souscription unifiée

### Objectif

`ekylibre.com` devient la vitrine commune Aiffel + Lyvre + Fourmy, avec un
parcours de souscription pointant vers `ekylibre-billing`.

### Pré-requis

- T1 fusionnée (endpoints billing disponibles).
- T2 et/ou T4 au moins en PR avancée, pour que la vitrine ait des
  captures d'écran à montrer.

### Étapes

**T5-1 — Pages produit (1 j, dépôt `ekylibre-sas`)**
- Trois pages : `/aiffel`, `/lyvre`, `/fourmy`. Même template, copy
  rédigée avec la communication Ekylibre.
- Capture d'écran Aiffel (Telegram + un exemple de réponse).

**T5-2 — Page « Comparer les offres » (0,5 j)**
- Tirée du `GET /pricing` servi par `ekylibre-billing` (nouvelle API
  publique à ajouter en T5-a).

**T5-3 — Boutons « S'abonner » + redirection (0,25 j)**
- Un bouton par produit, pointant vers
  `https://billing.ekylibre.com/subscribe?product=<nom>&source=sas`.

**T5-4 — Parcours souscription unifié côté billing (1 j)**
- Formulaire client, paiement Stancer, provisioning (déjà en place en T1
  pour Aiffel ; T5 ajoute les branches Lyvre et Fourmy du provisioning).
- Mail de bienvenue multi-produit.

**T5-5 — Page « Installer Aiffel soi-même » (0,25 j)**
- Lien dépôt public, bandeau D-REL, précise que Lyvre et Fourmy ne sont
  pas open-source (positionnement commercial).

**T5-6 — Mentions légales, CGV, RGPD mutualisées (0,5 j)**
- Mise à jour pour trois produits.

### PR cibles

- PR #T5.a (`ekylibre-billing`) — API publique `GET /pricing` + extension
  du provisioning pour Lyvre/Fourmy.
- PR #T5.b (`ekylibre-sas`) — Pages produit + boutons + redirections.
- PR #T5.c (`ekylibre-sas`) — Mentions légales, CGV, RGPD.
- PR #T5.d (`ekylibre-sas`) — Page install libre + liens.

### Critères de validation

- Depuis `ekylibre.com`, souscription à Aiffel bout-en-bout : paiement
  test → ferme Fourmy créée → compte Lyvre créé → bot Aiffel appairé →
  mail reçu.
- Pages SEO-propres (balises title/meta, sitemap).
- Les mentions légales couvrent explicitement les trois produits.

### Risques

- CGV à faire relire par l'équipe juridique → à anticiper dès T1.
- Risque de confusion commerciale entre « Aiffel abonné » et « Aiffel
  libre » : atténuer par un wording clair sur la page install libre.

## 7. Quality gates

| Gate | Quand | Vérifie |
|---|---|---|
| **Q1 — Sécurité** | fin T1, fin T2 | Tests d'isolation verts ; `test_core_vs_billing` vert ; `/security-review` sur T1 et T2 |
| **Q2 — Données** | fin T2 | 20 questions de référence sur `demo`, toutes sourcées, aucune hallucination |
| **Q3 — Écriture** | fin T2 | 10 interventions types écrites dans Fourmy et vérifiées |
| **Q4 — Compta** | fin T4 | 10 questions compta sourcées ; PII masquées en logs |
| **Q5 — Install libre** | fin T3 | Boot sur machine propre, doc complète, image publiée |
| **Q6 — Souscription** | fin T5 | Parcours complet depuis `ekylibre.com`, provisioning OK sur les trois produits |

## 8. Risques transversaux

| Risque | Effet | Parade |
|---|---|---|
| API Fourmy ou Lyvre en retard | T2/T4 bloqués | Démarrer T1 et T3 (ou les stubs) en attendant ; mocker les APIs en test |
| Perte de données lors du renommage `aiffel_*` → `billing_*` | Historique perdu | Migration en deux étapes, pg_dump avant chaque étape, test de reprise |
| Confusion entre Aiffel libre et abonné | Support non payé | Bandeau clair D-REL sur toutes les pages install libre et le README |
| Dérive de scope vers la refonte Keycloak (ADR-07 historique) | Perte de temps | Hors périmètre, à traiter plus tard |
| Clé maître perdue (Aiffel ou billing) | Secrets illisibles | Copie hors ligne obligatoire pour les deux clés, rappel dans la doc ops |
| Fermes pilotes revenant en live Ekylibre | Besoin de rétrocompat | Décision §2.2 : aucune ferme réelle, rupture nette ; sinon reconsidérer |

## 9. Planning indicatif (1 dev aidé de Claude Code)

| Semaine | Tranches avancées |
|---|---|
| S1 | T1 (complète) |
| S2 | T2-1 à T2-5 (connecteur Fourmy + écriture validée) ; T4-1 à T4-2 en parallèle |
| S3 | T2-6 à T2-9 (nettoyage Ekylibre, tests) → **M-Fourmy** ; T4-3 à T4-6 (connecteur Lyvre) → **M-Lyvre** ; T3 (install libre) |
| S4 | T5 (vitrine + souscription unifiée) → **Prêt commercial** |

Soit ~4 semaines calendaire pour un développeur seul épaulé par
Claude Code, avec un chevauchement T2/T4.

## 10. Mémoires à mettre à jour (après validation)

- `aiffel-project.md` : phase 1 close sur Ekylibre, phase 1-bis « Pivot
  Fourmy + billing commun » démarrée le 2026-11-20.
- `aiffel-public-private.md` : `aiffel-billing` → `ekylibre-billing`,
  dépôt privé autonome ; Aiffel 100 % public.
- `aiffel-pending.md` : supprimer les items `Ekylibre 401` et
  `revocation jeton partagé` (caducs) ; ajouter suites du pivot.
- `aiffel-infra.md` : retirer `ekylibre-dev.com` comme staging Aiffel,
  ajouter le staging Fourmy à venir.
- `aiffel-concept.md` : inchangé (marque Aiffel conservée, D-MRQ).
- `aiffel-avatars.md` : ajouter Fourmy à côté de Lyvre.

## 11. Prochaines étapes

1. Valider ce workflow (relecture éventuelle, ajustements).
2. Réponses opérationnelles requises avant T1 (si pas déjà prises) :
   domaine billing, nom du paquet client, PyPI ou GitHub pour le client.
3. Lancer `/sc:implement` sur **T1** en premier, PR par PR
   (T1.a → T1.b → T1.c → T1.d).
4. En parallèle, demander aux équipes Fourmy et Lyvre un planning de
   disponibilité de leurs APIs (D-ECH).

Rappel : cette commande n'exécute rien. Elle produit uniquement ce plan.
Toute implémentation passe par `/sc:implement` lot par lot.
