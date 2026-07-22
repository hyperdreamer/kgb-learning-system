"""Configuration, constants, and settings management."""

import os
import json
import logging
import tempfile


logger = logging.getLogger(__name__)

# --- Paths ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = SCRIPT_DIR  # kgb_srs package dir
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)  # parent: kgb_learning_system
DIR_DB = os.path.join(PROJECT_DIR, "db")
SETTINGS_FILE = os.path.join(PROJECT_DIR, "barsky_settings.json")


def normalize_settings_path(path) -> str:
    """Return an absolute, user-expanded path for a settings file."""
    return os.path.abspath(os.path.expanduser(os.fspath(path)))

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
    # Sentence card editor dialog (persisted on dialog close)
    "sentence_dialog_width": 720,
    "sentence_dialog_height": 640,
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
    # Named OpenAI-compatible provider profiles (switchable in Settings).
    # Legacy flat ai_base_url/ai_model/ai_api_key/ai_timeout are migrated
    # into a profile on load, then stripped — not stored as mirrors.
    "ai_active_provider": "Default",
    "ai_providers": {
        "Default": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key": "",
            "timeout": 30,
        }
    },
    # Language settings for AI prompts
    "explanation_language": "Chinese",
}

_POSITIVE_INT_SETTINGS = frozenset({
    "width",
    "height",
    "sentence_dialog_width",
    "sentence_dialog_height",
    "font_size",
    "content_font_size",
})
_STRING_SETTINGS = frozenset({
    "database_root",
    "default_database",
    "font_family",
    "content_font_family",
    "tts_voice",
    "tts_language",
    "explanation_language",
    "ai_active_provider",
})


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


def is_path_under_root(path: str, root: str) -> bool:
    """True if *path* is the same as or under *root* after canonicalization."""
    if not path or not root:
        return False
    abs_path = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    abs_root = os.path.realpath(os.path.abspath(os.path.expanduser(root)))
    try:
        common = os.path.commonpath([abs_path, abs_root])
    except ValueError:
        # Different drives on Windows, etc.
        return False
    return common == abs_root


def relative_db_path(path: str, root: str) -> str | None:
    """Return *path* relative to *root* if under root; else None."""
    if not path or not root:
        return None
    if not is_path_under_root(path, root):
        return None
    abs_path = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    abs_root = os.path.realpath(os.path.abspath(os.path.expanduser(root)))
    rel = os.path.relpath(abs_path, abs_root)
    return os.path.normpath(rel)


def resolve_default_database(settings=None) -> str:
    """Resolve settings['default_database'] to an absolute file path.

    Rules:
    - Empty / missing → ""
    - Absolute under get_database_root(settings) → that absolute path
    - Absolute not under root → "" (out of scope)
    - Relative → join with get_database_root(settings)
    File existence is not required for resolution.
    """
    if settings is None:
        settings = load_settings()
    value = (settings.get("default_database") or "").strip()
    if not value:
        return ""
    root = get_database_root(settings)
    if os.path.isabs(value) or value.startswith("~"):
        abs_value = os.path.abspath(os.path.expanduser(value))
        if is_path_under_root(abs_value, root):
            return abs_value
        return ""
    # Relative: join then re-check so ".." cannot escape the root.
    joined = os.path.normpath(os.path.join(root, value))
    if is_path_under_root(joined, root):
        return joined
    return ""


def normalize_default_database(value: str, root: str) -> str:
    """Normalize a chosen/stored default_database for persistence.

    - Empty → ""
    - Absolute under root → relative path via os.path.relpath
    - Relative that stays under root when joined → normalized relative
    - Outside root → ""
    """
    value = (value or "").strip()
    if not value:
        return ""
    root = os.path.abspath(os.path.expanduser(root)) if root else ""
    if not root:
        return ""
    if os.path.isabs(value) or value.startswith("~"):
        abs_value = os.path.abspath(os.path.expanduser(value))
        rel = relative_db_path(abs_value, root)
        return rel or ""
    # Relative: only keep if it stays under root when joined
    joined = os.path.normpath(os.path.join(root, value))
    rel = relative_db_path(joined, root)
    return rel or ""


def load_settings(settings_file=None):
    """Load settings from JSON file, merging with defaults.

    ``settings_file`` permits a launcher-selected config while preserving the
    project-root :data:`SETTINGS_FILE` as the default for existing callers.
    """
    settings_file = normalize_settings_path(settings_file or SETTINGS_FILE)
    settings = dict(DEFAULT_SETTINGS)
    # Deep-copy nested defaults so callers cannot mutate the module constant.
    settings["ai_providers"] = {
        name: dict(entry)
        for name, entry in DEFAULT_SETTINGS.get("ai_providers", {}).items()
    }
    loaded: dict = {}
    if os.path.isfile(settings_file):
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                loaded = raw
                for key, value in loaded.items():
                    if key in _POSITIVE_INT_SETTINGS:
                        if type(value) is int and value > 0:
                            settings[key] = value
                    elif key in _STRING_SETTINGS:
                        if isinstance(value, str):
                            settings[key] = value
                    elif key == "ai_providers":
                        # Provider mappings are normalized below. Other types
                        # cannot be safely used as profile collections.
                        if isinstance(value, dict):
                            settings[key] = value
                    elif key not in DEFAULT_SETTINGS:
                        # Preserve extension keys, but never let an invalid
                        # value replace a known default setting.
                        settings[key] = value
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Could not load settings from %s; using defaults: %s",
                settings_file,
                exc,
            )
    # With no usable profile mapping, drop the default bag so
    # ensure_ai_provider_profiles migrates legacy flat keys. Otherwise an
    # empty Default profile would clobber a real ai_api_key.
    if (
        "ai_providers" not in loaded
        or not isinstance(loaded.get("ai_providers"), dict)
    ):
        settings.pop("ai_providers", None)
        if "ai_active_provider" not in loaded:
            settings.pop("ai_active_provider", None)
    # Normalize AI provider profiles (migrates legacy flat-only configs).
    from .ai_provider import ensure_ai_provider_profiles

    ensure_ai_provider_profiles(settings)
    return settings


def save_settings(settings, settings_file=None):
    """Atomically save settings with owner-only permissions (API key safety).

    AI config is stored only under ``ai_providers`` / ``ai_active_provider``.
    Legacy flat ``ai_*`` keys are migrated into profiles, then stripped.
    ``settings_file`` permits a launcher-selected config file.
    """
    from .ai_provider import ensure_ai_provider_profiles

    settings_file = normalize_settings_path(settings_file or SETTINGS_FILE)
    ensure_ai_provider_profiles(settings)
    temp_path = None
    try:
        directory = os.path.dirname(settings_file)
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".barsky_settings.", dir=directory)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, settings_file)
        temp_path = None
        os.chmod(settings_file, 0o600)
    except Exception as e:
        raise OSError(f"Could not save settings: {e}") from e
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
