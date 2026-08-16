"""Graphics items for the canvas-based review UI."""

import logging
import re

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QGraphicsProxyWidget,
    QGraphicsRectItem,
    QTextBrowser,
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import (
    QColor,
    QBrush,
    QPen,
    QIcon,
    QDesktopServices,
    QFont,
    QTextCharFormat,
    QTextCursor,
)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView

    try:
        from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    except ImportError:
        QWebEnginePage = None
        QWebEngineSettings = None
    HAS_WEBENGINE = True
except ImportError:
    QWebEngineView = None
    QWebEnginePage = None
    QWebEngineSettings = None
    HAS_WEBENGINE = False

from .catalog import DatabaseType
from .markdown_utils import build_review_html
from .ui_theme import LIGHT_TOKENS, apply_semantic_role, review_card_stylesheet


logger = logging.getLogger(__name__)

_SENTENCE_REVIEW_METADATA_RE = re.compile(
    r"^Box\s+\d+\s*\|\s*ID:\s*\d+$", flags=re.IGNORECASE
)
_TTS_ANCHOR_PREFIX = "#barsky-tts-"


def _append_bold_range(ranges, document, start, end) -> None:
    """Append one non-empty document range and its visible speech text."""
    cursor = QTextCursor(document)
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    speech_text = cursor.selectedText().strip()
    if speech_text:
        ranges.append((start, end, speech_text))


def _sentence_review_bold_ranges(document) -> list[tuple[int, int, str]]:
    """Return non-metadata bold ranges eligible for sentence-review TTS."""
    ranges: list[tuple[int, int, str]] = []
    block = document.begin()
    while block.isValid():
        if block.blockNumber() == 0 and _SENTENCE_REVIEW_METADATA_RE.fullmatch(
            block.text().strip()
        ):
            block = block.next()
            continue

        run_start = None
        run_end = None
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            iterator += 1
            if not fragment.isValid():
                continue

            text_format = fragment.charFormat()
            is_unlinked_bold = (
                text_format.fontWeight() >= QFont.Weight.Bold.value
                and not text_format.isAnchor()
            )
            fragment_start = fragment.position()
            fragment_end = fragment_start + fragment.length()

            if is_unlinked_bold:
                if run_start is None:
                    run_start, run_end = fragment_start, fragment_end
                elif fragment_start == run_end:
                    run_end = fragment_end
                else:
                    _append_bold_range(ranges, document, run_start, run_end)
                    run_start, run_end = fragment_start, fragment_end
            elif run_start is not None:
                _append_bold_range(ranges, document, run_start, run_end)
                run_start = run_end = None

        if run_start is not None:
            _append_bold_range(ranges, document, run_start, run_end)
        block = block.next()

    return ranges


class ReviewCardNavigationPolicy:
    """Strict navigation policy for untrusted review-card HTML.

    The embedded review view is display-only.  Explicit HTTP(S) links may be
    handed to the operating system, while every navigation stays out of the
    card (including local-file, data, JavaScript, and custom schemes).
    """

    BLOCKED_SCHEMES = frozenset({"file", "data", "javascript", "qrc", "mailto", "ftp"})
    EXTERNAL_SCHEMES = frozenset({"http", "https"})

    @staticmethod
    def _url_text(url: QUrl | str) -> str:
        return url.toString() if isinstance(url, QUrl) else str(url or "")

    @classmethod
    def should_open_externally(cls, url: QUrl | str) -> bool:
        """Return whether *url* is an HTTP(S) link suitable for the desktop."""
        parsed = QUrl(cls._url_text(url))
        return (
            parsed.isValid()
            and parsed.scheme().lower() in cls.EXTERNAL_SCHEMES
            and bool(parsed.host())
        )

    @classmethod
    def allows_embedded_navigation(cls, url: QUrl | str) -> bool:
        """Review cards are never allowed to navigate their embedded page."""
        return False


def route_review_card_link(url: QUrl | str, opener=QDesktopServices.openUrl) -> bool:
    """Open a permitted card link outside the application, returning success."""
    if not ReviewCardNavigationPolicy.should_open_externally(url):
        return False
    qurl = url if isinstance(url, QUrl) else QUrl(str(url))
    return bool(opener(qurl))


