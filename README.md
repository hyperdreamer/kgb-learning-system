# KGB 5-Box SRS System

**Version 2.1.0**

A spaced-repetition flashcard application with **Markdown**, **MathJax** (LaTeX math), **AI-generated meanings**, and **Text-to-Speech** (Edge TTS) — built on PyQt6 + SQLite.

Language learning is **sentence-first**: sentence databases feed a shared sense catalog, which automatically projects a read-only word/phrase dictionary. See [CHANGELOG.md](CHANGELOG.md) for release history.

---

## Quick Start

```bash
# Install dependencies
pip install PyQt6 edge-tts PyQt6-WebEngine   # WebEngine optional but recommended for math

# Run
python main.py
```

Package version:

```bash
python -c "from kgb_srs import __version__; print(__version__)"
```

---

## Database Hierarchy

Databases live under a configurable **database root** (default: project `db/`).
Set it in **Settings → General → Database Directory**. When the root is assigned
or the app starts, the canonical category/subtype folders are created:

```
<database_root>/
├── Language-based/
│   ├── Sentence-based/       # Cards with sentence + unfamiliar words (source of truth)
│   └── Word-Phrase-based/    # Auto-projected dictionary (read-only)
└── Knowledge-based/          # Generic front/back cards (math, etc.)
```

Language-based databases use **two different subdirectories** to separate the
two types. New databases are created under the matching subtype folder.
Legacy folders such as `Languages/` and `Math/` remain readable.

The selection menu reflects this category/subtype hierarchy. The app infers and persists `database_type` metadata in each database. Knowledge-based databases do **not** show a duplicate subtype label in the menu.

---

## Database Types

### Sentence-based (`language_sentence`)

- **Front**: The sentence only, with matched unfamiliar surface forms **bolded in place** (e.g. stored lemma `insist on` highlights surface `insists on`). No separate Unfamiliar bullet list on the front.
- **Validation**: Every unfamiliar item must appear in the sentence (Unicode-safe, case-insensitive, whitespace-normalized). Literal match is tried first; if that fails, a local inflection-tolerant check accepts common tense/number forms and a comprehensive irregular-verb map (e.g. `insist on` ↔ `insists on`, `go` ↔ `went`/`gone`, `choose` ↔ `chose`/`chosen`). Multi-word phrases must still match consecutive tokens. Continuous scripts without spaces stay on the literal path. Regex metacharacters in items are treated as literal text. **AI is not used for the local path.** If Save still has residual misses and an AI provider is configured, the dialog may optionally ask AI only for those leftovers; any AI `found=true` claim must include a surface span that is re-verified to exist in the sentence.
- **AI generation**: In-dialog nonblocking AI generation for the **selected** unfamiliar item only. A **Generate Meaning** button starts a background QThread with the sentence + that one expression; controls are disabled during generation; on completion, that item's meaning field is filled. Meanings are always **contextual to the sentence**. **Save** is a separate user action. Back text is auto-derived from expression+meaning pairs (no separate back editor). Manual meaning edits remain available as a quiet escape hatch when AI is offline or a single meaning needs a fix. The Meaning panel shows only the currently selected list item (not every item at once).
- **Review**: The front shows the sentence with target surface spans in bold. The back shows the same highlighted sentence, a horizontal rule, then each expression with its contextual meaning once (no bullet list; the derived `cards.back` cache is not re-appended).
- **TTS**: Reads the sentence aloud.
- **Storage**: Cards table + normalized `unfamiliar_items` child records with `meaning TEXT NOT NULL DEFAULT ''`, optional `sense_id` FK to global `expression_senses`, FOREIGN KEY ON DELETE CASCADE, and UNIQUE(card_id, expression). Global sense identity is `(expression_norm, meaning_norm)`. The `cards.back` field is a rendered/cache representation. Meanings are **required** for new/edited sentence cards — bare expression strings without meanings are rejected at persistence. Migration preserves existing rows with empty meaning.
- **Sense inventory**: Generate Meaning asks AI to **reuse** a prior sense for the expression when it fits this sentence, or **create** a new sense. The meaning field is AI-primary (read-only); double-click unlocks rare manual repair.
- **Automatic Word/Phrase projection**: Creating a sentence DB (or opening an old one / starting the app) automatically creates and links a same-named read-only word/phrase DB under Word-Phrase-based. Unique `(expression, sense)` units are projected there; later sentence Saves re-sync it.
- **Duplicate detection**: A new card with the same normalized sentence and same normalized ordered list of expressions triggers an edit-offer rather than a silent duplicate.
- **Migration**: Existing databases without the `meaning` / `sense_id` columns are safely migrated on next open — `ALTER TABLE ADD COLUMN` and `expression_senses` creation are applied idempotently with no data loss.
- **Atomicity**: Card + child record insertion uses transactions with rollback on any error.
- **Search**: Searches sentence, unfamiliar expression, back, and child meaning fields. Supports OR groups containing AND terms (e.g., `math AND theorem OR topology`).

