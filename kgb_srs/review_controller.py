"""Daily-review state machine and scheduling behavior."""

import datetime
import random
import sqlite3
from typing import Literal, NamedTuple

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox

from .browse_dialog import _fetch_expressions_for_card
from .catalog import DatabaseType
from .db import rollback_after_failure
from .graphics import FlashCardItem
from .markdown_utils import markdown_to_plain_text
from .validation import (
    format_sentence_meaning_lines as _format_sentence_meaning_lines,
    highlight_unfamiliar_in_sentence as _highlight_sentence_for_items,
    sort_items_by_sentence_order as _sort_items_by_sentence_order,
)


def _card_speech_text(*segments) -> str:
    """Return plain-text card fields as distinct TTS speech segments."""
    spoken_segments = []
    for segment in segments:
        spoken_segment = markdown_to_plain_text(segment)
        if spoken_segment:
            spoken_segments.append(spoken_segment)
    return "\n".join(spoken_segments)


def _ordered_sentence_card_items(conn, card_id, sentence):
    """Fetch a sentence card's expressions in their displayed order."""
    items = _fetch_expressions_for_card(conn, card_id)
    return _sort_items_by_sentence_order(sentence, items)


def _sentence_card_speech_text(conn, card_id, sentence) -> str:
    """Build sentence-card TTS text from the same order used for display."""
    ordered_items = _ordered_sentence_card_items(conn, card_id, sentence)
    return _card_speech_text(
        sentence, *_format_sentence_meaning_lines(ordered_items)
    )


def _word_phrase_card_speech_text(front, back) -> str:
    """Build word/phrase TTS text with each sense line as a speech segment."""
    back_lines = (line.lstrip() for line in (back or "").splitlines())
    return _card_speech_text(front, *back_lines)


class ReviewHistoryEntry(NamedTuple):
    """A card left behind during review and the transition that left it."""

    card: tuple
    transition: Literal["skipped", "graded"]


