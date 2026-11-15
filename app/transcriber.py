"""Offline speech-to-text with faster-whisper, tuned for low latency.

``Transcriber``      loads one Whisper model from ``models/`` (never the internet).
``LiveTranscriber``  background thread: microphone audio -> text, as it is spoken.

How the live loop works
-----------------------
Audio accumulates in a growing window (the current "utterance"). About once a
second the whole window is transcribed again, so the text sharpens as more
context arrives; each pass is published as a *partial* line. The line becomes
*final* when the speaker pauses (voice-activity detection finds trailing silence)
or when the window gets long; then a new window starts. Silence is never sent
to the model.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from . import config
from .audio import AudioError, AudioSource

log = logging.getLogger(__name__)

SAMPLE_RATE = config.SAMPLE_RATE

# Hints that make Whisper spell Bible book names correctly. They deliberately contain
# no chapter or verse numbers: on room noise Whisper sometimes echoes its prompt, and
# an echoed "John 3:16" would become a false detection. A book name alone never does.
_PROMPTS: dict[str, str] = {
    "en": "A church sermon. Genesis, Psalms, Isaiah, Habakkuk, Matthew, John, Romans, "
          "Corinthians, Ephesians, Philippians, Hebrews, Revelation.",
    "fr": "Une prédication à l'église. Genèse, Psaumes, Ésaïe, Habacuc, Matthieu, Jean, Romains, "
          "Corinthiens, Éphésiens, Philippiens, Hébreux, Apocalypse.",
}
_PROMPTS["auto"] = _PROMPTS["en"] + " " + _PROMPTS["fr"]


class TranscriberError(RuntimeError):
    """A speech-model problem, with a message that can be shown to the operator."""


@dataclass(frozen=True)
class Segment:
    start: float   # seconds from the start of the audio window
    end: float
    text: str


def is_model_available(size: str) -> bool:
    return (config.whisper_model_dir(size) / "model.bin").exists()


class Transcriber:
    """One loaded Whisper model."""

    def __init__(self, size: str) -> None:
        if not is_model_available(size):
            raise TranscriberError(
                f"The speech model “{size}” is not downloaded yet. Connect to the internet once and run "
                f"the setup script again (or: python scripts/download_models.py --whisper {size})."
            )
        try:
            import ctranslate2
            from faster_whisper import WhisperModel
        except Exception as exc:
            raise TranscriberError(f"The speech library could not be loaded ({exc}). Run the setup script.") from exc

        self.size = size
        path = str(config.whisper_model_dir(size))
        threads = max(4, min(8, (os.cpu_count() or 4) // 2))
        self._model: Any = None
        self.device = "cpu"
        try:
            if ctranslate2.get_cuda_device_count() > 0:      # NVIDIA GPU: much faster
                self._model = WhisperModel(path, device="cuda", compute_type="float16", local_files_only=True)
                self.device = "cuda"
        except Exception as exc:
            log.warning("GPU not usable (%s); using the CPU.", exc)
            self._model = None
        if self._model is None:
            try:
                self._model = WhisperModel(
                    path, device="cpu", compute_type="int8", cpu_threads=threads, local_files_only=True
                )
            except Exception as exc:
                raise TranscriberError(f"Could not load the speech model “{size}” ({exc}).") from exc
        log.info("Speech model %s loaded on %s", size, self.device)

    def transcribe(self, audio: np.ndarray, language: str = "auto") -> list[Segment]:
        """Transcribe 16 kHz mono float32 audio. language: "auto", "en" or "fr"."""
        segments, _info = self._model.transcribe(
            audio,
            language=None if language == "auto" else language,
            beam_size=1,                         # greedy: fastest, good enough for clear speech
            temperature=0.0,
            condition_on_previous_text=False,    # each window stands alone (no error snowballing)
            initial_prompt=_PROMPTS.get(language, _PROMPTS["auto"]),
            vad_filter=True,                     # built-in VAD: silence is cut before the model runs
            vad_parameters={"min_silence_duration_ms": 400},
        )
        result: list[Segment] = []
        for seg in segments:
            text = seg.text.strip()
            # Typical hallucination on noise: low confidence + "probably no speech".
            if not text or (seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0):
                continue
            result.append(Segment(float(seg.start), float(seg.end), text))
        return result


def speech_spans(audio: np.ndarray) -> list[tuple[int, int]]:
    """(start, end) sample positions of speech in the audio, using faster-whisper's VAD."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    options = VadOptions(min_silence_duration_ms=300, speech_pad_ms=150)
    return [(int(s["start"]), int(s["end"])) for s in get_speech_timestamps(audio, options)]


