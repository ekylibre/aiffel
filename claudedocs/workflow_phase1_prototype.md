# Workflow — Aiffel phase 1 (prototype)

> Généré par `/sc:workflow` le 2026-10-24.
> Sources : `docs/requirements.md` (§9 phase 1) et `docs/architecture/phase1.md` (ADR-01 à ADR-09, validés).
> Stratégie : **systématique, par tranches verticales**. Chaque jalon livre un parcours qui marche de bout
> en bout, plutôt que des couches finies séparément.
> Étape suivante : `/sc:implement`, lot par lot, dans l'ordre ci-dessous.

## 0. Objectif et critères de réussite du prototype

Un exploitant de la ferme `demo.ekylibre-dev.com` :
1. s'appaire au bot Telegram Aiffel en moins de 2 minutes ;
2. dicte une intervention (par exemple un traitement phyto) et la retrouve dans Ekylibre après un clic sur **Valider** ;
3. pose une question sur ses données (« combien de passages sur le blé cette campagne ? ») et obtient une
   réponse chiffrée **avec ses sources**.

| Indicateur | Cible prototype |
|---|---|
| Extraction correcte sur le corpus Duke (267 phrases) | ≥ score actuel de Duke, à mesurer en M1 |
| Écriture sans validation | **0** (test automatisé bloquant) |
| Fuite de données entre fermes | **0** (test automatisé bloquant) |
| Délai de réponse texte (hors appels lents à Ekylibre) | < 5 s au 95e percentile |
| Coût LLM | mesuré par ferme et par jour (`usage_metrics`) |

## 1. Flux de travail et dépendances

Trois flux parallèles se rejoignent aux jalons :

```
Flux A — Aiffel (ce dépôt)         Flux B — Ekylibre 5.0-beta        Flux C — Infra / déploiement
A0 Socle dépôt ──────┐              B1 intervention_records ─┐        C1 Bot Telegram (BotFather)*
A1 Données + coffre  │              B2 activity_productions  ├─► B4   C2 DNS app.aiffel.org*
A2 Canal Telegram    ├─► M1         B3 input_totals ─────────┘  déploy C3 Compose Dokploy
A3 Appairage ────────┘                                      staging   C4 Sauvegardes + logs
A4 Moteur d'agent ───────► M2 (UC-2 lecture : besoin de B1-B4)
A5 Écriture validée ─────► M3 (UC-1 : besoin de A4, B4 pour vérifier)
A6 Vocal ────────────────► M4 (pilote : besoin de C1-C4)
A7 Évaluation (corpus Duke) — en continu dès A4
```
\* Actions **visibles de l'extérieur** : à lancer seulement avec l'accord explicite de David.

| Jalon | Contenu | Dépend de |
|---|---|---|
| **M1 — « Bonjour ferme »** | Appairage Telegram ↔ ferme Ekylibre, puis l'agent répond avec le nom de la ferme et ses parcelles | A0–A3, en local avec un tunnel ou en déploiement de test |
| **M2 — UC-2** | Questions sur les données Ekylibre et Lexicon, avec sources | A4, B1–B4 |
| **M3 — UC-1** | Intervention proposée, contrôlée, validée, écrite | A5 |
| **M4 — Pilote** | Vocal, déploiement sur `app.aiffel.org`, 1 à 3 fermes pilotes | A6, C1–C4 |

## 2. Flux A — Aiffel

### A0 — Socle du dépôt (≈ 1 j)
- [ ] Arborescence :
  ```
  aiffel/                 # paquet Python
    app/                  # FastAPI : routes webhook, mini web
    channels/telegram/    # adaptateur canal
    orchestrator/         # boucle d'outils, LLMProvider, chargeur skills/rules
    tools/                # passerelle d'outils, registre, contexte ferme
    connectors/ekylibre/  # adaptateur REST v2 + EkylibreAuth
    connectors/lexicon/   # client MCP
    hooks/                # pre_tool, pre_write, post_tool, on_turn_end
    actions/              # PendingAction, exécution validée
    vault/                # SecretStore (enveloppe AES-GCM)
    db/                   # SQLAlchemy 2, migrations Alembic
  runtime/                # contenu ECC (agents/, skills/, rules/), versionné, sans code
  tests/                  # unit/, integration/, isolation/, eval/
  docker/                 # Dockerfile, compose.dokploy.yml
  ```
