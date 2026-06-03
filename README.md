# KGB 5-Box SRS System

A spaced-repetition flashcard application with **Markdown**, **MathJax** (LaTeX math), and **Text-to-Speech** (Edge TTS) — built on PyQt6 + SQLite.

---

## Quick Start

```bash
# Install dependencies
pip install PyQt6 edge-tts PyQt6-WebEngine   # WebEngine optional but recommended for math

# Run
python main.py
```

---

## Concepts

### The 5-Box System

Cards move through 5 boxes based on how well you remember them:

| Box | Interval | Meaning |
|-----|----------|---------|
| 1   | 1 day    | Learning |
| 2   | 3 days   | Short-term |
| 3   | 7 days   | Medium-term |
| 4   | 30 days  | Long-term |
| 5   | 365 days | Mastered |

When you answer **correctly**, the card advances one box (longer interval).  
When you answer **incorrectly** from Box ≥3, it drops to Box 3. From Box 1–2, it stays in Box 1.

Cards in Box 5 have a **5% random chance** of being pulled back into Box 1 for a surprise review — so nothing is ever truly forgotten.

---

## Usage Guide

### 1. Creating a Database

Click **＋ New Database**. Your system's native file dialog opens — navigate to `db/` or any subdirectory, type a name, and save.

Database files end with `_barsky.db`. The suffix is added automatically if you omit it.

Organize with subdirectories: `db/Languages/English_barsky.db`, `db/Math/Topology_barsky.db`, etc. The app discovers them recursively.

### 2. Selecting a Database

Click **📂 Select Database** (or the current database name). A hierarchical menu opens showing all databases, organized by directory structure.

The currently loaded database is marked with **●**.

### 3. Adding Cards

Click **Add Word**. You'll enter:

- **Front** — the word, phrase, question, or prompt
- **Back** — the answer, translation, definition, or explanation

Both fields support **Markdown** and **MathJax**:

```markdown
# Definition

A **compact** space is one where every open cover
has a finite subcover.

Formally: $X$ is compact iff for every open cover
$\{U_\alpha\}_{\alpha \in A}$, there exists a finite
subcover $\{U_{\alpha_1}, \ldots, U_{\alpha_n}\}$.
```

If you add a word that already exists, you'll be taken to edit mode.

### 4. Daily Review

Click **Start Daily Review**. You'll see one card at a time (front side only).

Click **💡 Reveal Answer** to flip the card. Then either:

- **Drag the card** onto the green or red drop zone
- **Click** the green or red zone directly

The card moves up or down in boxes and a new review date is set.

Toggle **Review Randomly** if you want shuffled order.

### 5. Forced Review (Browse All)

- **Next Item** — sequential review starting from the current card (forward)
- **Previous Item** — reverse sequential review
- **Restart Current Review** — restart from the first card

Use these to browse your entire deck outside the daily schedule.

### 6. Browse & Edit

Click **Browse and Edit**. You can:

- **Search** with `AND`/`OR` logic (e.g. `compact AND theorem OR topology`)
- **Double-click** a row to edit a card
- **Select a row and click Edit** to modify
- **Delete Selected** to remove cards

Editing a card resets it to Box 1 (review today).

### 7. Text-to-Speech 🔊

Click **🔊 Listen** on the flash card to hear the front side (and back side when flipped) spoken aloud.

The TTS voice can be changed in Settings. Any [Edge TTS](https://github.com/rany2/edge-tts#voice-list) voice name works (e.g. `en-US-AvaMultilingualNeural`, `zh-CN-XiaoxiaoNeural`).

### 8. Deleting Cards

On the review screen, click **🗑 Delete Current Item** to permanently remove the current card. You'll see a confirmation dialog showing the card details — deletion is irreversible.

In Browse mode, select a row and click **Delete Selected**.

### 9. Settings

Click **Settings** to configure:

- Window size
- Font family and size
- Default database (auto-loaded on startup)
- TTS voice name

---

## Markdown & Math Support

### Markdown

Full GitHub-flavored Markdown is supported in card content:

- `**bold**`, `*italic*`, `~~strikethrough~~`
- `# Headings`, `- lists`, `1. numbered lists`
- `` `inline code` ``, ` ```code blocks``` `
- `> blockquotes`, `--- horizontal rules`
- Tables, links, images

### Math (MathJax)

LaTeX math renders beautifully when `PyQt6-WebEngine` is installed:

- Inline: `$E = mc^2$`, `\(a^2 + b^2 = c^2\)`
- Display: `$$\int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2}$$`
- Display: `\[\sum_{n=1}^{\infty} \frac{1}{n^2} = \frac{\pi^2}{6}\]`

Without PyQt6-WebEngine, math expressions display as raw LaTeX source.

---

## File Structure

```
kgb_srs/                    # Python package
├── __init__.py
├── config.py               # Settings, constants
├── db.py                   # Database init & discovery
├── dialogs.py              # Input dialogs
├── graphics.py             # Flash card & drop zones
├── main_window.py          # Main application window
├── markdown_utils.py       # Markdown + MathJax rendering
└── tts.py                  # Text-to-speech worker

main.py                     # Entry point
kgb_srs.py                  # Launcher (backwards-compatible)
db/                         # Database directory (git-ignored)
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
