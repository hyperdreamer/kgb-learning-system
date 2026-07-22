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

# --- Constants ---
MATH_TOKEN_PREFIX = "BARSKYMATHPLACEHOLDER"


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
    r"\s+style\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)",
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
    cleaned = _REVIEW_STYLE_ATTRIBUTE_RE.sub("", cleaned)

    def replace_url_attribute(match):
        name = match.group("name").lower()
        raw_value = match.group("value")
        value = raw_value[1:-1] if raw_value[:1] in {"\"", "'"} else raw_value
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
    token_map = {}
    token_prefix = MATH_TOKEN_PREFIX
    while token_prefix in text:
        token_prefix += "X"

    def make_token():
        return f"{token_prefix}{len(token_map)}TOKEN"

    def replace_pattern(pattern, source):
        def repl(match):
            token = make_token()
            token_map[token] = match.group(0)
            return token
        return re.sub(pattern, repl, source, flags=re.DOTALL)

    # Protect display math first, then inline math
    text = replace_pattern(r"(?<!\\)\$\$(.*?)(?<!\\)\$\$", text)
    text = replace_pattern(r"\\\[(.*?)\\\]", text)
    text = replace_pattern(r"\\\((.*?)\\\)", text)
    text = replace_pattern(r"(?<!\\)\$(?!\$)(?:\\.|[^\n$\\])+(?<!\\)\$", text)

    return text, token_map


def _restore_math_segments(rendered_html, token_map):
    """Restore protected math segments into rendered HTML."""
    for token, math_text in token_map.items():
        rendered_html = rendered_html.replace(
            token, html_lib.escape(math_text, quote=False)
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
    protected_text, token_map = _protect_math_segments(markdown_text)

    doc = QTextDocument()
    _set_qtextdocument_markdown(doc, protected_text)

    fragment = _extract_body_fragment(doc.toHtml())
    fragment = _restore_math_segments(fragment, token_map)
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
    protected_text, token_map = _protect_math_segments(markdown_text)

    doc = QTextDocument()
    _set_qtextdocument_markdown(doc, protected_text)
    plain = doc.toPlainText()

    for token, math_text in token_map.items():
        plain = plain.replace(token, _strip_math_delimiters(math_text))

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
    safe_font = str(font_family).replace("\\", "\\\\").replace("'", "\\'")
    try:
        safe_font_size = max(1, int(font_size))
    except (TypeError, ValueError):
        safe_font_size = 18

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body {{
    margin: 0;
    padding: 0;
    background-color: transparent;
  }}

  body {{
    font-family: '{safe_font}', Arial, sans-serif;
    font-size: {safe_font_size}px;
    color: #222;
    display: flex;
    justify-content: center;
    align-items: center;
    min-height: 100vh;
    box-sizing: border-box;
    overflow: auto;
  }}

  .content-wrapper {{
    text-align: left;
    display: inline-block;
    max-width: 100%;
    width: auto;
    padding: 20px;
    box-sizing: border-box;
    overflow-wrap: anywhere;
    word-wrap: break-word;
  }}

  .content-wrapper > *:first-child {{
    margin-top: 0;
  }}

  .content-wrapper > *:last-child {{
    margin-bottom: 0;
  }}

  p {{
    margin: 0.45em 0;
  }}

  h1, h2, h3, h4, h5, h6 {{
    margin: 0.65em 0 0.35em;
    line-height: 1.2;
  }}

  ul, ol {{
    margin: 0.45em 0 0.45em 1.3em;
    padding-left: 1.2em;
  }}

  li {{
    margin: 0.2em 0;
  }}

  blockquote {{
    border-left: 4px solid #bdbdbd;
    margin: 0.7em 0;
    padding: 0.2em 0 0.2em 0.8em;
    color: #555;
    background: rgba(0, 0, 0, 0.03);
  }}

  hr {{
    width: 100%;
    border: 0;
    border-top: 2px solid #cccccc;
    margin: 1em 0;
  }}

  pre {{
    background: #f6f8fa;
    border: 1px solid #dddddd;
    border-radius: 6px;
    padding: 10px;
    overflow-x: auto;
    white-space: pre-wrap;
  }}

  code {{
    background: #f3f3f3;
    border-radius: 4px;
    padding: 2px 4px;
    font-family: Consolas, Menlo, Monaco, monospace;
  }}

  pre code {{
    background: transparent;
    padding: 0;
  }}

  table {{
    border-collapse: collapse;
    margin: 0.7em 0;
    max-width: 100%;
  }}

  th, td {{
    border: 1px solid #cccccc;
    padding: 5px 8px;
  }}

  th {{
    background: #eeeeee;
    font-weight: bold;
  }}

  img {{
    max-width: 100%;
    height: auto;
  }}

  a {{
    color: #1565c0;
  }}
</style>
</head>
<body>
  <div class="content-wrapper">
    {html_body}
  </div>
</body>
</html>
"""
