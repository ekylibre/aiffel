# Architecture cible — Pivot Fourmy + billing commun

> Document produit par `/sc:design` le 2026-11-20.
> S'appuie sur le cahier des besoins `claudedocs/pivot_ekylibre_lyvre_fourmy.md`
> (décisions acquises §2, décisions complémentaires §7) et prolonge
> `docs/architecture/phase1.md`. Ne génère aucun code : prochain pas
> `/sc:workflow` puis `/sc:implement`.

## 1. Vue d'ensemble

Quatre produits se parlent autour d'un service de facturation commun, dans
deux modes d'exécution distincts (libre / abonné).

```
                       +----------------------+
                       |   ekylibre.com       |   vitrine commune : Aiffel, Lyvre, Fourmy
                       |   (ekylibre-sas)     |   tarifs publics, boutons "S'abonner"
                       +----------+-----------+
                                  | HTTP (redir)
                                  v
+---------------------+    +------+--------------+    +-------------------+
|  Utilisateur libre  |    |  ekylibre-billing   |    |     Stancer       |
|  (auto-hébergé)     |    |  (dépôt PRIVÉ)      +--->|     PSP carte/SEPA|
|                     |    |                     |    +-------------------+
|  BYO clés LLM       |    |  - souscription     |
|  BYO Fourmy/Lyvre   |    |  - provisioning     |----+ provisioning Dokploy
|                     |    |  - entitlements     |    | (lavz : Fourmy, Lyvre,
|       Aiffel (OSS)  |    |  - usage & quotas   |    |         Aiffel)
+----------+----------+    +---+-----+-----+-----+    +-------------------+
           |                   |     |     |
  pas de billing         events|     |events|
  tout en local                v     v     v
                           +---+--+ +-+----+ +------+
                           |Aiffel| |Lyvre | |Fourmy|  trois produits abonnés
                           |(abo) | |      | |      |
                           +--+---+ +------+ +------+
                              |        ^        ^
                              | reads  | reads  | reads/writes (interv.)
                              +--------+--------+
                              connecteurs Aiffel
```

### Deux modes, un même cœur Aiffel

- **Mode libre** : Aiffel seul, connecteurs Fourmy/Lyvre pointés par
  l'utilisateur, aucune extension billing chargée. Image `main`, sans SemVer
  (D-REL), BYO strict (D-BYO).
- **Mode abonné (lavz)** : Aiffel + extension `ekylibre-billing-client`
  (déclarée via l'entry-point `aiffel.extensions`). L'extension parle à
  `ekylibre-billing` pour vérifier l'abonnement, consomme l'`UsageEvent`
  émis par le cœur, et expose dans Telegram les commandes `/abonnement`,
  `/modele`, etc.

Le **cœur public Aiffel ne contient rien de billing** (D-BYO) : pas de
quota, pas d'essai, pas d'appel réseau à `ekylibre-billing`. L'extension
reste la seule porte d'entrée vers le billing.

## 2. Carte des dépôts (avant → après)

| Dépôt | Avant | Après | Visibilité |
|---|---|---|---|
| `ekylibre/aiffel` | Cœur + sous-module `extensions/aiffel-billing` + connecteur Ekylibre | Cœur Aiffel seul (connecteurs Fourmy/Lyvre), **sans** sous-module billing | Public |
| `ekylibre/aiffel-billing` | Sous-module d'Aiffel, Stancer + essai + crédits + provisioning, spécifique Aiffel | **Renommé** `ekylibre/ekylibre-billing`, dépôt autonome, trois produits | Privé (D-DEP) |
| `ekylibre/ekylibre-sas` (site) | Vitrine Ekylibre historique | Vitrine commune Aiffel + Lyvre + Fourmy, parcours de souscription vers billing | Public, tarifs publics |
| `ekylibre/fourmy` | — | Produit agro, expose l'API consommée par Aiffel (T-F*) | Ekylibre |
| `ekylibre/lyvre` | — | Produit compta/paie, expose l'API consommée par Aiffel (T-L*) | Ekylibre |
| `ekylibre/ekylibre` | Cœur historique | **Plus appelé par Aiffel**, hors périmètre pivot | Ekylibre |

### Opérations GitHub associées

1. **Renommage** `aiffel-billing` → `ekylibre-billing` (GitHub conserve une
   redirection ; mettre à jour les webhooks, les Dokploy, les CI).
2. **Désactivation** du sous-module `extensions/aiffel-billing` dans Aiffel
   (`.gitmodules`, `extensions/README.md`). Après le renommage, le billing
   est **cloné à côté**, pas sous Aiffel.
3. **Création** d'un petit paquet `ekylibre-billing-client` dans le nouveau
   dépôt (voir §4.3) — c'est lui qui sera installé comme extension par
   l'Aiffel abonné (via `pip install ekylibre-billing-client`), pas le dépôt
   billing complet.
4. **Archivage** d'ADR-07 dans Aiffel (jeton `simple-token` partagé devient
   caduc) ; remplacé par ADR-10 (§7 ci-dessous).

