"""In-memory application state: what is on the projector, the detections queue, settings."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .bible_db import BibleDB, VerseRef
from .config import Settings
from .detector import Candidate

SCREEN_CLEAR = "clear"   # theme background, no text
SCREEN_VERSE = "verse"   # a verse is showing
SCREEN_BLACK = "black"   # fully black output

MAX_QUEUE = 40           # oldest suggestions fall off the end


@dataclass
class QueueItem:
    """A detected verse waiting for the operator."""

    id: int
    ref: VerseRef
    whole_chapter: bool
    kind: str                # "direct" | (Phase 4) "quoted"
    followup: bool
    confidence: float
    heard: str
    shown: bool = False
    created: float = field(default_factory=time.time)


class AppState:
    """Single source of truth shared by the operator and projector screens."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.screen: str = SCREEN_CLEAR
        self.live: VerseRef | None = None   # verse currently (or last) on screen
        self.page: int = 0                  # page index for long passages
        self.page_count: int = 1            # reported by the projector screen
        self._before_black: str = SCREEN_CLEAR
        self.queue: list[QueueItem] = []    # newest first

    # -- which translations to show -----------------------------------------
    def translations(self, db: BibleDB) -> tuple[str, ...]:
        mode = self.settings.translation_mode
        wanted = db.translations if mode == "both" else (mode,)
        return tuple(t for t in wanted if t in db.translations) or db.translations

    def primary_translation(self, db: BibleDB) -> str:
        return self.translations(db)[0]

    # -- projector actions -----------------------------------------------------
    def show(self, ref: VerseRef) -> None:
        self.live = ref
        self.screen = SCREEN_VERSE
        self.page = 0
        self.page_count = 1

    def clear(self) -> None:
        self.live = None
        self.screen = SCREEN_CLEAR
        self.page = 0
        self.page_count = 1

    def toggle_black(self) -> None:
        """Black the output; pressing again restores what was there."""
        if self.screen == SCREEN_BLACK:
            self.screen = self._before_black
        else:
            self._before_black = self.screen
            self.screen = SCREEN_BLACK

    def turn_page(self, delta: int) -> None:
        self.page = max(0, min(self.page + delta, self.page_count - 1))

    def set_page_count(self, count: int) -> None:
        self.page_count = max(1, count)
        self.page = min(self.page, self.page_count - 1)

    # -- detections queue --------------------------------------------------------
    def add_candidate(self, candidate: Candidate) -> QueueItem:
        """Put a detection at the top of the queue (or refine the one it replaces)."""
        item = QueueItem(
            id=candidate.id, ref=candidate.ref, whole_chapter=candidate.whole_chapter,
            kind=candidate.kind, followup=candidate.followup,
            confidence=candidate.confidence, heard=candidate.heard,
        )
        if candidate.replaces is not None:
            for index, old in enumerate(self.queue):
                if old.id == candidate.replaces and not old.shown:
                    self.queue[index] = item        # same place in the list, better reference
                    return item
        self.queue.insert(0, item)
        del self.queue[MAX_QUEUE:]
        return item

    def queue_item(self, item_id: int) -> QueueItem | None:
        return next((item for item in self.queue if item.id == item_id), None)

    def dismiss(self, item_id: int) -> bool:
        before = len(self.queue)
        self.queue = [item for item in self.queue if item.id != item_id]
        return len(self.queue) != before

    def clear_queue(self) -> None:
        self.queue = []

    # -- snapshot sent to every connected screen ------------------------------
    def snapshot(self, db: BibleDB | None, displays: int = 0, error: str | None = None) -> dict[str, Any]:
        panels: list[dict[str, Any]] = []
        queue: list[dict[str, Any]] = []
        if db is not None:
            if self.live is not None:
                panels = db.passage(self.live, self.translations(db))
            primary = self.primary_translation(db)
            for item in self.queue:
                label = db.format_reference(item.ref, primary)
                if item.whole_chapter:
                    label = label.rsplit(":", 1)[0]
                queue.append(
                    {
                        **item.ref.to_dict(),
                        "id": item.id,
                        "reference": label,
                        "translation": primary,
                        "text": " ".join(v["text"] for v in db.get_range(primary, item.ref)),
                        "whole_chapter": item.whole_chapter,
                        "kind": item.kind,
                        "followup": item.followup,
                        "confidence": item.confidence,
                        "heard": item.heard,
                        "shown": item.shown,
                    }
                )
        return {
            "type": "state",
            "screen": self.screen,
            "ref": self.live.to_dict() if self.live else None,
            "panels": panels,
            "page": self.page,
            "page_count": self.page_count,
            "settings": self.settings.to_dict(),
            "queue": queue,
            "displays": displays,
            "error": error,
        }
