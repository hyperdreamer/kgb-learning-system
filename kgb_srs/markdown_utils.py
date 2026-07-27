"""Markdown + MathJax rendering helpers.

Key invariants:
1. Math placeholders use only letters/numbers so Markdown cannot mangle them.
2. HTML output preserves LaTeX delimiters for MathJax.
3. Plain-text extraction strips formatting for TTS.
"""

import re
import html as html_lib
from urllib.parse import urlsplit

from PyQt6.QtGui import QTextDocument

from .ui_theme import LIGHT_TOKENS, review_document_css

# --- Constants ---
MATH_PLACEHOLDER_PREFIX = "BARSKYMATHPLACEHOLDER"


# Review cards render user-authored Markdown in either QTextEdit or a web view.
# QTextDocument produces the markup, but its output can still contain links and
# resources supplied through Markdown/HTML.  Keep the fragment deliberately
# small: review cards have no need for active content or embedded resources.
_REVIEW_BLOCK_TAGS_RE = re.compile(
    r"<\s*(script|iframe|object|embed|base|meta|link|form)\b[^>]*>.*?</\s*\1\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_REVIEW_VOID_TAGS_RE = re.compile(
    r"<\s*(?:base|meta|link|input)\b[^>]*>", flags=re.IGNORECASE
)
_REVIEW_EVENT_ATTRIBUTE_RE = re.compile(
    r"\s+on[a-z0-9_-]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)",
    flags=re.IGNORECASE,
)
_REVIEW_STYLE_ATTRIBUTE_RE = re.compile(
    r"\s+style\s*=\s*(?P<value>\"[^\"]*\"|'[^']*'|[^\s>]+)",
    flags=re.IGNORECASE,
)
# QTextDocument emits Markdown bold/italic as span styles. Retain only those
# presentation semantics in canonical form; every other CSS declaration remains
# untrusted and is discarded.
_REVIEW_SAFE_BOLD_STYLE_RE = re.compile(
    r"(?:^|;)\s*font-weight\s*:\s*(?:bold|[6-9]00)\s*(?:;|$)",
    flags=re.IGNORECASE,
)
_REVIEW_SAFE_ITALIC_STYLE_RE = re.compile(
    r"(?:^|;)\s*font-style\s*:\s*italic\s*(?:;|$)",
    flags=re.IGNORECASE,
)
# QTextDocument emits a 40px left margin for Markdown quotes. Preserve only
# that canonical quote format (with either relevant Qt block-indent value), so
# examples remain subordinate without retaining arbitrary user CSS.
_REVIEW_SAFE_QT_BLOCK_INDENT_RE = re.compile(
    r"(?:^|;)\s*-qt-block-indent\s*:\s*(?P<indent>[01])\s*(?:;|$)",
    flags=re.IGNORECASE,
)
_REVIEW_SAFE_QT_INDENT_MARGIN_RE = re.compile(
    r"(?:^|;)\s*margin-left\s*:\s*40px\s*(?:;|$)",
    flags=re.IGNORECASE,
)
# This is the sole non-emphasis color emitted by the application: word/phrase
# meanings. Exact matching prevents arbitrary user-authored colors from
# surviving review-card sanitization.
_REVIEW_SAFE_WORD_PHRASE_MEANING_COLOR_RE = re.compile(
    r"(?:^|;)\s*color\s*:\s*#d32f2f\s*(?:;|$)",
    flags=re.IGNORECASE,
)
_REVIEW_URL_ATTRIBUTE_RE = re.compile(
    r"\s+(?P<name>href|src|poster|background|action|formaction|xlink:href)\s*="
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s>]+)",
    flags=re.IGNORECASE,
)


def is_safe_review_link(url: str) -> bool:
    """Return whether *url* is an absolute HTTP(S) link for a review card.

    Review cards never load resources.  This helper only permits the link
    destinations which the view's navigation policy may pass to the desktop.
    """
    try:
        parsed = urlsplit(html_lib.unescape(str(url)).strip())
    except (TypeError, ValueError):
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def sanitize_review_html_fragment(fragment: str) -> str:
    """Remove active/resource-bearing markup from a review-card fragment.

    HTTP(S) ``href`` values remain as links; the web-view policy opens those
    externally.  Every resource attribute is dropped, so neither Markdown
    images nor raw HTML can turn a card into a local or remote fetch surface.
    """
    cleaned = _REVIEW_BLOCK_TAGS_RE.sub("", fragment or "")
    cleaned = _REVIEW_VOID_TAGS_RE.sub("", cleaned)
    cleaned = _REVIEW_EVENT_ATTRIBUTE_RE.sub("", cleaned)

    def replace_style_attribute(match):
        raw_value = match.group("value")
        style_value = raw_value[1:-1] if raw_value[:1] in {'"', "'"} else raw_value
        style_value = html_lib.unescape(style_value)
        safe_declarations = []
        if _REVIEW_SAFE_BOLD_STYLE_RE.search(style_value):
            safe_declarations.append("font-weight: 700")
        if _REVIEW_SAFE_ITALIC_STYLE_RE.search(style_value):
            safe_declarations.append("font-style: italic")
        if _REVIEW_SAFE_WORD_PHRASE_MEANING_COLOR_RE.search(style_value):
            safe_declarations.append(f"color: {LIGHT_TOKENS['review_meaning']}")
        quote_indent = _REVIEW_SAFE_QT_BLOCK_INDENT_RE.search(style_value)
        if quote_indent and _REVIEW_SAFE_QT_INDENT_MARGIN_RE.search(style_value):
            safe_declarations.extend(
                ("margin-left: 40px", f"-qt-block-indent: {quote_indent['indent']}")
            )
        if not safe_declarations:
            return ""
        return f' style="{"; ".join(safe_declarations)};"'

    cleaned = _REVIEW_STYLE_ATTRIBUTE_RE.sub(replace_style_attribute, cleaned)

    def replace_url_attribute(match):
        name = match.group("name").lower()
        raw_value = match.group("value")
        value = raw_value[1:-1] if raw_value[:1] in {'"', "'"} else raw_value
        if name == "href" and is_safe_review_link(value):
            return f' href="{html_lib.escape(html_lib.unescape(value), quote=True)}"'
        return ""

    return _REVIEW_URL_ATTRIBUTE_RE.sub(replace_url_attribute, cleaned)


