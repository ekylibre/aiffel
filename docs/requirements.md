# Aiffel — Spécification des exigences (brainstorm v0.1)

> Statut : brouillon issu de la session `/sc:brainstorm` du 2026-10-24.
> Étape suivante : `/sc:design` (architecture) puis `/sc:workflow` (plan d'implémentation).

## 1. Vision

Aiffel est un **orchestrateur conversationnel pour l'exploitation agricole**. Il fédère les logiciels
que l'agriculteur utilise déjà (open source ou propriétaires) derrière une interface unique et simple
(Telegram, web, puis mobile natif). L'agriculteur active des **agents métier** issus d'un catalogue,
les personnalise, et leur parle en langage naturel. Aiffel sait **où lire** et **où écrire** chaque
donnée, et détient de façon sécurisée les **identifiants** des applications de la ferme.

Aiffel ne remplace pas les logiciels métier : il ne devient pas une nouvelle source de vérité, il
route, croise, contrôle et trace.

## 2. Utilisateurs et rôles

| Rôle | Besoin principal |
|---|---|
| **Exploitant** (propriétaire du compte ferme) | Parler à ses agents, connecter ses logiciels, valider les écritures |
| **Salarié agricole** | Saisir des interventions au champ avec des droits restreints |
| **Conseiller / technicien** (coop, chambre, CER) | Suivre plusieurs fermes, avec un accès délégué et révocable |
| **Intégrateur / éditeur** | Publier des connecteurs et des agents dans le catalogue |
| **Opérateur d'instance** (SaaS ou auto-hébergé) | Administrer une instance Aiffel |

## 3. Exigences fonctionnelles

### 3.1 Canaux
- **F-CAN-1** : conversation via **Telegram** (texte, vocal, photo, localisation).
- **F-CAN-2** : **interface web** pour converser et administrer (connecteurs, agents, droits, historique).
- **F-CAN-3** (après la V1) : application **mobile native** (usage au champ, hors-ligne, GPS).
- **F-CAN-4** : la conversation et le contexte sont continus d'un canal à l'autre pour un même utilisateur.

### 3.2 Connecteurs
- **F-CON-1** : connecteurs vers les logiciels open source (Ekylibre, Lexicon, Lyvre) et propriétaires
  (Mes Parcelles, Geofolia, Smag Farmer).
- **F-CON-2** : stratégie d'accès **mixte** par connecteur : API officielle ou partenariat en priorité,
  automatisation navigateur sinon, import/export de fichiers en dernier recours.
- **F-CON-3** : chaque connecteur déclare ses **capacités** (entités lisibles et écrivables, fraîcheur,
  limites), ce qui permet à Aiffel de router les requêtes.
- **F-CON-4** : une **cartographie des sources** par ferme indique, pour chaque type de donnée
  (parcellaire, interventions, stocks, compta…), quelle application fait foi en lecture et en écriture.
- **F-CON-5** : **Lexicon** (https://lexicon.osfarm.org/, association OSFarm, code MIT) est une base de
  référence complète, **strictement en lecture seule** : Aiffel n'y écrit jamais. Il couvre 54 jeux de données :
  lieux (communes, cadastre, parcelles PAC, météo), exploitations (entreprises, aides PAC, HVE),
  qualité et bio, productions, phytosanitaire ANSES, documents R&D ACTA, etc.
  - Accès : **serveur MCP** (`https://lexicon.osfarm.org/mcp`), API JSON/CSV, ou paquets versionnés à
    charger dans PostgreSQL (option pour les instances auto-hébergées ou hors-ligne).
  - Usages : compléter et contrôler les saisies (produit, dose homologuée, DAR, ZNT), enrichir les réponses
    (parcelle PAC, météo), alimenter les contrôles réglementaires (UC-3).
  - Les données gardent la licence de leur source : Aiffel doit respecter et afficher ces licences, et
    certains jeux sont réservés aux adhérents.
- **F-CON-8** : **Ekylibre** (ERP agricole AGPL-3.0, Rails 5.2, PostgreSQL/PostGIS) est le **système de
  référence opérationnel** de la phase 1 (parcellaire, interventions, stocks, produits). L'équipe
  **peut le faire évoluer** pour les besoins d'Aiffel (branche `5.0-beta`, code local
  `/home/djoulin/projects/ekylibre`, instance de test `*.ekylibre-dev.com`).
  - Multi-tenant par schéma PostgreSQL (gem `apartment`) ; le tenant est résolu par le **sous-domaine**
    (`demo.ekylibre-dev.com` = ferme `demo`) ou par l'en-tête `X-Tenant`. Une ferme Aiffel doit donc
    connaître **l'URL de son instance et son tenant**, et Aiffel doit gérer plusieurs instances
    (SaaS Ekylibre et auto-hébergées).
  - API v2 existante : `cultivable_zones` (index/create/update), `interventions` (index/create/update),
    `products`, `variants`, `procedures`, `farm_profiles/:year`, `farm_accountancy/:year`, `lexicon/*`.
  - Authentification actuelle : `POST /api/v2/tokens` (email + mot de passe), puis l'en-tête
    `Authorization: simple-token <email> <token>`. Ce jeton **n'est pas limité en portée** : il donne
    tous les droits de l'utilisateur et ne connaît pas l'expiration.
  - Ekylibre embarque déjà Lexicon (schéma persistant `lexicon`).
- **F-CON-9** : évolutions Ekylibre requises (à confirmer en design) : **jetons délégués à portée
  limitée** (lecture/écriture par ressource, expiration, révocation, par exemple via OAuth2), un
  **serveur MCP Ekylibre** (ou une interface équivalente), et une extension de l'API aux ressources
  nécessaires à UC-2/UC-3 (stocks, achats, registre phyto, IFT).
- **F-CON-6** : **Lyvre** (comptabilité open source, multi-tenant, en cours de finalisation) est la source
  de vérité comptable pour l'UC-4. Il exposera une API et un **serveur MCP** ; Aiffel doit savoir consommer
  un connecteur de type MCP, en plus des connecteurs API, navigateur et fichiers.
- **F-CON-7** : le multi-tenant de Lyvre doit correspondre au multi-tenant d'Aiffel (une ferme Aiffel est
  rattachée à un tenant Lyvre).

