"""FastAPI app: pages, REST API for the operator, and the WebSocket hub.

Flow: the operator page calls the REST API (search / show / clear ...). Every
change is broadcast as a full state snapshot over WebSocket to all connected
operator and projector screens, so they always agree.

Live listening: a background thread (transcriber.LiveTranscriber) turns the
microphone into text. Its callbacks are handed over to the server's event loop,
where the detector looks for references -- and, every couple of seconds, the
semantic matcher looks for quoted verses -- and fills the detections queue. Extra
WebSocket messages: "transcript" (live text), "level" (mic meter), "listen" (status).
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, config
from .audio import AudioError, AudioSource, list_input_devices, open_source
from .bible_db import BibleDB, BibleDBError, VerseRef
from .detector import Detector
from .reference_parser import BookIndex, ParserContext, ReferenceParser, parse_typed_reference
from .semantic_matcher import SemanticError, SemanticMatcher, sliding_windows, tail_windows
from . import semantic_matcher
from .state import AppState
from .transcriber import LiveTranscriber, Transcriber, is_model_available

log = logging.getLogger("ouranos")


# --------------------------------------------------------------------------
# WebSocket hub
# --------------------------------------------------------------------------
class Hub:
    """Keeps track of connected screens and broadcasts state to them."""

    def __init__(self) -> None:
        self._clients: dict[WebSocket, str] = {}  # socket -> role ("operator" | "display")

    @property
    def display_count(self) -> int:
        return sum(1 for role in self._clients.values() if role == "display")

    def add(self, ws: WebSocket) -> None:
        self._clients[ws] = "unknown"

    def set_role(self, ws: WebSocket, role: str) -> None:
        if ws in self._clients:
            self._clients[ws] = role

    def remove(self, ws: WebSocket) -> None:
        self._clients.pop(ws, None)

    async def broadcast(self, message: dict[str, Any]) -> None:
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
            except Exception:  # a closed/broken socket must never stop the others
                self.remove(ws)


class Runtime:
    """Everything the request handlers need (created at startup)."""

    def __init__(self) -> None:
        self.hub = Hub()
        self.state = AppState(config.Settings.load())
        self.books = BookIndex()
        self.db: BibleDB | None = None
        self.db_error: str | None = None
        self.parser: ReferenceParser | None = None
        self.detector: Detector | None = None
        try:
            self.db = BibleDB()
            self.parser = ReferenceParser(self.books, self.db)
            self.detector = Detector(self.parser, lambda: self.state.settings.dedupe_seconds)
        except BibleDBError as exc:
            self.db_error = str(exc)
            log.error("%s", exc)

        # -- live listening --
        self.loop: asyncio.AbstractEventLoop | None = None   # set at startup
        self.live: LiveTranscriber | None = None
        self.source: AudioSource | None = None
        self.listen: dict[str, Any] = {"status": "stopped", "message": "", "device": "", "model": ""}
        self.transcript: deque[dict[str, Any]] = deque(maxlen=60)   # recent final lines
        self._session = 0                 # +1 at every start; late callbacks of old sessions are ignored
        self._transcriber: Transcriber | None = None            # loaded model, kept between sessions
        self._level_task: asyncio.Task[None] | None = None

        # -- quoted-verse (semantic) detection --
        self.semantic: SemanticMatcher | None = None      # loaded on first use (takes a few seconds)
        self._semantic_state = "idle"                     # "idle" | "loading" | "ready" | "error"
        self._semantic_message = ""
        self._semantic_busy = False                       # a match is being computed
        self._semantic_last = 0.0                         # loop time of the last match
        self._recent_words: deque[str] = deque(maxlen=60)  # last words of finished transcript lines

    def need_db(self) -> BibleDB:
        if self.db is None:
            raise HTTPException(status_code=503, detail=self.db_error or "Bible database unavailable.")
        return self.db

    def snapshot(self) -> dict[str, Any]:
        snap = self.state.snapshot(self.db, displays=self.hub.display_count, error=self.db_error)
        snap["listen"] = self.listen
        snap["quoted"] = self.quoted_status()
        return snap

    # -- quoted-verse (semantic) detection --------------------------------------
    def quoted_status(self) -> dict[str, str]:
        """State of quoted-verse detection, for the operator screen."""
        if not self.state.settings.semantic_enabled:
            return {"status": "off", "message": ""}
        if self._semantic_state == "error":
            return {"status": "error", "message": self._semantic_message}
        if not semantic_matcher.is_available():
            return {"status": "missing",
                    "message": "Not set up yet: connect to the internet once and run the setup script again."}
        return {"status": "ready" if self.semantic else "loading" if self._semantic_state == "loading" else "idle",
                "message": ""}

    async def ensure_semantic(self) -> SemanticMatcher | None:
        """Load the embedding model and verse embeddings if wanted and not loaded yet."""
        if self.semantic is not None or not self.state.settings.semantic_enabled:
            return self.semantic if self.state.settings.semantic_enabled else None
        if self._semantic_state in ("loading", "error") or not semantic_matcher.is_available():
            return None
        self._semantic_state = "loading"
        await self.publish()
        try:
            self.semantic = await asyncio.to_thread(SemanticMatcher, self.db)
            self._semantic_state = "ready"
        except SemanticError as exc:
            self._semantic_state, self._semantic_message = "error", str(exc)
            log.error("Quoted-verse detection unavailable: %s", exc)
        except Exception as exc:
            self._semantic_state, self._semantic_message = "error", f"Could not start quoted-verse detection ({exc})."
            log.exception("Quoted-verse detection failed to load")
        await self.publish()
        return self.semantic

    def _maybe_match_quotes(self, session: int, partial: str, final: bool) -> None:
        """About every 2 seconds, compare the last words spoken with every verse."""
        settings = self.state.settings
        if not settings.semantic_enabled or self.semantic is None or self._semantic_busy or self.loop is None:
            return
        now = self.loop.time()
        if not final and now - self._semantic_last < 2.0:
            return
        windows = tail_windows(" ".join([*self._recent_words, partial]))
        if not windows:
            return
        self._semantic_busy, self._semantic_last = True, now
        asyncio.create_task(self._match_quotes(session, windows, settings.semantic_threshold))

    async def _match_quotes(self, session: int, windows: list[str], threshold: float) -> None:
        try:
            assert self.semantic is not None and self.detector is not None and self.db is not None
            matches = await asyncio.to_thread(self.semantic.match, windows, threshold)
            if session != self._session:
                return
            candidates = self.detector.feed_semantic(matches)
            for candidate in candidates:
                self.state.add_candidate(candidate)
                log.info("DETECT quoted %s %.2f (heard: %s)",
                         self.db.format_reference(candidate.ref, "KJV"), candidate.confidence, candidate.heard)
            if candidates:
                await self.publish()
        except Exception:
            log.exception("Quoted-verse matching failed")
        finally:
            self._semantic_busy = False

    async def publish(self) -> dict[str, Any]:
        snap = self.snapshot()
        await self.hub.broadcast(snap)
        return snap

    # -- live listening --------------------------------------------------------
    def _load_transcriber(self) -> Transcriber:
        """Called from the listening thread: load (or reuse) the selected speech model."""
        size = self.state.settings.whisper_model
        if self._transcriber is None or self._transcriber.size != size:
            self._transcriber = None                 # free the old model before loading another
            self._transcriber = Transcriber(size)
        return self._transcriber

    def _from_thread(self, coroutine: Any) -> None:
        """Hand a coroutine from the listening thread to the server's event loop."""
        if self.loop is not None and not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        else:
            coroutine.close()

    async def start_listening(self, device: str | None) -> None:
        if self.detector is None:
            raise HTTPException(status_code=503, detail=self.db_error or "Bible database unavailable.")
        settings = self.state.settings
        if device is not None and device != settings.input_device:
            settings.update({"input_device": device})
            settings.save()
        if not is_model_available(settings.whisper_model):
            raise HTTPException(
                status_code=400,
                detail=f"The speech model “{settings.whisper_model}” is not downloaded yet. Connect to the "
                       "internet once and run the setup script again, or choose another model in Settings.",
            )
        await self.stop_listening()
        try:
            source = open_source(settings.input_device)
        except AudioError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        self._session += 1
        session = self._session
        self.detector.reset()
        self._recent_words.clear()
        if self._semantic_state == "error":
            self._semantic_state = "idle"                # give quoted-verse detection another try
        asyncio.create_task(self.ensure_semantic())      # loads in the background, listening starts now
        self.source = source
        self.listen = {"status": "loading", "message": "Loading the speech model…",
                       "device": source.name, "model": settings.whisper_model}
        self.live = LiveTranscriber(
            source,
            get_transcriber=self._load_transcriber,
            get_language=lambda: self.state.settings.language,
            on_text=lambda utt, text, final: self._from_thread(self._on_text(session, utt, text, final)),
            on_status=lambda status, message: self._from_thread(self._on_status(session, status, message)),
        )
        self.live.start()
        self._level_task = asyncio.create_task(self._level_loop(session))
        log.info("LISTEN start: %s, model %s", source.name, settings.whisper_model)
        await self.hub.broadcast({"type": "listen", **self.listen})

    async def stop_listening(self) -> None:
        live, self.live = self.live, None
        self._session += 1                           # anything still arriving from the old thread is dropped
        if self._level_task is not None:
            self._level_task.cancel()
            self._level_task = None
        if live is not None:
            live.stop()
            await asyncio.to_thread(live.join, 10)   # wait for the current transcription pass
            log.info("LISTEN stop")
        self.source = None
        if self.listen["status"] != "error":
            self.listen = {**self.listen, "status": "stopped", "message": ""}
        await self.hub.broadcast({"type": "listen", **self.listen})
        await self.hub.broadcast({"type": "level", "value": 0})

    async def _level_loop(self, session: int) -> None:
        """Send the microphone level about 10 times a second while listening."""
        while session == self._session and self.source is not None:
            await self.hub.broadcast({"type": "level", "value": round(self.source.level, 3)})
            await asyncio.sleep(0.1)

    async def _on_status(self, session: int, status: str, message: str) -> None:
        if session != self._session:
            return
        self.listen = {**self.listen, "status": status, "message": message}
        if status == "error":
            # The listening thread has ended by itself (no microphone, model problem...).
            log.error("LISTEN error: %s", message)
            self.live = None
            self.source = None
            if self._level_task is not None:
                self._level_task.cancel()
                self._level_task = None
            await self.hub.broadcast({"type": "level", "value": 0})
        await self.hub.broadcast({"type": "listen", **self.listen})

    async def _on_text(self, session: int, utterance: int, text: str, final: bool) -> None:
        """New live text: look for references, update the queue, send the transcript line."""
        if session != self._session or self.detector is None or self.db is None:
            return
        line_id = session * 1_000_000 + utterance
        candidates, spans = self.detector.feed(line_id, text, final, self.state.primary_translation(self.db))
        for candidate in candidates:
            item = self.state.add_candidate(candidate)
            log.info("DETECT %s (heard: %s)", self.db.format_reference(candidate.ref, "KJV"), candidate.heard)
            # Auto mode: a clearly spoken reference goes straight to the projector.
            # Whole chapters and quoted verses always wait for the operator, and a
            # screen the operator has blacked out stays black.
            if (
                self.state.settings.auto_display
                and candidate.kind == "direct"
                and not candidate.whole_chapter
                and candidate.confidence >= config.AUTO_DISPLAY_CONFIDENCE
                and self.state.screen != "black"
                and self.db.validate(candidate.ref, self.state.primary_translation(self.db)) is not None
            ):
                self.state.show(candidate.ref)
                item.shown = True
                log.info("SHOW %s (auto)", self.db.format_reference(candidate.ref, "KJV"))
        line = {
            "type": "transcript", "id": line_id, "text": text, "final": final, "spans": spans,
            "pass_seconds": round(self.live.last_pass_seconds, 2) if self.live else 0,
        }
        if final:
            self.transcript.append(line)
        await self.hub.broadcast(line)
        if candidates:
            await self.publish()
        if final:
            self._recent_words.extend(text.split())
        self._maybe_match_quotes(session, "" if final else text, final)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config.setup_logging()
    app.state.rt = Runtime()
    app.state.rt.loop = asyncio.get_running_loop()
    log.info("%s %s started", config.APP_NAME, __version__)
    yield
    rt: Runtime = app.state.rt
    await rt.stop_listening()
    if rt.db is not None:
        rt.db.close()
    log.info("%s stopped", config.APP_NAME)