# --- Math Protection ---
def _protect_math_segments(text):
    """Temporarily replace LaTeX math segments before Markdown parsing.

    Supports $inline$, $$display$$, \\(inline\\), \\[display\\].
    """
    text = text or ""
    placeholder_map = {}
    placeholder_prefix = MATH_PLACEHOLDER_PREFIX
    while placeholder_prefix in text:
        placeholder_prefix += "X"

    def make_placeholder():
        return f"{placeholder_prefix}{len(placeholder_map)}PLACEHOLDER"

    def replace_pattern(pattern, source):
        def repl(match):
            placeholder = make_placeholder()
            placeholder_map[placeholder] = match.group(0)
            return placeholder

        return re.sub(pattern, repl, source, flags=re.DOTALL)

    # Protect display math first, then inline math
    text = replace_pattern(r"(?<!\\)\$\$(.*?)(?<!\\)\$\$", text)
    text = replace_pattern(r"\\\[(.*?)\\\]", text)
    text = replace_pattern(r"\\\((.*?)\\\)", text)
    text = replace_pattern(r"(?<!\\)\$(?!\$)(?:\\.|[^\n$\\])+(?<!\\)\$", text)

    return text, placeholder_map


def _restore_math_segments(rendered_html, placeholder_map):
    """Restore protected math segments into rendered HTML."""
    for placeholder, math_text in placeholder_map.items():
        rendered_html = rendered_html.replace(
            placeholder, html_lib.escape(math_text, quote=False)
        )
    return rendered_html


def _extract_body_fragment(full_html):
    match = re.search(
        r"<body[^>]*>(.*?)</body>", full_html, flags=re.IGNORECASE | re.DOTALL
    )
    return match.group(1) if match else full_html


def _set_qtextdocument_markdown(doc, markdown_text):
    """Render Markdown via Qt's QTextDocument with GitHub dialect if available."""
    try:
        features = QTextDocument.MarkdownFeature.MarkdownDialectGitHub
        doc.setMarkdown(markdown_text, features)
    except Exception:
        try:
            doc.setMarkdown(markdown_text)
        except Exception:
            doc.setPlainText(markdown_text)


# --- Public API ---
def markdown_to_html_fragment(markdown_text):
    """Convert Markdown to an HTML fragment, preserving MathJax delimiters."""
    markdown_text = (markdown_text or "").replace("\r\n", "\n").replace("\r", "\n")
    protected_text, placeholder_map = _protect_math_segments(markdown_text)

    doc = QTextDocument()
    _set_qtextdocument_markdown(doc, protected_text)

    fragment = _extract_body_fragment(doc.toHtml())
    fragment = _restore_math_segments(fragment, placeholder_map)
    return fragment


def _strip_math_delimiters(math_text):
    text = math_text.strip()
    if text.startswith("$$") and text.endswith("$$"):
        return text[2:-2].strip()
    if text.startswith(r"\[") and text.endswith(r"\]"):
        return text[2:-2].strip()
    if text.startswith(r"\(") and text.endswith(r"\)"):
        return text[2:-2].strip()
    if text.startswith("$") and text.endswith("$"):
        return text[1:-1].strip()
    return text


def markdown_to_plain_text(markdown_text):
    """Convert Markdown to plain text for TTS, stripping all formatting."""
    markdown_text = markdown_text or ""
    protected_text, placeholder_map = _protect_math_segments(markdown_text)

    doc = QTextDocument()
    _set_qtextdocument_markdown(doc, protected_text)
    plain = doc.toPlainText()

    for placeholder, math_text in placeholder_map.items():
        plain = plain.replace(placeholder, _strip_math_delimiters(math_text))

    plain = re.sub(r"\s+", " ", plain).strip()
    return plain


def build_review_html(markdown_text, font_family, font_size, include_mathjax=True):
    """Build the complete HTML document used by the review display.

    Markdown is rendered first and active/resource-bearing markup is removed.
    Math delimiters remain visibly available as a safe offline fallback.  The
    ``include_mathjax`` argument is retained for callers using the old API,
    but review HTML intentionally never loads a network MathJax script.
    """
    html_body = sanitize_review_html_fragment(markdown_to_html_fragment(markdown_text))
    document_css = review_document_css(font_family, font_size)

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>{document_css}</style>
</head>
<body>
  <div class="content-wrapper">
    {html_body}
  </div>
</body>
</html>
"""
