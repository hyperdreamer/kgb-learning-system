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

### Optimize sentence duplicate detection

`find_duplicate_sentence_card()` retains the current normalized, order-sensitive behavior but scans cards and child rows in Python. Optimize only when measured data shows a need; preserve duplicate-edit UX with regression coverage.

### Rename `_staged_settings()`

The method mutates the AI stage before returning settings. Its name is imperfect but the behavior is tested and localized; rename only as part of focused API cleanup.

## Deferred Lifecycle and Data Policy

### Canonical projection target symlink escape

The canonical word/phrase projection path can itself be a symlink resolving outside the configured database root. During sync, the current ownership comparison accepts this and can write/prune that external SQLite database. Rejecting this layout is the safest correction, but it would break existing users who intentionally store their canonical projections externally. Add a migration/discovery path and regression tests for both supported in-root links and rejected escape links before changing the policy.

### Nested sentence projection filename collisions

Canonical W/P projection filenames are currently flattened to the sentence database basename. Two nested sentence databases with the same filename therefore overwrite and prune one shared W/P projection, potentially mixing SRS history. Mirroring the validated relative directory structure is the correct long-term design, but existing installations may already have a shared flat projection whose history cannot be safely attributed. Add collision detection plus an explicit migration/discovery policy and tests before changing target paths.

### Markdown local-resource and navigation policy

Review cards render user-controlled Markdown in `QWebEngineView` with local-file and remote-network access enabled. Tightening this would prevent `file://` content access and arbitrary navigation, but can break existing card images/links and remote MathJax. Define the supported content policy, bundle or explicitly allow MathJax, then add URL sanitization and navigation tests before changing production settings.

### Main-window shutdown while TTS is still running

`closeEvent()` waits briefly, then may release its TTS worker while it is still active. A correct fix needs a pending-close state machine that retains the worker until actual thread completion, unlinks late output, and prevents re-entrant close behavior. This must be exercised with a controllable blocking worker.

### Sentence acceptance while membership AI is finishing

A successful membership-AI result accepts the dialog before the worker emits its real `finished` signal. Correcting this requires storing a pending accepted result and finalizing only after worker teardown, with precise signal-order tests. Changing modal completion order without that coverage risks a stuck dialog or lost accepted result.

### Reconcile existing Unicode-normalized W/P duplicates

Projection updates one normalized match and intentionally retains pre-existing duplicates. Automatic merging/removal would choose which independent SRS history survives. This needs an explicit archive/merge/review policy and NFC/NFD conflict tests before any migration.

## Recent Completed Reconsideration Fixes

- `36da7cc` — split the oversized UI modules into focused review, menu, browse, and dialog modules; retained `forms.py` as a compatibility facade; cached lazy AI worker classes; removed SQLite connection retention; and centralized sentence-item normalization/deduplication.
- `0f6a370` — atomic sentence-card rollback across nested sense helpers; canonical W/P link enforcement that preserves malformed legacy targets.
- `d490c0c` — AI-created senses are materialized only on Save; cancelling the dialog leaves no orphan sense.
