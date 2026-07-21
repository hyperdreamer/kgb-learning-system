# IGNORED.md — Deferred audit findings

Findings left unfixed after the audit→fix loop on `dev` (through commits
`876a37d` and `0eb2055`). These were **not** applied because they either
risk user-visible regressions, require careful UX/test rework, or are
optional cleanup rather than correctness bugs.

Re-open only with an explicit product decision and regression tests.

---

## 1. AI Generate Meaning materializes senses before Save

| Field | Value |
|-------|--------|
| **Severity** | Medium |
| **Files** | `kgb_srs/forms.py` (~739–741) — AI “create” path calls `create_or_get_sense(..., commit=True)` |
| **Behavior** | Generate Meaning with `action=create` inserts into `expression_senses` immediately. Cancel / reject leaves an unreferenced sense until a later insert/update runs `purge_orphan_senses`. |
| **Why deferred** | Changing commit timing can break sense_id wiring and the “Reused sense #N” / create UX. Needs coordinated dialog + Save tests. |
| **Risk if fixed** | Medium — preferred-sense identity and Save resolution must stay correct. |
| **Mitigation already in place** | W/P projection filters to senses with `unfamiliar_items` references (`group_senses_by_expression` / `sense_has_item_references`), so orphan catalog rows do **not** appear in the dictionary. Insert/update/delete paths purge orphans. |
| **Suggested fix (when ready)** | Create with `commit=False` and only persist on Save via existing `insert_sentence_card` / `update_sentence_card`, **or** call `purge_orphan_senses` on dialog reject/close when no save occurred. |

---

## 2. `WordPhraseCardDialog` dead code still present

| Field | Value |
|-------|--------|
| **Severity** | Low (maintainability) |
| **Files** | `kgb_srs/forms.py` (`WordPhraseCardDialog`); many tests in `tests/test_regression.py` |
| **Behavior** | Full manual W/P authoring dialog remains implemented and tested, but the main app is projection-only (no Add Entry / Edit / Delete for W/P; create dialog no longer offers W/P DBs). |
| **Why deferred** | Pure dead-code cleanup with large test churn; no runtime defect for users. |
| **Risk if fixed** | Low–medium test maintenance cost; risk of breaking residual helper paths if removed carelessly. |
| **Suggested fix (when ready)** | Gate or remove the dialog and migrate/delete only the tests that exclusively cover the dead UI path; keep any shared helpers still used by sentence flows. |

---

## 3. `find_duplicate_sentence_card` linear-scans all cards (O(n))

| Field | Value |
|-------|--------|
| **Severity** | Low (efficiency) |
| **Files** | `kgb_srs/schema.py:328-333` |
| **Behavior** | Fetches every card row then re-queries child items per candidate. O(n) with nested queries. |
| **Why deferred** | Performance optimization, not a correctness bug. Changing query logic risks breaking the duplicate-detection UX (edit-offer instead of silent duplicate). |
| **Risk if fixed** | Medium — duplicate detection is a user-facing gate. A missed duplicate silently creates a redundant card. |
| **Suggested fix (when ready)** | Push normalization into SQLite via generated column or registered collation, or filter candidates with SQL WHERE before Python-side loop. Needs duplicate-detection regression tests. |

---

## 4. QThread subclasses redefined on every worker creation

| Field | Value |
|-------|--------|
| **Severity** | Low (efficiency) |
| **Files** | `kgb_srs/ai_provider.py:640-739` |
| **Behavior** | `_get_ai_worker_class()` and siblings define new QThread subclasses on every invocation, re-executing the `class AIWorker(QThread):` block. |
| **Why deferred** | No correctness impact — signal binding is per-instance. Moving classes to module level risks breaking closure-variable capture or lazy-import ordering. |
| **Risk if fixed** | Medium — QThread with pyqtSignal requires QApplication to exist. Module-level class definitions at import time could fail if Qt isn't initialized yet. |
| **Suggested fix (when ready)** | Define classes once at module level, guarding with a lazy-init pattern or deferring the PyQt6 import to after QApplication creation. |

---

## 5. `parse_sense_assignment` silently swallows invalid `sense_id` (behavior change)