if QWebEnginePage is not None:

    class ReviewCardWebPage(QWebEnginePage):
        """Display-only page which delegates user links to the desktop."""

        def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
            link_click = (
                navigation_type
                == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
            )
            if link_click:
                route_review_card_link(url)
            return ReviewCardNavigationPolicy.allows_embedded_navigation(url)
else:
    ReviewCardWebPage = None  # type: ignore  # conditional Qt import


def configure_review_web_view(web_view) -> None:
    """Apply the review-card's no-script, no-local-content web policy."""
    if QWebEngineSettings is None:
        return
    settings = web_view.settings()
    settings.setAttribute(
        QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
        False,
    )
    settings.setAttribute(
        QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
        False,
    )
    settings.setAttribute(
        QWebEngineSettings.WebAttribute.JavascriptEnabled,
        False,
    )


def _set_transparent_web_view_background(web_view) -> None:
    """Apply optional WebEngine styling without disrupting card rendering."""
    try:
        web_view.page().setBackgroundColor(QColor("transparent"))
    except Exception:
        logger.warning(
            "Could not set the review-card WebEngine background.", exc_info=True
        )


class FlashCardItem(QGraphicsRectItem):
    """The draggable, flippable flash card in the center of the canvas."""

    def __init__(self, app_ref, cx, cy, cw, ch):
        super().__init__(-cw / 2, -ch / 2, cw, ch)
        self.app_ref = app_ref
        self.speech_text = ""
        self._tts_anchor_text = {}
        self.setPos(cx, cy)

        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable)
        self.setBrush(QBrush(QColor(LIGHT_TOKENS["surface"])))
        self.setPen(QPen(QColor(LIGHT_TOKENS["border"]), 1))

        self.proxy = QGraphicsProxyWidget(self)
        self.container = QWidget()
        self.container.setObjectName("reviewCardRoot")
        ui_font_family = self.app_ref.settings.get("font_family", "Arial")
        ui_font_size = self.app_ref.settings.get("font_size", 14)
        self.container.setStyleSheet(
            review_card_stylesheet(ui_font_family, ui_font_size)
        )

        self.layout = QVBoxLayout(self.container)
        self.layout.setContentsMargins(0, 0, 0, 0)

        # The card is embedded through QGraphicsProxyWidget. QWebEngineView
        # uses a separate composited surface that is not reliably painted in
        # that host, leaving an otherwise functional card blank. QTextBrowser
        # safely renders the generated HTML and provides explicit link signals
        # without allowing links to replace the review content.
        self.text_widget = QTextBrowser()
        self.text_widget.setReadOnly(True)
        self.text_widget.setOpenLinks(False)
        self.text_widget.setOpenExternalLinks(False)
        self.text_widget.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self.text_widget.anchorClicked.connect(self._handle_text_link)

        self.btn_layout = QHBoxLayout()

        self.tts_btn = QPushButton(" Listen")
        self.tts_btn.setIcon(QIcon.fromTheme("audio-volume-high"))
        self.tts_btn.setObjectName("ttsBtn")
        self.tts_btn.setToolTip("Speak this card (Alt+L)")
        self.tts_btn.setAccessibleName("Listen to card")
        self.tts_btn.setAccessibleDescription("Speak this review card (Alt+L).")
        self.tts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tts_btn.clicked.connect(self.trigger_tts)

        self.flip_btn = QPushButton(" Reveal Answer")
        self.flip_btn.setObjectName("revealBtn")
        self.flip_btn.setToolTip("Reveal the answer (Alt+R)")
        self.flip_btn.setAccessibleName("Reveal answer")
        self.flip_btn.setAccessibleDescription("Reveal this card's answer (Alt+R).")
        self.flip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.flip_btn.clicked.connect(
            self.app_ref.flip_card,
            Qt.ConnectionType.QueuedConnection,
        )

        self.incorrect_btn = QPushButton("Incorrect")
        self.incorrect_btn.setObjectName("incorrectBtn")
        self.incorrect_btn.setToolTip("Mark incorrect (Alt+Left or Alt+1)")
        self.incorrect_btn.setAccessibleName("Incorrect answer")
        self.incorrect_btn.setAccessibleDescription(
            "Mark this answer incorrect. Shortcuts: Alt+Left and Alt+1."
        )
        self.incorrect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.incorrect_btn.clicked.connect(
            lambda _checked=False: self.app_ref.process_answer(False)
        )

        self.correct_btn = QPushButton("Correct")
        self.correct_btn.setObjectName("correctBtn")
        self.correct_btn.setToolTip("Mark correct (Alt+Right or Alt+2)")
        self.correct_btn.setAccessibleName("Correct answer")
        self.correct_btn.setAccessibleDescription(
            "Mark this answer correct. Shortcuts: Alt+Right and Alt+2."
        )
        self.correct_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.correct_btn.clicked.connect(
            lambda _checked=False: self.app_ref.process_answer(True)
        )

        self.btn_layout.addWidget(self.tts_btn)
        self.btn_layout.addWidget(self.flip_btn, 1)
        self.btn_layout.addWidget(self.incorrect_btn, 1)
        self.btn_layout.addWidget(self.correct_btn, 1)

        for button, role in (
            (self.tts_btn, "secondary"),
            (self.flip_btn, "primary"),
            (self.incorrect_btn, "danger"),
            (self.correct_btn, "success"),
        ):
            apply_semantic_role(button, role)

        self.incorrect_btn.hide()
        self.correct_btn.hide()

        self.layout.addWidget(self.text_widget)
        self.layout.addLayout(self.btn_layout)

        self.top_margin = 35
        self.side_margin = 15
        self.bottom_margin = 15

        widget_w = int(cw - (self.side_margin * 2))
        widget_h = int(ch - self.top_margin - self.bottom_margin)
        self.container.setFixedSize(widget_w, widget_h)

        self.proxy.setWidget(self.container)
        self.proxy.setPos(-cw / 2 + self.side_margin, -ch / 2 + self.top_margin)

    def trigger_tts(self):
        if self.speech_text:
            self.app_ref.speak_text(self.speech_text, self.tts_btn)

    def _handle_text_link(self, url):
        """Speak internal sentence targets or route safe web links externally."""
        speech_text = self._tts_anchor_text.get(url.toString())
        if speech_text is not None:
            self.app_ref.speak_text(speech_text, self.tts_btn)
            return
        route_review_card_link(url)

    def _link_sentence_review_bold_text(self):
        """Turn sentence-card bold words and phrases into local TTS anchors."""
        self._tts_anchor_text = {}
        if getattr(self.app_ref, "_db_type", None) != DatabaseType.LANGUAGE_SENTENCE:
            return

        document = self.text_widget.document()
        for index, (start, end, speech_text) in enumerate(
            _sentence_review_bold_ranges(document)
        ):
            href = f"{_TTS_ANCHOR_PREFIX}{index}"
            self._tts_anchor_text[href] = speech_text

            cursor = QTextCursor(document)
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            link_format = QTextCharFormat()
            link_format.setAnchor(True)
            link_format.setAnchorHref(href)
            link_format.setToolTip(f'Listen to "{speech_text}"')
            cursor.mergeCharFormat(link_format)

    def set_text(self, display_text, is_flipped, text_to_speak=""):
        if text_to_speak:
            self.speech_text = text_to_speak

        font_fam = self.app_ref.settings.get("content_font_family", "Arial")
        font_sz = self.app_ref.settings.get("content_font_size", 18)

        html_template = build_review_html(
            display_text,
            font_family=font_fam,
            font_size=font_sz,
            include_mathjax=False,
        )
        self.text_widget.setHtml(html_template)
        self._link_sentence_review_bold_text()

        if is_flipped:
            self.flip_btn.hide()
            self.incorrect_btn.show()
            self.correct_btn.show()
        else:
            self.flip_btn.show()
            self.incorrect_btn.hide()
            self.correct_btn.hide()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.app_ref.check_card_drop(self)
