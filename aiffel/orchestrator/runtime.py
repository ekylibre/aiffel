"""Chargement du contenu agent au format ECC (ADR-03) depuis ``runtime/``.

- ``agents/<nom>/AGENT.md`` : identité et périmètre de l'agent ;
- ``rules/*.md`` : règles toujours présentes dans le prompt ;
- ``skills/<nom>/SKILL.md`` : workflows chargés à la demande (seuls ``name`` et ``description``
  figurent dans le prompt ; le corps est lu par l'outil ``load_skill``).

Le prompt « stable » ne dépend d'aucune ferme : il est identique d'une requête à l'autre et donc
mis en cache.
"""

from dataclasses import dataclass
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parents[2] / "runtime"


@dataclass(frozen=True)
class Document:
    name: str
    description: str
    body: str


def parse_markdown(text: str, fallback_name: str) -> Document:
    """Frontmatter minimal (``clé: valeur`` entre deux lignes ``---``), puis le corps."""
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---\n"):
        head, sep, rest = text[4:].partition("\n---\n")
        if sep:
            body = rest
            for line in head.splitlines():
                key, colon, value = line.partition(":")
                if colon:
                    meta[key.strip()] = value.strip()
    return Document(
        name=meta.get("name", fallback_name),
        description=meta.get("description", ""),
        body=body.strip(),
    )


@dataclass(frozen=True)
class AgentRuntime:
    agent: Document
    rules: tuple[Document, ...]
    skills: dict[str, Document]

    @classmethod
    def load(cls, root: Path = RUNTIME_DIR, agent: str = "assistant") -> "AgentRuntime":
        agent_doc = parse_markdown((root / "agents" / agent / "AGENT.md").read_text(), agent)
        rules = tuple(
            parse_markdown(path.read_text(), path.stem)
            for path in sorted((root / "rules").glob("*.md"))
        )
        skills = {}
        for path in sorted((root / "skills").glob("*/SKILL.md")):
            doc = parse_markdown(path.read_text(), path.parent.name)
            skills[doc.name] = doc
        return cls(agent_doc, rules, skills)

    def stable_prompt(self) -> str:
        parts = [self.agent.body, "# Règles"]
        parts += [rule.body for rule in self.rules]
        if self.skills:
            parts.append(
                "# Savoir-faire disponibles\n"
                "Avant une tâche qui correspond à l'un d'eux, charge-le avec l'outil `load_skill` "
                "et suis-le.\n"
                + "\n".join(f"- `{s.name}` : {s.description}" for s in self.skills.values())
            )
        return "\n\n".join(parts)
