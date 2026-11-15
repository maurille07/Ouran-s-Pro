"""Paths, constants and user settings.

Settings are a small dataclass saved to ``data/settings.json`` so the operator's
choices (translation, theme, font size) survive a restart.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, fields
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# --- Paths -----------------------------------------------------------------
ROOT_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = ROOT_DIR / "data"
STATIC_DIR: Path = ROOT_DIR / "static"
LOG_DIR: Path = ROOT_DIR / "logs"
BOOKS_JSON: Path = DATA_DIR / "books.json"
BIBLE_DB: Path = DATA_DIR / "bible.sqlite"
SETTINGS_JSON: Path = DATA_DIR / "settings.json"
LOG_FILE: Path = LOG_DIR / "ouranos.log"
MODELS_DIR: Path = ROOT_DIR / "models"   # offline speech + embedding models, downloaded by setup
BACKGROUNDS_DIR: Path = DATA_DIR / "backgrounds"   # optional projector background images
EMBEDDINGS_DIR: Path = DATA_DIR / "embeddings"   # one <translation>.npy + .index.npy per Bible

# Multilingual sentence-embedding model (English + French) for quoted-verse matching.
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_MODEL_DIR: Path = MODELS_DIR / "paraphrase-multilingual-MiniLM-L12-v2"

# --- Constants -------------------------------------------------------------
APP_NAME = "Ouranòs-Pro"
DEFAULT_HOST = "127.0.0.1"  # local only: nothing is exposed to the network
DEFAULT_PORT = 8765

TRANSLATIONS: tuple[str, ...] = ("KJV", "LSG")
TRANSLATION_MODES: tuple[str, ...] = ("KJV", "LSG", "both")
THEMES: tuple[str, ...] = ("dark", "light", "church")
MAX_RANGE_VERSES = 60  # safety cap on how many verses one "Show" can display
WHISPER_MODELS: tuple[str, ...] = ("base", "small", "medium")
LANGUAGES: tuple[str, ...] = ("auto", "en", "fr")
SAMPLE_RATE = 16_000   # Hz, mono: what the speech model expects
# Optional WAV file offered as a fake "microphone" (for testing without a preacher).
TEST_WAV_ENV = "OURANOS_TEST_WAV"
# Background images: plain file names only (no folders), common web formats.
BACKGROUND_NAME = re.compile(r"^[\w][\w .()-]{0,80}\.(?:jpg|jpeg|png|webp)$", re.IGNORECASE)
MAX_BACKGROUND_BYTES = 15 * 1024 * 1024
# Auto mode shows a detection by itself only at or above this confidence
# (spoken references with a verse; never quoted verses or whole chapters).
AUTO_DISPLAY_CONFIDENCE = 0.90


def whisper_model_dir(size: str) -> Path:
    return MODELS_DIR / f"whisper-{size}"


@dataclass
class Settings:
    """User-adjustable settings (persisted as JSON)."""

    translation_mode: str = "KJV"   # "KJV" | "LSG" | "both" (side by side)
    theme: str = "dark"             # "dark" | "light" | "church"
    font_scale: float = 1.0         # multiplier for projector text size
    show_verse_numbers: bool = True
    whisper_model: str = "small"    # "base" (fastest) | "small" | "medium" (most accurate)
    language: str = "auto"          # "auto" | "en" | "fr" -- language of the sermon
    input_device: str = ""          # microphone name; "" = the system default
    dedupe_seconds: float = 20.0    # do not re-suggest the same verse within this time
    semantic_enabled: bool = True   # also detect verses that are quoted / paraphrased
    semantic_threshold: float = 0.65  # minimum score (0..1) for a quoted-verse match
    auto_display: bool = False      # True: spoken references go straight to the projector
    background_image: str = ""      # file name in data/backgrounds/, "" = theme background

    def update(self, changes: dict[str, Any]) -> None:
        """Apply validated changes. Raises ValueError on a bad value."""
        for key, value in changes.items():
            if key == "translation_mode":
                if value not in TRANSLATION_MODES:
                    raise ValueError(f"translation_mode must be one of {TRANSLATION_MODES}")
                self.translation_mode = value
            elif key == "theme":
                if value not in THEMES:
                    raise ValueError(f"theme must be one of {THEMES}")
                self.theme = value
            elif key == "font_scale":
                self.font_scale = round(min(1.6, max(0.6, float(value))), 2)
            elif key == "show_verse_numbers":
                self.show_verse_numbers = bool(value)
            elif key == "whisper_model":
                if value not in WHISPER_MODELS:
                    raise ValueError(f"whisper_model must be one of {WHISPER_MODELS}")
                self.whisper_model = value
            elif key == "language":
                if value not in LANGUAGES:
                    raise ValueError(f"language must be one of {LANGUAGES}")
                self.language = value
            elif key == "input_device":
                self.input_device = str(value or "")[:200]
            elif key == "dedupe_seconds":
                self.dedupe_seconds = round(min(600.0, max(0.0, float(value))), 1)
            elif key == "auto_display":
                self.auto_display = bool(value)
            elif key == "background_image":
                name = str(value or "")
                if name and not BACKGROUND_NAME.match(name):
                    raise ValueError("background_image must be a .jpg, .png or .webp file name")
                self.background_image = name
            elif key == "semantic_enabled":
                self.semantic_enabled = bool(value)
            elif key == "semantic_threshold":
                self.semantic_threshold = round(min(0.95, max(0.4, float(value))), 2)
            else:
                raise ValueError(f"Unknown setting: {key}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    # -- persistence --
    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        """Load settings; fall back to defaults if the file is missing or broken."""
        settings = cls()
        path = path or SETTINGS_JSON
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return settings
        except (OSError, ValueError) as exc:
            logging.getLogger(__name__).warning("Could not read %s (%s); using defaults", path, exc)
            return settings
        known = {f.name for f in fields(cls)}
        for key, value in raw.items():
            if key in known:
                try:
                    settings.update({key: value})
                except (ValueError, TypeError):
                    pass  # ignore an invalid saved value, keep the default
        return settings

    def save(self, path: Path | None = None) -> None:
        path = path or SETTINGS_JSON
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        except OSError as exc:
            logging.getLogger(__name__).error("Could not save settings: %s", exc)


def setup_logging() -> None:
    """Log to logs/ouranos.log (rotating) and to the console."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return  # already configured
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    root.setLevel(logging.INFO)