| Field | Value |
|-------|--------|
| **Severity** | Low (observation, not a bug) |
| **Files** | `kgb_srs/ai_parser.py:215-221` |
| **Behavior** | Previously raised `AIValidationError` for non-integer `sense_id` values (e.g., literal `"null"` string from AI). Now silently falls back to `sense_id = None`. |
| **Why deferred** | Intentional trade-off in round 3 fix. The old behavior was overly strict for rare AI output quirks. The silent fallback is safer for end users but could mask future AI parsing regressions. |
| **Risk if changed back** | Low — restoring the error would re-break on literal `"null"` strings from certain LLMs. |

---

## 11. Sentence-card writes can become partially durable after a child-row failure

| Field | Value |
|-------|-------|
| **Severity** | Medium (data integrity) |
| **Files** | `kgb_srs/schema.py:394-476, 540-619`; `kgb_srs/senses.py:40-74, 138-194, 256-278` |
| **Behavior** | `insert_sentence_card()` / `update_sentence_card()` attempt an outer rollback, but `create_or_get_sense()` and `purge_orphan_senses()` call helpers that commit internally. A trigger rejecting a later `unfamiliar_items` row can therefore leave the card, earlier child row, and orphan senses persisted after the operation reports failure. |
| **Why deferred** | Correcting it requires transaction-aware schema/migration helpers and an audit of callers that currently rely on their implicit commits. It can affect legacy migration, projection, and CRUD boundaries. |
| **Risk if fixed** | Medium — a broad commit-timing change could regress callers or leave migrations uncommitted unless covered end-to-end. |
| **Suggested fix (when ready)** | Run schema/migration setup before CRUD, then use one transaction/savepoint with no nested commits until final success. Add trigger-induced rollback tests for both insert and update. |

---

## 12. A non-canonical linked W/P path can overwrite an unrelated database

| Field | Value |
|-------|-------|
| **Severity** | High (data loss) |
| **Files** | `kgb_srs/senses.py:478-491, 593-610, 619-630` |
| **Behavior** | A stored `linked_word_phrase_db` accepts any existing absolute path. Later projection sync derives into that target, pruning cards absent from the source and changing its metadata to `language_word_phrase`. A malformed link to a Knowledge database can delete its unrelated cards and retag it. |
| **Why deferred** | Existing installations may contain non-canonical legacy links. Enforcing the canonical same-name projection path needs a deliberate migration/repair policy rather than silently changing user data links. |
| **Risk if fixed** | Medium — link validation/migration can change existing database relationships and must avoid overwriting or orphaning a legacy projection. |
| **Suggested fix (when ready)** | Canonicalize and require `<database_root>/Language-based/Word-Phrase-based/<same-name>` before deriving. On invalid link, leave the target untouched, repair the stored link to the canonical path, and add a regression test using an existing Knowledge DB as the malformed target. |

---

## Round 7 fixes (applied on `dev`)

- Settings dialog close/reject/accept now defers destruction until all active voice, TTS-preview, AI-test, and model-refresh workers have actually finished; cleanup occurs only on the real close.
- Default database containment now resolves symlinks before validating and persisting paths, blocking defaults that physically escape the configured root.
- Removed the nonexistent `python -m kgb_srs.main` launcher claim.

Verification: 474 tests passed; `ruff check .` passed.

---

## Not ignored (fixed on `dev`)

See commits:

- `876a37d` — audit round 1 (W/P SRS preserve, orphan purge on write, review grading, SAVEPOINT, AI sense_id, TTS signal/lifetime, membership close guard, no manual W/P create, resume queue)
- `0eb2055` — audit round 2 (delete → purge + W/P re-sync, history scrub, Previous skips ghosts, TTS temp unlink, CHANGELOG count)
- `f48b8f3` — audit round 3 (meaning/example split via MeaningResult fields, atomic W/P derivation commit, DB-open error handling + conn close, search function registration cache, DB/TTS file permissions 0o600, non-integer sense_id silent fallback, 60+ irregular noun plurals)

Round 4 re-audit: **CLEAN** — no actionable findings remain (473 tests).

