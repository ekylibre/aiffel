# Déploiement sur Dokploy

Guide court pour poser Aiffel sur le projet Dokploy `aiffel` / env `production`
(serveur `seconde-nature.cloud`, accès `ssh lavz`, DNS `aiffel.org` chez Gandi).

Rappel sécurité : la **clé maître** (`AIFFEL_MASTER_KEY`) chiffre tous les secrets des fermes
(jetons Ekylibre, clés LLM). **La perdre les rend tous illisibles.** Garder une copie hors ligne,
pas dans Dokploy seul.

## 1. Pré-requis à faire une fois

1. **DNS** chez Gandi : enregistrement `A` (ou `AAAA`) de `app.aiffel.org` vers l'IP du serveur.
   Vérifier la propagation avec `dig +short app.aiffel.org`.
2. **Bot Telegram** via BotFather : créer `@AiffelBot` (ou autre), récupérer le jeton. Désactiver
   l'adhésion aux groupes et la lecture des messages de groupe (`/setjoingroups`, `/setprivacy`).
3. **Clé maître** : la générer et la conserver hors ligne :
   ```bash
   uv run python -c "from aiffel.vault import generate_key; print(generate_key())"
   # Format : 1:<clé base64>
   ```
4. **Secrets web** : deux chaînes aléatoires de 32 octets :
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"  # pour HASH_SALT
   python -c "import secrets; print(secrets.token_urlsafe(32))"  # pour TELEGRAM_WEBHOOK_SECRET
   ```
5. **Mots de passe Postgres** : un pour le propriétaire `aiffel`, un pour `aiffel_app`
   (ne pas réutiliser le même).

## 2. Création de l'application Dokploy

Dans le projet `aiffel`, créer une application de type **Compose** (pas Docker Compose Stack), source
GitHub `ekylibre/aiffel`, branche `main`, chemin `docker/compose.dokploy.yml`.

### Variables d'environnement à régler

Toutes se saisissent dans l'onglet « Environment » de Dokploy, pas dans `.env`.

| Variable | Description |
|---|---|
| `POSTGRES_PASSWORD` | Mot de passe du propriétaire Postgres `aiffel`. |
| `AIFFEL_APP_PASSWORD` | Mot de passe du rôle applicatif `aiffel_app` (RLS activée). |
| `AIFFEL_PUBLIC_BASE_URL` | `https://app.aiffel.org`. |
| `AIFFEL_MASTER_KEY` | Clé maître (`1:…`). |
| `AIFFEL_HASH_SALT` | Chaîne aléatoire ≥ 32 octets. |
| `AIFFEL_TELEGRAM_BOT_TOKEN` | Jeton BotFather. |
| `AIFFEL_TELEGRAM_WEBHOOK_SECRET` | Chaîne aléatoire ≥ 32 octets (header Telegram). |
| `AIFFEL_ANTHROPIC_API_KEY` | Clé Aiffel (facultative si une ferme apporte la sienne). |
| `AIFFEL_OPENROUTER_API_KEY` | Clé OpenRouter d'Aiffel (facultative). |
| `AIFFEL_PLANTNET_API_KEY` | Clé Pl@ntNet (facultative) : identification des maladies et des plantes sur photo. |

Variables facultatives (valeur par défaut dans `compose.dokploy.yml`) :
`AIFFEL_LOG_LEVEL`, `AIFFEL_LLM_PROVIDER`, `AIFFEL_LLM_MODEL`, `AIFFEL_LLM_EFFORT`,
`AIFFEL_EKYLIBRE_ALLOWED_DOMAINS`, `AIFFEL_STT_ENABLED`.

### Domaine

Onglet « Domains » : service `app`, port `8000`, host `app.aiffel.org`, HTTPS avec génération
automatique du certificat (Let's Encrypt via Traefik).

### Volume

Le compose déclare un volume nommé `aiffel-db-data` : Dokploy en garde le contenu entre
redéploiements. Pour une sauvegarde, voir §5.

## 3. Premier déploiement

1. **Déployer** depuis Dokploy. Ordre de démarrage géré par les `depends_on` :
   - `db` (healthcheck `pg_isready`),
   - `migrate` : applique `alembic upgrade head` puis `ALTER ROLE aiffel_app LOGIN PASSWORD …`,
     s'arrête (code 0 attendu),
   - `app` (healthcheck `GET /healthz`) et `worker`.
2. **Vérifier** depuis l'extérieur :
   ```bash
   curl -fsS https://app.aiffel.org/healthz
   curl -fsS https://app.aiffel.org/readyz
   ```
3. **Enregistrer le webhook Telegram**. Depuis Dokploy, exécuter dans le conteneur `app` :
   ```bash
   python -m aiffel.channels.telegram.webhook set
   ```
   Vérifier :
   ```bash
   curl -fsS "https://api.telegram.org/bot$AIFFEL_TELEGRAM_BOT_TOKEN/getWebhookInfo"
   ```
4. **Essai bout en bout** : `/start` au bot, suivre le lien, raccorder un compte Ekylibre
   `demo.ekylibre-dev.com`, puis `/ferme` renvoie les parcelles.

## 4. Mises à jour

- Déploiement : « Redeploy » dans Dokploy. Les migrations tournent automatiquement avant
  le redémarrage de l'app et du worker. Une migration qui échoue laisse l'ancienne version
  en service tant que la nouvelle n'est pas prête.
- **Rollback** : revenir au commit précédent (git revert ou tag) et redéployer. Les migrations
  Alembic sont écrites pour un `downgrade` quand c'est raisonnable ; sinon, restaurer la
  sauvegarde (§5) avant de rejouer.

## 5. Sauvegardes et secrets hors ligne

- **Postgres** : Dokploy peut faire un `pg_dump` planifié sur le service `db` (quotidien).
  Tester la restauration au moins une fois en staging.
- **Clé maître** : copie chiffrée hors ligne (gestionnaire de mots de passe de l'équipe).
  Sans elle, les secrets stockés en base deviennent illisibles.

## 6. Site vitrine (aiffel.org)

Le site commercial d'EKYLIBRE SAS n'est plus dans ce dépôt : il est déployé par une application
Dokploy distincte (`aiffel` / `production` / `site`), depuis un dépôt privé.

## 7. Dépannage rapide

| Symptôme | Piste |
|---|---|
| `app` reste en démarrage | `migrate` en échec : lire ses logs (variables manquantes ? schéma incompatible ?). |
| `/readyz` renvoie 503 `database injoignable` | mot de passe `AIFFEL_APP_PASSWORD` différent de celui injecté par `migrate`. Rejouer `migrate`. |
| Telegram ne reçoit rien | webhook non enregistré ou en-tête secret différent. Rejouer `python -m aiffel.channels.telegram.webhook set`. |
| Message « AIFFEL_MASTER_KEY requise » | variable d'environnement vide dans Dokploy. |