## 3. Structure des deux dépôts

### 3.1 Aiffel (`ekylibre/aiffel`)

```
aiffel/
  app/                      # inchangé (FastAPI, webhook, pairing web)
  channels/telegram/        # inchangé
  orchestrator/             # inchangé (boucle d'outils, LLM)
  tools/                    # outils Fourmy/Lyvre à la place d'Ekylibre
  connectors/
    fourmy/                 # remplace connectors/ekylibre/
      client.py             # REST client
      auth.py               # jeton Fourmy
      schemas.py            # Pydantic
      tool_schemas.json     # cache de prompt
    lyvre/                  # nouveau, lecture compta/paie
      client.py
      auth.py
      schemas.py
      tool_schemas.json
    plantnet/               # inchangé
    lexicon/                # inchangé
  hooks/                    # inchangé
  actions/                  # inchangé (PendingAction)
  vault/                    # inchangé
  db/                       # migrations : remplacer les colonnes/enums Ekylibre par Fourmy
  farm_context.py           # inchangé (abstractions)
  farm_llm.py               # inchangé
  memory.py                 # inchangé
  pairing.py                # adapté : vendeurs 'fourmy', 'lyvre'
  providers/                # inchangé
  rights.py                 # inchangé
  settings.py               # retirer AIFFEL_EKYLIBRE_*, ajouter AIFFEL_FOURMY_*, AIFFEL_LYVRE_*
  voice.py                  # inchangé
  worker.py                 # inchangé
  extensions.py             # inchangé (contract d'extension)

docker/
  Dockerfile                # inchangé
  compose.libre.yml         # NOUVEAU : stack d'installation libre (db + app + worker)
  compose.dokploy.yml       # inchangé mais sans le service `billing`

docs/
  architecture/
    phase1.md               # garde la trace historique
    pivot_fourmy_billing.md # CE DOCUMENT
  ops/
    dokploy.md              # inchangé
    install-libre.md        # NOUVEAU : mode libre, BYO, sans support (D-REL/D-BYO)
  requirements.md           # mis à jour : §9 référence Fourmy/Lyvre

runtime/                    # inchangé (skills ECC)
tests/                      # test_ekylibre_* → test_fourmy_* / test_lyvre_*
```

**Supprimé** : `aiffel/connectors/ekylibre/`, `extensions/aiffel-billing/`
(sous-module), toutes les mentions Ekylibre dans `CLAUDE.md`, règle non
négociable #5 d'ADR-07.

### 3.2 `ekylibre/ekylibre-billing` (dépôt renommé, privé)

```
ekylibre-billing/
  ekylibre_billing/           # service billing autonome
    api/                      # FastAPI: endpoints publics et internes
      public/                 # redirigés depuis ekylibre.com
        subscriptions.py      # POST /subscriptions (nouveau client)
        portal.py             # GET /portal/<token> (factures, résilier)
      internal/               # consommés par les produits abonnés
        entitlements.py       # GET /entitlements/<farm_id>
        events.py             # POST /events (usage)
        provisioning_hooks.py # callbacks Dokploy
    products/                 # un module par produit vendu
      aiffel.py               # règles de crédit (reprend plans.py actuel)
      lyvre.py                # règles de crédit Lyvre
      fourmy.py               # règles de crédit Fourmy
      packs.py                # offres groupées
    providers/
      stancer.py              # reprend l'actuel
      helloasso.py            # reprend l'actuel (si conservé)
    provisioning/
      dokploy.py              # création de services dans lavz
      fourmy_account.py       # création ferme Fourmy via son API
      lyvre_account.py        # création compte Lyvre via son API
      aiffel_bot.py           # création d'un pairing Aiffel
    db/                       # migrations Alembic propres, préfixes `billing_`
    vault/                    # coffre (identique au design Aiffel)
    scheduler/                # tâches périodiques (ex. essai expiré)
    web/                      # Jinja : portail client, pages de paiement
    mailer/                   # factures, relances
    settings.py
    logging.py

  client/
    ekylibre_billing_client/  # paquet Python installable par les produits abonnés
      __init__.py
      http.py                 # client HTTP typé des endpoints internal/
      aiffel_extension.py     # entry-point `aiffel.extensions`
      events.py               # helpers pour émettre les UsageEvent

  docker/
    Dockerfile
    compose.lavz.yml          # stack Dokploy (db + api + scheduler + web)

  tests/
  docs/
    ARCHITECTURE.md
    plans/                    # offres (markdown de référence)
  pyproject.toml              # deux packages : ekylibre-billing (serveur) + ekylibre-billing-client
```