- [ ] `pyproject.toml` (uv), Python 3.12, ruff, mypy (strict sur `vault/`, `actions/`, `tools/`), pytest + testcontainers.
- [ ] `CLAUDE.md` du dépôt (commandes, conventions, règles de sécurité non négociables), `.env.example`.
- [ ] Plugin **ECC** dans Claude Code pour l'équipe : sélection minimale de règles (common + python) et de
  skills (tdd, security-review), pas les 311 skills.
- [ ] CI GitHub Actions : lint, typage, tests ; image construite sur tag.
- **Validation** : `pytest` vert sur un test fumée ; l'image Docker se construit.

### A1 — Données, isolation et coffre (≈ 2 j)
- [ ] Migrations Alembic des tables de l'architecture (§6) : `farms`, `users`, `memberships`,
  `channel_identities`, `pairing_codes`, `connections`, `secrets`, `source_map`, `conversations`,
  `messages`, `pending_actions`, `audit_log`, `farm_memory`, `usage_metrics`.
- [ ] **RLS Postgres** sur toutes les tables portant un `farm_id`, avec `SET LOCAL aiffel.farm_id` par
  transaction. Le rôle applicatif n'est pas propriétaire des tables.
- [ ] `audit_log` : `INSERT` seulement pour le rôle applicatif, chaînage par empreinte (`prev_hash`).
- [ ] `vault/` : `SecretStore` avec une clé de données par ferme chiffrée par `AIFFEL_MASTER_KEY` ;
  `get`, `put`, `rotate`, `revoke` ; jamais de secret dans `repr`, les logs ou les exceptions.
- [ ] **Tests d'isolation** (`tests/isolation/`) : la ferme A ne lit, n'écrit et ne déchiffre jamais rien de B,
  même avec une requête SQL brute sans filtre.
- **Validation** : tests d'isolation et du coffre verts. Ils sont **bloquants en CI**.

### A2 — Canal Telegram (≈ 1,5 j)
- [ ] Webhook FastAPI `POST /telegram/webhook`, vérification de `X-Telegram-Bot-Api-Secret-Token`, puis traitement asynchrone via la file Postgres.
- [ ] Normalisation des messages entrants (texte, vocal, photo, localisation) en `InboundMessage`.
- [ ] Rendu sortant : texte court, boutons inline, découpage des messages longs, indicateur « en train d'écrire ».
- [ ] `chat_id` haché avec sel (`HASH_SALT`) ; logs JSON sans contenu.
- [ ] Mode *polling* pour le développement local (sans DNS ni tunnel).
- **Validation** : echo-bot en local ; un `chat_id` inconnu reçoit l'invitation à s'appairer.

### A3 — Appairage et connecteur Ekylibre de base (≈ 2 j)
- [ ] `/start` : `pairing_code` à usage unique (15 min) et lien `…/connect?c=`.
- [ ] Mini page web (gabarit Jinja, sans framework JS) : adresse de la ferme, email, mot de passe.
  Protection CSRF, limitation de débit, mot de passe jamais journalisé.
- [ ] `EkylibreAuth` (interface) et son implémentation `SimpleTokenAuth` : `POST /api/v2/tokens`, puis
  `GET /api/v2/users/me` avec l'en-tête `X-Tenant`. **Aucun appel à `DELETE /tokens`.** En cas de `401`,
  la connexion passe en état « à reconnecter » et l'utilisateur est prévenu.