app = FastAPI(title=config.APP_NAME, version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
config.BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/backgrounds", StaticFiles(directory=config.BACKGROUNDS_DIR), name="backgrounds")


def rt() -> Runtime:
    return app.state.rt


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------
_NO_CACHE = {"Cache-Control": "no-store"}


@app.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    return RedirectResponse("/operator")


@app.get("/operator", include_in_schema=False)
async def operator_page() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "operator.html", headers=_NO_CACHE)


@app.get("/display", include_in_schema=False)
async def display_page() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "display.html", headers=_NO_CACHE)


# --------------------------------------------------------------------------
# API models
# --------------------------------------------------------------------------
class RefBody(BaseModel):
    book_id: int = Field(ge=1, le=66)
    chapter: int = Field(ge=1)
    verse_start: int = Field(ge=1)
    verse_end: int | None = Field(default=None, ge=1)

    def to_ref(self) -> VerseRef:
        return VerseRef(self.book_id, self.chapter, self.verse_start, self.verse_end or self.verse_start)


class TextBody(BaseModel):
    text: str = Field(max_length=20_000)


class DeltaBody(BaseModel):
    delta: int = Field(ge=-1, le=1)


class ListenBody(BaseModel):
    device: str | None = Field(default=None, max_length=200)   # None = keep the saved microphone