### 3.3 Agents (catalogue et personnalisation)
- **F-AGT-1** : un catalogue d'agents fournis par Aiffel ou par des intégrateurs validés.
- **F-AGT-2** : l'exploitant active, désactive et paramètre un agent (cultures, labels, préférences,
  niveau d'autonomie) sans écrire de code.
- **F-AGT-3** : un agent se compose de briques inspirées d'ECC (voir §6) : skills, règles, hooks
  et mémoire.
- **F-AGT-4** : un routeur choisit l'agent compétent ou en coordonne plusieurs pour une demande.

### 3.3bis Annuaire des communs OSFarm
Source : https://www.osfarm.org/fr/communs/, flux JSON https://www.osfarm.org/data/communs.json
(265 fiches au 2026-10-24, dont 104 logiciels, 93 jeux de données, 40 matériels et 28 modèles ;
champs `id`, `titre`, `url`, `famille`, `categorie`, `licence`, `resume`, `interet`, `mots_cles`,
`techno`, `derniere_activite`, `type`, `partie_de`, `composants`).

- **F-CAT-1** : Aiffel **synchronise périodiquement** l'annuaire, en lecture seule, et l'utilise comme
  **catalogue de solutions** : chaque connecteur ou agent du catalogue Aiffel est relié à la fiche OSFarm
  correspondante (identifiant `id`).
- **F-CAT-2** : un agent « **Conseiller solutions** » répond aux questions du type « quel outil libre
  pour planifier mon maraîchage ? » ou « une API météo gratuite ? » à partir de l'annuaire. Il cite la fiche,
  la licence et la date de dernière activité, et signale si Aiffel sait déjà s'y connecter.
- **F-CAT-3** : l'annuaire sert à **prioriser la feuille de route des connecteurs** (logiciels libres
  ayant une API, catégories « Gestion d'exploitation » et « Données ouvertes et interopérabilité »).
- **F-CAT-4** : Aiffel respecte et affiche la licence de chaque fiche ; aucune donnée de ferme n'est envoyée à OSFarm.
- **F-CAT-5** (contribution) : Aiffel, Lyvre et les connecteurs publiés peuvent être **proposés à
  l'annuaire**. Au 2026-10-24, Ekylibre y figure, mais ni Lyvre ni Aiffel ; et l'entrée « Lex Icon » est un
  projet d'icônes, pas le Lexicon d'OSFarm.

