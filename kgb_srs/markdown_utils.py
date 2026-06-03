"""Markdown + MathJax rendering helpers.

Key invariants:
1. Math placeholders use only letters/numbers so Markdown cannot mangle them.
2. HTML output preserves LaTeX delimiters for MathJax.
3. Plain-text extraction strips formatting for TTS.
"""

import re
import html as html_lib

from PyQt6.QtGui import QTextDocument

# --- Constants ---
MATH_TOKEN_PREFIX = "BARSKYMATHPLACEHOLDER"


# --- Math Protection ---
def _protect_math_segments(text):
    """Temporarily replace LaTeX math segments before Markdown parsing.

    Supports $inline$, $$display$$, \\(inline\\), \\[display\\].
    """
    text = text or ""
    token_map = {}

    def make_token():
        return f"{MATH_TOKEN_PREFIX}{len(token_map)}TOKEN"

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

    Markdown is rendered first, while math is preserved for MathJax.
    """
    html_body = markdown_to_html_fragment(markdown_text)
    safe_font = str(font_family).replace("\\", "\\\\").replace("'", "\\'")

    mathjax_script = ""
    mathjax_typeset_script = ""

    if include_mathjax:
        mathjax_script = """
<script>
window.MathJax = {
  tex: {
    inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
    displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
    processEscapes: true
  },
  options: {
    skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']
  },
  startup: {
    typeset: true
  }
};
</script>
<script id="MathJax-script" async
        src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
"""
        mathjax_typeset_script = """
<script>
(function waitForMathJaxAndTypeset() {
  if (window.MathJax && window.MathJax.typesetPromise) {
    window.MathJax.typesetPromise();
  } else {
    window.setTimeout(waitForMathJaxAndTypeset, 100);
  }
})();
</script>
"""

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
{mathjax_script}
<style>
  html, body {{
    margin: 0;
    padding: 0;
    background-color: transparent;
  }}

  body {{
    font-family: '{safe_font}', Arial, sans-serif;
    font-size: {font_size}px;
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
  {mathjax_typeset_script}
</body>
</html>
"""
