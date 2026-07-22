"""Hierarchical database-selector menu construction."""

import os
import sqlite3

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMenu

from .catalog import (
    DB_DIR_KNOWLEDGE,
    DB_DIR_LANGUAGE_SENTENCE,
    DB_DIR_LANGUAGE_WORD_PHRASE,
    DatabaseType,
    build_catalog_tree,
    infer_database_type,
    read_database_type,
)
from .config import get_database_root
from .db import find_databases

_DB_MENU_STYLESHEET = (
    "QMenu::item {"
    " padding-left: 12px;"
    " padding-right: 28px;"
    " padding-top: 6px;"
    " padding-bottom: 6px;"
    " }"
)


def _compute_display_path(db_path, db_type, legacy_display):
    """Compute the catalog display path for a database."""
    from .catalog import display_path_for

    norm_path = os.path.normpath(db_path).replace("\\", "/")
    canon_dirs = {
        DatabaseType.LANGUAGE_SENTENCE: DB_DIR_LANGUAGE_SENTENCE.replace("\\", "/"),
        DatabaseType.LANGUAGE_WORD_PHRASE: DB_DIR_LANGUAGE_WORD_PHRASE.replace("\\", "/"),
        DatabaseType.KNOWLEDGE: DB_DIR_KNOWLEDGE.replace("\\", "/"),
    }
    canonical = canon_dirs.get(db_type, "")
    if canonical and canonical in norm_path:
        return display_path_for(db_path, db_type)

    category = db_type.category_display
    legacy = legacy_display.replace("\\", "/")
    if db_type == DatabaseType.KNOWLEDGE:
        return os.path.join(category, legacy)
    return os.path.join(category, db_type.display, legacy)


def _open_and_infer_type(
    db_path,
    *,
    connect=sqlite3.connect,
    read=read_database_type,
    infer=infer_database_type,
):
    """Open a DB briefly to read or infer its type."""
    conn = None
    try:
        conn = connect(db_path)
        db_type = read(conn)
        if db_type is not None:
            return db_type
    except (sqlite3.Error, OSError):
        pass
    finally:
        if conn is not None:
            conn.close()
    return infer(db_path)


def build_db_menu(owner, parent_menu, *, find=find_databases, infer=_open_and_infer_type):
    """Populate *parent_menu* with the owner's catalogued databases."""
    settings = getattr(owner, "settings", None) or {}
    entries = []
    for display, full_path in find(get_database_root(settings)):
        db_type = infer(full_path)
        entries.append((_compute_display_path(full_path, db_type, display), full_path, db_type))

    tree = build_catalog_tree(entries)
    current_path = getattr(owner, "current_db_path", None)

    def populate_menu(menu, subtree):
        menu.setStyleSheet(_DB_MENU_STYLESHEET)
        items = sorted(
            subtree.items(),
            key=lambda item: (not isinstance(item[1], dict), item[0].lower()),
        )
        for name, value in items:
            if isinstance(value, dict):
                submenu = QMenu(name, menu)
                populate_menu(submenu, value)
                menu.addMenu(submenu)
            else:
                db_path, _db_type = value
                action = menu.addAction(f"{'● ' if db_path == current_path else ''}{name}")
                action.setData(db_path)
        return menu

    return populate_menu(parent_menu, tree)


def _menu_contains(menu, target_path):
    """Return whether a nested menu includes an action for *target_path*."""
    for action in menu.actions():
        if action.menu():
            if _menu_contains(action.menu(), target_path):
                return True
        elif action.data() == target_path:
            return True
    return False


def _expand_to_path(menu, target_path):
    """Activate nested submenus down to the selected database action."""
    for action in menu.actions():
        if action.menu():
            if _menu_contains(action.menu(), target_path):
                menu.setActiveAction(action)
                QTimer.singleShot(
                    20,
                    lambda submenu=action.menu(), path=target_path: _expand_to_path(submenu, path),
                )
                return
        elif action.data() == target_path:
            menu.setActiveAction(action)
            return
