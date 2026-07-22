# IGNORED.md — Deferred Decisions and Compatibility Risks

This file contains only intentional deferrals and active compatibility risks. Completed work belongs in Git history.

## Intentional behavior

### Projection failures do not block sentence work

`main_window.py` deliberately catches word/phrase projection link or sync failures while loading or saving sentence cards. A sentence database remains usable when its derived W/P projection fails. Adding structured logging is deferred; it must not change this non-blocking contract.

### AI parser resilience for create actions

`parse_sense_assignment()` accepts a malformed or non-integer `sense_id` for AI `action=create` as no preferred sense. Reuse actions still validate referenced IDs. This remains intentional compatibility with imperfect model output.

### Toolbar guards tolerate construction order

Toolbar font styling uses guarded widget access because controls are created incrementally. A missing control skips only its own styling update; no runtime fault has been demonstrated.

## Compatibility risks

### Markerless legacy projection adoption requires an explicit user workflow

A canonical Word/Phrase projection that lacks ownership metadata is now preserved unchanged and reported as a typed conflict; automatic linking never claims or prunes it. The backend supports explicit backup-first adoption for a confirmed canonical W/P target, but the desktop confirmation/backup-discovery UI has not yet been added. Noncanonical, moved, or flat legacy links remain conservative failures because their source ownership cannot be proven safely. A dedicated migration dialog must show the source/target paths, require confirmation, retain the created backup, and never block sentence-database use.

### `kgb_srs.forms` private legacy exports

`_AIGenerateWorker` and `_apply_ui_font` remain available through `kgb_srs.forms` for existing callers and monkeypatch-based tests. New code should import them from `kgb_srs.form_helpers`. Removing the facade exports requires a documented deprecation cycle and compatibility migration.

### Focused regression-test modules replace `test_regression.py`

The former monolithic test module was intentionally removed in favor of focused `tests/test_*_regressions.py` modules. External scripts that invoke `tests/test_regression.py` must migrate to `python -m pytest tests/` or a relevant focused module; no compatibility forwarding file is retained because it would reintroduce the monolith/collection ambiguity.