### 3.3 `ekylibre/ekylibre-sas` (site public)

Ajouts minimaux (cadrage détaillé dans T5) :
- Trois pages produit (Aiffel, Lyvre, Fourmy) avec FAQ et captures.
- Une page « Comparer les offres » avec le JSON tarifs servi par
  `ekylibre-billing` (API publique `GET /pricing`).
- Un bouton « S'abonner » par produit redirigeant vers
  `https://billing.ekylibre.com/subscribe?product=aiffel&return=...`.
- Une page « Installer Aiffel soi-même » (D-REL) qui pointe sur
  `github.com/ekylibre/aiffel` et l'image Docker publique, avec un bandeau
  clair « image `main`, pas de versionnage, pas de support ».
- Mentions légales et CGV mutualisées pour les trois produits.

## 4. Contrats d'API

### 4.1 Aiffel → Fourmy (connecteur T-F)

Mêmes couvertures fonctionnelles que B1–B4 d'Ekylibre, portées sur Fourmy.
Auth : jeton Fourmy en `Authorization: Bearer ...` ; en-tête `X-Tenant` si
Fourmy est multi-tenant (à confirmer).

| Endpoint | Méthode | Rôle |
|---|---|---|
| `/api/v1/parcels` | GET | Lecture des parcelles (surface, culture, zone cultivable) |
| `/api/v1/intervention_records` | GET | Lectures d'interventions (filtres `started_after`, `campaign`, `procedure_name`, `target_id`, `input_product_id`, pagination) |
| `/api/v1/activity_productions` | GET | Productions de la campagne : activité, culture, variété, surface |
| `/api/v1/input_totals` | GET | Agrégats intrant × production × campagne |
| `/api/v1/procedures` | GET | Catalogue de procédures disponibles |
| `/api/v1/products` | GET | Catalogue d'intrants et de matériels |
| `/api/v1/farm_profile` | GET | Nom de la ferme, SIRET, campagne courante |
| `/api/v1/interventions` | POST | **Écriture** validée hors modèle (ADR-04) |
| `/api/v1/tokens` | POST | Obtention du jeton d'accès lors de l'appairage |
| `/api/v1/users/me` | GET | Vérification du jeton |

Règle : **aucun appel `DELETE /tokens`** — à écarter dans le contrat API
Fourmy, pour éviter de reproduire l'écueil Ekylibre (ADR-07 historique).

### 4.2 Aiffel → Lyvre (connecteur T-L)

Lecture seule. Objectif : répondre aux questions compta/paie courantes de l'exploitant (« combien
ai-je dépensé en engrais cette campagne ? », « qu'est-ce que je dois payer ce mois-ci ? »).

**Contrat réel (T4, 2026-11-27)**, qui remplace la première ébauche d'endpoints
(`/accounting/expenses`, `/tokens` par e-mail et mot de passe) : Lyvre a livré un accès par **clé
d'API** et des **outils d'agent** (`doc/api_mcp.md` et `claudedocs/design_api_mcp_aiffel.md` du dépôt
`ekylibre/lyvre`).

| Accès | Rôle |
|---|---|
| `GET /api/v1/agent/tools` | Outils ouverts à la clé, avec domaine et accès (lecture/écriture) ; sert à vérifier la clé et à relever ses domaines |
| `POST /api/v1/agent/tools/<outil>` | Exécute un outil (corps = arguments) ; mêmes outils que le serveur MCP `/mcp` |

