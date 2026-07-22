"""Focused regression coverage for review queue loading."""

import datetime
import sqlite3
from types import SimpleNamespace

from kgb_srs.review_controller import ReviewControllerMixin


class _CheckBox:
    def __init__(self, checked=False):
        self._checked = checked

    def isChecked(self):
        return self._checked


def test_review_controller_loads_due_or_all_cards_in_id_order():
    """Queue construction belongs to the review controller, not BarskyApp."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE cards ("
            "id INTEGER PRIMARY KEY, front TEXT, back TEXT, box INTEGER, next_review TEXT)"
        )
        today = datetime.date.today()
        conn.executemany(
            "INSERT INTO cards VALUES (?, ?, ?, ?, ?)",
            [
                (3, "due late", "", 1, today.isoformat()),
                (
                    1,
                    "due early",
                    "",
                    2,
                    (today - datetime.timedelta(days=1)).isoformat(),
                ),
                (2, "future", "", 3, (today + datetime.timedelta(days=1)).isoformat()),
            ],
        )
        state = SimpleNamespace(
            all_cards_checkbox=_CheckBox(), random_checkbox=_CheckBox()
        )

        assert ReviewControllerMixin._load_review_queue(state, conn.cursor()) == [
            (1, "due early", "", 2),
            (3, "due late", "", 1),
        ]

        state.all_cards_checkbox._checked = True
        assert ReviewControllerMixin._load_review_queue(state, conn.cursor()) == [
            (1, "due early", "", 2),
            (2, "future", "", 3),
            (3, "due late", "", 1),
        ]
    finally:
        conn.close()


def test_review_controller_shuffles_when_random_review_is_enabled(monkeypatch):
    """Shuffle preserves the fetched cards while changing their queue order."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE cards ("
            "id INTEGER PRIMARY KEY, front TEXT, back TEXT, box INTEGER, next_review TEXT)"
        )
        today = datetime.date.today().isoformat()
        conn.executemany(
            "INSERT INTO cards VALUES (?, ?, ?, ?, ?)",
            [(card_id, f"front {card_id}", "", 1, today) for card_id in range(1, 4)],
        )
        shuffled = []
        monkeypatch.setattr(
            "kgb_srs.review_controller.random.shuffle",
            lambda queue: (shuffled.append(list(queue)), queue.reverse()),
        )
        state = SimpleNamespace(
            all_cards_checkbox=_CheckBox(), random_checkbox=_CheckBox(checked=True)
        )

        queue = ReviewControllerMixin._load_review_queue(state, conn.cursor())

        assert [card[0] for card in shuffled[0]] == [1, 2, 3]
        assert [card[0] for card in queue] == [3, 2, 1]
    finally:
        conn.close()
