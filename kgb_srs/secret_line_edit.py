"""Password line edit with an in-field visibility toggle."""

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QLineEdit


def _make_eye_icons(size=20):
    """Return distinct icons for hidden and visible password states."""
    grey = QColor(0x75, 0x75, 0x75)
    pen = QPen(grey, 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

    margin = max(2, int(size * 0.22))
    cx = size / 2.0
    cy = size / 2.0
    eye_half_width = size / 2.0 - margin
    eye_half_height = size / 2.0 - margin

    def eye_outline():
        path = QPainterPath()
        path.moveTo(cx - eye_half_width, cy)
        path.quadTo(cx, cy - eye_half_height, cx + eye_half_width, cy)
        path.quadTo(cx, cy + eye_half_height, cx - eye_half_width, cy)
        path.closeSubpath()
        return path

    def render(draw):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(pen)
        draw(painter)
        painter.end()
        return pixmap

    def draw_hidden(painter):
        painter.drawPath(eye_outline())
        inset = margin * 0.7
        painter.drawLine(
            int(cx + eye_half_width - inset),
            int(cy - eye_half_height + inset),
            int(cx - eye_half_width + inset),
            int(cy + eye_half_height - inset),
        )

    def draw_visible(painter):
        painter.drawPath(eye_outline())
        iris_radius = size * 0.14
        painter.drawEllipse(QPointF(cx, cy), iris_radius, iris_radius)

    return QIcon(render(draw_hidden)), QIcon(render(draw_visible))


class SecretLineEdit(QLineEdit):
    """Password input with a trailing visibility-toggle action."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setEchoMode(QLineEdit.EchoMode.Password)

        self._icon_hidden, self._icon_visible = _make_eye_icons()
        self._toggle_action = QAction(self._icon_hidden, "Show API key", self)
        self._toggle_action.setCheckable(True)
        self._toggle_action.setToolTip("Show API key")
        self.addAction(self._toggle_action, QLineEdit.ActionPosition.TrailingPosition)
        self._toggle_action.toggled.connect(self._on_toggled)

    def _on_toggled(self, checked):
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        tip = "Hide API key" if checked else "Show API key"
        icon = self._icon_visible if checked else self._icon_hidden
        self.setEchoMode(mode)
        self._toggle_action.setToolTip(tip)
        self._toggle_action.setIcon(icon)
        self._toggle_action.setText(tip)