### Word/Phrase-based (`language_word_phrase`)

- **Front**: A word or phrase (one card per expression), shown **bold** during review.
- **Source of truth**: The **shared sense catalog** (`expression_senses`) built from sentence cards — not free-typed dictionary entries.
- **No manual editing**: Add Entry, Delete Entry, Edit, and AI Generate Meanings are **disabled**. Content is produced only by automatic projection from a sentence database.
- **Derived projection**: One card per expression; back lists each sense with its meaning and an indented example sentence where the surface form is **bold** (e.g. lemma `insist on` → **insists on**).
- **Linked auto-sync**: The sentence DB stores `linked_word_phrase_db`. App startup, DB open, and every sentence Save ensure the link exists and re-derive the W/P DB.
- **Review**: Standard front/back flip card with Markdown and MathJax rendering (SRS boxes still work).
- **Search**: Searches front and back (meanings/examples) fields. Browse is view-only for W/P (Edit/Delete selected disabled); use **Review Selected** (or double-click) to open a card for review.

### Knowledge-based (`knowledge`)

- **Generic front/back** cards with **no language AI prompts**.
- Preserves the original simple add/edit behavior for math decks, general knowledge, and other non-language databases.
- No AI generation is offered for knowledge cards.
- **Legacy**: Existing databases under `db/Math/` and other non-Languages legacy paths default to `knowledge` when metadata is absent.

---

## AI Providers

The app supports any **OpenAI-compatible** HTTP endpoint (GPT, DeepSeek, etc.).

### Configuration

In **Settings → AI Providers**, manage named profiles and configure the active one:

| Setting | Default | Description |
|---------|---------|-------------|
| Provider | `Default` | Named profile (Add / Rename / Delete) |
| Base URL | `https://api.openai.com/v1` | Provider endpoint |
| Model | `gpt-4o-mini` | Model id (Refresh lists `/models`) |
| API Key | *(blank)* | Your API key — **never committed** |
| Timeout | 30 s | Network timeout |
| Explanation Language | Chinese | Language for AI-generated explanations |
| **Test** | — | Checks that the staged model/API key are reachable and reports latency |

Settings store only `ai_active_provider` + `ai_providers`. Legacy flat `ai_base_url` / `ai_model` / `ai_api_key` / `ai_timeout` are migrated into a profile on load, then removed.

Use **Test** to validate the currently entered Base URL / Model / API Key / Timeout without saving. The check POSTs a minimal `chat/completions` request and shows success latency (ms) or a failure reason.

### Privacy

- The API key is stored in your local `barsky_settings.json` (git-ignored), which is written with owner-only (`0600`) permissions.
- Non-secret defaults are in `barsky_settings.example.json`; secrets are never logged or committed.
- Network calls run on a background QThread so the UI stays responsive.

### Sentence AI Workflow

1. Create/open the **Add Sentence Card** dialog.
2. Enter the sentence and unfamiliar items (type manually or use **Add selected text** to add highlighted text from the sentence).
3. Select one item in the list. The **Meaning** panel shows only that item.
4. Click **Generate Meaning** — AI either **reuses** a prior sense for that expression (if it fits this sentence) or **creates** a new contextual sense. The meaning field is AI-primary (read-only display); double-click unlocks rare manual repair.
5. Repeat for other items if needed. Back text is derived automatically from all expression+meaning pairs on Save.
6. **Save** runs membership + meaning checks. On failure the dialog stays open so you can fix or Cancel. Save is dimmed while the sentence or item list is empty.
7. On success, Save commits the card with expression+meaning pairs linked to global senses.
8. The linked word/phrase dictionary is created automatically with the sentence DB (and backfilled for older DBs on app startup). Sentence Saves keep it in sync — no manual Derive step.