class ReviewControllerMixin:
    """Review behavior mixed into :class:`main_window.BarskyApp`."""

    def _update_button_visibility(self):
        """Review-control state machine: idle vs active.

        IDLE   (no active review):
          - primary button → "Start Daily Review" or "Resume Daily Review"
          - Restart / Previous / Close → disabled

        ACTIVE (daily review in progress):
          - primary button → "Next"
          - Restart / Close → enabled
          - Previous → enabled once the session path has a prior card
            (after Next or a grade — reverse of Next)
        """
        has_db = self.conn is not None
        has_card = self.current_card is not None
        is_active = self.review_mode == "daily"
        has_paused = self._paused_review_mode == "daily" and (
            self._paused_review_card is not None
            or bool(self._paused_review_history)
            or bool(self._paused_cards_due)
            or bool(self._paused_daily_queue)
        )
        has_history = bool(self._daily_review_history)
        is_wp = (
            has_db
            and getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_WORD_PHRASE
        )

        # W/P is a derived projection — no manual Add/Delete Entry.
        if hasattr(self, "add_entry_btn"):
            self.add_entry_btn.setVisible(has_db and not is_wp)
            self.add_entry_btn.setEnabled(has_db and not is_wp)
        if hasattr(self, "delete_entry_btn"):
            self.delete_entry_btn.setVisible(has_db and not is_wp)
            self.delete_entry_btn.setEnabled(has_db and has_card and not is_wp)
        if hasattr(self, "browse_btn"):
            self.browse_btn.setEnabled(has_db)

        if not has_db:
            self.start_btn.setEnabled(False)
            self.restart_review_btn.setEnabled(False)
            self.previous_review_btn.setEnabled(False)
            self.close_review_btn.setEnabled(False)
            return

        if is_active:
            # ── ACTIVE state ──
            self.start_btn.setText(" Next")
            self.start_btn.setIcon(self._icon("go-next"))
            self.restart_review_btn.setEnabled(True)
            # Reverse of Next: enable only when there is a prior step.
            self.previous_review_btn.setEnabled(has_history)
            self.close_review_btn.setEnabled(True)
        else:
            # ── IDLE state ──
            if has_paused:
                self.start_btn.setText(" Resume Daily Review")
            else:
                self.start_btn.setText(" Start Daily Review")
            self.start_btn.setIcon(self._icon("media-playback-start"))
            self.start_btn.setEnabled(True)
            self.restart_review_btn.setEnabled(False)
            self.previous_review_btn.setEnabled(False)
            self.close_review_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Review Flow
    # ------------------------------------------------------------------
    def _on_primary_button_clicked(self):
        """Dispatch primary button based on current state.

        - IDLE  → start (or resume) daily review.
        - ACTIVE → advance to next card in the daily queue.
        """
        if self.review_mode == "daily":
            self._advance_daily_queue()
        else:
            self.start_review()

    def _advance_daily_queue(self):
        """Skip the current card and advance to the next in the daily queue.

        The current (ungraded) card is returned to the end of the queue
        so it will be reviewed later in this session.  It is also pushed
        onto the session path so Previous is the reverse of Next.
        """
        if not self.current_card or self.review_mode != "daily":
            return

        transition = self._current_card_transition or "skipped"
        self._daily_review_history.append(
            ReviewHistoryEntry(self.current_card, transition)
        )
        # A graded card restored by Previous has already left the queue.
        if transition != "graded":
            self.cards_due.append(self.current_card)
        self._update_button_visibility()
        self.show_next_card()

    def _load_review_queue(self, cursor):
        """Return the queue for a fresh daily review session.

        Due-only when *All cards* is unchecked; every card when checked.
        """
        all_cards = bool(
            getattr(self, "all_cards_checkbox", None)
            and self.all_cards_checkbox.isChecked()
        )
        if all_cards:
            cursor.execute("SELECT id, front, back, box FROM cards ORDER BY id")
        else:
            cursor.execute(
                "SELECT id, front, back, box FROM cards WHERE next_review <= ?",
                (datetime.date.today().isoformat(),),
            )
        queue = list(cursor.fetchall())
        if self.random_checkbox.isChecked():
            random.shuffle(queue)
        else:
            queue.sort(key=lambda card: card[0])
        return queue

    def _previous_daily_card(self):
        """Step back one card in this daily session path (reverse of Next).

        Works after Next (skip) or after a grade.  The current card returns
        to the front of the queue; the prior path entry is restored.
        If there is no prior step, this is a no-op.
        """
        if self.review_mode != "daily" or not self._daily_review_history:
            return

        # Skip deleted/missing history entries instead of showing a ghost card.
        previous = None
        while self._daily_review_history:
            candidate = self._daily_review_history.pop()
            prev_id = candidate.card[0]
            if self.conn is not None:
                c = self.conn.cursor()
                c.execute(
                    "SELECT id, front, back, box FROM cards WHERE id = ?",
                    (prev_id,),
                )
                fresh = c.fetchone()
                if fresh is None:
                    continue
                previous = ReviewHistoryEntry(fresh, candidate.transition)
            else:
                previous = candidate
            break

        if previous is None:
            self._update_button_visibility()
            return

        # A restored grade is no longer an ungraded queue member.
        if self.current_card is not None and self._current_card_transition != "graded":
            self.cards_due.insert(0, self.current_card)

        # Next-skip puts the prior card at the end of the queue; remove it
        # so we do not show a duplicate after restoring it as current.
        prev_card = previous.card
        prev_id = prev_card[0]
        self.cards_due = [c for c in self.cards_due if c[0] != prev_id]

        if self.card_ui:
            self.scene.removeItem(self.card_ui)
            self.card_ui = None

        self.current_card = prev_card
        self._current_card_transition = previous.transition
        self.is_current_flipped = False
        self.draw_card_ui()
        self._update_button_visibility()

    def _restart_daily_review(self):
        """Restart the current daily session from the beginning.

        Re-reads the queue with the current *All cards* / Shuffle options
        (so toggling All cards then Restart picks up the new mode).
        Clears review history. Only has effect during an active daily review.
        """
        if self.review_mode != "daily":
            return

        if self.card_ui:
            self.scene.removeItem(self.card_ui)
            self.card_ui = None

        if self.conn is not None:
            c = self.conn.cursor()
            self.cards_due = self._load_review_queue(c)
            self._daily_queue_snapshot = list(self.cards_due)
        else:
            self.cards_due = list(self._daily_queue_snapshot)

        self._daily_review_history = []
        self.current_card = None
        self._current_card_transition = None

        self.show_next_card()

    def close_review(self):
        """Pause the active daily review and return to idle.

        The current card, remaining queue, original queue snapshot, and
        review history are all preserved so the session can be resumed
        exactly where it left off.  Closing does not modify the database.
        """
        if self.review_mode != "daily":
            return

        # Queue may already be empty (finished session still active so
        # Previous can restore the last graded card). Pause whatever remains.
        self._paused_review_card = self.current_card
        self._paused_review_mode = self.review_mode
        self._paused_cards_due = list(self.cards_due)
        self._paused_daily_queue = list(self._daily_queue_snapshot)
        self._paused_review_history = list(self._daily_review_history)
        self._paused_current_card_transition = self._current_card_transition

        if self.card_ui:
            self.scene.removeItem(self.card_ui)
            self.card_ui = None

        self.current_card = None
        self._current_card_transition = None
        self.review_mode = ""
        self._daily_review_history = []
        self._daily_queue_snapshot = []

        self._update_button_visibility()

    def _resume_paused_card(self, cursor):
        """Restore a paused card with its review-transition provenance.

        If the paused card was deleted from DB, clear state and continue with
        the queue silently.
        """
        paused = self._paused_review_card
        paused_transition = self._paused_current_card_transition
        self._paused_review_card = None
        self._paused_review_mode = ""
        self._paused_current_card_transition = None

        if paused is None:
            return

        cursor.execute(
            "SELECT id, front, back, box FROM cards WHERE id = ?",
            (paused[0],),
        )
        fresh = cursor.fetchone()
        if fresh is None:
            return  # card deleted — skip silently

        paused_id = fresh[0]
        self.cards_due = [c for c in self.cards_due if c[0] != paused_id]
        self.current_card = fresh
        self._current_card_transition = paused_transition

    def delete_current_card(self):
        if getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_WORD_PHRASE:
            QMessageBox.information(
                self,
                "Read-only Word/Phrase Database",
                "Word/phrase cards are a projection of the shared sense catalog.\n\n"
                "Delete or change senses via sentence cards; this dictionary "
                "updates automatically. Manual delete is disabled.",
            )
            return

        if not self.current_card:
            QMessageBox.information(
                self, "Nothing to Delete", "No card is currently displayed."
            )
            return

        card_id, front, back, box = self.current_card
        preview = front[:80] + ("..." if len(front) > 80 else "")
        msg = (
            f"You are about to <b>permanently delete</b> the current card:\n\n"
            f"<b>ID:</b> {card_id} | <b>Box:</b> {box}\n"
            f"<b>Front:</b> {preview}\n\n"
            f"This action <span style='color:red;'>cannot be undone</span>.\n"
            f"Are you sure you want to delete it?"
        )
        reply = QMessageBox.question(
            self,
            "⚠ Permanently Delete Card",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if self._delete_card_by_id(card_id) is None:
            return

        QMessageBox.information(
            self, "Deleted", f"Card #{card_id} has been permanently deleted."
        )
        self.show_next_card()

    def start_review(self):
        """Start (or resume) the daily review of due cards.

        On first start, queries all due cards and saves a snapshot for
        Restart.  On resume after close, restores the preserved queue,
        snapshot, and history so the session continues from where it was
        paused.
        """
        if not self.conn:
            return

        self.review_mode = "daily"

        if self.current_card is not None:
            if self.card_ui:
                self.scene.removeItem(self.card_ui)
                self.card_ui = None
            self.current_card = None
        self._current_card_transition = None

        c = self.conn.cursor()

        # Distinguish first-start from resume-after-close.
        # A finished queue may pause with no current card but with history /
        # snapshot still worth restoring for Previous / Restart.
        resume_daily = self._paused_review_mode == "daily" and (
            self._paused_review_card is not None
            or bool(self._paused_review_history)
            or bool(self._paused_cards_due)
            or bool(self._paused_daily_queue)
        )

        if resume_daily:
            # Restore deep session state preserved by close_review() before
            # re-inserting the paused card at the front of the queue.
            self.cards_due = list(self._paused_cards_due)
            self._daily_queue_snapshot = list(self._paused_daily_queue)
            self._daily_review_history = list(self._paused_review_history)
            self._paused_cards_due = []
            self._paused_daily_queue = []
            self._paused_review_history = []
            # Clear mode flag; _resume_paused_card clears the card pointer.
            self._paused_review_mode = ""
        else:
            # ── First start: due-only, or all cards when opted in ──
            self.cards_due = self._load_review_queue(c)

            self._daily_review_history = []
            # First start: snapshot the complete queue for Restart.
            self._daily_queue_snapshot = list(self.cards_due)

        # Resume paused card directly so a restored grade cannot become an
        # ungraded queue card.
        self._resume_paused_card(c)

        if self.current_card is not None:
            self.is_current_flipped = False
            self.draw_card_ui()
            return

        if not self.cards_due:
            # Finished-but-paused session: restore active shell so Previous
            # can still walk graded history. Do not treat as "nothing due".
            if resume_daily and (
                self._daily_review_history or self._daily_queue_snapshot
            ):
                self.current_card = None
                self._update_button_visibility()
                return
            all_mode = bool(
                getattr(self, "all_cards_checkbox", None)
                and self.all_cards_checkbox.isChecked()
            )
            empty_msg = (
                "No cards in this database."
                if all_mode
                else "No cards due for review today!"
            )
            QMessageBox.information(self, "Done", empty_msg)
            self.review_mode = ""
            self._daily_queue_snapshot = []
            self._daily_review_history = []
            self._current_card_transition = None
            self._update_button_visibility()
            return

        self.show_next_card()

    def restart_current_review(self):
        """Restart the current daily review session (called from Restart button)."""
        if not self.conn:
            return
        self._restart_daily_review()

    def show_next_card(self):
        if self.card_ui:
            self.scene.removeItem(self.card_ui)
            self.card_ui = None

        while self.cards_due:
            stale_card = self.cards_due.pop(0)
            card_id = stale_card[0]

            c = self.conn.cursor()
            c.execute("SELECT id, front, back, box FROM cards WHERE id = ?", (card_id,))
            fresh_card = c.fetchone()

            if fresh_card:
                self.current_card = fresh_card
                self._current_card_transition = None
                self.is_current_flipped = False
                self.draw_card_ui()
                return

        # Queue exhausted — no more ungraded due cards.
        # Keep daily mode + graded history so Previous can still restore the
        # last graded card (clearing history here made Previous a permanent
        # no-op after the final grade). Restart / Close still work.
        # If there is nothing to go back to either, fully end the session.
        if self._daily_review_history:
            QMessageBox.information(self, "Done", "You have finished your reviews.")
            self.current_card = None
            self._current_card_transition = None
            self._update_button_visibility()
            return

        QMessageBox.information(self, "Done", "You have finished your reviews.")
        self.current_card = None
        self._current_card_transition = None
        self.review_mode = ""
        self._daily_review_history = []
        self._daily_queue_snapshot = []
        self._update_button_visibility()

    def draw_card_ui(self):
        if not self.current_card:
            return

        card_id, front, back, box = self.current_card

        zone_y = getattr(self, "_zone_y", self.scene.height() - 100)

        w = max(400, self.scene.width())
        h = max(400, self.scene.height())

        cw = int(w * 0.90)
        ch = int(h * 0.75)

        available = zone_y - 20
        if ch > available:
            ch = max(200, available)

        cx = w / 2
        cy = available / 2

        self.card_ui = FlashCardItem(self, cx, cy, cw, ch)

        metadata_md = f"**Box {box}** | ID: `{card_id}`"

        if self.is_current_flipped:
            spoken_text = _card_speech_text(front, back)

            if getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_SENTENCE:
                display_md = self._build_sentence_card_display(
                    card_id, front, back, flipped=True, metadata=metadata_md
                )
                spoken_text = _sentence_card_speech_text(self.conn, card_id, front)
            elif getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_WORD_PHRASE:
                display_md = self._build_word_phrase_card_display(
                    front, back, flipped=True, metadata=metadata_md
                )
                spoken_text = _word_phrase_card_speech_text(front, back)
            else:
                display_md = f"{metadata_md}\n\n{front}\n\n---\n\n{back}"

            self.card_ui.set_text(display_md, True, spoken_text)
        else:
            spoken_front = markdown_to_plain_text(front)

            if getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_SENTENCE:
                display_md = self._build_sentence_card_display(
                    card_id, front, back, flipped=False, metadata=metadata_md
                )
            elif getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_WORD_PHRASE:
                display_md = self._build_word_phrase_card_display(
                    front, back, flipped=False, metadata=metadata_md
                )
            else:
                display_md = f"{metadata_md}\n\n{front}"

            self.card_ui.set_text(display_md, False, spoken_front)

        self.scene.addItem(self.card_ui)
        self._update_button_visibility()

    def _build_sentence_card_display(self, card_id, sentence, back, flipped, metadata):
        """Build display content for a sentence-based card.

        Front: sentence with matched surface forms bolded in place
        (e.g. lemma ``insist on`` → bold ``insists on``). No separate
        Unfamiliar list — the mark is the list.

        Back: same highlighted sentence, then each expression with its
        contextual meaning. Items are ordered by first appearance in the
        sentence. Multiple items are numbered and separated as distinct
        blocks so Markdown keeps them on separate lines.
        """
        ordered = _ordered_sentence_card_items(self.conn, card_id, sentence)
        highlighted = _highlight_sentence_for_items(sentence, ordered)

        if flipped:
            lines = [metadata, "", highlighted, "", "---", ""]
            meaning_lines = _format_sentence_meaning_lines(ordered)
            # Blank line between entries so Markdown does not collapse them.
            lines.append("\n\n".join(meaning_lines))
            # cards.back is a derived cache of the same expression+meaning
            # pairs — do not append it again under a second separator.
            return "\n".join(lines)

        # Front: focus on the sentence; bold only the learning targets.
        return f"{metadata}\n\n{highlighted}"

    def _build_word_phrase_card_display(self, front, back, flipped, metadata):
        """Build display content for a word/phrase dictionary card.

        Front: bold expression only (``**insist on**``).
        Back: bold expression, then sense list with indented examples
        (examples already embed bold surface forms from derive).
        """
        expr = (front or "").strip()
        bold_front = f"**{expr}**" if expr else ""
        if not flipped:
            return f"{metadata}\n\n{bold_front}"
        body = (back or "").strip()
        if body:
            return f"{metadata}\n\n{bold_front}\n\n---\n\n{body}"
        return f"{metadata}\n\n{bold_front}"

    def flip_card(self):
        if not self.current_card:
            return

        self.is_current_flipped = True
        card_id, front, back, box = self.current_card

        metadata_md = f"**Box {box}** | ID: `{card_id}`"

        spoken_text = _card_speech_text(front, back)
        if getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_SENTENCE:
            display_md = self._build_sentence_card_display(
                card_id, front, back, flipped=True, metadata=metadata_md
            )
            spoken_text = _sentence_card_speech_text(self.conn, card_id, front)
        elif getattr(self, "_db_type", None) == DatabaseType.LANGUAGE_WORD_PHRASE:
            display_md = self._build_word_phrase_card_display(
                front, back, flipped=True, metadata=metadata_md
            )
            spoken_text = _word_phrase_card_speech_text(front, back)
        else:
            display_md = f"{metadata_md}\n\n{front}\n\n---\n\n{back}"

        self.card_ui.set_text(display_md, True, spoken_text)

    def check_card_drop(self, card_item):
        if not self.incorrect_zone or not self.correct_zone:
            return
        card_rect = card_item.sceneBoundingRect()
        inc_rect = self.incorrect_zone.sceneBoundingRect()
        cor_rect = self.correct_zone.sceneBoundingRect()

        if card_rect.intersects(inc_rect):
            QTimer.singleShot(0, lambda: self.process_answer(correct=False))
        elif card_rect.intersects(cor_rect):
            QTimer.singleShot(0, lambda: self.process_answer(correct=True))
        else:
            card_item.setPos(self.scene.width() / 2, (self.scene.height() - 100) / 2)

    def process_answer(self, correct):
        if not self.current_card:
            return
        # Drop zones and other paths must not grade an unrevealed card.
        if not self.is_current_flipped:
            return

        card_id, front, back, _stale_box = self.current_card
        today = datetime.date.today()

        c = self.conn.cursor()
        # Always grade from the latest DB box so Previous/re-grade is correct.
        c.execute("SELECT box FROM cards WHERE id = ?", (card_id,))
        row = c.fetchone()
        if row is None:
            self.show_next_card()
            return
        current_box = int(row[0])

        new_box = min(current_box + 1, 5) if correct else (3 if current_box >= 3 else 1)
        intervals = {1: 1, 2: 3, 3: 7, 4: 30, 5: 365}
        next_review_str = (
            today + datetime.timedelta(days=intervals[new_box])
        ).isoformat()

        try:
            c.execute(
                "UPDATE cards SET box = ?, next_review = ? WHERE id = ?",
                (new_box, next_review_str, card_id),
            )
            self.conn.commit()
        except sqlite3.Error:
            rollback_after_failure(self.conn, "review card grading")
            QMessageBox.warning(
                self, "Could not grade card", "The card could not be graded."
            )
            return

        # Session path: grade also leaves the current card behind (like Next).
        if self.review_mode == "daily":
            self._daily_review_history.append(
                ReviewHistoryEntry((card_id, front, back, new_box), "graded")
            )
            # Reflect Previous availability immediately (before next card draw).
            self._update_button_visibility()

        self.show_next_card()
