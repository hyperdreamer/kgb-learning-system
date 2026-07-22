# IGNORED.md — Deferred Decisions

This file contains only intentional deferrals. Completed work belongs in Git history.

## Intentional behavior

### Projection failures do not block sentence work

`main_window.py` deliberately catches word/phrase projection link or sync failures while loading or saving sentence cards. A sentence database remains usable when its derived W/P projection fails. Adding structured logging is deferred; it must not change this non-blocking contract.

### AI parser resilience for create actions

`parse_sense_assignment()` accepts a malformed or non-integer `sense_id` for AI `action=create` as no preferred sense. Reuse actions still validate referenced IDs. This remains intentional compatibility with imperfect model output.

### Toolbar guards tolerate construction order

Toolbar font styling uses guarded widget access because controls are created incrementally. A missing control skips only its own styling update; no runtime fault has been demonstrated.

## Audit Deferrals

### Best-effort recovery preserves the original operation failure

The `S110`/`B110` notices in `browse_dialog.py`, `main_window.py`, and
`review_controller.py` cover failed SQLite rollbacks after an operation has
already failed. These rollback attempts must not hide the original failure
presented to the user. The corresponding `graphics.py` notice covers optional
WebEngine background styling, and `senses.py` `S110`/`S112` notices cover
best-effort startup discovery across potentially inaccessible database files.
Structured diagnostic logging is deferred until the application has an agreed
logging policy; these paths must remain non-fatal.

### SRS review randomization is not cryptographic

The `S311`/`B311` notices in `main_window.py` use `random` only to
occasionally return a Box 5 card to review. It is a user-experience selection,
not a security decision, so replacing it with a cryptographic generator has no
security benefit.

### Markdown token prefix is not a credential

The `S105`/`B105` notice for `MATH_TOKEN_PREFIX` in `markdown_utils.py` is a
rendering sentinel for temporary math placeholders. It is never used for
authentication, authorization, or secret storage.

### Development build detection uses fixed Git arguments

The `S607`/`B404`/`B603` notices in `version.py` arise from the fixed,
shell-free command `["git", "branch", "--show-current"]`, used only to display
a `.dev` version suffix. The command contains no interpolated input. Replacing
it with repository metadata parsing would need to retain behavior for
worktrees and other Git layouts, so that larger compatibility change is
deferred.
