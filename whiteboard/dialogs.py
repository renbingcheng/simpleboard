"""Small, touch-friendly tool settings and pen diagnostics widgets."""

from __future__ import annotations

from .i18n import TextBindings, tr

from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QFrame, QGridLayout,
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
QFrame#toolPopover { background: #FFFFFF; border: 1px solid #DEE5EF; border-radius: 14px; }
QLabel { color: #24334A; background: transparent; border: none; }
QLabel#popoverTitle { font-size: 16px; font-weight: 600; }
QLabel#muted { color: #718097; font-size: 11px; }
QPushButton { background: #F4F6FA; border: 1px solid #E4E9F1; border-radius: 8px; min-height: 44px; padding: 0 12px; color: #24334A; }
QPushButton:hover { background: #EAF0FD; border-color: #B6CAF8; }
QPushButton:checked { background: #EAF0FF; border-color: #356AF0; color: #2456C9; }
QSlider::groove:horizontal { height: 5px; background: #E8EDF5; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #356AF0; border-radius: 2px; }
QSlider::handle:horizontal { background: #FFFFFF; border: 2px solid #356AF0; width: 19px; margin: -8px 0; border-radius: 10px; }
QCheckBox { color: #24334A; min-height: 44px; spacing: 10px; }
QCheckBox::indicator { width: 20px; height: 20px; }
"""


class BrushPopover(QFrame):
    brush_changed = Signal(object)
    pressure_changed = Signal(bool, float)

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
            button = QToolButton()
            button.setFixedSize(44, 44)
            button.setCheckable(True)
            button.setChecked(QColor(color) == QColor(brush.color))
            button.setToolTip(color)
            button.setAccessibleName(tr('颜色 ') + color)
            button.setStyleSheet(
                "QToolButton { background: " + color + "; border: 2px solid #DFE5EE; border-radius: 22px; }"
                "QToolButton:checked { border: 4px solid #356AF0; }"
                "QToolButton:hover { border-color: #7598EE; }")
            self.color_group.addButton(button)
            button.clicked.connect(lambda checked=False, c=color: self._set_brush(color=c))
            palette.addWidget(button, i // 6, i % 6)
        layout.addLayout(palette)
        custom = QPushButton(tr('自定义颜色…'))
        custom.clicked.connect(self._choose_color)
        layout.addWidget(custom)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr('粗细')))
        self.width_value = QLabel()
        self.width_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(self.width_value)
        layout.addLayout(row)
        self.width_slider = QSlider(Qt.Orientation.Horizontal)
        self.width_slider.setMinimumHeight(44)
        self.width_slider.setRange(10, 480 if brush.kind == "highlighter" else 240)
        self.width_slider.setValue(round(float(brush.width) * 10))
        self.width_value.setText(f"{brush.width:g} px")
        self.width_slider.valueChanged.connect(self._width_changed)
        layout.addWidget(self.width_slider)
        if brush.kind != "highlighter":
            self.pressure_toggle = QCheckBox(tr('启用压感 · 应用于全部画笔'))
            self.pressure_toggle.setChecked(pressure_enabled)
            layout.addWidget(self.pressure_toggle)
            pressure_row = QHBoxLayout()
            pressure_row.addWidget(QLabel(tr('压感灵敏度')))
            self.sensitivity_label = QLabel(f"{sensitivity:.1f}×")
            self.sensitivity_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            pressure_row.addWidget(self.sensitivity_label)
            layout.addLayout(pressure_row)
            self.sensitivity_slider = QSlider(Qt.Orientation.Horizontal)
            self.sensitivity_slider.setRange(5, 20)
            self.sensitivity_slider.setValue(round(sensitivity * 10))
            self.sensitivity_slider.setMinimumHeight(44)
            self.sensitivity_slider.setEnabled(pressure_enabled)
            self.sensitivity_slider.valueChanged.connect(self._pressure_changed)
            self.pressure_toggle.toggled.connect(self._pressure_changed)
            layout.addWidget(self.sensitivity_slider)
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

    def _pressure_changed(self, *_):
        enabled = self.pressure_toggle.isChecked()
        sensitivity = self.sensitivity_slider.value() / 10
        self.sensitivity_slider.setEnabled(enabled)
        self.sensitivity_label.setText(f"{sensitivity:.1f}×")
        self.pressure_changed.emit(enabled, sensitivity)


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
                             "lasso": "套索选择", "pan": "移动画布", "mouse": "鼠标",
                             "pinch": "双指缩放", "touch_pan": "单指平移"}
                    text = tr(names.get(str(value), str(value)))
                else:
                    text = str(value)
            except (ValueError, TypeError):
                text = str(value)
            label.setText(text)
