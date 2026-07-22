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

### Database creation pathname race (TOCTOU)

`BarskyApp.create_database()` validates that a requested database path does not exist and subsequently opens it with SQLite. A concurrent local process could create or replace that pathname in the gap. The simple `O_CREAT | O_EXCL` reservation suggested by audit is insufficient because another process can still replace the pathname after the reservation descriptor is closed and before SQLite opens it. A correct cross-platform repair needs an ownership-safe creation/open design and explicit failure semantics; it is deferred to avoid shipping a partial guarantee that could regress database creation.

### `kgb_srs.forms` private legacy exports

`_AIGenerateWorker` and `_apply_ui_font` remain available through `kgb_srs.forms` for existing callers and monkeypatch-based tests. New code should import them from `kgb_srs.form_helpers`. Removing the facade exports requires a documented deprecation cycle and compatibility migration.

### Focused regression-test modules replace `test_regression.py`

The former monolithic test module was intentionally removed in favor of focused `tests/test_*_regressions.py` modules. External scripts that invoke `tests/test_regression.py` must migrate to `python -m pytest tests/` or a relevant focused module; no compatibility forwarding file is retained because it would reintroduce the monolith/collection ambiguity.
