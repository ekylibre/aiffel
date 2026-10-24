"""Messages vocaux (A6) : transcription locale avec faster-whisper (NF-SOUV, ADR-09).

Configuration reprise de Duke : modèle *small*, ``int8`` sur CPU, français, filtre de voix (VAD)
et ``beam_size=5``. L'audio n'est jamais écrit sur disque : il reste en mémoire le temps de la
transcription, puis il est abandonné. La transcription est renvoyée à l'utilisateur pour qu'il
puisse la corriger.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from typing import Any, Protocol

import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aiffel.db.models import UsageMetric
from aiffel.db.session import farm_session

log = structlog.get_logger()

DEFAULT_MAX_SECONDS = 180
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
STT_METRIC_MODEL = "stt"

TOO_LONG = (
    "Votre message vocal est trop long ({seconds} s). Limitez-vous à {limit} s, ou écrivez-moi."
)
FAILED = (
    "Je n'ai pas réussi à écouter votre message vocal. Réessayez, ou écrivez-moi votre demande."
)
EMPTY = "Je n'ai rien entendu dans ce message vocal. Réessayez dans un endroit plus calme."
UNAVAILABLE = (
    "La transcription des vocaux est indisponible pour le moment. Écrivez-moi votre demande."
)


class STTUnavailableError(RuntimeError):
    """Moteur de transcription absent (dépendance non installée, modèle introuvable)."""


class AudioTooLargeError(ValueError):
    """Fichier audio au-delà de la taille acceptée."""


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes) -> str: ...


# Récupère l'audio d'un message auprès du canal (Telegram : getFile puis téléchargement).
AudioFetcher = Callable[[str, int], Awaitable[bytes]]


@dataclass
class WhisperTranscriber:
    """faster-whisper chargé à la première note vocale ; transcription dans un fil séparé."""

    model_name: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "fr"
    cache_dir: str | None = None
    _model: Any = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def transcribe(self, audio: bytes) -> str:
        model = await self._ensure_model()
        return await asyncio.to_thread(self._run, model, audio)

    def _run(self, model: Any, audio: bytes) -> str:
        # PyAV (dépendance de faster-whisper) décode l'OGG/Opus de Telegram : pas de ffmpeg système.
        segments, _info = model.transcribe(
            BytesIO(audio), language=self.language, vad_filter=True, beam_size=5
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise STTUnavailableError(
                        "faster-whisper absent : uv sync --extra stt"
                    ) from exc
                log.info("stt.loading_model", model=self.model_name, device=self.device)
                self._model = await asyncio.to_thread(
                    WhisperModel,
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                    download_root=self.cache_dir,
                )
                log.info("stt.model_loaded", model=self.model_name)
        return self._model


@dataclass(frozen=True)
class VoiceResult:
    text: str | None
    error: str | None = None


@dataclass
class VoiceService:
    sessions: async_sessionmaker[AsyncSession]
    fetch: AudioFetcher
    transcriber: Transcriber
    max_seconds: int = DEFAULT_MAX_SECONDS
    max_bytes: int = DEFAULT_MAX_BYTES

    async def transcribe(
        self, farm_id: uuid.UUID, file_id: str, duration_s: int | None
    ) -> VoiceResult:
        # Durée annoncée par le canal : on refuse avant de télécharger quoi que ce soit.
        if duration_s is not None and duration_s > self.max_seconds:
            return VoiceResult(None, TOO_LONG.format(seconds=duration_s, limit=self.max_seconds))
        started = datetime.now(UTC)
        try:
            audio = await self.fetch(file_id, self.max_bytes)
            text = (await self.transcriber.transcribe(audio)).strip()
        except STTUnavailableError:
            log.warning("stt.unavailable")
            return VoiceResult(None, UNAVAILABLE)
        except AudioTooLargeError:
            return VoiceResult(None, TOO_LONG.format(seconds="?", limit=self.max_seconds))
        except Exception as exc:
            # Pas de message d'exception : il pourrait contenir une URL avec le jeton du bot.
            log.warning("stt.failed", error_type=type(exc).__name__)
            return VoiceResult(None, FAILED)
        elapsed = (datetime.now(UTC) - started).total_seconds()
        log.info("stt.done", audio_s=duration_s, ms=int(elapsed * 1000))
        await self._record(farm_id, duration_s or 0)
        if not text:
            return VoiceResult(None, EMPTY)
        return VoiceResult(text)

    async def _record(self, farm_id: uuid.UUID, seconds: int) -> None:
        """``usage_metrics.stt_seconds`` : durée d'audio transcrite, par ferme et par jour."""
        async with farm_session(self.sessions, farm_id) as session:
            stmt = insert(UsageMetric).values(
                farm_id=farm_id,
                day=datetime.now(UTC).date(),
                model=STT_METRIC_MODEL,
                stt_seconds=seconds,
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["farm_id", "day", "model"],
                    set_={"stt_seconds": UsageMetric.stt_seconds + stmt.excluded.stt_seconds},
                )
            )
