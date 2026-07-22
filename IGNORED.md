# IGNORED.md — Deferred Decisions

This file contains only intentional deferrals. Completed work belongs in Git history.

## Intentional behavior

### Projection failures do not block sentence work

`main_window.py` deliberately catches word/phrase projection link or sync failures while loading or saving sentence cards. A sentence database remains usable when its derived W/P projection fails. Adding structured logging is deferred; it must not change this non-blocking contract.

### AI parser resilience for create actions

`parse_sense_assignment()` accepts a malformed or non-integer `sense_id` for AI `action=create` as no preferred sense. Reuse actions still validate referenced IDs. This remains intentional compatibility with imperfect model output.

### Toolbar guards tolerate construction order

Toolbar font styling uses guarded widget access because controls are created incrementally. A missing control skips only its own styling update; no runtime fault has been demonstrated.