### 3.4 Cas d'usage V1
- **UC-1 Enregistrer une intervention** : « J'ai traité la parcelle des Grands Champs avec X à 1,5 L/ha ».
  Aiffel extrait, complète (date, parcelle, produit via Lexicon), contrôle (dose, DAR, ZNT), présente
  un récapitulatif, et **écrit après confirmation** dans l'application de traçabilité qui fait foi.
- **UC-2 Interroger ses données** : « Combien d'azote sur mes blés ? ». La réponse croise plusieurs
  sources et **cite ses sources**.
- **UC-3 Réglementaire / PAC** : préparation des éléments de déclaration, contrôles de conformité
  (IFT, ZNT, DAR, registre phyto).
- **UC-4 Gestion / compta** : stocks, factures, coûts par parcelle ou par culture.

### 3.5 Écriture et validation
- **F-ECR-1** : toute écriture vers un logiciel tiers passe par une **proposition, une confirmation
  explicite de l'utilisateur, puis l'exécution**.
- **F-ECR-2** : la confirmation est faisable en un geste sur Telegram (boutons inline).
- **F-ECR-3** : chaque écriture est journalisée (qui, quoi, où, quand, sur quelle validation) et
  annulable quand le logiciel cible le permet.
- **F-ECR-4** : les droits d'écriture dépendent du rôle (un salarié peut proposer, l'exploitant valide…).

### 3.6 Coffre d'identifiants
- **F-VLT-1** : stockage des identifiants, jetons OAuth et clés API de chaque application de la ferme.
- **F-VLT-2** : les secrets ne sont **jamais exposés au modèle de langage** ; seuls les connecteurs y
  accèdent, au moment de l'exécution.
- **F-VLT-3** : l'exploitant peut consulter, renouveler et révoquer chaque accès, et révoquer les
  délégations (conseiller).

### 3.7 Mémoire
- **F-MEM-1** : une mémoire par ferme (parcellaire usuel, alias de parcelles, habitudes, préférences).
- **F-MEM-2** : la mémoire est consultable et modifiable par l'exploitant (transparence, RGPD).

## 4. Exigences non fonctionnelles