def _checked(ref: VerseRef) -> VerseRef:
    """Validate a reference against the real chapter/verse counts (404 if it does not exist)."""
    db = rt().need_db()
    valid = db.validate(ref, rt().state.primary_translation(db))
    if valid is None:
        raise HTTPException(status_code=404, detail="That reference does not exist.")
    return valid


def _describe(ref: VerseRef) -> dict[str, Any]:
    """A reference plus its text in the current translation mode (for previews)."""
    db = rt().need_db()
    panels = db.passage(ref, rt().state.translations(db))
    return {**ref.to_dict(), "panels": panels}


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@app.get("/api/state")
async def get_state() -> dict[str, Any]:
    return rt().snapshot()


@app.get("/api/search")
async def search(q: str = "") -> dict[str, Any]:
    """Manual search: a typed reference ('jn 3 16', 'romains 8:28') or keywords ('God so loved')."""
    db = rt().need_db()
    state = rt().state
    query = q.strip()
    if not query:
        return {"kind": "empty", "results": []}
    primary = state.primary_translation(db)

    parsed = parse_typed_reference(query, rt().books)
    if parsed is not None:
        results: list[dict[str, Any]] = []
        if parsed.verse_start is None:
            # Whole chapter ("Psalm 23"): list every verse so the operator can pick one.
            for verse in db.get_range(primary, VerseRef(parsed.book_id, parsed.chapter, 1, 999)):
                ref = VerseRef(parsed.book_id, parsed.chapter, verse["verse"], verse["verse"])
                results.append(
                    {**ref.to_dict(), "translation": primary,
                     "reference": db.format_reference(ref, primary), "text": verse["text"]}
                )
        else:
            ref = db.validate(
                VerseRef(parsed.book_id, parsed.chapter, parsed.verse_start, parsed.verse_end or parsed.verse_start),
                primary,
            )
            if ref is not None:
                text = " ".join(v["text"] for v in db.get_range(primary, ref))
                results.append(
                    {**ref.to_dict(), "translation": primary,
                     "reference": db.format_reference(ref, primary), "text": text}
                )
        if results:
            return {"kind": "reference", "results": results}
        # A reference that does not exist: fall through to keyword search.

    return {"kind": "keyword", "results": db.search_text(query, state.translations(db))}


