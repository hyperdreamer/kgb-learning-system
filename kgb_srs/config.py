"""Configuration, constants, and settings management."""

import os
import json

# --- Paths ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = SCRIPT_DIR  # kgb_srs package dir
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)  # parent: 00.KGB_Learning_System
DIR_DB = os.path.join(PROJECT_DIR, "db")
SETTINGS_FILE = os.path.join(PROJECT_DIR, "barsky_settings.json")

# --- Default Settings ---
DEFAULT_SETTINGS = {
    "width": 900,
    "height": 700,
    "font_family": "Arial",
    "font_size": 14,
    "default_database": "",
    "tts_voice": "en-US-AvaMultilingualNeural",
}


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
    """Save settings to JSON file."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        print(f"Error saving settings: {e}")
