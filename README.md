# KGB 5-Box SRS System

A spaced-repetition flashcard application with **Markdown**, **MathJax** (LaTeX math), **AI-generated meanings**, and **Text-to-Speech** (Edge TTS) — built on PyQt6 + SQLite.

---

## Quick Start

```bash
# Install dependencies
pip install PyQt6 edge-tts PyQt6-WebEngine   # WebEngine optional but recommended for math

# Run
python main.py
```

---

## Database Hierarchy

Databases are organized into two top-level categories:

```
db/
├── Language-based/
│   ├── Sentence-based/       # Cards with sentence + unfamiliar words
│   └── Word-Phrase-based/    # Traditional word/phrase → meaning cards
└── Knowledge-based/          # Generic front/back cards (math, etc.)
```

The selection menu reflects this category/subtype hierarchy. The app infers and persists `database_type` metadata in each database. Knowledge-based databases do **not** show a duplicate subtype label in the menu.

---

## Database Types

### Sentence-based (`language_sentence`)

- **Front**: A sentence plus one or more unfamiliar words or phrases.
- **Validation**: Every unfamiliar item must appear literally in the sentence (Unicode-safe, case-insensitive, whitespace-normalized). Regex metacharacters are treated as literal text.
- **AI generation**: In-dialog nonblocking AI generation. A **Generate** button starts a background QThread; controls are disabled during generation; on completion, per-item meaning fields are populated and editable. **Save** is a separate user action.
- **Review**: The front shows the sentence with unfamiliar items listed. The back shows each expression with its AI-generated contextual meaning.
- **TTS**: Reads the sentence aloud.
- **Storage**: Cards table + normalized `unfamiliar_items` child records with `meaning TEXT NOT NULL DEFAULT ''`, FOREIGN KEY ON DELETE CASCADE, and UNIQUE(card_id, expression). The `cards.back` field is a rendered/cache representation. Meanings are **required** for new/edited sentence cards — bare expression strings without meanings are rejected at persistence. Migration preserves existing rows with empty meaning.
- **Duplicate detection**: A new card with the same normalized sentence and same normalized ordered list of expressions triggers an edit-offer rather than a silent duplicate.
- **Migration**: Existing databases without the `meaning` column are safely migrated on next open — an `ALTER TABLE ADD COLUMN` is applied idempotently with no data loss.
- **Atomicity**: Card + child record insertion uses transactions with rollback on any error.
- **Search**: Searches sentence, unfamiliar expression, back, and child meaning fields. Supports OR groups containing AND terms (e.g., `math AND theorem OR topology`).

### Word/Phrase-based (`language_word_phrase`)

- **Front**: A word or phrase.
- **Dialog**: The **WordPhraseCardDialog** provides inline editing of 1-2 meaning rows, each with a meaning text and example sentence. At least one non-empty meaning+example row is required on Save.
- **AI generation**: In-dialog nonblocking AI generation via a **Generate Meanings** button. Up to 2 common modern meanings are produced, each with a non-empty example sentence. Responses with missing/empty examples or more than 2 meanings are rejected with a visible error. Controls are disabled during generation; on completion, rows are populated and editable.
- **Manual editing**: Users may add/remove the second row, edit meaning and example fields, and validate before saving.
- **Review**: Standard front/back flip card with Markdown and MathJax rendering.
- **Search**: Searches front and back (meanings/examples) fields.

### Knowledge-based (`knowledge`)

- **Generic front/back** cards with **no language AI prompts**.
- Preserves the original simple add/edit behavior for math decks, general knowledge, and other non-language databases.
- No AI generation is offered for knowledge cards.
- **Legacy**: Existing databases under `db/Math/` and other non-Languages legacy paths default to `knowledge` when metadata is absent.

---

## AI Provider

The app supports any **OpenAI-compatible** HTTP endpoint (GPT, DeepSeek, etc.).

### Configuration

In **Settings → AI Provider**, configure:

| Setting | Default | Description |
|---------|---------|-------------|
| Base URL | `https://api.openai.com/v1` | Provider endpoint |
| Model | `gpt-4o-mini` | Model name |
| API Key | *(blank)* | Your API key — **never committed** |
| Timeout | 30 s | Network timeout |
| Learned Language | English | Language of the content being studied |
| Explanation Language | Chinese | Language for AI-generated explanations |
| **Test** | — | Checks that the staged model/API key are reachable and reports latency |

Use **Test** to validate the currently entered Base URL / Model / API Key / Timeout without saving. The check POSTs a minimal `chat/completions` request and shows success latency (ms) or a failure reason.

### Privacy

