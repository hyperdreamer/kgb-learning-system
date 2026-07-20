"""Configuration, constants, and settings management."""

import os
import json
import tempfile

# --- Paths ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = SCRIPT_DIR  # kgb_srs package dir
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)  # parent: kgb_learning_system
DIR_DB = os.path.join(PROJECT_DIR, "db")
SETTINGS_FILE = os.path.join(PROJECT_DIR, "barsky_settings.json")

# Canonical relative layout under the database root.
# Language-based uses two subtype directories; Knowledge-based is flat.
CANONICAL_DB_SUBDIRS = (
    os.path.join("Language-based", "Sentence-based"),
    os.path.join("Language-based", "Word-Phrase-based"),
    "Knowledge-based",
)

# --- Default Settings ---
DEFAULT_SETTINGS = {
    "width": 900,
    "height": 700,
    "font_family": "Arial",
    "font_size": 14,
    # Flashcard study content (separate from UI chrome)
    "content_font_family": "Arial",
    "content_font_size": 18,
    # Root folder for all databases. Empty → project db/ (DIR_DB).
    "database_root": "",
    "default_database": "",
    "tts_voice": "en-US-AvaMultilingualNeural",
    # Audio page language filter ("" = All languages)
    "tts_language": "",
    # AI provider defaults (non-secret)
    "ai_base_url": "https://api.openai.com/v1",
    "ai_model": "gpt-4o-mini",
    "ai_api_key": "",
    "ai_timeout": 30,
    # Language settings for AI prompts
    "explanation_language": "Chinese",
}


def get_database_root(settings=None) -> str:
    """Resolve the configured database root directory.

    Empty / missing ``database_root`` falls back to the project ``db/`` path
    (``DIR_DB``) so existing installs keep working without a settings change.
    """
    if settings is None:
        settings = load_settings()
    root = (settings.get("database_root") or "").strip()
    if not root:
        return DIR_DB
    return os.path.abspath(os.path.expanduser(root))


def ensure_database_root_structure(root: str | None = None) -> str:
    """Create the canonical category/subtype directories under *root*.

    Layout::

        <root>/
        ├── Language-based/
        │   ├── Sentence-based/
        │   └── Word-Phrase-based/
        └── Knowledge-based/

    Returns the absolute root path. Missing parents are created. Existing
    directories and files (including legacy ``Languages/`` / ``Math/``) are left
    untouched.
    """
    if root is None:
        root = get_database_root()
    root = os.path.abspath(os.path.expanduser(root))
    os.makedirs(root, exist_ok=True)
    for subdir in CANONICAL_DB_SUBDIRS:
        os.makedirs(os.path.join(root, subdir), exist_ok=True)
    return root


def load_settings():
    """Load settings from JSON file, merging with defaults."""
    settings = dict(DEFAULT_SETTINGS)
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings.update(json.load(f))
        except Exception as e:
            print(f"Error loading settings: {e}")
    return settings


def save_settings(settings):
    """Atomically save settings with owner-only permissions (API key safety)."""
    temp_path = None
    try:
        directory = os.path.dirname(SETTINGS_FILE)
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".barsky_settings.", dir=directory)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, SETTINGS_FILE)
        temp_path = None
        os.chmod(SETTINGS_FILE, 0o600)
    except Exception as e:
        raise OSError(f"Could not save settings: {e}") from e
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
