"""Deterministic lifecycle tests for residual membership AI workers."""

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
