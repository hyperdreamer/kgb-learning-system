# IGNORED.md — Deferred Decisions and Low-Priority Work

This file contains only items that remain intentionally deferred after the audit/reconsideration work through the current `dev` state. Completed fixes are recorded in Git history, not duplicated here.

## Intentional Behavior

### Projection failures do not block sentence work

`main_window.py` deliberately catches projection link/sync errors while loading or saving sentence cards. The sentence database remains usable if its derived W/P projection fails. Logging can be added later without changing this non-blocking contract.

### AI parser resilience for create actions

`parse_sense_assignment()` tolerates a malformed/non-integer `sense_id` for AI `action=create` and treats it as no preferred sense. Reuse actions still validate referenced IDs. This is intentional compatibility with imperfect model output.

### Toolbar guards tolerate initialization order

Toolbar font styling uses guarded widget access because controls are constructed incrementally. A missing control only skips its style update; there is no demonstrated runtime fault.

## Recent Completed Reconsideration Fixes

- Pending commit — hardened review-card rendering and worker lifecycles; optimized duplicate lookup and clarified settings staging; made W/P projection paths safe, collision-free, and conflict-aware without automatic SRS-history merges.
- `36da7cc` — split the oversized UI modules into focused review, menu, browse, and dialog modules; retained `forms.py` as a compatibility facade; cached lazy AI worker classes; removed SQLite connection retention; and centralized sentence-item normalization/deduplication.
- `0f6a370` — atomic sentence-card rollback across nested sense helpers; canonical W/P link enforcement that preserves malformed legacy targets.
- `d490c0c` — AI-created senses are materialized only on Save; cancelling the dialog leaves no orphan sense.
