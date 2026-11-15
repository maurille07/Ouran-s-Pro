"""Live listening: queue API, error handling, and an end-to-end run on recorded speech.

The end-to-end tests need the "base" speech model (python scripts/download_models.py
--whisper base); they are skipped when it is not downloaded.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from app import audio, config
from app.bible_db import VerseRef
from app.detector import Candidate
from app.transcriber import LiveTranscriber, Transcriber, is_model_available

WAV = Path(__file__).parent / "data" / "sermon_en.wav"
needs_model = pytest.mark.skipif(
    not (is_model_available("base") and WAV.exists()), reason="speech model 'base' not downloaded"
)


def _candidate(candidate_id: int, ref: VerseRef, **extra) -> Candidate:
    fields = dict(whole_chapter=False, kind="direct", followup=False, confidence=0.97, heard="test")
    return Candidate(id=candidate_id, ref=ref, **{**fields, **extra})


# ------------------------------------------------------------------ audio helpers
def test_resample_to_16k() -> None:
    one_second_48k = np.sin(np.linspace(0, 440 * 2 * np.pi, 48_000)).astype(np.float32)
    assert audio._resample(one_second_48k, 48_000).size == 16_000
    assert audio._resample(one_second_48k[:44_100], 44_100).size == 16_000
    assert audio._resample(one_second_48k[:16_000], 16_000).size == 16_000


def test_level_meter_range() -> None:
    assert audio._level_from(np.zeros(1600, dtype=np.float32)) == 0.0
    assert audio._level_from(np.ones(1600, dtype=np.float32)) == 1.0
    assert 0.3 < audio._level_from(np.full(1600, 0.05, dtype=np.float32)) < 0.8


def test_list_devices_never_raises() -> None:
    assert isinstance(audio.list_input_devices(), list)


# ---------------------------------------------------------------------- queue API
def test_queue_show_step_dismiss(client) -> None:
    state = client.app.state.rt.state
    state.add_candidate(_candidate(1, VerseRef(43, 3, 16, 16)))
    state.add_candidate(_candidate(2, VerseRef(45, 8, 1, 1), whole_chapter=True, confidence=0.85))

    queue = client.get("/api/state").json()["queue"]
    assert [q["reference"] for q in queue] == ["Romans 8", "John 3:16"]       # newest first
    assert queue[1]["text"].startswith("For God so loved")

    snap = client.post("/api/queue/1/show").json()
    assert snap["screen"] == "verse" and snap["panels"][0]["reference"] == "John 3:16"
    assert next(q for q in snap["queue"] if q["id"] == 1)["shown"] is True

    snap = client.post("/api/queue/1/step", json={"delta": 1}).json()          # live card: projector follows
    assert snap["panels"][0]["reference"] == "John 3:17"

    snap = client.post("/api/queue/2/step", json={"delta": 1}).json()          # not shown: only the card moves
    assert next(q for q in snap["queue"] if q["id"] == 2)["reference"] == "Romans 8:2"
    assert snap["panels"][0]["reference"] == "John 3:17"

    snap = client.post("/api/queue/2/dismiss").json()
    assert [q["id"] for q in snap["queue"]] == [1]
    assert client.post("/api/queue/2/show").status_code == 404
    assert client.post("/api/queue/clear").json()["queue"] == []


def test_refined_candidate_replaces_the_chapter_card(client) -> None:
    state = client.app.state.rt.state
    state.add_candidate(_candidate(1, VerseRef(45, 8, 1, 1), whole_chapter=True))
    state.add_candidate(_candidate(2, VerseRef(45, 8, 28, 28), replaces=1))
    assert [q["reference"] for q in client.get("/api/state").json()["queue"]] == ["Romans 8:28"]


def test_devices_endpoint(client) -> None:
    data = client.get("/api/audio/devices").json()
    assert set(data["models"]) == {"base", "small", "medium"}
    assert isinstance(data["devices"], list)


def test_start_without_model_gives_clear_message(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path)                        # no models here
    response = client.post("/api/listen/start", json={})
    assert response.status_code == 400
    assert "not downloaded" in response.json()["detail"]
    assert client.get("/api/state").json()["listen"]["status"] == "stopped"


def test_start_with_missing_microphone_gives_clear_message(client) -> None:
    client.post("/api/settings", json={"whisper_model": "base"})
    if not is_model_available("base"):
        pytest.skip("speech model 'base' not downloaded")
    response = client.post("/api/listen/start", json={"device": "No Such Microphone 9000"})
    # Either refused straight away, or reported by the listening thread a moment later.
    if response.status_code == 200:
        deadline = time.time() + 20
        while time.time() < deadline and client.get("/api/state").json()["listen"]["status"] == "loading":
            time.sleep(0.2)
        listen = client.get("/api/state").json()["listen"]
        assert listen["status"] == "error" and "not found" in listen["message"]
    else:
        assert response.status_code == 400


def test_new_settings_validation(client) -> None:
    assert client.post("/api/settings", json={"whisper_model": "huge"}).status_code == 400
    assert client.post("/api/settings", json={"language": "de"}).status_code == 400
    snap = client.post("/api/settings", json={"language": "fr", "dedupe_seconds": 45}).json()
    assert snap["settings"]["language"] == "fr" and snap["settings"]["dedupe_seconds"] == 45


# ------------------------------------------------------------- real speech, end to end
@needs_model
def test_live_transcriber_on_recorded_speech() -> None:
    lines: dict[int, str] = {}
    statuses: list[str] = []
    transcriber = Transcriber("base")
    live = LiveTranscriber(
        audio.WavFileSource(WAV, realtime=False),
        get_transcriber=lambda: transcriber,
        get_language=lambda: "en",
        on_text=lambda utt, text, final: lines.__setitem__(utt, text),
        on_status=lambda status, message: statuses.append(status),
    )
    live.start()
    deadline = time.time() + 60
    while time.time() < deadline and "Corinthians" not in " ".join(lines.values()):
        time.sleep(0.2)
    live.stop()
    live.join(15)
    text = " ".join(lines[k] for k in sorted(lines))
    assert statuses[:2] == ["loading", "listening"] and statuses[-1] == "stopped"
    assert "Romans" in text and "John" in text and "Corinthians" in text


@needs_model
def test_listening_fills_the_queue(client, monkeypatch) -> None:
    """Server + WAV 'microphone' + speech model + detector -> verses in the queue."""
    monkeypatch.setenv(config.TEST_WAV_ENV, str(WAV))
    assert {"name": audio.TEST_DEVICE_NAME} in client.get("/api/audio/devices").json()["devices"]
    client.post("/api/settings", json={"whisper_model": "base", "language": "en"})
    assert client.post("/api/listen/start", json={"device": audio.TEST_DEVICE_NAME}).status_code == 200

    wanted = {"Romans 8:28", "John 3:16", "1 Corinthians 13:4-7"}
    found: set[str] = set()
    deadline = time.time() + 75                       # the recording lasts about 32 seconds
    while time.time() < deadline and not wanted <= found:
        time.sleep(0.5)
        snap = client.get("/api/state").json()
        found = {q["reference"] for q in snap["queue"]}
    assert snap["listen"]["status"] == "listening"
    snap = client.post("/api/listen/stop").json()
    assert snap["listen"]["status"] == "stopped"
    assert wanted <= found, f"queue had {found}"
    assert snap["screen"] == "clear"                  # manual mode: nothing shown by itself