| ID | Exigence |
|---|---|
| **NF-SEC-1** | Secrets chiffrés au repos, avec clé par tenant ; rotation ; audit des accès |
| **NF-SEC-2** | Protection contre l'injection de prompt (messages, documents et pages web lus par les connecteurs) |
| **NF-SEC-3** | Isolation stricte entre fermes (multi-tenant), y compris pour les conseillers multi-fermes |
| **NF-RGPD** | Hébergement en UE, registre des traitements, export et suppression des données de la ferme |
| **NF-DEP** | Déploiement en SaaS **et** en auto-hébergement (coopératives, chambres) à partir d'un même code |
| **NF-OSS** | Cœur open source (licence à définir) ; connecteurs propriétaires éventuellement séparés |
| **NF-FIA** | Un connecteur en panne ne bloque pas les autres ; dégradation explicite (« Geofolia indisponible ») |
| **NF-TRA** | Traçabilité réglementaire : journal des écritures non modifiable |
| **NF-UX** | Utilisable sans formation ; réponses courtes ; le vocal marche dans un environnement bruyant |
| **NF-PERF** | Réponse conversationnelle < 5 s hors appels aux logiciels tiers lents (à préciser) |
| **NF-IAC-1** (IA de confiance, explicabilité) | Toute réponse factuelle cite ses sources (logiciel, fiche Lexicon, document) ; toute proposition d'écriture montre d'où vient chaque valeur |
| **NF-IAC-2** (hybridation homme-machine) | L'humain décide : validation avant écriture, autonomie réglable par agent (voir Q7) |
| **NF-IAC-3** (droit à l'erreur) | Correction et annulation simples après coup ; Aiffel dit quand il ne sait pas plutôt que d'inventer (hallucination) |
| **NF-SOB** (sobriété numérique) | Utiliser le plus petit modèle suffisant, un modèle local quand c'est possible, et mesurer la consommation par ferme (tokens, énergie) ; surveiller l'effet rebond |
| **NF-SOUV** (souveraineté) | Pouvoir fonctionner avec un fournisseur LLM européen ou un modèle local ; données de ferme hébergées en UE et réversibles |
| **NF-EXT** | Ajouter un connecteur ou un agent ne modifie pas le cœur |

## 5. User stories et critères d'acceptation (échantillon)

**US-1** — En tant qu'exploitant, je dicte une intervention sur Telegram pour ne pas la ressaisir le soir.
- Étant donné une ferme reliée à Ekylibre, quand j'envoie un vocal décrivant un traitement, Aiffel
  m'affiche un récapitulatif structuré avec les boutons *Valider* / *Corriger*.
- Si la dose dépasse l'homologation, ou si la parcelle est en ZNT, Aiffel bloque ou avertit **avant**
  de proposer la validation.
- Après *Valider*, l'intervention apparaît dans Ekylibre et Aiffel me renvoie la référence créée.

**US-2** — En tant que conseiller, je consulte les IFT de mes 30 fermes sans avoir leurs mots de passe.
- Je ne vois que les fermes qui m'ont délégué l'accès ; je n'accède jamais aux identifiants.
- La révocation par l'exploitant prend effet immédiatement.

**US-3** — En tant qu'exploitant, je connecte Geofolia depuis le web.
- Aiffel propose le meilleur mode d'accès disponible (API, sinon identifiants) et explique les risques.
- Un test de connexion confirme l'accès et liste les données disponibles.

**US-4** — En tant qu'intégrateur, je publie un connecteur.
- Le connecteur déclare ses capacités, passe une suite de tests de conformité et une revue de sécurité.

## 6. Intégration du concept ECC

ECC (MIT, https://github.com/affaan-m/ecc) est retenu **sur deux plans** :

**a) Outil de développement d'Aiffel** : plugin ECC dans Claude Code pour l'équipe (plan → TDD →
revue → vérification → mémoire).

**b) Modèle d'exécution des agents agricoles** : on reprend la décomposition d'ECC :

| ECC | Aiffel |
|---|---|
| Agents | Agents métier (traçabilité, agronomie, PAC, gestion) avec un contexte propre |
| Skills | Workflows métier chargés à la demande (« enregistrer un traitement »…) |
| Hooks | Garde-fous **déterministes, hors du LLM** : validation avant écriture, contrôles réglementaires, audit |
| Rules | Réglementation et contraintes de la ferme (bio, HVE, cahiers des charges) |
| Memory / instincts | Mémoire par ferme |
| AgentShield | Revue de sécurité des agents et connecteurs du catalogue |

Le Claude Agent SDK lit nativement ce format (skills, subagents, hooks) : c'est une piste à évaluer
pendant `/sc:design`, sans engagement pour l'instant.

## 6bis. Existant et retour d'expérience

Aiffel **repart de zéro** : c'est un nouveau projet indépendant. Le projet R&D *Assistant personnel de
l'agriculteur multi-canal* (juin 2026, `ekylibre/claudedocs/`) sert de **retour d'expérience**, pas de
base de code imposée.

| Composant existant | Statut pour Aiffel |
|---|---|
| **Duke** (chatbot agricole Python : reconnaissance des entités F1 0,94, STT Whisper, routeur LLM Claude/Mistral/Ollama) | **À évaluer en design** : agent du catalogue, brique réutilisée, ou remplacé |
| **zero-mobile** (React Native Expo, hors-ligne WatermelonDB, pilote Android) | **Non décidé** : support du mobile Aiffel ou nouvelle application (phase 5) |
| **NanoClaw** (Agent SDK, un conteneur par ferme, Telegram) | Non repris ; enseignements ci-dessous |
| **OneCLI** (passerelle qui injecte les identifiants) | **À évaluer en design**, face à un coffre auto-hébergé (exigence NF-DEP) |

**Enseignements à intégrer :**
- **Isolation** : le pilote a dû choisir entre un conteneur par ferme (isolation forte, coût × N) et
  un routage central (isolation logique). Le même arbitrage s'applique à Aiffel (et à Hermes) ; il est à
  trancher en design.
