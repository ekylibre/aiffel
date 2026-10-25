# Communication et identité d'Aiffel

Ce dossier rassemble le concept, l'identité visuelle et les supports destinés au monde agricole. Ce travail est mené en
parallèle du développement. Version 0.1 du 2026-10-24.

## Supports

| Fichier | Public | Version en ligne (privée) |
|---|---|---|
| `identite.html` | Monde agricole élargi : conseillers, coopératives, chambres, OSFarm/GAIA | https://claude.ai/artifact/LaMQYdKaJWQTajen9qAwBk |
| `fiche-a4.html`, `fiche-a4.pdf` | Agriculteurs, fiche recto à imprimer et distribuer | https://claude.ai/artifact/2aBFeCpE2F1yYP2KXBPqBq |
| `schema.html`, `schema.pdf` | Schéma fonctionnel en tour Eiffel : agriculteur au sommet, outils dans la base | https://claude.ai/artifact/P8wiJxf5Cpsn5h5pagSy3m |
| `site/` (racine du dépôt) | Grand public : site vitrine (concept, offres, garanties) et mentions légales, déployé par `docker/compose.site.yml` (voir `docs/ops/dokploy.md` §6) | https://aiffel.org (public, en ligne depuis le 2026-10-25) |
| `signe.svg`, `signe-petit.svg` | Logo : version complète, et version simplifiée sous 24 px | |
| Document commercial (doc partagé, pas de fichier) | Prospects et partenaires : offres Libre (Telegram), En ligne (app.aiffel.org) et Pro (WhatsApp) | https://claude.ai/artifact/T2TFefaA6knbLPPLAjAJKr |
| `veille-concurrentielle.md` | Interne : comparatif Tellia, Hector, Hectar AI, xSupra, Terragrow (2026-10-25) | |

Les pages HTML sont écrites pour l'outil Artifact : elles n'ont ni `<!doctype>` ni `<html>`. Pour regénérer un PDF (par exemple la fiche A4) :

```bash
cd docs/communication
{ printf '<!doctype html><html lang="fr"><meta charset="utf-8">'; cat fiche-a4.html; } > /tmp/fiche-print.html
google-chrome --headless=new --no-pdf-header-footer --virtual-time-budget=8000 \
  --print-to-pdf=fiche-a4.pdf /tmp/fiche-print.html
```

La commande s'applique à l'identique pour `schema.html` → `schema.pdf`.

## Concept

- **Constat** : l'IA fait fleurir les outils mais crée de l'instabilité. Il y a trop d'applications nouvelles, trop
  d'anciennes qu'on ne peut pas quitter, des données personnelles partout et des mots de passe ingérables.
- **Le nom** : Aiffel s'entend comme Eiffel. La tour tient depuis 1889 parce que des milliers de pièces simples y sont
  assemblées en une structure solide. Aiffel relie de la même façon les applications et les données utiles à une
  ferme, en un ensemble simple et durable.
- **Devise** : Solide · Simple · Durable.
- **Accroche** : « Dites-le une fois. Aiffel le range au bon endroit. »
- **Promesse** : un seul interlocuteur pour tous les logiciels de la ferme, qui cite ses sources et attend votre accord
  avant d'écrire.
- **Engagements** :
  - frugal : rien à acheter, le plus petit modèle qui suffit, transcription vocale locale, aucune donnée en double ;
  - 100 % ouvert : code et connecteurs ouverts, installable en service hébergé dans l'UE ou sur le serveur d'une
    ferme, d'une coopérative ou d'une CUMA, réversible.
- **Compatibilité** : « Branché sur tous les outils », dans cet ordre de préférence : par API ou MCP, sinon par les
  fichiers (Excel, CSV, PDF, photos), et en dernier recours avec les identifiants de la ferme gardés dans un coffre
  chiffré.
- **Principes** : ils viennent de *L'IA des Champs* : explicabilité, hybridation homme-machine, droit à l'erreur,
  sobriété, souveraineté.

## Identité visuelle

