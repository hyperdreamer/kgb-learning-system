"""Compatibility facade for card-entry and database-creation dialogs.

The dialog implementations live in focused modules. Existing imports from
``kgb_srs.forms`` remain supported for third-party callers and older tests.
"""

from .database_creation_dialog import DBCreationDialog
from .form_helpers import _AIGenerateWorker, _apply_ui_font
from .sentence_card_dialog import SentenceCardDialog
from .word_phrase_dialog import WordPhraseCardDialog

__all__ = [
    "SentenceCardDialog",
    "WordPhraseCardDialog",
    "DBCreationDialog",
]
