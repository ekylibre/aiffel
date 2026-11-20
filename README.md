# Aiffel

Orchestrateur conversationnel pour l'exploitation agricole : l'agriculteur parle à ses agents sur Telegram
(puis sur le web et le mobile), et Aiffel lit et écrit dans les logiciels de la ferme (Fourmy, Lyvre,
Lexicon…) avec une validation humaine avant chaque écriture.

Aiffel s'utilise de deux façons :

- **auto-hébergée (libre)** : image Docker publique `ghcr.io/ekylibre/aiffel:main`, BYO strict,
  pas de support. Voir [docs/ops/install-libre.md](docs/ops/install-libre.md).
- **hébergée (payante)** : abonnement sur [ekylibre.com](https://ekylibre.com), aux côtés de
  Lyvre et Fourmy. Pivot documenté dans
  [docs/architecture/pivot_fourmy_billing.md](docs/architecture/pivot_fourmy_billing.md) (ADR-10).

## Documentation

- Exigences : [docs/requirements.md](docs/requirements.md)
- Architecture de la phase 1 : [docs/architecture/phase1.md](docs/architecture/phase1.md)
- Architecture du pivot : [docs/architecture/pivot_fourmy_billing.md](docs/architecture/pivot_fourmy_billing.md)
- Plan d'implémentation phase 1 : [claudedocs/workflow_phase1_prototype.md](claudedocs/workflow_phase1_prototype.md)
- Plan d'implémentation du pivot : [claudedocs/workflow_pivot_fourmy_billing.md](claudedocs/workflow_pivot_fourmy_billing.md)
- Développement : [CLAUDE.md](CLAUDE.md)

Licence : AGPL-3.0-or-later.