---

## 6. `_REGISTERED_CONNS` global set retains connection references (minor memory leak)

| Field | Value |
|-------|--------|
| **Severity** | Low (efficiency) |
| **Files** | `kgb_srs/search.py:36` |
| **Behavior** | Module-level `set()` accumulates every `sqlite3.Connection` for which `kgb_contains` was registered. Closed connections are never removed, preventing GC. Accumulation across `load_database` calls is unbounded. |
| **Why deferred** | `sqlite3.Connection` objects are not hashable for `weakref.WeakSet` — the natural fix is incompatible. In practice connections are long-lived, making the leak negligible. |
| **Risk if fixed** | Low — using `weakref` or connection-id-based schemes would work but adds complexity for minimal practical benefit. |
| **Suggested fix (when ready)** | Use `id(conn)` as the cache key instead of `conn` itself, or accept the leak as de minimis.

---

## Round 4+ fixes (applied on `dev`)

See commits:

- `c9b89f7` — audit round 4 (15 findings: M1 duplicate test class, M2 dead _split_meaning_example, M4 92 ruff → 0, M5 double TTS signal wiring, L1-L12 unused imports/variables, _make_http_call inlined)
- `641d0e2` — re-audit regression (C1 _clear_all_rows method restored, L1 dead getattr removed, L2 unused logic param documented)

Round 5 final re-audit: **CLEAN** — 472 tests, ruff 0 violations, ACTIONABLE FINDINGS: None.

---

## Round 6 fixes (applied on `dev`)

See commit:

- `334c200` — audit round 6 (M1: unsafe e.reason→getattr in worker threads; M2: narrow bare except→(sqlite3.Error, OSError) in _open_and_infer_type; L4: rename _http_request→http_request public; L5: remove dead create_ai_worker)

---

## 7. `except Exception: pass` around `ensure_linked_word_phrase_database` (intentional)

| Field | Value |
|-------|--------|
| **Severity** | Low |
| **Files** | `kgb_srs/main_window.py:887-888` |
| **Behavior** | Auto-linking the W/P projection during DB load silently catches and ignores all exceptions. |
| **Why deferred** | Intentional defensive design — the outer handler (line 894) properly closes the connection on real errors. W/P linking failure should not abort the entire DB load. |
| **Suggested fix (when ready)** | Log the exception at warning level without changing control flow. |

---

## 8. `_sync_linked_word_phrase_quiet` swallows sync errors (intentional)

| Field | Value |
|-------|--------|
| **Severity** | Low |
| **Files** | `kgb_srs/main_window.py:1266-1268` |
| **Behavior** | Called after every sentence-card insert/update/delete. Silently passes all exceptions. |
| **Why deferred** | Intentional — comment says "Never block sentence save on projection failure." This is a deliberate UX choice. |
| **Suggested fix (when ready)** | Log the exception at warning level without changing control flow. |

---

## 9. `_staged_settings()` mutation side effect (naming concern)

| Field | Value |
|-------|--------|
| **Severity** | Low |
| **Files** | `kgb_srs/settings_dialog.py:1037` |
| **Behavior** | Method name reads like a pure getter but calls `_capture_ai_fields_to_stage()` which mutates `_ai_stage`. |
| **Why deferred** | Renaming or restructuring could break call sites (`save_and_apply`, tests). Low severity — the side effect is the method's actual purpose, just poorly named. |
| **Suggested fix (when ready)** | Rename to `_build_staged_settings()` or move the capture call to a single explicit call site. |

---

## 10. `hasattr` guard pattern silently degrades on widget rename

| Field | Value |
|-------|--------|
| **Severity** | Low |
| **Files** | `kgb_srs/main_window.py:199-206` |
| **Behavior** | `_apply_toolbar_font_styles` uses `hasattr(self, "db_btn")` etc. If a widget is renamed, styling is silently skipped with no warning. |
| **Why deferred** | Design concern, not a bug. Moving to explicit attribute registration would add fragility to initialization ordering. |
| **Suggested fix (when ready)** | Register toolbar widgets in a class-level list/tuple and iterate over known names.
