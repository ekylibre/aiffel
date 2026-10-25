"""Worker : traite la file des messages entrants (``python -m aiffel.worker``)."""

import asyncio
import contextlib
import signal
from collections.abc import Sequence

import structlog

from aiffel import queue
from aiffel.channels.dispatcher import Dispatcher
from aiffel.channels.telegram.api import TelegramClient, TelegramError
from aiffel.channels.telegram.parse import CHANNEL as TELEGRAM
from aiffel.channels.telegram.parse import parse_update
from aiffel.db.session import global_session
from aiffel.extensions import Periodic

log = structlog.get_logger()


class Worker:
    def __init__(
        self,
        dispatcher: Dispatcher,
        telegram: TelegramClient,
        periodic: Sequence[Periodic] = (),
    ) -> None:
        self.dispatcher = dispatcher
        self.telegram = telegram
        self.sessions = dispatcher.sessions
        # Tâches régulières des extensions (échéances, file d'envoi…), à côté de la file.
        self.periodic = tuple(periodic)

    async def run_once(self) -> bool:
        """Traite au plus un travail ; ``False`` si la file est vide."""
        async with global_session(self.sessions) as session:
            job = await queue.claim(session)
        if job is None:
            return False
        try:
            await self._process(job)
        except TelegramError as exc:
            log.warning("job.retry", job_id=job.id, attempts=job.attempts, error=str(exc))
            async with global_session(self.sessions) as session:
                await queue.fail(session, job, str(exc))
        except Exception as exc:
            # Pas de message d'exception dans les logs : il peut contenir du contenu utilisateur.
            log.exception("job.error", job_id=job.id, error_type=type(exc).__name__)
            async with global_session(self.sessions) as session:
                await queue.fail(session, job, type(exc).__name__)
        else:
            async with global_session(self.sessions) as session:
                await queue.complete(session, job.id)
        return True

    async def _process(self, job: queue.ClaimedJob) -> None:
        if job.channel != TELEGRAM:
            log.warning("job.unknown_channel", job_id=job.id, channel=job.channel)
            return
        message = parse_update(job.payload)
        if message is None:
            return
        log.info("message.received", job_id=job.id, kind=message.kind.value)
        if message.callback_id:
            await self.telegram.answer_callback(message.callback_id)
        await self.telegram.typing(message.external_chat_id)
        for reply in await self.dispatcher.handle(message):
            await self.telegram.send(message.external_chat_id, reply)
        log.info("message.answered", job_id=job.id)

    async def _run_periodic(self, task: Periodic, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await task.run()
            except Exception as exc:
                # Une erreur ne doit ni arrêter le worker, ni empêcher le passage suivant.
                log.exception("periodic.error", task=task.name, error_type=type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), task.every.total_seconds())

    async def run_forever(self, idle_sleep: float = 0.5, stop: asyncio.Event | None = None) -> None:
        if stop is None:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError):
                    loop.add_signal_handler(sig, stop.set)
        tasks = [asyncio.create_task(self._run_periodic(t, stop)) for t in self.periodic]
        log.info("worker.started", periodic=[t.name for t in self.periodic])
        try:
            while not stop.is_set():
                if not await self.run_once():
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), idle_sleep)
        finally:
            stop.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        log.info("worker.stopped")


async def _main() -> None:
    from aiffel.app.container import build_services

    services = build_services()
    try:
        await services.worker().run_forever()
    finally:
        await services.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
