# Extensions locales

Chaque sous-dossier est un paquet Python qui complète Aiffel sans que le cœur le connaisse : il se
déclare par l'entry point `aiffel.extensions` (et, s'il a des tables, `aiffel.migrations`). Voir
`aiffel/extensions.py`. L'image Docker installe tout paquet présent ici.

Les extensions tirées d'un dépôt git (par exemple le client du billing d'Aiffel en ligne, dépôt
privé `ekylibre/ekylibre-billing`) ne passent plus par ce dossier : l'image les installe depuis
l'argument de build `AIFFEL_EXTENSIONS` (voir `docker/Dockerfile` et `docs/ops/dokploy.md`).

Développement local avec le client du billing, cloné à côté d'Aiffel :

```bash
uv pip install -e ../ekylibre-billing/client
```
