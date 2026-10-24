# runtime/ — contenu de l'agent (format ECC)

Uniquement du texte versionné ; le code qui le charge est dans `aiffel/orchestrator/`.

- `agents/<nom>/AGENT.md` : identité, périmètre et outils autorisés d'un agent.
- `skills/<nom>/SKILL.md` : workflow métier chargé à la demande (frontmatter `name` et `description` ; corps lu via `load_skill`).
- `rules/*.md` : règles toujours chargées dans le prompt.

Contenu ajouté avec le lot A4 (`claudedocs/workflow_phase1_prototype.md`).