@app.post("/api/passage")
async def passage(body: RefBody) -> dict[str, Any]:
    """Text of a reference, for the operator's preview (does not change the projector)."""
    return _describe(_checked(body.to_ref()))


@app.post("/api/neighbor")
async def neighbor(body: RefBody, delta: int = 1) -> dict[str, Any]:
    """The verse after (delta=1) or before (delta=-1) the given reference."""
    db = rt().need_db()
    ref = _checked(body.to_ref())
    nxt = db.neighbor(ref, 1 if delta >= 0 else -1, rt().state.primary_translation(db))
    if nxt is None:
        raise HTTPException(status_code=404, detail="No more verses in that direction.")
    return _describe(nxt)


@app.post("/api/detect")
async def detect(body: TextBody) -> dict[str, Any]:
    """Find spoken-style references in a pasted text (to test detection without audio).

    Nothing is shown on the projector. Each call starts with a fresh context, so a
    bare "verse 5" only works after a chapter mentioned earlier in the same text.
    """
    runtime = rt()
    db = runtime.need_db()
    assert runtime.parser is not None
    primary = runtime.state.primary_translation(db)
    detections: list[dict[str, Any]] = []
    for found in runtime.parser.parse(body.text, ParserContext(), primary):
        whole_chapter = found.verse_start is None
        first = found.verse_start or 1
        ref = VerseRef(found.book_id, found.chapter, first, found.verse_end or first)
        label = db.format_reference(ref, primary)
        if whole_chapter:
            label = label.rsplit(":", 1)[0]                 # "Psalms 23"
        detections.append(
            {
                **ref.to_dict(),
                "whole_chapter": whole_chapter,
                "kind": found.kind,                         # "direct" | "followup"
                "start": found.start,
                "end": found.end,
                "matched": found.text,
                "translation": primary,
                "reference": label,
                "text": " ".join(v["text"] for v in db.get_range(primary, ref)),
            }
        )

    # Quoted / paraphrased verses: slide a window over the text and keep verses that
    # win at least two windows (the same "seen twice" rule as live listening).
    matcher = await runtime.ensure_semantic()
    windows = sliding_windows(body.text) if matcher is not None else []
    if matcher is not None and windows:
        threshold = runtime.state.settings.semantic_threshold
        best = await asyncio.to_thread(matcher.best_per_text, [w[2] for w in windows], threshold)
        wins: dict[tuple[int, int, int], list[tuple[float, int]]] = {}
        for position, match in enumerate(best):
            if match is not None:
                wins.setdefault((match.book_id, match.chapter, match.verse), []).append((match.score, position))
        named = {(d["book_id"], d["chapter"], v) for d in detections if not d["whole_chapter"]
                 for v in range(d["verse_start"], d["verse_end"] + 1)}
        taken: list[tuple[int, int]] = []            # text spans already explained by a better match
        for key, hits in sorted(wins.items(), key=lambda item: -max(item[1])[0]):
            if key in named or (len(hits) < 2 and len(windows) > 2):
                continue
            score, position = max(hits)
            start, end, words = windows[position]
            if any(start < other_end and other_start < end for other_start, other_end in taken):
                continue                             # e.g. John 3:15 inside a quote of John 3:16
            ref = VerseRef(key[0], key[1], key[2], key[2])
            if db.validate(ref, primary) is None:
                continue
            taken.append((start, end))
            detections.append(
                {
                    **ref.to_dict(),
                    "whole_chapter": False,
                    "kind": "quoted",
                    "confidence": round(score, 2),
                    "start": start,
                    "end": end,
                    "matched": words,
                    "translation": primary,
                    "reference": db.format_reference(ref, primary),
                    "text": " ".join(v["text"] for v in db.get_range(primary, ref)),
                }
            )
        detections.sort(key=lambda d: (d["start"], d["end"]))
    return {"detections": detections, "quoted": runtime.quoted_status()}


