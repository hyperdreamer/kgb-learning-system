"""Compatibility facade for card-entry and database-creation dialogs.

The dialog implementations live in focused modules. Existing imports from
``kgb_srs.forms`` remain supported for third-party callers and older tests.

``_AIGenerateWorker`` is a private compatibility export for callers that have
historically imported it from this facade. New code should import the worker
from :mod:`kgb_srs.form_helpers` instead.
"""

from .database_creation_dialog import DBCreationDialog
from .form_helpers import _AIGenerateWorker, _apply_ui_font  # Private compatibility exports.
from .sentence_card_dialog import SentenceCardDialog
from .word_phrase_dialog import WordPhraseCardDialog

__all__ = [
    "SentenceCardDialog",
    "WordPhraseCardDialog",
    "DBCreationDialog",
    # Private legacy compatibility exports; prefer kgb_srs.form_helpers.
    "_AIGenerateWorker",
    "_apply_ui_font",
]
