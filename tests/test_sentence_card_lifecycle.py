"""Headless lifecycle tests for sentence-card residual membership checks."""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox


class _Signal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class _ControllableWorker:
    def __init__(self):
        self.result = _Signal()
        self.error = _Signal()
        self.finished = _Signal()
        self.started = False
        self.deleted = 0

    def start(self):
        self.started = True

    def deleteLater(self):
        self.deleted += 1


class _Event:
    ignored = False

    def ignore(self):
        self.ignored = True


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _start_membership_worker(monkeypatch, dialog):
    import kgb_srs.sentence_card_dialog as dialog_module

    worker = _ControllableWorker()
    monkeypatch.setattr(dialog_module, "_create_ai_worker", lambda *_args: worker)
    dialog._start_membership_ai_check("I went home.", ["go"], ["go"], SimpleNamespace())
    return worker


def test_reopened_verified_surface_accepts_without_ai(qapp):
    from kgb_srs.sentence_card_dialog import SentenceCardDialog

    dialog = SentenceCardDialog(
        sentence="Yesterday he lay down.",
        items=[("lie", "recline", 7, "lay")],
    )

    dialog._accept()

    assert dialog.result_items == [("lie", "recline", 7, "lay")]
    assert dialog.result_verified_surfaces == {"lie": "lay"}
    dialog.close()


def test_changed_sentence_does_not_retain_verified_surface_without_ai(
    monkeypatch, qapp
):
    from kgb_srs.sentence_card_dialog import SentenceCardDialog

    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append(args[2]),
    )
    dialog = SentenceCardDialog(
        sentence="Yesterday he rested.",
        items=[("lie", "recline", 7, "lay")],
    )

    dialog._accept()

    assert dialog.result_items == []
    assert warnings
    assert "lie" in warnings[0]
    dialog.close()


def test_removing_expression_clears_persisted_verified_surface(qapp):
    from kgb_srs.sentence_card_dialog import SentenceCardDialog

    dialog = SentenceCardDialog(
        sentence="Yesterday he lay down.",
        items=[("lie", "recline", 7, "lay")],
    )

    dialog._remove_selected()

    assert "lie" not in dialog._persisted_verified_surfaces
    dialog.close()


def test_membership_ai_merges_new_surfaces_with_retained_surfaces(monkeypatch, qapp):
    import kgb_srs.sentence_card_dialog as dialog_module
    from kgb_srs.sentence_card_dialog import SentenceCardDialog

    worker = _ControllableWorker()
    monkeypatch.setattr(dialog_module, "_create_ai_worker", lambda *_args: worker)
    monkeypatch.setattr(dialog_module, "parse_membership_claims", lambda *_args: [])
    monkeypatch.setattr(
        dialog_module,
        "apply_ai_membership_claims",
        lambda *_args: SimpleNamespace(
            valid=True,
            accepted_surfaces={"missing": "found"},
        ),
    )
    dialog = SentenceCardDialog(
        sentence="Yesterday he lay down; found it later.",
        items=[("lie", "recline", 7, "lay"), ("missing", "lost", 8)],
    )

    dialog._start_membership_ai_check(
        "Yesterday he lay down; found it later.",
        ["lie", "missing"],
        ["missing"],
        SimpleNamespace(),
        {"lie": "lay"},
    )
    worker.result.emit("valid result")
    worker.finished.emit()

    assert dialog.result_verified_surfaces == {"lie": "lay", "missing": "found"}
    assert dialog.result_items == [
        ("lie", "recline", 7, "lay"),
        ("missing", "lost", 8, "found"),
    ]
    dialog.close()


def test_valid_membership_result_waits_for_matching_finished(monkeypatch, qapp):
    import kgb_srs.sentence_card_dialog as dialog_module
    from kgb_srs.sentence_card_dialog import SentenceCardDialog

    dialog = SentenceCardDialog(sentence="I went home.", items=["go"])
    worker = _start_membership_worker(monkeypatch, dialog)
    accepted = []
    monkeypatch.setattr(dialog_module, "parse_membership_claims", lambda *_args: [])
    monkeypatch.setattr(
        dialog_module,
        "apply_ai_membership_claims",
        lambda *_args: SimpleNamespace(valid=True, accepted_surfaces={"go": "went"}),
    )
    dialog._finish_accept = lambda *args, **kwargs: accepted.append((args, kwargs))

    worker.result.emit("valid result")

    assert accepted == []
    assert dialog._membership_worker is worker
    assert dialog._membership_pending_accept is not None
    assert not dialog._save_btn.isEnabled()
    assert not dialog._cancel_btn.isEnabled()
    close_event = _Event()
    dialog.closeEvent(close_event)
    assert close_event.ignored

    worker.finished.emit()
    assert dialog._membership_worker is None
    assert len(accepted) == 1
    assert worker.deleted == 1

    worker.finished.emit()  # duplicate/stale completion is a no-op
    assert len(accepted) == 1
    assert worker.deleted == 1
    dialog.close()


@pytest.mark.parametrize("outcome", ["invalid", "error"])
def test_failed_membership_paths_restore_controls_only_after_finished(
    monkeypatch, qapp, outcome
):
    import kgb_srs.sentence_card_dialog as dialog_module
    from kgb_srs.sentence_card_dialog import SentenceCardDialog

    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: None)
    dialog = SentenceCardDialog(sentence="I went home.", items=["go"])
    worker = _start_membership_worker(monkeypatch, dialog)

    if outcome == "invalid":
        monkeypatch.setattr(dialog_module, "parse_membership_claims", lambda *_args: [])
        monkeypatch.setattr(
            dialog_module,
            "apply_ai_membership_claims",
            lambda *_args: SimpleNamespace(valid=False, missing=["go"]),
        )
        worker.result.emit("invalid result")
    else:
        worker.error.emit("provider failed")

    assert dialog._membership_worker is worker
    assert not dialog._save_btn.isEnabled()
    assert not dialog._generate_btn.isEnabled()
    assert not dialog._cancel_btn.isEnabled()

    worker.finished.emit()
    assert dialog._membership_worker is None
    assert dialog._cancel_btn.isEnabled()
    assert dialog._save_btn.isEnabled()
    assert worker.deleted == 1
    dialog.close()
