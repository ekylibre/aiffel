"""Décodage réel d'un vocal OGG/Opus par faster-whisper (régression : PyAV 19 l'avait cassé)."""

import io
import math

import pytest


def _ogg_opus(seconds: float = 1.0) -> bytes:
    av = pytest.importorskip("av")
    numpy = pytest.importorskip("numpy")
    buffer = io.BytesIO()
    container = av.open(buffer, "w", format="ogg")
    stream = container.add_stream("libopus", rate=48000)
    stream.layout = "mono"
    t = numpy.arange(int(48000 * seconds)) / 48000
    samples = (0.2 * numpy.sin(2 * math.pi * 440 * t)).astype(numpy.float32)
    for start in range(0, len(samples), 960):
        frame = av.AudioFrame.from_ndarray(
            samples[start : start + 960].reshape(1, -1), format="flt", layout="mono"
        )
        frame.rate = 48000
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)
    container.close()
    return buffer.getvalue()


def test_telegram_voice_format_is_decoded() -> None:
    audio = pytest.importorskip("faster_whisper.audio")
    samples = audio.decode_audio(io.BytesIO(_ogg_opus()), sampling_rate=16000)
    assert abs(len(samples) - 16000) < 400  # 1 s à 16 kHz


def test_agricultural_vocabulary_is_given_to_whisper() -> None:
    from aiffel.voice import AGRI_PROMPT, WhisperTranscriber

    seen: dict[str, object] = {}

    class Model:
        def transcribe(self, _audio: object, **options: object) -> tuple[list[object], None]:
            seen.update(options)
            return [], None

    WhisperTranscriber()._run(Model(), b"")
    assert seen["initial_prompt"] == AGRI_PROMPT and "pulvérisé" in AGRI_PROMPT
    assert seen["language"] == "fr" and seen["vad_filter"] is True
