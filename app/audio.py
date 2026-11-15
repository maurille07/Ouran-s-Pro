"""Microphone capture: device list, 16 kHz mono stream, live level meter.

The capture runs in PortAudio's own thread and only appends small blocks to a
buffer; the transcriber thread collects them with ``read_new()``. Voice-activity
gating (skipping silence) is done by the transcriber with faster-whisper's VAD.
"""
from __future__ import annotations

import logging
import math
import os
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from . import config

log = logging.getLogger(__name__)

SAMPLE_RATE = config.SAMPLE_RATE
TEST_DEVICE_NAME = "Test file (WAV)"


class AudioError(RuntimeError):
    """A microphone problem, with a message that can be shown to the operator."""


class AudioSource(Protocol):
    """Anything that produces 16 kHz mono float32 audio."""

    name: str
    level: float

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def read_new(self) -> np.ndarray: ...


def _sounddevice() -> Any:
    try:
        import sounddevice
    except Exception as exc:  # missing PortAudio library, etc.
        raise AudioError(f"The audio system could not be loaded ({exc}).") from exc
    return sounddevice


def _level_from(samples: np.ndarray) -> float:
    """Loudness of a block as 0..1 (-60 dB .. 0 dB), for the level meter."""
    if samples.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    return max(0.0, min(1.0, (20.0 * math.log10(rms + 1e-7) + 60.0) / 60.0))


def _resample(samples: np.ndarray, source_rate: int) -> np.ndarray:
    """Convert mono audio to 16 kHz (speech only needs simple interpolation)."""
    if source_rate == SAMPLE_RATE or samples.size == 0:
        return samples
    if source_rate % SAMPLE_RATE == 0:                  # e.g. 48 kHz: average groups of 3
        step = source_rate // SAMPLE_RATE
        usable = samples[: samples.size - samples.size % step]
        return usable.reshape(-1, step).mean(axis=1).astype(np.float32)
    count = int(round(samples.size * SAMPLE_RATE / source_rate))
    positions = np.linspace(0, samples.size - 1, count)
    return np.interp(positions, np.arange(samples.size), samples).astype(np.float32)


# --------------------------------------------------------------------------
# Device list
# --------------------------------------------------------------------------
def _input_devices() -> list[tuple[int, dict[str, Any]]]:
    """Input devices of one host API (so each microphone is listed once)."""
    sd = _sounddevice()
    devices = list(enumerate(sd.query_devices()))
    apis = sd.query_hostapis()
    wanted = sd.default.hostapi
    if sys.platform == "win32":
        # WASAPI gives full device names (MME cuts them at 31 characters).
        for index, api in enumerate(apis):
            if "WASAPI" in api["name"]:
                wanted = index
    return [(i, d) for i, d in devices if d["hostapi"] == wanted and d["max_input_channels"] > 0]


def test_wav_path() -> Path | None:
    """WAV file offered as a fake microphone when OURANOS_TEST_WAV is set."""
    value = os.environ.get(config.TEST_WAV_ENV, "").strip()
    return Path(value) if value and Path(value).is_file() else None


def list_input_devices() -> list[dict[str, Any]]:
    """Microphones the operator can choose from: [{"name": ...}]. Never raises."""
    names: list[str] = []
    try:
        for _, device in _input_devices():
            name = " ".join(str(device["name"]).split())    # some drivers put line breaks in names
            if name not in names:
                names.append(name)
    except Exception as exc:
        log.warning("Could not list microphones: %s", exc)
    if test_wav_path() is not None:
        names.append(TEST_DEVICE_NAME)
    return [{"name": name} for name in names]


def open_source(device_name: str = "") -> AudioSource:
    """The audio source for a device name ("" = system default microphone)."""
    if device_name == TEST_DEVICE_NAME:
        path = test_wav_path()
        if path is None:
            raise AudioError("The test WAV file is not available.")
        return WavFileSource(path)
    return AudioCapture(device_name)


