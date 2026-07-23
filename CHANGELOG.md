# Changelog

All notable changes to the KGB 5-Box SRS System are documented here.

## [Unreleased]

### Added

- Generate a contextual AI meaning automatically whenever a new unfamiliar
  word or phrase is added to a sentence card. Running **Generate Meaning** again
  replaces the current generated or manually entered meaning on success.
- Made bold words and phrases in sentence-based review cards clickable and
  keyboard-accessible, speaking only the activated target through the existing
  TTS pipeline while leaving card metadata noninteractive.

## [2.2.1] — 2026-07-22

### Documentation and Licensing

- Adopted the GNU General Public License version 3 only (`GPL-3.0-only`);
  the complete license text is included in [LICENSE](LICENSE).
- Documented the optional PyQt WebEngine and headless multimedia test
  environment limitations for future release validation.

### Maintenance

- Formatted Python sources and tests with Ruff.

## [2.2.0] — 2026-07-22

### Changes

- Remember the **All cards** review selection separately for each database,
  matching the existing **Shuffle** behavior.
- Add natural TTS pauses between card fields, each sentence-card
  expression/meaning entry, and each word/phrase definition/example when the
  content lacks terminal punctuation.

### Security and Reliability

- Made best-effort SQLite rollback, projection discovery, and optional
  WebEngine styling failures diagnosable through standard logging without
  blocking the original user operation.
- Switched the Box 5 review lottery to `secrets`, renamed the Markdown math
  placeholder sentinel, and preserved collision-safe rendering behavior.
- Replaced development-branch subprocess detection with direct `.git/HEAD`
  metadata parsing that supports normal and linked worktrees.
- Rendered review-card content with Qt's proxy-safe text widget, preventing
  blank cards when the optional WebEngine package is installed.
- Preserved safe Markdown bold formatting for unfamiliar terms in the
  proxy-safe review renderer.

### Compatibility

- Deprecated private form-helper aliases from `kgb_srs.forms`; dialogs and
  first-party tests now use `kgb_srs.form_helpers`, while legacy imports warn
  and remain available through 2.x.
- Restored `tests/test_regression.py` as a deprecated direct-only pytest entry
  point for the focused regression modules. Normal suite discovery excludes it
  to prevent duplicate collection.

## [2.1.0] — 2026-07-22

### Changes

- AI provider refactor: stage profiles before save, richer test feedback,
  multi-model support with model listing from provider endpoints.
- Sentence dialog improvements: refined validation flow, duplicate detection
  fixes, atomic card + sense persistence with rollback on error.
- Settings dialog expanded with categorized tabs, UI/content font separation,
  default database path scoped to configured root.
- Word/Phrase projection hardening: canonical path enforcement, re-sync on
  open/save/startup, Unicode duplicate detection.
- Validation: inflection-tolerant matching expanded with full irregular-verb
  map, continuous-script bypass, and regex-literal passthrough.
- Expanded test coverage: settings dialog, review controls, main-window
  helpers, regression suite, AI provider integration tests.
- Documentation: IGNORED.md for deferred decisions, updated README and
  file structure reference.

## [2.0.0] — 2026-07-21

Major language-learning release: sentence databases become the source of truth,
with a shared sense catalog and automatic word/phrase projections.

### Highlights

- **Sentence-based databases** with unfamiliar expressions, contextual meanings,
  and in-place surface bolding during review (`insist on` → **insists on**).
- **Shared sense catalog** (`expression_senses`) keyed by expression + sense.
- **Automatic Word/Phrase projection** — same-named read-only dictionary under
  Word-Phrase-based; created/synced on create, open, startup, and sentence Save.
- **No manual W/P authoring** — Add/Edit/Delete/Generate disabled; Browse Review only.
- **Configurable database root** with canonical category folders and relative
  default-database paths.
- **Categorized Settings** (General, Appearance, Audio & Speech, AI Providers)
  with separate UI vs content fonts, Azure-style TTS voice picker, and AI Test.
- **Review UX** — pausable daily review, Restart/Previous, canvas close control,
  Browse **Review Selected**, full **Next Review** column.
- **Alt+ keyboard shortcuts** for all frequent actions (never steals typing).
- **Validation** — literal + inflection-tolerant matching, irregular verbs,
  optional AI residual membership check.
- **Comprehensive automated coverage** across catalog, schema, senses,
  validation, search, AI, and UI.

### Breaking / migration notes

- Word/Phrase DBs are now projections of sentence senses, not free-typed decks.
  Existing linked W/P DBs re-sync on open/save; manual W/P mutation paths are gone.
- Settings store the default database as a path **relative to** Database Directory.
- `barsky_settings.json` remains machine-local (git-ignored); use the example template.

## [1.0.0] — 2026-07-19

Initial packaged release: 5-box SRS core, Markdown/MathJax review, Edge TTS,
settings, and basic SQLite card storage.
