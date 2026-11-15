"""Phase 5: auto-display mode, background pictures, settings persistence."""
from __future__ import annotations

import pytest

from app import config

# A valid 1x1 PNG.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360606060000000050001a5f645400000000049454e44ae426082"
)


def hear(client, utterance: int, text: str, final: bool = True) -> None:
    """Feed a line of 'transcript' to the server as if the preacher had just said it."""
    runtime = client.app.state.rt
    client.portal.call(runtime._on_text, runtime._session, utterance, text, final)


# ------------------------------------------------------------------ auto display
def test_manual_mode_never_shows_by_itself(client) -> None:
    hear(client, 1, "Please turn to John 3:16.")
    snap = client.get("/api/state").json()
    assert snap["screen"] == "clear"
    assert [q["reference"] for q in snap["queue"]] == ["John 3:16"] and not snap["queue"][0]["shown"]


def test_auto_mode_shows_spoken_references(client) -> None:
    client.post("/api/settings", json={"auto_display": True})
    hear(client, 1, "Please turn to John 3:16.")
    snap = client.get("/api/state").json()
    assert snap["screen"] == "verse" and snap["panels"][0]["reference"] == "John 3:16"
    assert snap["queue"][0]["shown"] is True

    hear(client, 2, "Now look at verse 17.")                      # follow-up reference
    assert client.get("/api/state").json()["panels"][0]["reference"] == "John 3:17"


def test_auto_mode_does_not_show_whole_chapters(client) -> None:
    client.post("/api/settings", json={"auto_display": True})
    hear(client, 1, "Open your Bibles to Romans chapter 8.")
    snap = client.get("/api/state").json()
    assert snap["screen"] == "clear"                               # waits for the operator
    assert snap["queue"][0]["reference"] == "Romans 8" and not snap["queue"][0]["shown"]


def test_auto_mode_respects_black_screen(client) -> None:
    client.post("/api/settings", json={"auto_display": True})
    client.post("/api/black")
    hear(client, 1, "Please turn to John 3:16.")
    snap = client.get("/api/state").json()
    assert snap["screen"] == "black" and not snap["queue"][0]["shown"]


def test_auto_mode_never_shows_quoted_verses(client) -> None:
    from app.semantic_matcher import SemanticMatch

    client.post("/api/settings", json={"auto_display": True})
    runtime = client.app.state.rt
    quote = SemanticMatch(19, 23, 1, 0.95, "KJV", "the Lord is my shepherd I shall not want")
    runtime.detector.feed_semantic([quote])
    for candidate in runtime.detector.feed_semantic([quote]):
        runtime.state.add_candidate(candidate)
    snap = client.get("/api/state").json()
    assert snap["queue"][0]["kind"] == "quoted" and snap["screen"] == "clear"


# ------------------------------------------------------------ background pictures
@pytest.fixture()
def backgrounds(tmp_path, monkeypatch):
    folder = tmp_path / "backgrounds"
    monkeypatch.setattr(config, "BACKGROUNDS_DIR", folder)
    return folder


def test_upload_and_choose_background(client, backgrounds) -> None:
    assert client.get("/api/backgrounds").json() == {"images": [], "selected": ""}
    response = client.post("/api/backgrounds", params={"name": "Church hall (1).png"}, content=TINY_PNG)
    assert response.status_code == 200
    assert response.json()["settings"]["background_image"] == "Church hall (1).png"
    assert (backgrounds / "Church hall (1).png").read_bytes() == TINY_PNG
    assert client.get("/api/backgrounds").json()["images"] == ["Church hall (1).png"]

    snap = client.post("/api/settings", json={"background_image": ""}).json()     # back to the theme
    assert snap["settings"]["background_image"] == ""


@pytest.mark.parametrize("name", ["../evil.png", "a/b.png", "script.js", "nofileextension", ".png", "x" * 120 + ".png"])
def test_bad_background_names_are_refused(client, backgrounds, name: str) -> None:
    assert client.post("/api/backgrounds", params={"name": name}, content=TINY_PNG).status_code == 400
    assert not backgrounds.exists() or list(backgrounds.iterdir()) == []


def test_background_limits(client, backgrounds, monkeypatch) -> None:
    assert client.post("/api/backgrounds", params={"name": "empty.png"}, content=b"").status_code == 400
    monkeypatch.setattr(config, "MAX_BACKGROUND_BYTES", 10)
    assert client.post("/api/backgrounds", params={"name": "big.png"}, content=TINY_PNG).status_code == 400
    assert client.post("/api/settings", json={"background_image": "missing.png"}).status_code == 400
    assert client.post("/api/settings", json={"background_image": "../x.png"}).status_code == 400


# --------------------------------------------------------------------- persistence
def test_all_settings_survive_a_restart(tmp_path) -> None:
    path = tmp_path / "settings.json"
    settings = config.Settings()
    settings.update({
        "translation_mode": "both", "theme": "church", "font_scale": 1.2, "show_verse_numbers": False,
        "whisper_model": "base", "language": "fr", "input_device": "USB Mixer", "dedupe_seconds": 45,
        "semantic_enabled": False, "semantic_threshold": 0.7, "auto_display": True,
        "background_image": "hall.jpg",
    })
    settings.save(path)
    assert config.Settings.load(path) == settings


def test_broken_settings_file_falls_back_to_defaults(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert config.Settings.load(path) == config.Settings()
    path.write_text('{"theme": "neon", "font_scale": "big", "language": "fr", "unknown": 1}', encoding="utf-8")
    loaded = config.Settings.load(path)
    assert loaded.theme == "dark" and loaded.font_scale == 1.0 and loaded.language == "fr"


# ------------------------------------------------------- clearing / starting fresh
def test_clear_transcript_keeps_detections(client) -> None:
    hear(client, 1, "Please turn to John 3:16.")
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()                                           # state snapshot
        assert len(ws.receive_json()["lines"]) == 1                 # transcript history for late joiners
        assert client.post("/api/transcript/clear").json() == {"ok": True}
        assert ws.receive_json() == {"type": "transcript_clear"}
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "hello", "role": "display"})
        assert ws.receive_json()["type"] == "state"                 # no transcript history any more
    assert [q["reference"] for q in client.get("/api/state").json()["queue"]] == ["John 3:16"]


def test_start_fresh_resets_everything_but_settings(client) -> None:
    client.post("/api/settings", json={"theme": "church"})
    hear(client, 1, "Open to Romans chapter 8.")
    hear(client, 2, "Look at verse 28.")
    client.post("/api/show", json={"book_id": 45, "chapter": 8, "verse_start": 28})

    snap = client.post("/api/session/reset").json()
    assert snap["queue"] == [] and snap["screen"] == "clear" and snap["panels"] == []
    assert snap["settings"]["theme"] == "church"                    # settings are kept
    assert len(client.app.state.rt.transcript) == 0

    hear(client, 3, "Now verse 5.")                                 # the old chapter is forgotten
    assert client.get("/api/state").json()["queue"] == []
    hear(client, 4, "Romans 8:28 again.")                           # and so is the "already suggested" memory
    assert [q["reference"] for q in client.get("/api/state").json()["queue"]] == ["Romans 8:28"]
