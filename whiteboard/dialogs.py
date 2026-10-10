"""Small, touch-friendly tool settings and pen diagnostics widgets."""

from __future__ import annotations

from .i18n import TextBindings, tr

from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QColorDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QPushButton, QSlider, QToolButton,
    QVBoxLayout, QWidget,
)

from .models import Brush


INK = "#24334A"
BLUE = "#356AF0"


def tool_icon(name: str, color: str = INK, size: int = 28) -> QIcon:
    """Draw original vector icons; no external icon/font/runtime dependency."""
    image = QPixmap(size * 2, size * 2)
    image.setDevicePixelRatio(2)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 28, size / 28)
    pen = QPen(QColor(color), 1.8, Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if name in ("pen", "highlighter"):
        painter.save()
        if QColor(color).lightness() > 215:
            painter.setPen(QPen(QColor("#A2AFC3"), 1.8, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.translate(14, 14)
        painter.rotate(35)
        if name == "pen":
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRectF(-3.5, -10, 7, 14), 2, 2)
            path = QPainterPath(QPointF(-3.5, 4))
            path.lineTo(0, 10)
            path.lineTo(3.5, 4)
            path.closeSubpath()
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawPath(path)
            painter.setBrush(QColor(color))
            painter.drawEllipse(QPointF(0, 9), 1.1, 1.1)
        else:
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRectF(-5, -10, 10, 13), 2, 2)
            path = QPainterPath(QPointF(-4, 3))
            path.lineTo(-3, 8)
            path.lineTo(3, 8)
            path.lineTo(4, 3)
            painter.setBrush(QColor("#FFFFFF"))
            painter.drawPath(path)
            painter.drawLine(QPointF(-3, 10), QPointF(3, 10))
        painter.restore()
    elif name == "eraser":
        path = QPainterPath(QPointF(5, 16))
        for point in ((15, 5), (24, 13), (15, 23), (11, 23), (5, 18)):
            path.lineTo(*point)
        path.closeSubpath()
        painter.setBrush(QColor("#F2E9FF"))
        painter.drawPath(path)
        painter.drawLine(QPointF(10, 11), QPointF(19, 19))
        painter.drawLine(QPointF(12, 23), QPointF(25, 23))
    elif name == "lasso":
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawEllipse(QRectF(3, 3, 21, 17))
        painter.setPen(QPen(QColor(color), 1.8, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        path = QPainterPath(QPointF(8, 18))
        path.cubicTo(3, 27, 16, 26, 11, 20)
        painter.drawPath(path)
    elif name == "select":
        # Build around the shaft axis, then tilt the whole pointer. This keeps
        # the two shoulders balanced and the narrow shaft edges parallel.
        painter.save()
        painter.translate(13, 14)
        painter.rotate(-30)
        path = QPainterPath(QPointF(0, -11))
        for point in ((-8, 3), (-2, 1), (-2, 9), (2, 9), (2, 1), (8, 3)):
            path.lineTo(*point)
        path.closeSubpath()
        painter.drawPath(path)
        painter.restore()
    elif name in ("undo", "redo"):
        if name == "redo":
            painter.translate(28, 0)
            painter.scale(-1, 1)
        path = QPainterPath(QPointF(5, 10))
        path.lineTo(16, 10)
        path.cubicTo(26, 10, 26, 23, 15, 23)
        painter.drawPath(path)
        painter.drawLine(QPointF(5, 10), QPointF(10, 5))
        painter.drawLine(QPointF(5, 10), QPointF(10, 15))
    elif name == "pan":
        path = QPainterPath(QPointF(7, 14))
        path.lineTo(7, 7)
        path.cubicTo(7, 4, 10, 4, 10, 7)
        path.lineTo(10, 3)
        path.cubicTo(10, 0, 13, 0, 13, 3)
        path.lineTo(13, 9)
        path.lineTo(13, 4)
        path.cubicTo(13, 1, 16, 1, 16, 4)
        path.lineTo(16, 10)
        path.lineTo(16, 7)
        path.cubicTo(16, 4, 19, 4, 19, 7)
        path.lineTo(19, 17)
        path.cubicTo(19, 26, 11, 26, 8, 21)
        path.lineTo(3, 15)
        path.cubicTo(1, 12, 4, 11, 7, 14)
        painter.drawPath(path)
    elif name in ("plus", "minus"):
        painter.drawLine(QPointF(7, 14), QPointF(21, 14))
        if name == "plus":
            painter.drawLine(QPointF(14, 7), QPointF(14, 21))
    elif name == "fit":
        for x, y, sx, sy in ((5, 5, 1, 1), (23, 5, -1, 1),
                             (5, 23, 1, -1), (23, 23, -1, -1)):
            painter.drawLine(QPointF(x, y), QPointF(x + sx * 5, y))
            painter.drawLine(QPointF(x, y), QPointF(x, y + sy * 5))
        painter.drawRoundedRect(QRectF(10, 10, 8, 8), 1, 1)
    elif name == "new":
        painter.drawRoundedRect(QRectF(5, 3, 18, 22), 3, 3)
        painter.drawLine(QPointF(10, 14), QPointF(18, 14))
        painter.drawLine(QPointF(14, 10), QPointF(14, 18))
    elif name == "open":
        path = QPainterPath(QPointF(3, 22))
        for point in ((3, 7), (11, 7), (14, 10), (24, 10), (24, 22)):
            path.lineTo(*point)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QPointF(4, 14), QPointF(23, 14))
    elif name == "image":
        painter.drawRoundedRect(QRectF(3, 5, 22, 18), 2.5, 2.5)
        painter.drawEllipse(QPointF(18.5, 10), 2, 2)
        path = QPainterPath(QPointF(3, 19))
        path.lineTo(9, 12)
        path.lineTo(16, 20)
        path.lineTo(20, 16)
        path.lineTo(25, 21)
        painter.drawPath(path)
    elif name == "save":
        path = QPainterPath(QPointF(5, 3))
        for point in ((20, 3), (24, 7), (24, 24), (4, 24), (4, 3)):
            path.lineTo(*point)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawRect(QRectF(9, 3, 10, 7))
        painter.drawRoundedRect(QRectF(9, 16, 11, 8), 1, 1)
    elif name == "menu":
        for y in (7, 14, 21):
            painter.drawLine(QPointF(5, y), QPointF(23, y))
    elif name == "close":
        painter.drawLine(QPointF(8, 8), QPointF(20, 20))
        painter.drawLine(QPointF(20, 8), QPointF(8, 20))
    elif name == "brand":
        painter.setBrush(QColor(BLUE))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(1, 1, 26, 26), 8, 8)
        painter.setPen(QPen(Qt.GlobalColor.white, 2.7, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        path = QPainterPath(QPointF(6, 18))
        path.cubicTo(10, 3, 9, 24, 15, 10)
        path.cubicTo(19, 2, 16, 23, 23, 12)
        painter.drawPath(path)
    painter.end()
    return QIcon(image)


POPOVER_STYLE = """
QFrame#toolPopover { background: #FFFFFF; border: 1px solid #DEE5EF; border-radius: 16px; }
QLabel { color: #34435B; background: transparent; border: none; font-size: 12px; }
QLabel#popoverTitle { color: #202E44; font-size: 16px; font-weight: 600; }
QLabel#muted { color: #718097; font-size: 11px; }
QLabel#valueBadge { color: #425673; background: #F2F5FA; border-radius: 6px; padding: 3px 8px; }
QLabel:disabled { color: #9BA6B7; }
QLabel#valueBadge:disabled { color: #9BA6B7; background: #F7F8FA; }
QPushButton { background: #F7F9FC; border: 1px solid #E4EAF3; border-radius: 10px; min-height: 44px; padding: 0 12px; color: #34435B; }
QPushButton:hover { background: #EFF4FF; border-color: #B8CBF2; }
QPushButton:pressed { background: #E5EDFF; }
QPushButton:checked { background: #EDF3FF; border-color: #8DAAF2; color: #285AC9; }
QPushButton:focus { border-color: #356AF0; }
QSlider::groove:horizontal { height: 4px; background: #E8EDF5; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #356AF0; border-radius: 2px; }
QSlider::handle:horizontal { background: #FFFFFF; border: 2px solid #356AF0; width: 16px; margin: -8px 0; border-radius: 10px; }
QSlider::handle:horizontal:hover, QSlider::handle:horizontal:focus { background: #EDF3FF; }
QSlider::sub-page:horizontal:disabled { background: #D8DFEA; }
QSlider::handle:horizontal:disabled { border-color: #C9D2E1; background: #F7F9FC; }
"""


class ColorSwatch(QToolButton):
    """A small color chip inside a full-sized touch/keyboard target."""

    def __init__(self, color: str):
        super().__init__()
        self.color = QColor(color)
        self.setFixedSize(44, 44)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        if self.underMouse() or self.hasFocus():
            painter.setPen(QPen(QColor("#A5BCF0"), 1) if self.hasFocus() else Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#F0F4FC"))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10)
        if self.isChecked():
            painter.setPen(QPen(QColor(BLUE), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, 19, 19)
        painter.setPen(QPen(QColor("#D8E0EC"), 1) if self.color.lightness() > 220 else Qt.PenStyle.NoPen)
        painter.setBrush(self.color)
        painter.drawEllipse(center, 14.5, 14.5)
        if self.isChecked():
            painter.setPen(QPen(QColor(INK if self.color.lightness() > 180 else "#FFFFFF"), 1.8,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            path = QPainterPath(center + QPointF(-4, 0))
            path.lineTo(center + QPointF(-1, 3))
            path.lineTo(center + QPointF(5, -3))
            painter.drawPath(path)
        painter.end()


class BrushPopover(QFrame):
    brush_changed = Signal(object)
    sensitivity_changed = Signal(float)

    def __init__(self, brush: Brush, pressure_enabled: bool,
                 sensitivity: float, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("toolPopover")
        self.setStyleSheet(POPOVER_STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.brush = replace(brush)
        self.setFixedWidth(348)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        title = QLabel(tr('荧光笔') if brush.kind == "highlighter" else tr('画笔设置'))
        title.setObjectName("popoverTitle")
        layout.addWidget(title)
        palette = QGridLayout()
        palette.setSpacing(7)
        self.color_group = QButtonGroup(self)
        colors = ("#222222", "#D53D4C", "#EF8534", "#E7B91A", "#22A06B", "#3478E5",
                  "#7254CD", "#CF579B", "#FFFFFF", "#6B7789", "#4EC9CC", "#FFF066")
        for i, color in enumerate(colors):
            button = ColorSwatch(color)
            button.setChecked(QColor(color) == QColor(brush.color))
            button.setToolTip(color)
            button.setAccessibleName(tr('颜色 ') + color)
            self.color_group.addButton(button)
            button.clicked.connect(lambda checked=False, c=color: self._set_brush(color=c))
            palette.addWidget(button, i // 6, i % 6)
        layout.addLayout(palette)
        custom = QPushButton(tr('自定义颜色…'))
        custom.clicked.connect(self._choose_color)
        layout.addWidget(custom)
        width_section = QVBoxLayout()
        width_section.setSpacing(0)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr('粗细')))
        row.addStretch()
        self.width_value = QLabel()
        self.width_value.setObjectName("valueBadge")
        self.width_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(self.width_value)
        width_section.addLayout(row)
        self.width_slider = QSlider(Qt.Orientation.Horizontal)
        self.width_slider.setMinimumHeight(44)
        self.width_slider.setRange(10, 480 if brush.kind == "highlighter" else 240)
        self.width_slider.setValue(round(float(brush.width) * 10))
        self.width_value.setText(f"{brush.width:g} px")
        self.width_slider.valueChanged.connect(self._width_changed)
        width_section.addWidget(self.width_slider)
        layout.addLayout(width_section)
        if brush.kind != "highlighter":
            sensitivity_section = QVBoxLayout()
            sensitivity_section.setSpacing(0)
            pressure_row = QHBoxLayout()
            self.sensitivity_title = QLabel(tr('压感灵敏度'))
            self.sensitivity_title.setEnabled(pressure_enabled)
            pressure_row.addWidget(self.sensitivity_title)
            pressure_row.addStretch()
            self.sensitivity_label = QLabel(f"{sensitivity:.1f}×")
            self.sensitivity_label.setObjectName("valueBadge")
            self.sensitivity_label.setEnabled(pressure_enabled)
            self.sensitivity_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            pressure_row.addWidget(self.sensitivity_label)
            sensitivity_section.addLayout(pressure_row)
            self.sensitivity_slider = QSlider(Qt.Orientation.Horizontal)
            self.sensitivity_slider.setRange(5, 20)
            self.sensitivity_slider.setValue(round(sensitivity * 10))
            self.sensitivity_slider.setMinimumHeight(44)
            self.sensitivity_slider.setEnabled(pressure_enabled)
            self.sensitivity_slider.valueChanged.connect(self._sensitivity_changed)
            sensitivity_section.addWidget(self.sensitivity_slider)
            layout.addLayout(sensitivity_section)
            note = QLabel(tr('轻触细，重按粗；鼠标使用固定粗细。'))
        else:
            note = QLabel(tr('单笔自交不加深，分笔叠画加深。'))
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)

    def _set_brush(self, **changes):
        self.brush = replace(self.brush, **changes)
        self.brush_changed.emit(self.brush)

    def _width_changed(self, value: int):
        self.width_value.setText(f"{value / 10:g} px")
        self._set_brush(width=value / 10)

    def _choose_color(self):
        # A native modal dialog must not compete with an active Qt.Popup grab.
        self.hide()
        color = QColorDialog.getColor(QColor(self.brush.color), self.parentWidget(), tr('选择画笔颜色'))
        if color.isValid():
            self._set_brush(color=color.name())
            self.color_group.setExclusive(False)
            for button in self.color_group.buttons():
                button.setChecked(QColor(button.toolTip()) == color)
            self.color_group.setExclusive(True)
        self.show()

    def _sensitivity_changed(self, *_):
        sensitivity = self.sensitivity_slider.value() / 10
        self.sensitivity_label.setText(f"{sensitivity:.1f}×")
        self.sensitivity_changed.emit(sensitivity)


class EraserPopover(QFrame):
    settings_changed = Signal(float, bool)

    def __init__(self, radius: float, whole: bool, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("toolPopover")
        self.setStyleSheet(POPOVER_STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedWidth(312)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        title = QLabel(tr('橡皮擦'))
        title.setObjectName("popoverTitle")
        layout.addWidget(title)
        self.mode_group = QButtonGroup(self)
        row = QHBoxLayout()
        self.local_button = QPushButton(tr('局部擦除'))
        self.whole_button = QPushButton(tr('整笔擦除'))
        for button in (self.local_button, self.whole_button):
            button.setCheckable(True)
            self.mode_group.addButton(button)
            row.addWidget(button)
        self.whole_button.setChecked(whole)
        self.local_button.setChecked(not whole)
        self.mode_group.buttonClicked.connect(self._changed)
        layout.addLayout(row)
        self.radius_label = QLabel(tr('擦除半径  {p0:g} px', p0=radius))
        layout.addWidget(self.radius_label)
        self.radius_slider = QSlider(Qt.Orientation.Horizontal)
        self.radius_slider.setRange(4, 80)
        self.radius_slider.setValue(round(radius))
        self.radius_slider.setMinimumHeight(44)
        self.radius_slider.valueChanged.connect(self._changed)
        layout.addWidget(self.radius_slider)
        note = QLabel(tr('翻转 Surface Pen 可临时切换为橡皮。'))
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)

    def _changed(self, *_):
        radius = float(self.radius_slider.value())
        self.radius_label.setText(tr('擦除半径  {p0:g} px', p0=radius))
        self.settings_changed.emit(radius, self.whole_button.isChecked())


class DiagnosticsPanel(QFrame):
    closed = Signal()
    export_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.text_bindings = TextBindings()
        self._last_data = {}
        self.setObjectName("diagnostics")
        self.setStyleSheet("""
            QFrame#diagnostics { background: #F8FAFE; border: 1px solid #DCE5F2; border-radius: 12px; }
            QLabel { border: none; background: transparent; color: #4D607B; font-size: 11px; }
            QToolButton { border: none; background: transparent; }
        """)
        self.setFixedWidth(280)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 10, 12)
        header = QHBoxLayout()
        title = self.text_bindings.bind(QLabel(), 'text', '输入诊断 · F12')
        title.setStyleSheet("font-weight: 600; color: #24334A;")
        header.addWidget(title)
        close = QToolButton()
        close.setIcon(tool_icon("close", size=20))
        close.setFixedSize(44, 44)
        close.clicked.connect(self.closed.emit)
        header.addWidget(close)
        layout.addLayout(header)
        self.values = {}
        labels = (("source", '输入源'), ("tool", '当前工具'), ("pressure", '压力'),
                  ("tilt_x", '倾斜 X'), ("tilt_y", '倾斜 Y'), ("samples", '采样点'),
                  ("paint_ms", '绘制耗时'), ("cache_mb", '缓存'), ("scale", '缩放'))
        for key, name in labels:
            row = QHBoxLayout()
            row.addWidget(self.text_bindings.bind(QLabel(), "text", name))
            value = QLabel("—")
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(value)
            self.values[key] = value
            layout.addLayout(row)
        export = self.text_bindings.bind(QPushButton(), 'text', '导出输入记录…')
        export.setMinimumHeight(44)
        export.clicked.connect(self.export_requested.emit)
        layout.addWidget(export)

    def retranslate(self):
        self.text_bindings.refresh()
        self.update_values(self._last_data)

    def update_values(self, data: dict):
        self._last_data.update(data)
        for key, label in self.values.items():
            value = data.get(key)
            if value is None:
                continue
            try:
                if key == "pressure":
                    text = f"{float(value):.3f}"
                elif key in ("tilt_x", "tilt_y"):
                    text = f"{float(value):.1f}°"
                elif key == "paint_ms":
                    text = f"{float(value):.2f} ms"
                elif key == "cache_mb":
                    text = f"{float(value):.1f} MiB"
                elif key == "scale":
                    text = f"{float(value) * 100:.0f}%"
                elif key in ("tool", "source"):
                    names = {"pen": "画笔", "highlighter": "荧光笔", "eraser": "橡皮擦",
                             "lasso": "套索选择", "select": "选择图片", "pan": "移动画布", "mouse": "鼠标",
                             "pinch": "双指缩放", "touch_pan": "单指平移"}
                    text = tr(names.get(str(value), str(value)))
                else:
                    text = str(value)
            except (ValueError, TypeError):
                text = str(value)
            label.setText(text)