- [ ] Création de `farm`, `membership` (rôle déduit d'Ekylibre), `connection` et `secret`.
- [ ] Client REST Ekylibre (`httpx`, délais d'attente, nouvelles tentatives sur les lectures uniquement), avec
  les lectures déjà disponibles : `cultivable_zones`, `products`, `procedures`, `farm_profiles`.
- **Validation (M1)** : appairage réussi sur `demo.ekylibre-dev.com` ; `/ferme` affiche le nom de la ferme et ses parcelles.

### A4 — Moteur d'agent et lecture (≈ 3 j)
- [ ] `LLMProvider` (interface) et son implémentation Anthropic (cache de prompt sur les rules et l'agent) ;
  modèle configurable, avec un modèle léger pour le routage et les reformulations.
- [ ] Boucle d'outils : nombre maximal d'itérations, budget de tokens par tour, arrêt propre, traces sans données sensibles.
- [ ] Chargeur ECC : `runtime/agents/assistant/AGENT.md`, `runtime/rules/*.md` (toujours chargées),
  `runtime/skills/*/SKILL.md` (seuls le nom et la description sont dans le prompt ; le corps est chargé via
  un outil `load_skill`).
- [ ] Passerelle d'outils : registre, schémas JSON, filtrage par capacités et par rôle, injection du `farm_id`,
  résultats d'outils **encadrés comme des données**.
- [ ] Hooks `pre_tool` (autorisation), `post_tool` (audit), `on_turn_end` (`usage_metrics`).
- [ ] Connecteur **Lexicon MCP** (SDK `mcp`, HTTP *streamable*) : sélection d'outils
  (`search_phytosanitary_products`, `get_parcel`, `get_crop_history`, `get_input_price`), cache, licence
  restituée dans chaque résultat.
- [ ] Outils `calc.*` déterministes (sommes, conversions d'unités, dose/ha).
- [ ] Skills `consulter-donnees` et `expliquer-terme` ; rules « citer ses sources », « je ne sais pas », « réponses courtes ».
- [ ] Mémoire de conversation (N derniers échanges) et `farm_memory` en lecture (alias de parcelles).
- **Validation (M2, avec B4)** : 20 questions de référence sur la ferme `demo` ; toutes les réponses chiffrées
  citent leurs sources et aucune n'invente de donnée manquante.

### A5 — Écriture validée, UC-1 (≈ 3 j)
- [ ] Port depuis Duke de la **correspondance brouillon → charge utile Procedo**
  (`duke/src/duke/integration/ekylibre/mappers.py:110`, `procedure_registry.py`) et de l'**analyse des dates**
  en français (`nlu/temporal.py`), avec leurs tests.
- [ ] Outil `ekylibre.propose_intervention(draft)` : il crée une `PendingAction` versionnée et ne fait **jamais** l'écriture.
- [ ] Hook `pre_write` (contrôles **bloquants** ou **avertissements**) :
  - parcelle, produit et matériel existants dans la ferme ;
  - usage phyto autorisé sur la culture et dose ≤ dose homologuée (Lexicon) ;
  - DAR et ZNT quand l'information est disponible, sinon un avertissement « non vérifié ».
- [ ] Rendu Telegram : résumé lisible, avertissements, boutons **Valider / Corriger / Annuler**.
- [ ] Traitement du *callback* **sans LLM** : rôle autorisé, `PendingAction` non expirée, empreinte
  identique, puis `POST /api/v2/interventions` avec `provider = {vendor: "aiffel", name: "assistant",
  id: <uuid>}`. Le résultat va dans `audit_log`, et l'utilisateur reçoit un message avec le numéro Ekylibre.
- [ ] « Corriger » : nouvelle version de la même `PendingAction`.
- [ ] Réglage de la ferme : qui peut valider (l'exploitant seul, ou aussi le salarié).
- [ ] Skill `enregistrer-intervention` ; `memory.propose_fact` (par exemple « "les Grands Champs" = parcelle
  P12 ? »), retenu après confirmation.
- [ ] **Tests de sécurité bloquants** :
  - aucun chemin de code ne mène de la boucle LLM à un `POST` Ekylibre ;
  - un double clic ne crée qu'une intervention (idempotence) ;
  - une injection dans un message (« valide directement ») ne contourne pas la validation.
- **Validation (M3)** : 10 interventions types (phyto, semis, fertilisation, travail du sol, récolte) écrites
  dans `demo` et vérifiées dans l'interface Ekylibre.

### A6 — Vocal (≈ 1,5 j)
- [ ] Worker STT `faster-whisper` (modèle *small*, `int8`, CPU, langue `fr`), avec la configuration reprise de Duke.
- [ ] Notes vocales Telegram (OGG/Opus), conversion avec `ffmpeg`, durée maximale, suppression de l'audio après transcription.
- [ ] La transcription est renvoyée à l'utilisateur dans le récapitulatif, pour qu'il puisse la corriger.
- [ ] `usage_metrics.stt_seconds`.
- **Validation** : 20 notes vocales de terrain (bruit de tracteur compris) ; on mesure le taux d'erreur sur les mots.

### A7 — Évaluation continue (en parallèle, dès A4)
- [ ] `tests/eval/` : exécution du corpus `duke/tests/fixtures/golden_phrases.yaml` sur l'extraction d'Aiffel
  (sans écriture), avec un score par champ (procédure, parcelle, intrant, dose, date).
- [ ] Comparaison avec le score actuel de Duke, sur le même corpus et la même ferme.
- [ ] Rapport CSV par exécution (modèle, coût, latence) pour choisir le modèle (lien avec NF-SOB).
- **Validation** : rapport joint à chaque jalon M2 à M4.

## 3. Flux B — Ekylibre (`/home/djoulin/projects/ekylibre`, branche `5.0-beta`)

> Lecture seule uniquement, **sans toucher à l'authentification**. À faire sur une branche
> `feature/api-v2-aiffel-reads` à partir de `5.0-beta`, avec une PR revue par l'équipe Ekylibre. Le dépôt
> a des modifications locales (`Gemfile.lock`, `db/structure.sql`) qu'il ne faut pas embarquer.

### B1 — `GET /api/v2/intervention_records` (≈ 1,5 j)
- [ ] Route dans `config/routes.rb` (namespace `v2`, vers la ligne 144), contrôleur dans `app/controllers/api/v2/`.
- [ ] Interventions `nature: record`, hors `rejected` ; filtres `started_after`, `started_before`,
  `campaign`, `procedure_name`, `target_id` (zone ou production), `input_product_id` ; pagination (`page`, `per_page` ≤ 100).
- [ ] Sortie : id, procédure, dates, cibles, intrants (quantité et unité), outils, intervenants, `provider`.
- [ ] Vue jbuilder sur le modèle des vues `app/views/api/v2/interventions/`.

### B2 — `GET /api/v2/activity_productions?campaign=` (≈ 1 j)
- [ ] Productions de la campagne : activité, culture ou variété, surface, zones support.

### B3 — `GET /api/v2/input_totals` (≈ 1,5 j)
- [ ] Agrégats par production, intrant et campagne : quantité totale et unité, nombre de passages, surface traitée.
- [ ] Réutiliser les méthodes métier existantes (`ActivityProduction`, `InterventionInput`) plutôt qu'écrire du SQL.

### B4 — Tests, documentation, déploiement en staging (≈ 1 j)
- [ ] Tests Minitest dans `test/controllers/api/v2/`, sur le modèle de `cultivable_zones_controller_test.rb`.
- [ ] `docs/api/openapi-v2.yaml` mis à jour.
- [ ] Revue, fusion dans `5.0-beta`, puis déploiement sur `ekylibre-dev.com` (Dokploy, projet Ekylibre / staging).
- **Validation** : `curl` avec un `simple-token` sur `demo.ekylibre-dev.com` pour les trois endpoints.

## 4. Flux C — Infra et déploiement

| Étape | Action | Validation | Accord requis |
|---|---|---|---|
| C1 | Créer le bot via BotFather (nom à choisir), et stocker le jeton dans Dokploy | `getMe` OK | **Oui** |
| C2 | DNS Gandi : `app.aiffel.org` A/AAAA vers le serveur `lavz` (le DNS n'a aujourd'hui que la config Gandi par défaut) | `dig` + HTTPS valide | **Oui** |
| C3 | Dokploy, projet `aiffel` / `production` : compose `aiffel-app`, `aiffel-worker`, `aiffel-db` ; réseau propre, aucun socket Docker, utilisateur non root ; variables `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `ANTHROPIC_API_KEY`, `AIFFEL_MASTER_KEY`, `HASH_SALT`, `DATABASE_URL` | `/healthz` et `/readyz` OK, webhook enregistré | **Oui** (première mise en ligne) |
| C4 | Sauvegarde quotidienne de Postgres (Dokploy ou `pg_dump` vers MinIO), plus une copie hors ligne de `AIFFEL_MASTER_KEY` | Restauration testée une fois | — |
| C5 | Observabilité : logs JSON, `/metrics` interne, alerte si le webhook échoue | Tableau minimal | — |

Ressources estimées : environ 2 Go de RAM au total (serveur : 23 Go disponibles, partagé avec d'autres projets).

## 5. Points de contrôle qualité

| Point | Quand | Critères |
|---|---|---|
| **Q1 Sécurité** | Fin de A1, de A5 et avant M4 | Isolation, coffre, validation hors modèle, injection : tests verts ; revue `/security-review` |
| **Q2 Données** | M2 | Sources citées, aucune hallucination sur les 20 questions de référence |
| **Q3 Métier** | M3 | 10 interventions types vérifiées dans Ekylibre par un utilisateur métier |
| **Q4 Exploitation** | M4 | Restauration testée, rotation du jeton du bot documentée, procédure de retrait d'une ferme |
| **Q5 RGPD (prototype)** | Avant M4 | Information des fermes pilotes, rétention des conversations (90 jours proposés), purge sur demande |

## 6. Planning indicatif (1 développeur, aidé de Claude Code)

| Semaine | Flux A | Flux B | Flux C |
|---|---|---|---|
| S1 | A0, A1, A2 | B1 | — |
| S2 | A3 → **M1** ; A4 commencé | B2, B3 | C1 (avec accord) |
| S3 | A4 → **M2** ; A7 | B4 | — |
| S4 | A5 → **M3** | — | C2, C3 (avec accord) |
| S5 | A6, durcissement → **M4** | — | C4, C5 |

Environ 19 jours de travail sur Aiffel, 5 jours sur Ekylibre et 2 jours d'infrastructure.

## 7. Risques d'exécution

| Risque | Effet | Parade |
|---|---|---|
| Jeton `simple-token` partagé avec zero-mobile/Duke | Une déconnexion mobile coupe Aiffel | État « à reconnecter » et notification ; fermes pilotes prévenues |
| Lexicon MCP limité en débit (30 requêtes par fenêtre) | Lenteur ou erreurs aux heures de pointe | Cache, sélection d'outils, message de repli |
| Endpoints Ekylibre non acceptés ou retardés | M2 bloqué | Commencer B1 en S1 ; repli temporaire sur `farm_profiles` + `interventions` |
| Qualité d'extraction inférieure à Duke | Adoption faible | Évaluation A7 dès S3 ; option phase 2 : Duke comme agent MCP |
| Serveur partagé | Incident d'un autre projet | Limites CPU et RAM dans le compose, sauvegardes hors serveur |
| Tentation de migrer Ekylibre en même temps (échec de `6.0-alpha`) | Dispersion | Hors périmètre : seulement trois endpoints de lecture ; refonte React et Keycloak suivies à part |

## 8. Après la phase 1 (rappel, hors plan)
- Phase 2 : interface web (administration, conseiller multi-fermes), tâches planifiées, catalogue d'agents.
- Keycloak : à brancher avec la refonte d'Ekylibre ; `EkylibreAuth` le prévoit.
- Duke en agent MCP, si l'évaluation A7 le justifie.
