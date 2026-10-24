# CLAUDE.md

Aiffel est un orchestrateur conversationnel qui fédère les logiciels d'une exploitation agricole
(Ekylibre, Lexicon, puis Lyvre et d'autres) derrière Telegram, puis le web et le mobile.

- Exigences : `docs/requirements.md`
- Architecture phase 1 (ADR) : `docs/architecture/phase1.md`
- Plan d'implémentation : `claudedocs/workflow_phase1_prototype.md`

## Commandes

```bash
uv sync                                        # dépendances (Python 3.12 géré par uv)
uv run pytest -q                               # tests
uv run ruff check . && uv run ruff format .    # lint et format
uv run mypy aiffel                             # typage (strict sur vault/, actions/, tools/)
docker compose -f docker/compose.dev.yml up -d db   # Postgres local sur 127.0.0.1:5442
uv run alembic upgrade head                    # migrations (AIFFEL_MIGRATION_DATABASE_URL, propriétaire)
uv run uvicorn aiffel.app.main:app --reload    # application en local (rôle aiffel_app)
uv run python -m aiffel.worker                  # worker de la file des messages entrants
uv run python -m aiffel.channels.telegram.poll  # dev : Telegram en polling (bot de test, sans webhook)
docker build -f docker/Dockerfile -t aiffel .  # image
```

## Organisation

| Dossier | Rôle |
|---|---|
| `aiffel/app/` | FastAPI : webhook Telegram, mini web de raccordement |
| `aiffel/channels/` | Adaptateurs de canaux (Telegram) |
| `aiffel/orchestrator/` | Boucle d'outils, `LLMProvider`, chargement des skills et des rules |
| `aiffel/tools/` | Passerelle d'outils : registre, contexte ferme, filtrage par rôle et capacités |
| `aiffel/connectors/` | Ekylibre (REST v2, `EkylibreAuth`), Lexicon (client MCP) |
| `aiffel/hooks/` | Hooks déterministes : `pre_tool`, `pre_write`, `post_tool`, `on_turn_end` |
| `aiffel/actions/` | `PendingAction` et exécution validée |
| `aiffel/vault/` | `SecretStore` (chiffrement d'enveloppe) |
| `aiffel/db/` | SQLAlchemy 2, migrations Alembic |
| `runtime/` | Contenu agent au format ECC (`agents/`, `skills/`, `rules/`) : du texte, pas de code |

## Règles non négociables

1. **Aucune écriture dans un logiciel tiers sans validation humaine hors modèle** (ADR-04) : un outil
   d'écriture crée une `PendingAction`, et seul le callback du bouton Valider (code déterministe) exécute.
2. **Le modèle ne voit jamais un secret ni un `farm_id`** : la passerelle d'outils injecte le contexte de
   ferme et le connecteur déchiffre le secret au moment de l'appel (ADR-01, ADR-06).
3. **Pas d'outil générique** pour l'agent : ni shell, ni fichiers, ni HTTP libre.
4. **Logs** : jamais de contenu utilisateur, de jeton, d'email ou de `chat_id` en clair (`aiffel/logging.py`).
5. **Ekylibre** : ne jamais appeler `DELETE /api/v2/tokens`, car le `simple-token` est partagé avec
   zero-mobile et Duke (ADR-07).
6. Les tests `tests/isolation/` et ceux de validation des écritures sont **bloquants**.

## Base de données

- Schéma `aiffel`. Les migrations tournent avec le **propriétaire** ; l'application se connecte avec le
  rôle **`aiffel_app`**, créé `NOLOGIN` par la migration. Au déploiement :
  `ALTER ROLE aiffel_app LOGIN PASSWORD '...'`.
- Données d'une ferme : toujours via `farm_session(sessions, farm_id)`, ce qui fixe `aiffel.farm_id`
  pour la RLS. Seule exception inter-fermes : la fonction `aiffel.farms_of_user(user_id)`.
- `audit_log` : `INSERT` et `SELECT` seulement pour l'application ; empreintes chaînées calculées par
  un trigger ; vérification par `aiffel.db.audit.verify_chain`.
- Clé maître : `AIFFEL_MASTER_KEY=1:<clé>` (générer avec
  `uv run python -c "from aiffel.vault import generate_key; print(generate_key())"`). **La perdre rend
  tous les secrets illisibles** : il faut une copie hors ligne.

## Conventions

- Code et identifiants en anglais ; docstrings, commentaires et documentation en français.
- Typage complet ; Pydantic v2 aux frontières (API, outils, connecteurs).
- Commits en anglais, au présent, avec un titre court.

## Outillage de développement (ECC)

L'équipe utilise le plugin ECC (https://github.com/affaan-m/ecc) dans Claude Code, avec une sélection
minimale : règles `common` et `python`, skills TDD et revue de sécurité. Il s'installe par
`/plugin marketplace add affaan-m/ecc`, puis `/plugin install ecc@ecc` ; on désactive ensuite ce qui ne sert pas.