@app.post("/api/show")
async def show(body: RefBody) -> dict[str, Any]:
    ref = _checked(body.to_ref())
    rt().state.show(ref)
    log.info("SHOW %s", rt().need_db().format_reference(ref, "KJV"))
    return await rt().publish()


@app.post("/api/clear")
async def clear() -> dict[str, Any]:
    rt().state.clear()
    log.info("CLEAR")
    return await rt().publish()


@app.post("/api/black")
async def black() -> dict[str, Any]:
    rt().state.toggle_black()
    log.info("BLACK -> %s", rt().state.screen)
    return await rt().publish()


@app.post("/api/page")
async def page(body: DeltaBody) -> dict[str, Any]:
    """Turn the page of a long passage on the projector."""
    rt().state.turn_page(body.delta)
    return await rt().publish()


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return rt().state.settings.to_dict()


@app.post("/api/settings")
async def update_settings(changes: dict[str, Any]) -> dict[str, Any]:
    runtime = rt()
    state = runtime.state
    wanted_image = changes.get("background_image")
    if wanted_image and not (config.BACKGROUNDS_DIR / str(wanted_image)).is_file():
        raise HTTPException(status_code=400, detail="That background image was not found.")
    old_mode, old_model = state.settings.translation_mode, state.settings.whisper_model
    was_quoting = state.settings.semantic_enabled
    try:
        state.settings.update(changes)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if state.settings.translation_mode != old_mode:
        state.page = 0  # the text changed, so restart paging
    state.settings.save()
    if state.settings.whisper_model != old_model and runtime.live is not None:
        await runtime.start_listening(None)          # restart with the new speech model
    if state.settings.semantic_enabled and not was_quoting and runtime.live is not None:
        asyncio.create_task(runtime.ensure_semantic())
    return await runtime.publish()


