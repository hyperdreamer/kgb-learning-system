"""Compatibility facade for card-entry and database-creation dialogs.

The dialog implementations live in focused modules. Public dialog imports from
``kgb_srs.forms`` remain supported. The private helper aliases are deprecated
through the 2.x series; import them from :mod:`kgb_srs.form_helpers` instead.
"""

import warnings

from . import form_helpers
from .database_creation_dialog import DBCreationDialog
from .sentence_card_dialog import SentenceCardDialog
from .word_phrase_dialog import WordPhraseCardDialog

_LEGACY_HELPER_NAMES = ("_AIGenerateWorker", "_apply_ui_font")

__all__ = [
    "SentenceCardDialog",
    "WordPhraseCardDialog",
    "DBCreationDialog",
    *_LEGACY_HELPER_NAMES,
]


def __getattr__(name: str):
    if name not in _LEGACY_HELPER_NAMES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    warnings.warn(
        f"{__name__}.{name} is deprecated; import it from "
        "kgb_srs.form_helpers instead. The alias will be removed in 3.0.",
        DeprecationWarning,
        stacklevel=2,
    )
    return getattr(form_helpers, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LEGACY_HELPER_NAMES))
