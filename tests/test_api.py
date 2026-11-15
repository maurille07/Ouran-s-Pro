"""End-to-end API tests: search, show, clear, black, settings, WebSocket updates."""
from __future__ import annotations

JOHN_3_16 = {"book_id": 43, "chapter": 3, "verse_start": 16, "verse_end": 16}


def test_pages_are_served(client) -> None:
    assert "Ouranòs" in client.get("/operator").text
    assert client.get("/display").status_code == 200
    assert client.get("/static/display.js").status_code == 200


def test_search_reference(client) -> None:
    data = client.get("/api/search", params={"q": "jn 3 16"}).json()
    assert data["kind"] == "reference"
    assert data["results"][0]["reference"] == "John 3:16"


def test_search_whole_chapter_lists_verses(client) -> None:
    data = client.get("/api/search", params={"q": "psalm 23"}).json()
    assert data["kind"] == "reference"
    assert [r["verse_start"] for r in data["results"]] == [1, 2, 3, 4, 5, 6]


def test_search_keywords(client) -> None:
    data = client.get("/api/search", params={"q": "God so loved"}).json()
    assert data["kind"] == "keyword"
    assert data["results"][0]["reference"] == "John 3:16"


def test_search_nonexistent_reference_shows_nothing_wrong(client) -> None:
    data = client.get("/api/search", params={"q": "John 3:99"}).json()
    assert all(r["reference"] != "John 3:99" for r in data["results"])


def test_search_follows_translation(client) -> None:
    client.post("/api/settings", json={"translation_mode": "LSG"})
    data = client.get("/api/search", params={"q": "romains 8:28"}).json()
    assert data["results"][0]["reference"] == "Romains 8:28"
    assert data["results"][0]["text"].startswith("Nous savons")


def test_show_clear_black(client) -> None:
    snap = client.post("/api/show", json=JOHN_3_16).json()
    assert snap["screen"] == "verse"
    assert snap["panels"][0]["reference"] == "John 3:16"
    assert snap["panels"][0]["verses"][0]["text"].startswith("For God so loved")

    snap = client.post("/api/black").json()
    assert snap["screen"] == "black"
    snap = client.post("/api/black").json()          # toggles back
    assert snap["screen"] == "verse" and snap["ref"] == JOHN_3_16

    snap = client.post("/api/clear").json()
    assert snap["screen"] == "clear" and snap["panels"] == []


def test_show_rejects_nonexistent_reference(client) -> None:
    bad = {"book_id": 43, "chapter": 3, "verse_start": 99}
    assert client.post("/api/show", json=bad).status_code == 404
    assert client.post("/api/show", json={"book_id": 67, "chapter": 1, "verse_start": 1}).status_code == 422
    assert client.get("/api/state").json()["screen"] == "clear"


def test_both_translations_side_by_side(client) -> None:
    client.post("/api/settings", json={"translation_mode": "both"})
    snap = client.post("/api/show", json=JOHN_3_16).json()
    assert [p["translation"] for p in snap["panels"]] == ["KJV", "LSG"]
    assert snap["panels"][1]["reference"] == "Jean 3:16"


def test_neighbor(client) -> None:
    nxt = client.post("/api/neighbor", params={"delta": 1}, json=JOHN_3_16).json()
    assert (nxt["chapter"], nxt["verse_start"]) == (3, 17)
    prev = client.post("/api/neighbor", params={"delta": -1}, json=JOHN_3_16).json()
    assert prev["verse_start"] == 15
    first = {"book_id": 1, "chapter": 1, "verse_start": 1}
    assert client.post("/api/neighbor", params={"delta": -1}, json=first).status_code == 404


def test_settings_validation_and_persistence(client, tmp_path) -> None:
    assert client.post("/api/settings", json={"theme": "neon"}).status_code == 400
    assert client.post("/api/settings", json={"nope": 1}).status_code == 400
    snap = client.post("/api/settings", json={"theme": "church", "font_scale": 9}).json()
    assert snap["settings"]["theme"] == "church"
    assert snap["settings"]["font_scale"] == 1.6      # clamped
    assert '"church"' in (tmp_path / "settings.json").read_text(encoding="utf-8")


def test_websocket_receives_live_updates(client) -> None:
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["screen"] == "clear"            # snapshot on connect
        ws.send_json({"type": "hello", "role": "display"})
        assert ws.receive_json()["displays"] == 1

        client.post("/api/show", json=JOHN_3_16)
        message = ws.receive_json()
        assert message["screen"] == "verse"
        assert message["panels"][0]["reference"] == "John 3:16"

        # The projector reports that the passage needs 3 pages; the operator can turn them.
        ws.send_json({"type": "pages", "count": 3})
        assert ws.receive_json()["page_count"] == 3
        client.post("/api/page", json={"delta": 1})
        assert ws.receive_json()["page"] == 1

        client.post("/api/clear")
        assert ws.receive_json()["screen"] == "clear"


def test_detect_pasted_text(client) -> None:
    text = "Turn with me to Romans chapter eight. Look at verse 28. Then First Corinthians 13:4-7."
    found = client.post("/api/detect", json={"text": text}).json()["detections"]
    assert [d["reference"] for d in found] == ["Romans 8", "Romans 8:28", "1 Corinthians 13:4-7"]
    assert [d["kind"] for d in found] == ["direct", "followup", "direct"]
    assert found[0]["whole_chapter"] is True and found[0]["verse_start"] == 1
    assert text[found[1]["start"]:found[1]["end"]] == "verse 28"
    assert found[1]["text"].startswith("And we know that all things work together")
    assert client.get("/api/state").json()["screen"] == "clear"      # detection never shows anything


def test_detect_french_follows_translation(client) -> None:
    client.post("/api/settings", json={"translation_mode": "LSG"})
    found = client.post("/api/detect", json={"text": "Jean chapitre trois verset seize"}).json()["detections"]
    assert found[0]["reference"] == "Jean 3:16"
    assert found[0]["text"].startswith("Car Dieu a tant aim")


def test_detect_nothing(client) -> None:
    assert client.post("/api/detect", json={"text": "God is good, all the time."}).json()["detections"] == []
