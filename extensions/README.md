# Extensions

Chaque sous-dossier est un paquet Python qui complète Aiffel sans que le cœur le connaisse : il se
déclare par l'entry point `aiffel.extensions` (et, s'il a des tables, `aiffel.migrations`). Voir
`aiffel/extensions.py`.

L'image Docker installe tout paquet présent ici. Un dossier peut être un sous-module git pointant
vers un dépôt privé : sans accès à ce dépôt (fork, CI publique), il reste vide et Aiffel fonctionne
sans l'extension.

```bash
git submodule update --init                    # récupère les extensions accessibles
uv pip install -e extensions/<nom>             # développement local avec une extension
```