# callback(utterance_id, text, final)
TextCallback = Callable[[int, str, bool], None]
# callback(status, message) with status in "loading" | "listening" | "stopped" | "error"
StatusCallback = Callable[[str, str], None]


class LiveTranscriber(threading.Thread):
    """Background thread turning a live audio source into partial/final text lines."""

    MIN_NEW_AUDIO = 0.5      # seconds of new audio before transcribing again
    PAUSE = 0.7              # seconds of trailing silence that end an utterance
    MAX_WINDOW = 12.0        # seconds: longer windows are cut at a segment boundary
    KEEP_SILENCE = 0.3       # seconds kept in front of the next utterance

    def __init__(
        self,
        source: AudioSource,
        get_transcriber: Callable[[], Transcriber],
        get_language: Callable[[], str],
        on_text: TextCallback,
        on_status: StatusCallback,
    ) -> None:
        super().__init__(name="live-transcriber", daemon=True)
        self.source = source
        self._get_transcriber = get_transcriber
        self._get_language = get_language
        self._on_text = on_text
        self._on_status = on_status
        self._stop_event = threading.Event()
        self._utterance = 0
        self.last_pass_seconds = 0.0     # how long the latest transcription took (for diagnostics)

    def stop(self) -> None:
        self._stop_event.set()

    # -- thread body ----------------------------------------------------------
    def run(self) -> None:
        try:
            self._on_status("loading", "Loading the speech model…")
            transcriber = self._get_transcriber()       # may take several seconds the first time
            if self._stop_event.is_set():
                return
            self.source.start()
        except (AudioError, TranscriberError) as exc:
            self._on_status("error", str(exc))
            return
        except Exception as exc:
            log.exception("Could not start listening")
            self._on_status("error", f"Could not start listening ({exc}).")
            return

        self._on_status("listening", f"Listening on {self.source.name}")
        try:
            self._loop(transcriber)
            self._on_status("stopped", "")
        except Exception as exc:
            log.exception("Live transcription stopped unexpectedly")
            self._on_status("error", f"Listening stopped unexpectedly ({exc}).")
        finally:
            self.source.stop()

    def _loop(self, transcriber: Transcriber) -> None:
        window = np.zeros(0, dtype=np.float32)   # audio of the current utterance
        fresh = 0                                 # samples added since the last transcription
        open_text = ""                            # partial text already published for this utterance

        def close_utterance(text: str) -> None:
            nonlocal open_text
            if text:
                self._on_text(self._utterance, text, True)
            self._utterance += 1
            open_text = ""

        while not self._stop_event.is_set():
            new = self.source.read_new()
            if new.size:
                window = np.concatenate([window, new])
                fresh += new.size
            if fresh < self.MIN_NEW_AUDIO * SAMPLE_RATE:
                time.sleep(0.05)
                continue
            fresh = 0

            spans = speech_spans(window)
            if not spans:                         # only silence/noise: do not run the model
                if open_text:
                    close_utterance(open_text)
                window = window[-int(self.KEEP_SILENCE * SAMPLE_RATE):]
                continue

            started = time.perf_counter()
            segments = transcriber.transcribe(window, self._get_language())
            self.last_pass_seconds = time.perf_counter() - started
            text = " ".join(s.text for s in segments).strip()
            trailing_silence = (window.size - spans[-1][1]) / SAMPLE_RATE

            if trailing_silence >= self.PAUSE:
                # The speaker paused: this utterance is complete.
                close_utterance(text)
                window = window[-int(self.KEEP_SILENCE * SAMPLE_RATE):]
            elif window.size > self.MAX_WINDOW * SAMPLE_RATE:
                # Long uninterrupted speech: finalise all but the last segment and keep going.
                if len(segments) > 1:
                    close_utterance(" ".join(s.text for s in segments[:-1]).strip())
                    window = window[int(segments[-1].start * SAMPLE_RATE):]
                    open_text = segments[-1].text
                    self._on_text(self._utterance, open_text, False)
                else:
                    close_utterance(text)
                    window = window[-int(self.KEEP_SILENCE * SAMPLE_RATE):]
            elif text and text != open_text:
                open_text = text
                self._on_text(self._utterance, text, False)