- **Socket Docker** : monté pour lancer les conteneurs d'agents, il donne l'équivalent de root sur l'hôte.
  Aiffel ne doit pas dépendre d'un socket Docker partagé.
- **Jetons Ekylibre complets** : le pilote les a acceptés comme risque résiduel en V1. Aiffel en fait un
  **prérequis** (F-CON-9).
- **Liste blanche des `chat_id`, confirmation avant toute écriture, logs sans contenu ni jeton,
  `chat_id` haché** : à reprendre tels quels.
- **Enrôlement manuel** (fichier YAML édité par un opérateur) : ne passe pas à l'échelle. Aiffel exige un
  **enrôlement en libre-service** (appairage du canal par code depuis le web).
- **WhatsApp** : canal professionnel dominant chez les agriculteurs, à ajouter à la feuille de route après Telegram.
- **Tâches planifiées vers la messagerie** (DAR, stock bas, météo, récap du soir) : besoin confirmé.

## 6ter. Inspirations tirées de l'annuaire OSFarm

Sélection initiale, à revoir en design :

| Fiche | Intérêt pour Aiffel |
|---|---|
| **farmOS** (GPL-2.0), **LiteFarm** (GPL-3.0), **Tania**, **Brinjel** (AGPL-3.0, maraîchage) | Candidats connecteurs « gestion d'exploitation » après Ekylibre, et éléments de comparaison fonctionnelle |
| **Data Food Consortium** (standard OWL/RDF) | Format pivot possible pour les échanges entre connecteurs (produits, catalogues, commandes) |
| **Calculatrice IFT** (ministère) | Référence de calcul pour UC-3, à confronter à Lexicon |
| **Météo-France**, **Open-Meteo**, **IGN** | Sources météo et géographiques (déjà en partie dans Lexicon) pour les alertes et les contrôles de traitement |
| **Triple Performance** (wiki Neayi, CC-BY-SA) | Base de connaissances agronomiques pour les réponses de l'agent agronome (avec citation) |
| **Pl@ntNet** | Identification d'adventices ou de maladies à partir d'une photo envoyée sur Telegram |
| **Traccar**, **Home Assistant**, **OpenRemote** | Données de capteurs et de GPS (Ekylibre a déjà un plugin Traccar) |
| **Gaia** (La Ferme Digitale + OSFarm, IA générative agricole) | Projet frère : convergence à étudier |
| **Mistral AI** | Fournisseur LLM souverain (lié à Q6) |
| **brapi-mcp-server**, **epicpast/nsip** (serveurs MCP agricoles), **ZD-claude-plugin** (agents/skills Claude Code géospatiaux) | Exemples du motif « connecteur MCP » et « skills métier » retenu pour Aiffel |
| **FFT_nano** (agent agricole par SMS), **Agronaut** (agent agronomique local) | Projets comparables : positionnement et bonnes idées |

## 6quater. GAIA et *L'IA des Champs*

**GAIA** (https://www.gaia-ia.org/, La Ferme Digitale + OSFarm, avec Mistral AI) est un **projet distinct**.
Il organise des hackathons d'IA générative agricole pour produire des **prototypes**, sans viser la gestion
complète d'une ferme. Il compte 8 hackathons depuis le SIA 2024 (SIVAL, SPACE, Tech'Elevage, SARA Abidjan),
avec des démonstrateurs Hugging Face (`gaia-mistral` : chatbot, RAG PDF, nuisibles/élevage).

Apports pour Aiffel :
- **Vivier d'agents** : un prototype de hackathon qui a fait ses preuves peut devenir un **agent du catalogue
  Aiffel** (rôle intégrateur, F-AGT-1). Exemples de thèmes : stress thermique, pâturage, rendement en serre,
  analyse de sols, diagnostic de maladies.
- **Banc d'essai** : Aiffel peut proposer aux hackathons un kit (API/MCP de test, ferme `demo`, données
  anonymisées) pour que les prototypes se branchent directement dessus.
- **Sources de données** (mallette GAIA, 27 sources), en complément de Lexicon : E-Phy (ANSES), Agritox,
  EU Pesticides Database, médicaments vétérinaires, Légifrance (Code rural), Agreste, API Triple Performance,
  Open-Meteo/Météo-France, IGN, Pl@ntNet, GBIF, SIRENE. Pour chacune : vérifier si Lexicon la couvre déjà
  avant d'écrire un connecteur.