- **Signe** : un A en treillis, dont les deux jambes évoquent la tour Eiffel et le palissage. La barre est une bande
  verte, une parcelle en herbe à l'horizon. Trois sillons partent sous la barre ; ils disparaissent sous 24 px.
- **Palette** :

  | Nom | Valeur | Usage |
  |---|---|---|
  | Blé en herbe | `#58AD45` (sombre `#6CBF5A`) | Accent unique : barre du signe, Valider, surlignages |
  | Terre de sillon | `#16211C` | Texte et signe, fond des versions sombres |
  | Calcaire | `#F1F3EE` | Fond principal |
  | Bouillie | `#245F86` | Liens, étiquettes de source |
  | Lichen | `#56655D` | Texte secondaire |

- **Typographie** (Google Fonts) :
  - Big Shoulders Display pour les titres ;
  - Atkinson Hyperlegible pour le texte ;
  - IBM Plex Mono pour les données.
- **Ton** :
  - Aiffel emploie les noms de la ferme (« Parcelle du Moulin, 12,4 ha ») et dit ce qui manque et comment avancer.
  - Il dit qui fait quoi : « Je prépare l'intervention, vous validez ».
  - Il donne des faits vérifiables plutôt que des slogans (« révolutionnez… »).

## Écrans : conformité avec le code

Les maquettes reprennent les textes réels du bot (relevé du 2026-10-24) :

- l'accusé du vocal, dans `aiffel/channels/handlers.py` ;
- le récapitulatif « 📝 Intervention à valider », dans `aiffel/tools/catalog.py` ;
- les contrôles, dans `aiffel/actions/checks.py` ;
- les boutons ✅ Valider, ✏️ Corriger et ✖️ Annuler, dans `aiffel/orchestrator/agent.py` ;
- la confirmation « Enregistrée dans Ekylibre ✅ (n° …) », dans `aiffel/actions/service.py`.

Les phrases des cas de terrain viennent du corpus de Duke (267 phrases, banc A7). Si ces textes changent dans le code,
il faut mettre à jour les maquettes.

L'intervention de la démo s'adosse à une vraie sortie Lexicon : BOUILLIE BORDELAISE MANICA N.C. (AMM 2010618, firme
MANICA S.P.A.), homologuée sur Vigne contre Mildiou — dose max 7,5 kg/ha, DAR 21 jours, 5 applications. Vérifié le
2026-10-25 via `search_phytosanitary_products` sur `https://lexicon.osfarm.org/mcp`.

À l'état actuel du code :

- aucune annulation n'est possible après validation ;
- le récapitulatif n'a pas d'étiquette de source par ligne ;
- les photos et les PDF ne sont pas analysés.

La facture reste donc une **projection** (phase 3, avec Lyvre), présentée comme telle.

## Points ouverts

- [ ] La réponse « mildiou » de la galerie est plausible, mais aucune source agronomique ne la soutient en phase 1. La
      garder ou la remplacer par une demande de précision.
- [ ] Vérifier juridiquement la connexion par identifiants aux logiciels propriétaires (Geofolia, Smag Farmer,
      MesParcelles, Isacompta) au regard de leurs conditions d'utilisation.
- [ ] Ajouter un QR code vers https://aiffel.org sur la fiche A4 et le schéma : le site est en ligne.
- [ ] Mentions légales : ajouter le capital social et la ville du greffe du RCS d'EKYLIBRE
      (`site/mentions-legales.html`), et vérifier l'adresse légale d'Hostinger.
- [ ] Le site affirme « open source » alors que le dépôt `ekylibre/aiffel` est privé et sans `LICENSE` :
      publier le dépôt sous licence, ou adoucir la formulation.
- [ ] Vérifier que la boîte `contact@aiffel.org` (affichée sur le site) existe chez Gandi.
- [ ] Mettre à jour le doc commercial : le point « aiffel.org est encore parqué » est réglé.
- [ ] Le message `/aide` du bot est périmé (`aiffel/channels/handlers.py:14-20`) : à signaler côté développement.
- [ ] Supports suivants possibles : verso questions-réponses de la fiche, deck de présentation, design system.