- The API key is stored in your local `barsky_settings.json` (git-ignored), which is written with owner-only (`0600`) permissions.
- Non-secret defaults are in `barsky_settings.example.json`; secrets are never logged or committed.
- Network calls run on a background QThread so the UI stays responsive.

### Sentence AI Workflow

1. Create/open the **Add Sentence Card** dialog.
2. Enter the sentence and unfamiliar items (type manually or use **Add selected text** to add highlighted text from the sentence).
3. Click **Generate Meanings** — the button disables while AI runs in the background (no busy-waiting).
4. On completion, per-item meaning fields are populated and editable.
5. **Validate** to verify all items appear in the sentence.
6. **Save** commits the card with expression+meaning pairs.

---

## In-Dialog Features

### Add Selected Text
The sentence dialog includes an **Add selected text** button. Highlight any text in the sentence editor and click it to add the selection as an unfamiliar item.

### Validation
Click **Validate** to confirm every unfamiliar item appears literally in the sentence. Items not found are reported with a clear error.

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
- The `default_database` setting continues working as before.

---

## Usage Guide

### 1. Creating a Database

Click **＋ New**. A dialog appears:

1. Select the category and subtype (Sentence-based, Word/Phrase-based, or Knowledge-based).
2. Enter a name (validated for path safety).
3. The database is created in the canonical directory with metadata.

### 2. Selecting a Database

Click **📂 Select Database**. The hierarchical menu shows:

- **Language-based** → Sentence-based / Word-Phrase-based
- **Knowledge-based** → (legacy hierarchy directly underneath)

The currently loaded database is marked with **●**. Legacy databases appear under their inferred category.

### 3. Adding Cards

Click **Add Entry**. The dialog adapts to the database type:

- **Sentence-based**: Full dialog with sentence input, unfamiliar item management, selected-text addition, in-dialog AI generation, validation, and per-item meaning editing.
- **Word/Phrase-based**: Front/back entry with optional AI generation for meanings.
- **Knowledge-based**: Simple front/back entry with no AI prompts — preserves original generic behavior.

### 4. Review Controls

- **Start Daily Review** — begins a review of all cards due today. During an active review, this primary button becomes **Next** and skips the current card to the end of the same daily queue.
- **Previous** — returns to the most recently graded card in the current daily session. It is disabled while no review is active.
- **Restart** — restarts the current daily session from its original due-card queue and clears session history. It is disabled while no review is active.
- **Close Review** — pauses the active review without grading or advancing. The current card, remaining queue, original queue, and session history are preserved. The inactive primary button becomes **Resume Daily Review**, which restores the paused card first. Closing has no effect on the database.
- **Delete Entry** — permanently deletes the currently displayed card from the database after confirmation. The review advances to the next queued card. If the deleted card was the paused card, paused state is cleared. **Delete Entry** is enabled whenever a database is loaded and a card is displayed (not only during reviews).

### 5. Browse & Edit

Click **Browse and Edit**. Search with AND/OR logic:

- **Sentence-based**: Searches sentence text, unfamiliar expressions, back, and child meanings. OR groups may contain AND terms (e.g., `math AND theorem OR topology`). Plain multi-word queries search as a literal substring.
- **Word/Phrase-based**: Searches front and back.

Double-click a row to edit. Sentence cards open the full sentence dialog with re-validation.

### 6. Text-to-Speech 🔊

TTS reads the sentence (front) and back content when flipped. Configure voice in Settings.

### 7. Settings

Configure window size, fonts, default database, TTS voice, AI provider, and language preferences.

---

## File Structure

```
kgb_srs/                    # Python package
├── __init__.py
├── config.py               # Settings, constants
├── catalog.py              # Database type enum, metadata inference
├── schema.py               # DB init, migration, CRUD helpers
├── db.py                   # Backward-compat re-exports from schema
├── validation.py           # Sentence literal matching (Unicode-safe)
├── search.py               # Subtype-aware AND/OR search
├── ai_provider.py          # OpenAI-compatible HTTP client
├── ai_parser.py            # AI JSON response parsing & validation
├── forms.py                # SentenceCardDialog, WordPhraseCardDialog, DBCreationDialog
├── dialogs.py              # Generic DynamicInputDialog
├── graphics.py             # Flash card & drop zones
├── main_window.py          # Main application window
├── markdown_utils.py       # Markdown + MathJax rendering
└── tts.py                  # Text-to-speech worker

main.py                     # Entry point
kgb_srs.py                  # Launcher (backwards-compatible)
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

- Python 3.9+
- PyQt6
- edge-tts
- PyQt6-WebEngine *(optional, for MathJax rendering)*

```
pip install PyQt6 edge-tts PyQt6-WebEngine
```