**_L'IA des Champs — Une autre culture de l'IA_** (Hervé Pillaud, Karine Cailleaux-Breton, David Joulin,
Éditions France Agricole ; glossaire https://ia-des-champs.lovable.app/glossaire, environ 250 termes) :
- Ses principes (IA de confiance, explicabilité, hybridation homme-machine, droit à l'erreur, sobriété,
  souveraineté numérique et alimentaire, gouvernance des données) deviennent les exigences **NF-IAC**,
  **NF-SOB** et **NF-SOUV** (§4).
- Le glossaire sert de **référentiel de vocabulaire** : Aiffel explique ses termes techniques à
  l'agriculteur avec les définitions du livre (onboarding, aide contextuelle), sous réserve de l'accord
  de l'éditeur (Q16).

## 7. Risques identifiés

1. **CGU des éditeurs propriétaires** : l'automatisation avec les identifiants de l'agriculteur peut
   être interdite ou bloquée. Aucune relation n'existe aujourd'hui avec Isagri, Smag ou les Chambres :
   les connecteurs propriétaires sont repoussés en phase 4, et une démarche de partenariat est à lancer
   d'ici là.
2. **Responsabilité du coffre d'identifiants** : cible de grande valeur ; responsabilité juridique en cas de fuite.
3. **Erreur réglementaire** : une intervention mal enregistrée a des conséquences en cas de contrôle.
   D'où la validation humaine et les hooks déterministes.
4. **Périmètre V1** : traité par le découpage en phases (§9).
6. **Dépendance à Lyvre** : il est encore en cours de finalisation, donc son API et son MCP peuvent évoluer
   avant la phase 3.
5. **Sources contradictoires** : la même donnée diffère entre deux logiciels.

## 8. Questions ouvertes

- ~~Q1~~ : tranchée, voir §9.
- ~~Q2~~ : Lyvre est la comptabilité open source multi-tenant, avec une API et un MCP (voir F-CON-6).
- ~~Q3~~ : aucune relation éditeur pour l'instant (voir risque 1).
- **Q10** : rôle de Duke dans Aiffel ?
- **Q11** : l'application mobile d'Aiffel est-elle zero-mobile ou une nouvelle application ?
- **Q12** : coffre d'identifiants : OneCLI, Vault/OpenBao, Infisical ou solution maison ?
- **Q13** : isolation des agents : un conteneur par ferme ou un routage central avec isolation logique ?
- **Q14** : quelle est la licence du flux `communs.json`, et peut-on proposer des fiches (Lyvre, Aiffel) ?
- ~~Q15~~ : GAIA est un projet distinct (hackathons de prototypes) ; voir §6quater.
- **Q16** : peut-on réutiliser le glossaire de *L'IA des Champs* dans Aiffel (droits de l'éditeur) ?
- **Q9** : quand l'API et le MCP de Lyvre seront-ils stables ? Qui en maintient le contrat ?
- **Q4** : modèle économique (abonnement ferme, licence structure, catalogue payant) ?
- **Q5** : licence open source du cœur (AGPL, Apache-2.0…) ?
- **Q6** : quel(s) fournisseur(s) de LLM, et les instances auto-hébergées doivent-elles pouvoir utiliser un modèle local ?
- **Q7** : quel niveau d'autonomie autoriser plus tard (écriture sans validation pour les actions à faible risque) ?
- **Q8** : exigences hors-ligne pour le mobile ?

## 9. Découpage en phases (validé)

| Phase | Périmètre | Connecteurs | Canal |
|---|---|---|---|
| **1 — MVP** | UC-1 Enregistrer une intervention, UC-2 Interroger ses données | Ekylibre, Lexicon | Telegram |
| **2** | Administration (connecteurs, agents, droits, délégation conseiller) et conversation | idem | Web |
| **3** | UC-3 Réglementaire / PAC, UC-4 Gestion / compta | + Lyvre (API/MCP) | Telegram + Web |
| **4** | Logiciels propriétaires (mode d'accès mixte) | + Mes Parcelles, Geofolia, Smag Farmer | idem |
| **5** | Application mobile native, marketplace intégrateurs | — | + Mobile |
