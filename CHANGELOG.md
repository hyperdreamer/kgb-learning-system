# Changelog

All notable changes to the KGB 5-Box SRS System are documented here.

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
- **Categorized Settings** (General, Appearance, Audio & Speech, AI Provider)
  with separate UI vs content fonts, Azure-style TTS voice picker, and AI Test.
- **Review UX** — pausable daily review, Restart/Previous, canvas close control,
  Browse **Review Selected**, full **Next Review** column.
- **Alt+ keyboard shortcuts** for all frequent actions (never steals typing).
- **Validation** — literal + inflection-tolerant matching, irregular verbs,
  optional AI residual membership check.
- **421 tests** covering catalog, schema, senses, validation, search, AI, UI.

### Breaking / migration notes

- Word/Phrase DBs are now projections of sentence senses, not free-typed decks.
  Existing linked W/P DBs re-sync on open/save; manual W/P mutation paths are gone.
- Settings store the default database as a path **relative to** Database Directory.
- `barsky_settings.json` remains machine-local (git-ignored); use the example template.

## [1.0.0] — 2026-07-19

Initial packaged release: 5-box SRS core, Markdown/MathJax review, Edge TTS,
settings, and basic SQLite card storage.