- Authentification : `Authorization: Bearer lyv_…`. Une clé = une structure + un utilisateur +
  des domaines (Comptabilité, Banque, Achats, Ventes, Tiers, TVA et fiscal, Immobilisations,
  Agronomie, Échéancier). Erreurs : 401 (clé refusée), 403 (domaine non lu), 422 (erreur d'outil),
  429 (`Retry-After`).
- Aiffel n'expose que les **21 outils de lecture** (`connectors/lyvre/tool_schemas.json`) ; les
  outils d'écriture de Lyvre (brouillons d'achats, d'écritures, de tiers) ne sont pas exposés.
- REST plutôt que MCP côté Aiffel : même registre, codes HTTP explicites, pas de session.

### 4.3 Aiffel → `ekylibre-billing` (via `ekylibre-billing-client`)

Appels sortants côté extension Aiffel, jamais depuis le cœur.

| Endpoint | Méthode | Appelant | Rôle |
|---|---|---|---|
| `/internal/entitlements/{farm_id}` | GET | extension Aiffel (démarrage, périodiquement) | Renvoie `{status: active\|trial\|suspended\|none, expires_at, quotas, offer}` |
| `/internal/events` | POST | extension Aiffel (`on_turn_end`, `on_voice_transcribed`, `on_plantnet_call`) | Enregistre un `UsageEvent` (tokens, STT_sec, appels Pl@ntNet, Fourmy, Lyvre, coût) |
| `/internal/pairing_hooks/aiffel` | POST | `ekylibre-billing` → extension Aiffel (provisioning) | Fournit le `pairing_code` et le jeton Fourmy à la nouvelle ferme abonnée |

Auth : clé partagée `AIFFEL_BILLING_API_KEY`, portée par la ferme dans son
coffre. Toujours TLS (sortie Dokploy).

### 4.4 `ekylibre-billing` → Stancer (inchangé)

Migré depuis `aiffel_billing/stancer.py`. Même PSP, même clé `stest_`/
`sprod_` (D-DEP).

### 4.5 `ekylibre.com` → `ekylibre-billing` (parcours souscription)

Redirections explicites, pas d'intégration JS :

```
POST ekylibre.com/go/subscribe?product=aiffel
  302 Location: https://billing.ekylibre.com/subscribe?product=aiffel&source=sas
```

Puis le billing gère toute la suite (formulaire client, paiement Stancer,
provisioning, mail de bienvenue, lien d'appairage Aiffel).

### 4.6 `ekylibre-billing` → infra lavz (provisioning)

Un `ProvisioningJob` par souscription, portant les étapes :

1. **Fourmy** : `POST /api/v1/farms` (création ferme + compte propriétaire).
2. **Lyvre** : `POST /api/v1/accounts` (création compte client).
3. **Aiffel** : `POST /api/v1/pairing_codes` sur l'instance Aiffel partagée
   lavz (crée un `pairing_code` lié à la ferme Fourmy fraîchement créée).
4. Envoi du mail de bienvenue (code d'appairage Telegram, lien portail).

Tâche asynchrone (scheduler), idempotente, avec audit chaîné (modèle
`audit_log` repris d'Aiffel).

## 5. Modèle de données

### 5.1 Aiffel (changements)

| Table | Changement |
|---|---|
| `connections` | Colonne `vendor` acceptant `fourmy`, `lyvre` ; retrait de `ekylibre` |
| `pairing_codes` | Idem (`purpose` étendu) |
| `secrets` | Préfixes de type étendus (`fourmy:bearer`, `lyvre:bearer`) |
| `source_map` | Nouvelle convention de préfixe (`fourmy://…`, `lyvre://…`) ; les anciennes lignes `ekylibre://` sont supprimées puisqu'aucune ferme réelle n'est branchée (décision §2.2 du cahier) |
| `pending_actions` | Vendor-tag passé à `fourmy` ; les contrôles `pre_write` adaptés aux produits Fourmy |
| `audit_log` | Inchangé |

Migration Alembic `0006_pivot_fourmy.py` : renommage/altération des
contraintes + nettoyage des entrées `ekylibre`.

### 5.2 `ekylibre-billing` (modèle neuf, repris de `aiffel-billing`)

| Table | Rôle |
|---|---|
| `billing_customers` | Client facturé (SIRET, adresse, email, moyen de paiement) |
| `billing_subscriptions` | `customer_id`, `product` (aiffel/lyvre/fourmy/pack), `offer`, `state`, `trial_ends_at`, `renews_at` |
| `billing_provisioning_jobs` | Pipeline de provisioning (étape, idempotency key, audit) |
| `billing_usage_events` | `farm_id`, `product`, `kind` (tokens/stt/plantnet/fourmy_call), `value`, `cost`, reçus via `/internal/events` |
| `billing_invoices` | Factures émises (PDF via `fpdf2`, idem actuel) |
| `billing_payments` | Paiements Stancer (statut, callback) |
| `billing_credit_rules` | Règles de crédit par produit (tokens offerts, limites essai) |
| `billing_portal_tokens` | Jetons à usage unique pour le portail client |
| `audit_log` | Chaîné, mêmes contraintes qu'Aiffel |

RLS : isolation par `customer_id` dans les tables destinées à l'affichage
portail client.

### 5.3 Index des entitlements côté Aiffel (cache)

L'extension Aiffel maintient une table locale `billing_entitlements_cache`
(dans le schéma `aiffel`) avec `farm_id`, `status`, `expires_at`, `offer`,
`fetched_at`. Rafraîchie :
- au démarrage du worker,
- périodiquement (toutes les 10 min),
- à l'event webhook `subscription_changed` envoyé par `ekylibre-billing`
  quand il existe (optionnel pour T1).

Le cœur Aiffel ne lit pas cette table : c'est l'extension qui en fait
quelque chose (refuser un tour LLM, afficher un message).

## 6. Flux clés (séquences)

### 6.1 Souscription unifiée depuis `ekylibre.com`

```
Utilisateur  ekylibre.com  ekylibre-billing   Stancer   Fourmy   Lyvre   Aiffel(lavz)
   |             |              |                |         |        |         |
   |-click"abonner"->           |                |         |        |         |
   |             |--302 ------->|                |         |        |         |
   |<------------|              |                |         |        |         |
   |-form client------->|                        |         |        |         |
   |             |      |----paiement---------->|         |        |         |
   |             |      |<---OK-----------------|         |        |         |
   |             |      |-- provisioning job --|          |        |         |
   |             |      |   POST /farms ------------------>|       |         |
   |             |      |   POST /accounts ------------------->|   |         |
   |             |      |   POST /pairing_codes ----------------------->|   |
   |             |      |-- mail bienvenue (code Telegram) ---|   |         |
   |<----- mail ------- |                                     |   |         |
   |-start bot Telegram ----------------------------------------------->| |
```

### 6.2 Appairage Aiffel en mode libre

```
Utilisateur    Aiffel(auto-h)    Fourmy local
    |              |                   |
    |-/start--->   |                   |
    |<-lien ferme--|                   |
    |-formulaire : URL Fourmy + email + MdP ->|
    |              |-- POST /tokens ->|
    |              |<-- jeton --------|
    |              |-- GET /users/me->|
    |              |<-- 200 ----------|
    |<-- bonjour ferme ----|          |
```

Pas de contact avec `ekylibre-billing`. Pas de vérification de quota. Les
clés LLM, Pl@ntNet et Lyvre se saisissent dans la même mini page web ou
via `/cle` (page existante).

### 6.3 UC-1 — Enregistrer une intervention (version Fourmy)

Identique à la séquence ADR-04 d'aujourd'hui, en remplaçant `ekylibre` par
`fourmy`. L'outil `ekylibre_proposer_intervention` devient
`fourmy_proposer_intervention`. Les tests bloquants
`tests/integration/test_validated_writes.py::test_no_code_path_from_the_agent_to_a_fourmy_write`
doivent être réécrits strictement à l'identique sur le nouveau connecteur.

### 6.4 UC-2 — Questions sur Fourmy + Lyvre

```
Utilisateur       Aiffel             Fourmy                 Lyvre
     |                |                 |                     |
     | "combien phy->|                 |                     |
     | tos cette     |                 |                     |
     | campagne ?"   |                 |                     |
     |                |--input_totals->|                     |
     |                |<--data---------|                     |
     |                |--expenses(phyto)------------------->|
     |                |<--data------------------------------|
     | <--réponse chiffrée avec sources Fourmy + Lyvre --|
```

Le hook `on_turn_end` déclenche, en mode abonné, l'émission d'un
`UsageEvent` vers `ekylibre-billing`.

## 7. ADR-10 — Pivot vers Fourmy + billing commun

**Statut** : adopté le 2026-11-20 (remplace ADR-07).

**Décision** : Aiffel supprime toute dépendance à Ekylibre. L'écosystème
métier cible devient Fourmy (agro) + Lyvre (compta/paie). Le billing
`aiffel-billing` est renommé `ekylibre-billing`, sort du dépôt Aiffel,
devient un service autonome qui provisionne les trois produits. Aiffel
reste utilisable en mode libre sans billing (D-BYO).

**Conséquences** :
- Perte temporaire de couverture : l'intégration Fourmy/Lyvre doit être
  reconstruite (voir §4.1 et §4.2) ; les équipes Fourmy et Lyvre livrent
  leurs API en parallèle (D-ECH), Aiffel traite les deux dans la même
  vague (T2 ∥ T4).
- Simplification : plus de jeton `simple-token` partagé avec zero-mobile et
  Duke (ADR-07 caduc), la règle non négociable #5 du `CLAUDE.md` disparaît.
- Nouveau domaine de confiance : `ekylibre-billing` n'accède jamais aux
  conversations ni aux secrets de ferme ; il reçoit uniquement des
  `UsageEvent` agrégés et émet des provisioning jobs (NF-SEC-2).
- Deux modes étanches : le cœur Aiffel public ignore totalement le billing ;
  l'extension `ekylibre-billing-client` est le seul pont (NF-SEC-1).

**Alternatives écartées** :
- Garder Ekylibre en parallèle de Fourmy : complexité d'entretien, aucune
  ferme réelle à migrer, choix tranché le 2026-11-20.
- Fusionner Aiffel dans Lyvre ou Fourmy en tant que module : perdrait
  l'étanchéité et la publication publique du cœur (D-MRQ).
- Billing par produit : trois systèmes à maintenir, cohérence tarifaire
  difficile, parcours de souscription éclaté.

## 8. Impact sur les ADR existants

| ADR | Titre court | Statut |
|---|---|---|
| ADR-01 | Isolation logique stricte | **Conservé** (étendu à Fourmy/Lyvre) |
| ADR-02 | Boucle d'outils maison sur l'API Messages | **Conservé** |
| ADR-03 | Modèle ECC au runtime | **Conservé** |
| ADR-04 | L'agent propose, le code exécute | **Conservé** (cible : Fourmy) |
| ADR-05 | Connecteurs type MCP | **Conservé** (Fourmy, Lyvre, Pl@ntNet, Lexicon) |
| ADR-06 | Coffre intégré (enveloppe AES-GCM) | **Conservé** |
| ADR-07 | `simple-token` Ekylibre | **Caduc** — remplacé par ADR-10 |
| ADR-08 | Duke en phase 1 : réutilisation d'actifs | **Conservé** |
| ADR-09 | Python 3.12 | **Conservé** |
| ADR-10 | **Pivot Fourmy + billing commun** | **Adopté** (ce document, §7) |

## 9. Déploiement (Dokploy)

### Mode abonné (lavz)

```
projet Dokploy "aiffel"           projet Dokploy "ekylibre-billing"   projet Dokploy "fourmy"   projet Dokploy "lyvre"
  aiffel-app  ──────────┐           billing-api                         fourmy-*                  lyvre-*
  aiffel-worker  ───────┤           billing-scheduler
  aiffel-db             │           billing-db
  aiffel-migrate (one-shot) │       billing-migrate (one-shot)
  (extension ekylibre-billing-client installée dans aiffel-app, aiffel-worker)
                         │
                         └── env : AIFFEL_BILLING_API_URL=https://billing.ekylibre.com,
                                   AIFFEL_BILLING_API_KEY=<secret>
```

- **Sauvegarde** : pg_dump quotidien sur chaque base, copie hors ligne de
  `AIFFEL_MASTER_KEY` **et** `BILLING_MASTER_KEY`.
- **Observabilité** : chaque service expose `/metrics` ; dashboard commun à
  lavz (Grafana si disponible).
- **Secrets** : jamais dans Git, portés par Dokploy.

### Mode libre

- `docker compose -f compose.libre.yml up` : `aiffel-db`, `aiffel-migrate`,
  `aiffel-app`, `aiffel-worker`.
- Aucune ressource réseau externe imposée.
- Pas d'extension billing installée.
- Doc `docs/ops/install-libre.md` : avertissement clair D-REL (pas de
  version taguée, pas de support).

## 10. Impacts sécurité (synthèse)

- **NF-SEC-1** : règles non négociables d'Aiffel conservées intégralement.
- **NF-SEC-2** : `ekylibre-billing` ignore le contenu des conversations ; il
  ne reçoit que des `UsageEvent` et des métadonnées de ferme (id, offre,
  expiration).
- **NF-SEC-3** : clés d'API (Anthropic, Fourmy, Lyvre, Stancer, Pl@ntNet,
  `AIFFEL_BILLING_API_KEY`) toujours au coffre (Aiffel et billing ont
  chacun le leur).
- **Tests bloquants hérités** : `tests/isolation/`, `test_validated_writes`
  → portés à l'identique sur Fourmy, et un nouveau test d'étanchéité
  Aiffel ↔ `ekylibre-billing` (le cœur ne doit jamais appeler le billing).
- **Nouveau test d'étanchéité** : `tests/isolation/test_core_vs_billing.py`
  vérifie par analyse statique qu'aucun module `aiffel/**` (hors
  `extensions.py`) n'importe `ekylibre_billing_client`.

## 11. Correspondance avec les besoins (§4 du cahier)

| Besoin | Couvert par |
|---|---|
| F-CONN-1 (Fourmy) | §3.1, §4.1, §5.1, §6.3 |
| F-CONN-2 (Lyvre) | §3.1, §4.2, §5.1, §6.4 |
| F-CORE-1 (suppression Ekylibre) | §2, §3.1, §5.1, ADR-07 caduc |
| F-CORE-2 (généralisation du nommage) | §5.1 |
| F-CORE-3 (features conservées) | §3.1, §8 |
| F-EXT-1 (sortie du billing) | §2, §3.2, §4.3 |
| F-INST-1 (mode libre) | §1, §3.1 (compose.libre.yml), §6.2, §9 |
| F-DOC-1 (doc) | §3.1, §8 (phase1 historisé), §7 (ADR-10) |
| F-BILL-1 (billing autonome) | §3.2, §9 |
| F-BILL-2 (parcours unifié) | §4.5, §4.6, §6.1 |
| F-BILL-3 (API internal) | §4.3 |
| F-BILL-4 (portail client) | §3.2, §5.2 |
| F-BILL-5 (hooks cycle de vie) | §3.2 (`provisioning_hooks.py`), §4.3 |
| F-BILL-6 (migration douce) | §2 (renommage), §5.2 (préfixes) |
| F-SITE-1 à F-SITE-5 | §2 (ekylibre-sas), §3.3 |
| NF-SEC-1/2/3 | §10 |
| NF-PERF-1 | §9 (mode libre) |
| NF-OPS-1 | §9 |
| NF-DOC-1 | ce document (français) |

## 12. Points ouverts transférés à `/sc:workflow`

1. **Shape exacte de l'API Fourmy** : alignement avec l'équipe Fourmy
   (Q1 tranchée, calendrier à préciser côté équipe).
2. **Shape exacte de l'API Lyvre** : idem, à cadrer en atelier avec
   l'équipe Lyvre dès T4 démarré.
3. **Domaine du billing** (`billing.ekylibre.com` vs sous-chemin) : choix de
   déploiement à trancher en T1.
4. **Nom précis du paquet client** (`ekylibre-billing-client`, `-sdk`,
   `-aiffel-adapter`) : marginal, à valider au moment de la création du
   dépôt.
5. **Catalogue de produits vendus séparément vs pack** : §3.2
   `products/packs.py` prévu, offre exacte à décider côté commercial.
6. **Reprise de l'historique de `aiffel-billing`** : conservation des
   `UsageEvent` et des `Subscription` existants au renommage — plan de
   migration de données à écrire dans T1 (devrait être trivial, un seul
   produit historisé).

Prochaine étape : `/sc:workflow` pour produire le plan d'implémentation
par tranches (T1 → T5) avec dépendances, risques, critères de validation
et livrables PR.
