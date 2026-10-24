"""File des messages entrants, sur une table Postgres (pas de Redis en phase 1, ADR-09).

- Le webhook n'écrit que dans la file et répond aussitôt à Telegram.
- Le worker réserve un travail (``FOR UPDATE SKIP LOCKED``), le traite, puis le clôt. Le contenu
  brut est effacé dès la fin, réussie ou définitivement échouée.
- Un travail resté « en cours » plus de ``STALE_AFTER`` (worker arrêté net) est repris.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from aiffel.db.models import InboundJob

MAX_ATTEMPTS = 5
STALE_AFTER = timedelta(minutes=5)


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    channel: str
    payload: dict[str, Any]
    attempts: int


async def enqueue(
    session: AsyncSession, channel: str, external_update_id: int, payload: dict[str, Any]
) -> bool:
    """Ajoute un travail ; ``False`` si cet événement est déjà connu (renvoi par le canal)."""
    result = await session.execute(
        insert(InboundJob)
        .values(channel=channel, external_update_id=external_update_id, payload=payload)
        .on_conflict_do_nothing(index_elements=["channel", "external_update_id"])
        .returning(InboundJob.id)
    )
    return result.scalar_one_or_none() is not None


async def claim(session: AsyncSession) -> ClaimedJob | None:
    now = datetime.now(UTC)
    job = await session.scalar(
        select(InboundJob)
        .where(
            or_(
                (InboundJob.status == "pending") & (InboundJob.run_after <= now),
                (InboundJob.status == "processing") & (InboundJob.locked_at < now - STALE_AFTER),
            )
        )
        .order_by(InboundJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None or job.payload is None:
        return None
    job.status, job.locked_at, job.attempts = "processing", now, job.attempts + 1
    await session.flush()
    return ClaimedJob(id=job.id, channel=job.channel, payload=job.payload, attempts=job.attempts)


async def complete(session: AsyncSession, job_id: int) -> None:
    await session.execute(
        update(InboundJob)
        .where(InboundJob.id == job_id)
        .values(status="done", payload=None, locked_at=None)
    )


async def fail(session: AsyncSession, job: ClaimedJob, error: str) -> None:
    """Replanifie avec un délai croissant, ou abandonne après ``MAX_ATTEMPTS`` essais."""
    if job.attempts >= MAX_ATTEMPTS:
        values: dict[str, Any] = {"status": "failed", "payload": None}
    else:
        delay = timedelta(seconds=2**job.attempts)
        values = {"status": "pending", "run_after": datetime.now(UTC) + delay}
    await session.execute(
        update(InboundJob)
        .where(InboundJob.id == job.id)
        .values(locked_at=None, last_error=error[:200], **values)
    )