# --------------------------------------------------------------------------
# API: projector background images
# --------------------------------------------------------------------------
def _background_names() -> list[str]:
    try:
        files = [f.name for f in config.BACKGROUNDS_DIR.iterdir() if f.is_file()]
    except OSError:
        return []
    return sorted((name for name in files if config.BACKGROUND_NAME.match(name)), key=str.lower)


@app.get("/api/backgrounds")
async def backgrounds() -> dict[str, Any]:
    """Images in data/backgrounds/ that can be used behind the verse."""
    return {"images": _background_names(), "selected": rt().state.settings.background_image}


@app.post("/api/backgrounds")
async def upload_background(request: Request, name: str) -> dict[str, Any]:
    """Save an image chosen by the operator (raw file bytes in the request body) and use it."""
    name = name.strip()
    if not config.BACKGROUND_NAME.match(name):
        raise HTTPException(status_code=400, detail="Please choose a .jpg, .png or .webp picture with a simple name.")
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="The picture is empty.")
    if len(data) > config.MAX_BACKGROUND_BYTES:
        raise HTTPException(status_code=400, detail="The picture is too large (15 MB maximum).")
    try:
        config.BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)
        (config.BACKGROUNDS_DIR / name).write_bytes(data)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save the picture ({exc}).") from exc
    rt().state.settings.update({"background_image": name})
    rt().state.settings.save()
    log.info("BACKGROUND %s (%d kB)", name, len(data) // 1024)
    return await rt().publish()


# --------------------------------------------------------------------------
# API: live listening
# --------------------------------------------------------------------------
@app.get("/api/audio/devices")
async def audio_devices() -> dict[str, Any]:
    """Microphones to choose from, and which speech models are downloaded."""
    devices = await asyncio.to_thread(list_input_devices)
    return {
        "devices": devices,
        "selected": rt().state.settings.input_device,
        "models": {size: is_model_available(size) for size in config.WHISPER_MODELS},
    }


@app.post("/api/listen/start")
async def listen_start(body: ListenBody) -> dict[str, Any]:
    await rt().start_listening(body.device)
    return await rt().publish()


@app.post("/api/listen/stop")
async def listen_stop() -> dict[str, Any]:
    await rt().stop_listening()
    return await rt().publish()


# --------------------------------------------------------------------------
# API: detections queue
# --------------------------------------------------------------------------
def _queue_item(item_id: int):
    item = rt().state.queue_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="That detection is no longer in the queue.")
    return item