# --------------------------------------------------------------------------
# Microphone
# --------------------------------------------------------------------------
class AudioCapture:
    """Captures a microphone as 16 kHz mono float32."""

    def __init__(self, device_name: str = "") -> None:
        self.name = device_name or "System default"
        self._wanted = device_name
        self.level = 0.0
        self._lock = threading.Lock()
        self._blocks: list[np.ndarray] = []
        self._stream: Any = None
        self._rate = SAMPLE_RATE

    def _find_device(self) -> tuple[int | None, dict[str, Any] | None]:
        if not self._wanted:
            return None, None
        for index, device in _input_devices():
            if " ".join(str(device["name"]).split()) == self._wanted:
                return index, device
        raise AudioError(
            f"Microphone “{self._wanted}” was not found. Is it plugged in? Choose another one in the list."
        )

    def start(self) -> None:
        sd = _sounddevice()
        index, device = self._find_device()
        try:
            info = device or sd.query_devices(kind="input")
        except Exception as exc:
            raise AudioError("No microphone was found. Plug one in (or the mixer's USB cable) and try again.") from exc
        native_rate = int(info.get("default_samplerate") or 48_000)
        max_channels = int(info.get("max_input_channels") or 1)
        # Not every driver accepts 16 kHz mono directly: fall back to what the device prefers.
        attempts = [(SAMPLE_RATE, 1), (native_rate, 1), (native_rate, min(max_channels, 2)), (native_rate, max_channels)]
        last_error: Exception | None = None
        for rate, channels in dict.fromkeys(attempts):
            try:
                self._rate = rate
                self._stream = sd.InputStream(
                    device=index, samplerate=rate, channels=channels, dtype="float32",
                    blocksize=int(rate * 0.1), callback=self._callback,
                )
                self._stream.start()
                log.info("Microphone open: %s (%d Hz, %d ch)", self.name, rate, channels)
                return
            except Exception as exc:
                last_error = exc
                self._stream = None
        raise AudioError(f"Could not open the microphone “{self.name}” ({last_error}).")

    def _callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        mono = indata.mean(axis=1) if indata.shape[1] > 1 else indata[:, 0]
        block = _resample(mono.astype(np.float32), self._rate)
        self.level = _level_from(block)
        with self._lock:
            self._blocks.append(block)

    def read_new(self) -> np.ndarray:
        """Audio captured since the previous call."""
        with self._lock:
            blocks, self._blocks = self._blocks, []
        return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        self.level = 0.0
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:
                log.warning("Error while closing the microphone: %s", exc)


# --------------------------------------------------------------------------
# WAV file played as if it were a microphone (testing aid)
# --------------------------------------------------------------------------
def load_wav(path: Path) -> np.ndarray:
    """Read a 16-bit PCM WAV file as 16 kHz mono float32."""
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise AudioError("The test WAV file must be 16-bit PCM.")
        rate, channels = wav.getframerate(), wav.getnchannels()
        data = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return _resample(data, rate)


class WavFileSource:
    """Feeds a WAV file in real time, then silence."""

    def __init__(self, path: Path, realtime: bool = True) -> None:
        self.name = TEST_DEVICE_NAME
        self.level = 0.0
        self._audio = load_wav(path)
        self._realtime = realtime
        self._lock = threading.Lock()
        self._blocks: list[np.ndarray] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._feed, name="wav-source", daemon=True)
        self._thread.start()

    def _feed(self) -> None:
        block_size = SAMPLE_RATE // 10
        position = 0
        if not self._realtime:                              # tests: hand over the whole file at once
            with self._lock:
                self._blocks.append(self._audio)
            position = self._audio.size
        while not self._stop.is_set():
            block = self._audio[position: position + block_size]
            position += block_size
            if block.size < block_size:                     # after the file: silence
                block = np.concatenate([block, np.zeros(block_size - block.size, dtype=np.float32)])
            self.level = _level_from(block)
            with self._lock:
                self._blocks.append(block)
            time.sleep(0.1)

    def read_new(self) -> np.ndarray:
        with self._lock:
            blocks, self._blocks = self._blocks, []
        return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)

    def stop(self) -> None:
        self._stop.set()
        self.level = 0.0
