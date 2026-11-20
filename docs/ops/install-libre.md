# Installation libre d'Aiffel

> **Avertissement — aucun support fourni (D-REL)**
>
> Cette procédure décrit un mode d'exécution **auto-hébergé, non supporté** d'Aiffel. L'image
> publique `ghcr.io/ekylibre/aiffel:main` suit la branche `main` du dépôt :
>
> - **pas de version taguée**, pas de changelog public, pas de SemVer ;
> - **pas de rétro-compatibilité garantie** (le schéma Postgres peut changer d'un jour à l'autre) ;
> - **pas de garantie de disponibilité** ni de délai de correction ;
> - **vos données vous appartiennent** et vous êtes responsable de leurs sauvegardes.
>
> L'offre **supportée** passe par l'abonnement sur [ekylibre.com](https://ekylibre.com) (hébergement
> lavz, sauvegardes, surveillance, migration) ; voir [D-DEP](../../claudedocs/pivot_ekylibre_lyvre_fourmy.md).

## 1. Ce que fait l'installation libre

- Démarre un bot Telegram Aiffel chez vous, connecté à **votre** base Postgres locale.
- Branche l'agent sur **vos** clés LLM (BYO strict, décision [D-BYO](../../claudedocs/pivot_ekylibre_lyvre_fourmy.md)).
- Vous laisse pointer les connecteurs vers **votre** instance Fourmy (gestion agricole) et Lyvre
  (compta/paie) une fois que ceux-ci auront livré leur API (voir § 5).
- **N'embarque aucun service de facturation** : pas de quota, pas d'essai, pas d'abonnement.

## 2. Ce qu'il vous faut

| Élément | Comment l'obtenir |
|---|---|
| Un serveur Linux (≥ 2 Go RAM) | n'importe quel hébergeur ou une machine chez vous |
| Docker Engine + Docker Compose v2 | <https://docs.docker.com/engine/install/> |
| Un bot Telegram | parler à [@BotFather](https://t.me/BotFather), commande `/newbot` |
| Une URL HTTPS publique joignable par Telegram | reverse-proxy devant l'app (Caddy, Traefik, nginx…) |
| Une clé LLM Anthropic (recommandé) ou OpenRouter | <https://console.anthropic.com/> |
| Optionnel : une clé Pl@ntNet | <https://my.plantnet.org/> |

## 3. Démarrage

```bash
# 1. Cloner le dépôt
git clone https://github.com/ekylibre/aiffel.git
cd aiffel

# 2. Copier l'exemple d'environnement
cp .env.libre.example .env
# puis éditer .env (voir § 4 ci-dessous pour chaque variable)

# 3. Lancer la stack
docker compose -f docker/compose.libre.yml --env-file .env up -d

# 4. Vérifier la santé de l'application
curl http://127.0.0.1:8000/healthz
# → {"status":"ok"}
```

L'application écoute par défaut sur `127.0.0.1:8000`. Placez votre reverse-proxy TLS devant, en
passant `AIFFEL_PUBLIC_BASE_URL=https://votre-domaine` dans `.env` pour que les URLs d'appairage
renvoient la bonne base.

## 4. Variables d'environnement

Toutes dans `.env`, aucune ne doit être laissée vide **sauf** si marquée optionnelle.

| Variable | Rôle | Comment la générer |
|---|---|---|
| `AIFFEL_PUBLIC_BASE_URL` | URL HTTPS où Telegram joint le webhook | votre reverse-proxy |
| `POSTGRES_PASSWORD` | Propriétaire Postgres (migrations) | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `AIFFEL_APP_PASSWORD` | Rôle applicatif (soumis à la RLS) | idem |
| `AIFFEL_MASTER_KEY` | Clé maître du coffre (voir note ci-dessous) | commande ci-dessous |
| `AIFFEL_HASH_SALT` | Sel pour hacher les `chat_id` dans les logs | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `AIFFEL_TELEGRAM_BOT_TOKEN` | Token donné par BotFather | — |
| `AIFFEL_TELEGRAM_WEBHOOK_SECRET` | Clé partagée avec Telegram | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `AIFFEL_ANTHROPIC_API_KEY` | Clé LLM Anthropic (recommandé) | console.anthropic.com |
| `AIFFEL_OPENROUTER_API_KEY` | Clé LLM OpenRouter (optionnel) | openrouter.ai |
| `AIFFEL_PLANTNET_API_KEY` | Clé Pl@ntNet (optionnel) | my.plantnet.org |
| `AIFFEL_STT_ENABLED` | Transcription des vocaux (défaut : `true`) | — |
| `AIFFEL_HTTP_BIND` / `AIFFEL_HTTP_PORT` | Interface et port d'écoute de l'app (défaut `127.0.0.1:8000`) | — |

Pour générer la clé maître :

```bash
docker run --rm -it ghcr.io/ekylibre/aiffel:main \
  python -c "from aiffel.vault import generate_key; print(generate_key())"
# Format attendu : 1:<clé base64>
```

> **Conservez la clé maître hors ligne.** Elle chiffre tous les secrets des fermes (jetons Fourmy,
> Lyvre, clés LLM des fermes). **La perdre les rend tous illisibles**, sans récupération possible.

## 5. Enregistrer le webhook Telegram

Une fois l'application joignable en HTTPS :

```bash
docker compose -f docker/compose.libre.yml exec app \
  python -m aiffel.channels.telegram.webhook set
```

Vérifier avec `getWebhookInfo` que l'URL et le secret sont bien ceux attendus.

## 6. Appairage d'une ferme

1. L'exploitant parle à votre bot Telegram et envoie `/start`.
2. Le bot répond avec un lien de raccordement à ouvrir dans un navigateur.
3. **Partie Fourmy / Lyvre à venir (T2 et T4)** : pour le moment, les connecteurs métier Aiffel
   sont en cours de refonte dans le cadre du pivot
   [ADR-10](../architecture/pivot_fourmy_billing.md). Cette section sera complétée dès que les
   API Fourmy (gestion agricole) et Lyvre (compta/paie) seront disponibles. En attendant, Aiffel
   démarre correctement sans connecteur, mais n'a aucune donnée métier à lire.

## 7. Sauvegardes

Rien n'est automatique en mode libre. Vous êtes responsable de :

- la sauvegarde régulière du volume Postgres `aiffel-libre-db` ;
- la copie hors ligne de `AIFFEL_MASTER_KEY` ;
- la rotation des secrets (bot Telegram, clés LLM) en cas de fuite.

Exemple de sauvegarde minimale (à adapter) :

```bash
docker compose -f docker/compose.libre.yml exec db \
  pg_dump -U aiffel aiffel | gzip > "aiffel-$(date +%F).sql.gz"
```

## 8. Mise à jour

```bash
docker compose -f docker/compose.libre.yml pull
docker compose -f docker/compose.libre.yml up -d
```

L'image `:main` peut casser la rétro-compatibilité à tout moment. **Testez d'abord sur une
instance hors production** et sauvegardez la base avant chaque mise à jour.

## 9. Désinstallation

```bash
docker compose -f docker/compose.libre.yml down
docker volume rm aiffel-libre_aiffel-libre-db   # détruit la base locale
```

## 10. Signalement de bugs

Issues GitHub bienvenues, mais **aucun engagement de délai**. Pour un support garanti, passez par
l'abonnement sur [ekylibre.com](https://ekylibre.com).