@app.post("/api/queue/{item_id}/show")
async def queue_show(item_id: int) -> dict[str, Any]:
    """Put a detected verse on the projector."""
    item = _queue_item(item_id)
    ref = _checked(item.ref)
    rt().state.show(ref)
    item.shown = True
    log.info("SHOW %s (from queue)", rt().need_db().format_reference(ref, "KJV"))
    return await rt().publish()


@app.post("/api/queue/{item_id}/step")
async def queue_step(item_id: int, body: DeltaBody) -> dict[str, Any]:
    """Move a detection to the next / previous verse before (or while) showing it."""
    db = rt().need_db()
    state = rt().state
    item = _queue_item(item_id)
    moved = db.neighbor(item.ref, 1 if body.delta >= 0 else -1, state.primary_translation(db))
    if moved is None:
        raise HTTPException(status_code=404, detail="No more verses in that direction.")
    was_live = item.shown and state.screen == "verse" and state.live == item.ref
    item.ref, item.whole_chapter = moved, False
    if was_live:
        state.show(moved)                            # the projector follows
    return await rt().publish()


@app.post("/api/queue/{item_id}/dismiss")
async def queue_dismiss(item_id: int) -> dict[str, Any]:
    rt().state.dismiss(item_id)
    return await rt().publish()


@app.post("/api/queue/clear")
async def queue_clear() -> dict[str, Any]:
    rt().state.clear_queue()
    return await rt().publish()


# --------------------------------------------------------------------------
# API: starting fresh
# --------------------------------------------------------------------------
@app.post("/api/transcript/clear")
async def transcript_clear() -> dict[str, Any]:
    """Empty the live transcript on every operator screen. Detections are kept."""
    runtime = rt()
    runtime.transcript.clear()
    await runtime.hub.broadcast({"type": "transcript_clear"})
    log.info("TRANSCRIPT cleared")
    return {"ok": True}


@app.post("/api/session/reset")
async def session_reset() -> dict[str, Any]:
    """Start fresh (new sermon, new service): empty transcript, detections and projector.

    Listening keeps running if it is on. The detector also forgets which chapter was
    being read and which verses it suggested, so nothing carries over.
    """
    runtime = rt()
    runtime.transcript.clear()
    runtime._recent_words.clear()
    runtime.state.clear_queue()
    runtime.state.clear()
    if runtime.detector is not None:
        runtime.detector.reset()
    await runtime.hub.broadcast({"type": "transcript_clear"})
    log.info("SESSION reset")
    return await runtime.publish()


# --------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    runtime = rt()
    await ws.accept()
    runtime.hub.add(ws)
    try:
        await ws.send_json(runtime.snapshot())
        if runtime.transcript:
            await ws.send_json({"type": "transcript_history", "lines": list(runtime.transcript)})
        while True:
            message = await ws.receive_json()
            kind = message.get("type") if isinstance(message, dict) else None
            if kind == "hello":
                # A screen introduces itself: {"type": "hello", "role": "display"}
                runtime.hub.set_role(ws, "display" if message.get("role") == "display" else "operator")
                await runtime.publish()
                if message.get("role") == "operator":
                    # An operator is here: warm up quoted-verse detection (takes ~15 s) in the
                    # background so it is ready before the sermon starts.
                    asyncio.create_task(runtime.ensure_semantic())
            elif kind == "pages":
                # The projector tells us how many pages the current passage needs.
                try:
                    count = int(message.get("count", 1))
                except (TypeError, ValueError):
                    count = 1
                if count != runtime.state.page_count:
                    runtime.state.set_page_count(count)
                    await runtime.publish()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # malformed message etc.
        log.warning("WebSocket closed: %s", exc)
    finally:
        runtime.hub.remove(ws)
        await runtime.hub.broadcast(runtime.snapshot())