---

## In-Dialog Features

### Add Selected Text
The sentence dialog includes an **Add selected text** button. Highlight any text in the sentence editor and click it to add the selection as an unfamiliar item.

### Save-time validation
There is no separate **Validate** button. **Save** is the only gate:

- Dimmed until the sentence is non-empty **and** at least one unfamiliar item exists.
- Blocks with a warning (dialog stays open) if any item is missing a meaning — type one or use **Generate Meaning**.
- Checks that every item appears in the sentence (local rules; optional AI residual for irregular forms).

### Duplicate Detection
When creating a new sentence card, if a card with the same normalized sentence and same normalized ordered list of expressions already exists, you're offered to edit the existing card instead.

---

## Migration & Backward Compatibility

- **Existing databases are not destructively rewritten.** All legacy cards remain usable.
- When opening a database without `database_type` metadata:
  - Paths under `db/Languages/` default to `language_word_phrase`.
  - Paths under `db/Math/` and other non-Languages paths default to `knowledge`.
  - Inferred metadata is persisted to the settings table for future use.
- The `unfamiliar_items` table is migrated on open to include the `meaning` column if absent. This is idempotent and preserves all data.
- Database names are validated against path traversal, separators, NUL, and control characters.
- The `default_database` setting is scoped to the configured Database Directory
  and is stored as a **relative path** under that root. Absolute values that
  still live under the root are resolved at runtime and rewritten to relative
  form on the next save; absolute paths outside the root are treated as unset.

---

## Usage Guide

### 1. Creating a Database

Click **＋ New**. A dialog appears:

1. Select Sentence-based or Knowledge-based.
2. Enter a name (validated for path safety).
3. The database is created in the canonical directory with metadata. Each
   sentence database automatically gets a linked, read-only Word/Phrase-based
   projection.

### 2. Selecting a Database

Click **📂 Select Database**. The hierarchical menu shows:

- **Language-based** → Sentence-based / Word-Phrase-based
- **Knowledge-based** → (legacy hierarchy directly underneath)

The currently loaded database is marked with **●**. Legacy databases appear under their inferred category.

### 3. Adding Cards

Click **Add Entry**. The dialog adapts to the database type:

- **Sentence-based**: Full dialog with sentence input, unfamiliar item management, selected-text addition, in-dialog AI generation, validation, and compact per-item meaning fields (back is auto-derived on Save).
- **Word/Phrase-based**: Projection-only dictionary auto-created from sentence databases (shared sense catalog). No manual Add Entry; edit senses via sentence cards.
- **Knowledge-based**: Simple front/back entry with no AI prompts — preserves original generic behavior.

### 4. Review Controls

All shortcuts use **Alt** so they never steal plain typing.

- **Start Daily Review** — begins a review of all cards due today. During an active review, this primary button becomes **Next** and skips the current card to the end of the same daily queue. Shortcut: **Alt+S**.
- **Reveal Answer** — flips the card (shortcut: **Alt+R**).
- **Incorrect / Correct** — grade after flip: **Alt+← / Alt+1** = Incorrect, **Alt+→ / Alt+2** = Correct (or drag the card onto the drop zones).
- **Listen** — speak the card (shortcut: **Alt+L**).
- **Previous** — returns to the last graded card in this session (shortcut: **Alt+P**).
- **Restart** — rebuilds the current daily queue from current eligibility and the current **All cards** / Shuffle settings, then clears session history. It is disabled while no review is active. Shortcut: **Alt+T**.
- **Close Review** — pauses the active review without grading or advancing. The current card, remaining queue, original queue, and session history are preserved. The inactive primary button becomes **Resume Daily Review**, which restores the paused card first. Closing has no effect on the database. Shortcut: **Alt+X**.
- **Delete Entry** — permanently deletes the currently displayed card from the database after confirmation. The review advances to the next queued card. If the deleted card was the paused card, paused state is cleared. Enabled when a non–word/phrase database is loaded and a card is displayed. Hidden on word/phrase databases (projection-only). Shortcut: **Alt+D**.

### 5. Browse & Edit

Click **Browse and Edit** (shortcut: **Alt+B**). Search with AND/OR logic:

- **Sentence-based**: Searches sentence text, unfamiliar expressions, back, and child meanings. OR groups may contain AND terms (e.g., `math AND theorem OR topology`). Plain multi-word queries search as a literal substring.
- **Word/Phrase-based**: Searches front and back.

Buttons:

- **Review Selected** — opens the selected card in a one-card review session (flip / grade / Close Review). Available for all database types; on word/phrase DBs this is the primary action. Shortcut: **Alt+R**.
- **Edit Selected** — edit the card (sentence dialog for sentence DBs; simple front/back for knowledge). Disabled on word/phrase DBs. Shortcut: **Alt+E**.
- **Delete Selected** — delete after confirmation. Disabled on word/phrase DBs. Shortcut: **Alt+D**.

Double-click a row to edit (sentence/knowledge) or review (word/phrase).

Other chrome shortcuts: **Alt+N** Add Entry, **Alt+,** Settings.

### 6. Text-to-Speech 🔊

TTS reads the sentence (front) and back content when flipped. Choose a voice under **Settings → Audio & Speech**: filter by language and gender, search by name, preview a short sample from a list row, then save the selected Edge TTS voice. The language filter is remembered with your settings.

### 7. Settings

Configure:

| Category | Settings |
|----------|----------|
| **General** | Database Directory (root folder), Default Database (file) |
| **Appearance** | Window size, UI font (app chrome + card edit dialogs), content font (study card HTML) |
| **Audio & Speech** | TTS voice (language/gender/search filters, row preview) |
| **AI Providers** | Named profiles, base URL, model, API key, timeout, explanation language, Test |

When you set **Database Directory**, the app creates:

```
<database_root>/Language-based/Sentence-based/
<database_root>/Language-based/Word-Phrase-based/
<database_root>/Knowledge-based/
```

Empty `database_root` keeps the portable project default (`db/`).

**Default Database** may only be chosen inside the Database Directory. The
setting is stored as a path relative to that root (for example
`Language-based/Word-Phrase-based/English_barsky.db`), and is resolved back to
an absolute path when the app opens the default database. The Default Database
file picker uses Qt's non-native dialog so navigation cannot leave the root
(sidebar is limited to that directory; leaving it snaps back).

---

## File Structure

```
kgb_srs/                    # Python package (__version__ = 2.1.0)
├── __init__.py
├── config.py               # Settings, constants, database root helpers
├── catalog.py              # Database type enum, metadata inference
├── schema.py               # DB init, migration, CRUD helpers
├── db.py                   # Backward-compat re-exports from schema
├── senses.py               # Shared sense catalog + W/P projection
├── validation.py           # Sentence matching (literal + inflection-tolerant)
├── search.py               # Subtype-aware AND/OR search
├── ai_provider.py          # OpenAI-compatible HTTP client
├── ai_parser.py            # AI JSON response parsing & validation
├── forms.py                # Backward-compatible dialog import facade
├── form_helpers.py         # Shared card-dialog styling and AI worker
├── sentence_card_dialog.py # Sentence card editor dialog
├── word_phrase_dialog.py   # Legacy W/P editor compatibility dialog
├── database_creation_dialog.py # Database creation dialog
├── dialogs.py              # Generic DynamicInputDialog
├── settings_dialog.py      # Categorized settings UI
├── graphics.py             # Flash card & drop zones
├── main_window.py          # Main application window
├── markdown_utils.py       # Markdown + MathJax rendering
└── tts.py                  # Text-to-speech worker

main.py                     # Entry point
kgb_srs.py                  # Launcher (backwards-compatible)
CHANGELOG.md                # Release history
tests/                      # pytest test suite
db/                         # Database directory (git-ignored)
```

---

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific module tests
python -m pytest tests/test_validation.py -v
python -m pytest tests/test_catalog.py -v
python -m pytest tests/test_schema.py -v
python -m pytest tests/test_ai_parser.py -v
python -m pytest tests/test_ai_provider.py -v
python -m pytest tests/test_search.py -v
python -m pytest tests/test_regression.py -v
```

---

## Requirements

- Python 3.10+
- PyQt6
- edge-tts
- PyQt6-WebEngine *(optional, for MathJax rendering)*

```
pip install PyQt6 edge-tts PyQt6-WebEngine
```
