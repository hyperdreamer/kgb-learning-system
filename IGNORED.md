# IGNORED.md — Deferred Decisions and Low-Priority Work

This file contains only items that remain intentionally deferred after the audit/reconsideration work through the current `dev` state. Completed fixes are recorded in Git history, not duplicated here.

## Intentional Behavior

### Projection failures do not block sentence work

`main_window.py` deliberately catches projection link/sync errors while loading or saving sentence cards. The sentence database remains usable if its derived W/P projection fails. Logging can be added later without changing this non-blocking contract.

### AI parser resilience for create actions

`parse_sense_assignment()` tolerates a malformed/non-integer `sense_id` for AI `action=create` and treats it as no preferred sense. Reuse actions still validate referenced IDs. This is intentional compatibility with imperfect model output.

### Toolbar guards tolerate initialization order

Toolbar font styling uses guarded widget access because controls are constructed incrementally. A missing control only skips its style update; there is no demonstrated runtime fault.

## Deferred Cleanup and Optimization

### Remove legacy `WordPhraseCardDialog`

The application is projection-only for W/P databases, but the legacy dialog and direct tests remain. Removal is maintenance work with broad test/API churn and no user-facing defect.

### Optimize sentence duplicate detection

`find_duplicate_sentence_card()` retains the current normalized, order-sensitive behavior but scans cards and child rows in Python. Optimize only when measured data shows a need; preserve duplicate-edit UX with regression coverage.

### Rework lazy AI worker classes

AI worker factories recreate QThread subclasses per request. This has no correctness impact. Caching the existing lazy factories is possible but is cleanup, not a functional fix.

### Rename `_staged_settings()`

The method mutates the AI stage before returning settings. Its name is imperfect but the behavior is tested and localized; rename only as part of focused API cleanup.

### Connection registration retention

`search.py` retains connection objects after function registration. The natural weak-reference approach is incompatible with `sqlite3.Connection`, and `id(conn)` reuse is unsafe. The effect is negligible for the application’s long-lived UI connections; defer a fix until a connection lifecycle API exists.

## Deferred Lifecycle and Data Policy

### Canonical projection target symlink escape

The canonical word/phrase projection path can itself be a symlink resolving outside the configured database root. During sync, the current ownership comparison accepts this and can write/prune that external SQLite database. Rejecting this layout is the safest correction, but it would break existing users who intentionally store their canonical projections externally. Add a migration/discovery path and regression tests for both supported in-root links and rejected escape links before changing the policy.

### Markdown local-resource and navigation policy

Review cards render user-controlled Markdown in `QWebEngineView` with local-file and remote-network access enabled. Tightening this would prevent `file://` content access and arbitrary navigation, but can break existing card images/links and remote MathJax. Define the supported content policy, bundle or explicitly allow MathJax, then add URL sanitization and navigation tests before changing production settings.

### Main-window shutdown while TTS is still running

`closeEvent()` waits briefly, then may release its TTS worker while it is still active. A correct fix needs a pending-close state machine that retains the worker until actual thread completion, unlinks late output, and prevents re-entrant close behavior. This must be exercised with a controllable blocking worker.

### Sentence acceptance while membership AI is finishing

A successful membership-AI result accepts the dialog before the worker emits its real `finished` signal. Correcting this requires storing a pending accepted result and finalizing only after worker teardown, with precise signal-order tests. Changing modal completion order without that coverage risks a stuck dialog or lost accepted result.

### Reconcile existing Unicode-normalized W/P duplicates

Projection updates one normalized match and intentionally retains pre-existing duplicates. Automatic merging/removal would choose which independent SRS history survives. This needs an explicit archive/merge/review policy and NFC/NFD conflict tests before any migration.

## Recent Completed Reconsideration Fixes

- `0f6a370` — atomic sentence-card rollback across nested sense helpers; canonical W/P link enforcement that preserves malformed legacy targets.
- `d490c0c` — AI-created senses are materialized only on Save; cancelling the dialog leaves no orphan sense.
