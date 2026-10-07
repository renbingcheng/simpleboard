"""Offline whiteboard window, floating tools, and safe document lifecycle."""

from __future__ import annotations

from .i18n import LANGUAGES, TextBindings, set_language, tr

from ctypes import wintypes
from dataclasses import replace
from pathlib import Path
import re
import sys

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from . import APP_DISPLAY_NAME
from .canvas import Canvas
from .dialogs import BLUE, BrushPopover, DiagnosticsPanel, EraserPopover, tool_icon
from .models import BoardDocument, Brush
from .native_pen import NativeTailEraser
from .recovery import RecoveryManager
from .scene import Scene
from .settings import AppSettings, app_data_directory
from .storage import DocumentError, load_document, save_document


def file_filter():
    return tr('{p0} 白板 (*.qboard);;所有文件 (*)', p0=APP_DISPLAY_NAME)

# MSG fields and power event codes from the Windows SDK, not Qt event numbers.
# https://learn.microsoft.com/windows/win32/api/winuser/ns-winuser-msg
# https://learn.microsoft.com/windows/win32/power/wm-powerbroadcast
WM_POWERBROADCAST = 0x0218
_INPUT_RESET_POWER_EVENTS = frozenset({
    0x0004,  # PBT_APMSUSPEND
    0x0007,  # PBT_APMRESUMESUSPEND (resume caused by user input)
    0x0012,  # PBT_APMRESUMEAUTOMATIC (every resume)
})

WINDOW_STYLE = """
QMainWindow { background: #FFFFFF; }
QWidget { font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI'; font-size: 12px; }
QFrame#topCard {
    background: #FFFFFF; border: none; border-bottom: 1px solid #E2E6ED; border-radius: 0;
}
QFrame#toolsCard, QFrame#selectionCard {
    background: #FFFFFF; border: 1px solid #DCE2EC; border-radius: 12px;
}
QFrame#zoomCard { background: transparent; border: none; }
QLabel { color: #24334A; background: transparent; }
QLabel#fileTitle { font-size: 13px; font-weight: 600; }
QLabel#fileStatus { font-size: 10px; color: #78869A; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 8px; color: #34435B; padding: 0; }
QToolButton:hover { background: #F1F4F9; border-color: #E3E8F1; }
QToolButton:pressed { background: #DCE8FF; }
QToolButton:checked { background: #EAF0FF; border: 2px solid #7195ED; }
QToolButton:disabled { color: #CBD2DF; }
QToolButton::menu-indicator { image: none; }
QPushButton { background: #F3F6FB; color: #34435B; border: 1px solid #E2E9F3; border-radius: 9px; min-height: 42px; padding: 0 12px; }
QPushButton:hover { background: #EAF0FD; }
QPushButton#zoomLabel { background: transparent; border: none; padding: 0; min-height: 42px; }
QMenu { background: #FFFFFF; color: #24334A; border: 1px solid #DCE4EF; border-radius: 10px; padding: 7px; menu-scrollable: 1; }
QMenu::item { min-height: 34px; padding: 5px 22px 5px 16px; border-radius: 6px; }
QMenu::item:selected { background: #EAF0FF; color: #2859CC; }
QMenu::item:disabled { color: #A9B3C2; }
QMenu::separator { height: 1px; background: #E9EDF4; margin: 6px 8px; }
QLabel#toast { background: #24334A; color: #FFFFFF; border-radius: 10px; padding: 11px 18px; font-size: 12px; }
QMessageBox { background: #FFFFFF; }
"""


class MainWindow(QMainWindow):
    """A window can be tested without touching user recovery/settings files."""

    def __init__(self, initial_path=None, recovery_enabled=True, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else AppSettings()
        set_language(self.settings.language)
        self.text_bindings = TextBindings()
        self.setWindowTitle(tr('未命名白板 — {p0}', p0=APP_DISPLAY_NAME))
        self.setWindowIcon(tool_icon("brand", size=48))
        self.resize(1280, 800)
        self.setMinimumSize(720, 560)
        self.setStyleSheet(WINDOW_STYLE)
        self.scene = Scene(parent=self)
        self.current_path: Path | None = None
        self._external_dirty = False
        self._loading = False
        self._initial_path = initial_path
        self._tool = ""
        self._active_pen = 0
        self._recovery_blocked = False
        self._recovery_saved = False
        self._recovery_error = False
        self._startup_done = False
        self._closing = False
        self._popover = None
        self._normal_geometry = None
        self.recovery: RecoveryManager | None = None
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(350)
        self._settings_timer.timeout.connect(self._save_settings)

        self.host = QWidget()
        self.host.setObjectName("canvasHost")
        self.host.installEventFilter(self)
        self.setCentralWidget(self.host)
        host_layout = QVBoxLayout(self.host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = Canvas(self.scene, self.host)
        host_layout.addWidget(self.canvas)
        self.native_tail_eraser = NativeTailEraser(self, self.canvas)
        self._create_actions()
        self._build_top_bar()
        self._build_tools()
        self._build_zoom()
        self._build_selection()
        self.diagnostics = DiagnosticsPanel(self.host)
        self.diagnostics.closed.connect(lambda: self.diagnostics_action.setChecked(False))
        self.diagnostics.export_requested.connect(self._export_input_diagnostics)
        self.diagnostics.hide()
        self.toast_label = QLabel(self.host)
        self.toast_label.setObjectName("toast")
        self.toast_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toast_label.setTextFormat(Qt.TextFormat.PlainText)
        self.toast_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._toast_text = ""
        self.toast_label.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_label.hide)

        self.canvas.view_changed.connect(self._on_view_changed)
        self.canvas.selection_changed.connect(self._on_selection_changed)
        self.canvas.diagnostics_changed.connect(self.diagnostics.update_values)
        self.canvas.tool_override_changed.connect(self._on_tool_override)
        self.canvas.edit_in_progress.connect(self._on_document_changed)
        self.scene.changed.connect(self._on_document_changed)
        self.scene.undo_stack.cleanChanged.connect(self._update_title)
        self.scene.undo_stack.canUndoChanged.connect(self.undo_button.setEnabled)
        self.scene.undo_stack.canRedoChanged.connect(self.redo_button.setEnabled)
        self.undo_button.setEnabled(False)
        self.redo_button.setEnabled(False)

        if recovery_enabled:
            self.recovery = RecoveryManager(self._snapshot, parent=self)
            self._recovery_blocked = self.recovery.available()
            self.recovery.error.connect(self._on_recovery_error)
            self.recovery.saved.connect(self._on_recovery_saved)
        self._select_pen(0)
        self.canvas.set_eraser(self.settings.eraser_radius, self.settings.eraser_whole)
        self._refresh_recent_menu()
        self._update_title()
        QTimer.singleShot(0, self._startup)

    @property
    def is_dirty(self) -> bool:
        return self._external_dirty or self.canvas.has_active_edit or not self.scene.undo_stack.isClean()

    def nativeEvent(self, event_type, message):
        # QWidget's Windows native callback provides MSG*, whose pointer-sized
        # fields are supplied by ctypes.wintypes rather than hand-packed offsets.
        # Keep this synchronous: queued pointer samples after wake must not join
        # an unfinished pre-suspend gesture. Do not flush disk I/O in WndProc.
        # https://doc.qt.io/qt-6/qwidget.html#nativeEvent
        # Shiboken VoidPtr can have a valid address but size zero, and thus be
        # false in a bool check. Test its address, not its truth value.
        if (sys.platform == "win32" and bytes(event_type) == b"windows_generic_MSG"
                and message is not None and int(message) != 0):
            native = wintypes.MSG.from_address(int(message))
            tail_input = getattr(self, "native_tail_eraser", None)
            if tail_input is not None:
                # Observe the complete Windows stream before Qt translates it.
                # Qt still needs DOWN/UPDATE/UP to maintain device/button state;
                # Canvas suppresses duplicate editing while the tail owns it.
                tail_input.handle_message(native)
            if native.message == WM_POWERBROADCAST and native.wParam in _INPUT_RESET_POWER_EVENTS:
                canvas = getattr(self, "canvas", None)
                if canvas is not None:
                    canvas.cancel_input()
                    # Windows requires TRUE after handling WM_POWERBROADCAST.
                    return True, 1
        # False explicitly returns the message to Qt for normal processing.
        return False, 0

    def _action(self, text, callback, shortcuts=(), checkable=False, text_values=None):
        action = QAction(self)
        self.text_bindings.bind(action, "text", text, **(text_values or {}))
        action.setCheckable(checkable)
        if isinstance(shortcuts, (str, QKeySequence.StandardKey)):
            shortcuts = (shortcuts,)
        if shortcuts:
            action.setShortcuts([QKeySequence(value) for value in shortcuts])
        action.triggered.connect(callback)
        self.addAction(action)
        return action

    def _create_actions(self):
        self.new_action = self._action('新建白板', self.new_document, "Ctrl+N")
        self.open_action = self._action('打开白板…', self.open_document, "Ctrl+O")
        self.save_action = self._action('保存', self.save_document, "Ctrl+S")
        self.save_as_action = self._action('另存为…', self.save_as_document, "Ctrl+Shift+S")
        self.export_action = self._action('导出…', self.export_document, "Ctrl+Shift+E")
        self.undo_action = self._action('撤销', self._undo, "Ctrl+Z")
        self.redo_action = self._action('重做', self._redo, ("Ctrl+Y", "Ctrl+Shift+Z"))
        self.delete_action = self._action('删除选中笔迹', self.canvas.delete_selection, ("Delete", "Backspace"))
        self.delete_action.setEnabled(False)
        self.clear_action = self._action('清空白板…', self.clear_document)
        self.fullscreen_action = self._action('全屏', self.toggle_fullscreen, "F11", True)
        self.diagnostics_action = self._action('输入诊断', lambda checked: None, "F12", True)
        self.diagnostics_action.toggled.connect(self._toggle_diagnostics)
        self.grid_action = self._action('始终显示网格', self.canvas.set_grid_enabled, checkable=True)
        self._action('退出全屏 / 取消选择', self._escape, "Escape")
        self._action('原始大小', self.canvas.reset_zoom, "Ctrl+0")
        self._action('适应全部笔迹', self.canvas.fit_all, "Ctrl+1")
        self._action('放大', lambda: self.canvas.zoom_by(1.2), ("Ctrl++", "Ctrl+="))
        self._action('缩小', lambda: self.canvas.zoom_by(1 / 1.2), "Ctrl+-")
        self._action('荧光笔', lambda: self._select_tool("highlighter"), "H")
        self._action('橡皮擦', lambda: self._select_tool("eraser"), "E")
        self._action('套索选择', lambda: self._select_tool("lasso"), "L")
        self._action('移动画布', lambda: self._select_tool("pan"), "V")
        self._action('画笔', lambda: self._select_pen(self._active_pen), "P")
        for index in range(6):
            self._action('画笔 {p0}', lambda checked=False, i=index: self._select_pen(i), str(index + 1), text_values={'p0': index + 1})
        self.scene.undo_stack.canUndoChanged.connect(self.undo_action.setEnabled)
        self.scene.undo_stack.canRedoChanged.connect(self.redo_action.setEnabled)
        self.undo_action.setEnabled(False)
        self.redo_action.setEnabled(False)

    def _card(self, name, margins=(10, 8, 10, 8), spacing=4):
        card = QFrame(self.host)
        card.setObjectName(name)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(*margins)
        layout.setSpacing(spacing)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 5)
        from PySide6.QtGui import QColor
        shadow.setColor(QColor(37, 59, 95, 20))
        card.setGraphicsEffect(shadow)
        return card, layout

    def _button(self, icon_name, tooltip, callback=None, *, checkable=False, color=None, width=44, text_values=None):
        button = QToolButton()
        button.setFixedSize(width, 44)
        button.setIconSize(QSize(27, 27))
        button.setIcon(tool_icon(icon_name, color or "#34435B"))
        self.text_bindings.bind(button, "toolTip", tooltip, **(text_values or {}))
        self.text_bindings.bind(button, "accessibleName", tooltip, **(text_values or {}))
        button.setCheckable(checkable)
        if callback is not None:
            button.clicked.connect(callback)
        return button

    def _separator(self):
        line = QFrame()
        line.setFixedSize(1, 27)
        line.setStyleSheet("background: #E7ECF4; border: none;")
        return line

    def _build_top_bar(self):
        # The fixed header reserves canvas space and keeps all document/view
        # commands on one horizontal baseline, including on a portrait Surface.
        self.top_card = QFrame(self.host)
        self.top_card.setObjectName("topCard")
        self.top_card.setFixedHeight(56)
        self.host.layout().setContentsMargins(0, 56, 0, 0)
        layout = QHBoxLayout(self.top_card)
        layout.setContentsMargins(10, 5, 10, 6)
        layout.setSpacing(6)
        self.menu_button = self._button("menu", '菜单')
        self.menu_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.menu_button.setIconSize(QSize(21, 21))
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        layout.addWidget(self.menu_button)
        layout.addWidget(self._separator())
        title_column = QVBoxLayout()
        title_column.setSpacing(0)
        self.file_title = QLabel(tr('未命名白板'))
        self.file_title.setObjectName("fileTitle")
        self.file_title.setMinimumWidth(90)
        self.file_title.setMaximumWidth(340)
        self.file_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.file_status = QLabel()
        self.file_status.setObjectName("fileStatus")
        self.file_status.setMinimumWidth(0)
        self.file_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title_column.addWidget(self.file_title)
        title_column.addWidget(self.file_status)
        layout.addLayout(title_column)
        layout.addStretch(1)
        self.undo_button = self._button("undo", '撤销 · Ctrl+Z', self._undo)
        self.redo_button = self._button("redo", '重做 · Ctrl+Y', self._redo)
        self.undo_button.setIconSize(QSize(23, 23))
        self.redo_button.setIconSize(QSize(23, 23))
        layout.addWidget(self.undo_button)
        layout.addWidget(self.redo_button)
        layout.addWidget(self._separator())

        # Retain named controls for callers; file actions live in the menu at
        # every window width, so resizing never changes how they are reached.
        self.brand_label = QLabel(self.top_card)
        self.offline_badge = QLabel(self.top_card)
        self.new_button = self._button("new", '新建白板 · Ctrl+N', self.new_document)
        self.open_button = self._button("open", '打开白板 · Ctrl+O', self.open_document)
        for control in (self.brand_label, self.offline_badge, self.new_button, self.open_button):
            control.setParent(self.top_card)
            control.hide()
        self.main_menu = QMenu(self)
        self.main_menu.addAction(self.new_action)
        self.main_menu.addAction(self.open_action)
        self.main_menu.addAction(self.save_action)
        self.main_menu.addAction(self.save_as_action)
        self.main_menu.addAction(self.export_action)
        self.recent_menu = self.main_menu.addMenu('')
        self.text_bindings.bind(self.recent_menu, 'title', '最近打开')
        self.recent_menu.setToolTipsVisible(True)
        self.recent_menu.aboutToShow.connect(self._refresh_recent_menu)
        self.main_menu.addSeparator()
        self.main_menu.addAction(self.clear_action)
        self.recovery_action = self._action('恢复上次未保存白板…', self._show_recovery)
        self.main_menu.addAction(self.recovery_action)
        self.main_menu.addSeparator()
        self.main_menu.addAction(self.grid_action)
        self.main_menu.addAction(self.fullscreen_action)
        self.main_menu.addAction(self.diagnostics_action)
        self.main_menu.addSeparator()
        self.main_menu.addAction(self._action('快捷键与操作', self._show_help))
        self._build_language_menu()
        self.menu_button.setMenu(self.main_menu)

    def _build_language_menu(self):
        menu = self.main_menu.addMenu("")
        self.text_bindings.bind(menu, "title", "语言 / Language")
        self.language_group = QActionGroup(self)
        self.language_group.setExclusive(True)
        self.language_actions = {}
        for code, name in LANGUAGES.items():
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(code == self.settings.language)
            self.language_group.addAction(action)
            action.triggered.connect(lambda checked=False, value=code: self.change_language(value))
            self.language_actions[code] = action

    def change_language(self, code):
        if code not in LANGUAGES or code == self.settings.language:
            return
        if self._popover is not None:
            try:
                self._popover.close()
            except RuntimeError:
                pass  # WA_DeleteOnClose may already have deleted the popup.
            self._popover = None
        self.settings.language = code
        set_language(code)
        self.text_bindings.refresh()
        self.diagnostics.retranslate()
        self.language_actions[code].setChecked(True)
        self._refresh_recent_menu()
        self._on_selection_changed(len(self.canvas.selection_ids))
        self._update_title()
        self._toast(tr('语言已切换'))
        self._save_settings()

    def _build_tools(self):
        self.tools_card, layout = self._card("toolsCard", (8, 7, 8, 7), 5)
        self.tools_card.graphicsEffect().setBlurRadius(16)
        self.tools_card.graphicsEffect().setOffset(0, 3)
        self.pen_buttons = []
        for index, brush in enumerate(self.settings.pens):
            button = self._button(
                "pen", '画笔 {p0} · {p1}\n再次点按设置颜色、粗细与压感',
                lambda checked=False, i=index: self._pen_clicked(i),
                checkable=True, color=brush.color, text_values={'p0': index + 1, 'p1': index + 1})
            self.pen_buttons.append(button)
            layout.addWidget(button)
        layout.addSpacing(4)
        layout.addWidget(self._separator())
        layout.addSpacing(4)
        self.tool_buttons = {}
        specs = (
            ("highlighter", '荧光笔 · H\n再次点按设置颜色与粗细'),
            ("eraser", '橡皮擦 · E\n再次点按设置擦除方式与大小'),
            ("lasso", '套索选择 · L\n圈选笔迹后拖动，Delete 删除'),
            ("pan", '移动画布 · V\n也可用手指移动、双指缩放'),
        )
        for name, tooltip in specs:
            button = self._button(name, tooltip, lambda checked=False, n=name: self._tool_clicked(n),
                                  checkable=True,
                                  color=self.settings.highlighter.color if name == "highlighter" else None)
            self.tool_buttons[name] = button
            layout.addWidget(button)
        self.tools_card.setFixedSize(self.tools_card.sizeHint().width(), 60)

    def _build_zoom(self):
        self.zoom_card = QFrame(self.top_card)
        self.zoom_card.setObjectName("zoomCard")
        layout = QHBoxLayout(self.zoom_card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._button("minus", '缩小 · Ctrl+-', lambda: self.canvas.zoom_by(1 / 1.2)))
        self.zoom_label = QPushButton("100%")
        self.zoom_label.setObjectName("zoomLabel")
        self.zoom_label.setFixedSize(54, 44)
        self.text_bindings.bind(self.zoom_label, 'toolTip', '恢复 100% · Ctrl+0')
        self.zoom_label.clicked.connect(self.canvas.reset_zoom)
        layout.addWidget(self.zoom_label)
        layout.addWidget(self._button("plus", '放大 · Ctrl++', lambda: self.canvas.zoom_by(1.2)))
        layout.addWidget(self._button("fit", '适应全部笔迹 · Ctrl+1', self.canvas.fit_all))
        self.zoom_card.setFixedSize(186, 44)
        self.top_card.layout().addWidget(self.zoom_card)

    def _build_selection(self):
        self.selection_card, layout = self._card("selectionCard", (12, 3, 6, 3), 8)
        self.selection_label = QLabel()
        layout.addWidget(self.selection_label)
        delete = self.text_bindings.bind(QPushButton(), 'text', '删除')
        self.text_bindings.bind(delete, 'toolTip', '删除选中笔迹 · Delete')
        delete.clicked.connect(self.canvas.delete_selection)
        layout.addWidget(delete)
        self.selection_card.setFixedHeight(52)
        self.selection_card.hide()

    def eventFilter(self, watched, event):
        if watched is self.host and event.type() == QEvent.Type.Resize:
            self._layout_overlays()
        return super().eventFilter(watched, event)

    def _layout_overlays(self):
        if not hasattr(self, "toast_label"):
            return
        width, height = self.host.width(), self.host.height()
        self.top_card.setGeometry(0, 0, width, 56)
        self.file_title.setFixedWidth(min(340, max(90, width - 458)))
        self.file_status.setMaximumWidth(self.file_title.maximumWidth())
        self.top_card.layout().activate()
        self._update_title()
        self.tools_card.move((width - self.tools_card.width()) // 2, height - 76)
        # Floating panels are outside the host layout; explicitly size them
        # after translation instead of retaining QFrame's initial 30 px height.
        self.diagnostics.adjustSize()
        self.diagnostics.move(16, 68)
        self.selection_card.adjustSize()
        self.selection_card.move(width - self.selection_card.width() - 16, 68)
        if self.toast_label.isVisible():
            self._layout_toast()
        for overlay in (self.top_card, self.tools_card, self.zoom_card,
                        self.diagnostics, self.selection_card, self.toast_label):
            overlay.raise_()

    def _startup(self):
        if self._startup_done:
            return
        self._startup_done = True
        self._layout_overlays()
        if self.recovery and self.recovery.available():
            decision = self._recovery_prompt()
            if decision in ("restored", "cancel"):
                self._initial_path = None
                return
        path, self._initial_path = self._initial_path, None
        if path:
            self._load_path(Path(path))
        if getattr(self.settings, "load_error", None):
            self._toast(self.settings.load_error, 7000)
        self.canvas.setFocus()

    def _select_pen(self, index):
        self.canvas.finish_interaction()
        self._active_pen = index
        self._tool = "pen"
        brush = replace(self.settings.pens[index], pressure_enabled=self.settings.pressure_enabled,
                        sensitivity=self.settings.sensitivity)
        self.canvas.set_tool("pen")
        self.canvas.set_brush(brush)
        self._refresh_tool_checks()
        self.canvas.setFocus()

    def _select_tool(self, name):
        self.canvas.finish_interaction()
        self._tool = name
        self.canvas.set_tool(name)
        if name == "highlighter":
            self.canvas.set_brush(replace(self.settings.highlighter, pressure_enabled=False))
        elif name == "eraser":
            self.canvas.set_eraser(self.settings.eraser_radius, self.settings.eraser_whole)
        self._refresh_tool_checks()
        self.canvas.setFocus()

    def _refresh_tool_checks(self):
        # Selection represents the user's choice. Pen-tail/barrel overrides
        # affect Canvas input and cursor only, not the persistent tool buttons.
        tool = self._tool
        for index, button in enumerate(self.pen_buttons):
            button.setChecked(tool == "pen" and index == self._active_pen)
        for name, button in self.tool_buttons.items():
            button.setChecked(tool == name)

    def _pen_clicked(self, index):
        repeated = self._tool == "pen" and self._active_pen == index
        self._select_pen(index)
        if repeated:
            self._show_brush_popover(index)

    def _tool_clicked(self, name):
        repeated = self._tool == name
        self._select_tool(name)
        if repeated and name == "highlighter":
            self._show_brush_popover(None)
        elif repeated and name == "eraser":
            self._show_eraser_popover()

    def _show_popup(self, popup, anchor):
        if self._popover is not None:
            try:
                self._popover.close()
            except RuntimeError:
                pass
        self._popover = popup
        popup.adjustSize()
        point = anchor.mapToGlobal(QPoint(anchor.width() // 2, 0))
        screen = anchor.screen().availableGeometry()
        x = min(max(point.x() - popup.width() // 2, screen.left() + 8), screen.right() - popup.width() - 8)
        y = max(screen.top() + 8, point.y() - popup.height() - 12)
        popup.move(x, y)
        popup.show()

    def _show_brush_popover(self, index):
        brush = self.settings.highlighter if index is None else self.settings.pens[index]
        popup = BrushPopover(brush, self.settings.pressure_enabled, self.settings.sensitivity, self)
        popup.brush_changed.connect(lambda updated, i=index: self._change_brush(i, updated))
        popup.pressure_changed.connect(self._change_pressure)
        anchor = self.tool_buttons["highlighter"] if index is None else self.pen_buttons[index]
        self._show_popup(popup, anchor)

    def _show_eraser_popover(self):
        popup = EraserPopover(self.settings.eraser_radius, self.settings.eraser_whole, self)
        popup.settings_changed.connect(self._change_eraser)
        self._show_popup(popup, self.tool_buttons["eraser"])

    def _change_brush(self, index, brush: Brush):
        if index is None:
            self.settings.highlighter = replace(brush, pressure_enabled=False)
            self.tool_buttons["highlighter"].setIcon(tool_icon("highlighter", brush.color))
            if self._tool == "highlighter":
                self.canvas.set_brush(self.settings.highlighter)
        else:
            self.settings.pens[index] = replace(brush)
            self.pen_buttons[index].setIcon(tool_icon("pen", brush.color))
            if self._tool == "pen" and self._active_pen == index:
                self.canvas.set_brush(replace(brush, pressure_enabled=self.settings.pressure_enabled,
                                             sensitivity=self.settings.sensitivity))
        self._settings_timer.start()

    def _change_pressure(self, enabled, sensitivity):
        self.settings.pressure_enabled = enabled
        self.settings.sensitivity = sensitivity
        for brush in self.settings.pens:
            brush.pressure_enabled, brush.sensitivity = enabled, sensitivity
        if self._tool == "pen":
            self.canvas.set_brush(replace(self.settings.pens[self._active_pen]))
        self._settings_timer.start()

    def _change_eraser(self, radius, whole):
        self.settings.eraser_radius, self.settings.eraser_whole = radius, whole
        self.canvas.set_eraser(radius, whole)
        self._settings_timer.start()

    def _save_settings(self):
        self._settings_timer.stop()
        try:
            self.settings.save()
        except (DocumentError, OSError) as exc:
            self._toast(str(exc), 7000)

    def _on_tool_override(self, _tool):
        self._refresh_tool_checks()

    def _on_view_changed(self, scale):
        self.zoom_label.setText(f"{scale * 100:.0f}%")
        if self.recovery and not self._loading and not self._recovery_blocked and self.is_dirty:
            self.recovery.schedule()

    def _on_selection_changed(self, count):
        self.delete_action.setEnabled(bool(count))
        self.selection_label.setText(tr('已选择 {p0} 笔', p0=count))
        self.selection_card.setVisible(bool(count))
        self._layout_overlays()

    def _on_document_changed(self, *_):
        if not self._loading:
            self._recovery_saved = False
            if self.recovery and not self._recovery_blocked:
                self.recovery.schedule()
        self._update_title()

    def _update_title(self, *_):
        name = self.current_path.name if self.current_path else tr('未命名白板')
        suffix = " *" if self.is_dirty else ""
        self.setWindowTitle(f"{name}{suffix} — {APP_DISPLAY_NAME}")
        self.file_title.setText(self.file_title.fontMetrics().elidedText(
            name + suffix, Qt.TextElideMode.ElideMiddle, self.file_title.maximumWidth()))
        self.file_title.setToolTip(str(self.current_path) if self.current_path else tr('未命名白板'))
        if self._recovery_blocked:
            status = tr('上次草稿已保留 · 自动恢复暂停')
        elif self._recovery_error:
            status = tr('恢复副本保存失败 · 请手动保存')
        elif self.is_dirty:
            status = tr('未保存 · 恢复副本已更新') if self._recovery_saved else tr('有未保存的更改')
        elif self.current_path:
            status = tr('已保存')
        else:
            status = ""
        self.file_status.setText(self.file_status.fontMetrics().elidedText(
            status, Qt.TextElideMode.ElideRight, self.file_status.maximumWidth()))
        self.file_status.setToolTip(status)
        self.file_status.setAccessibleName(status)
        self.file_status.setVisible(bool(status))
        self.clear_action.setEnabled(bool(self.scene.document.strokes))
        self.export_action.setEnabled(bool(self.scene.document.strokes))
        self.recovery_action.setVisible(self._recovery_blocked)

    def _snapshot(self):
        self.canvas.sync_view_to_document()
        return self.canvas.snapshot_document()

    def _on_recovery_saved(self):
        self._recovery_saved = True
        self._recovery_error = False
        self._update_title()

    def _on_recovery_error(self, message):
        self._recovery_error = True
        self._update_title()
        self._toast(message, 8000)

    def _discard_recovery(self):
        if self.recovery is None or self._recovery_blocked:
            return True
        try:
            self.recovery.discard()
            self.recovery.source_path = str(self.current_path) if self.current_path else None
            self._recovery_saved = False
            self._recovery_error = False
            return True
        except DocumentError as exc:
            self._on_recovery_error(str(exc))
            return False

    def _maybe_leave_document(self):
        self.canvas.finish_interaction()
        if not self.is_dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle(tr('保存白板'))
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(tr('要保存对白板的更改吗？'))
        box.setInformativeText(tr('保存后可在本机重新打开并继续编辑。'))
        save = box.addButton(tr('保存'), QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton(tr('不保存'), QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(tr('取消'), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.setEscapeButton(cancel)
        box.exec()
        if box.clickedButton() is save:
            return self.save_document()
        if box.clickedButton() is discard:
            return True
        return False

    def new_document(self, *_):
        if not self._maybe_leave_document():
            return False
        if not self._discard_recovery():
            return False
        self._replace_document(BoardDocument(), None)
        self._toast(tr('已新建白板'))
        return True

    def open_document(self, *_):
        path, _ = QFileDialog.getOpenFileName(self, tr('打开白板'), self._default_directory(), file_filter())
        if not path:
            return False
        return self._load_path(Path(path))

    def _load_path(self, path: Path):
        # Parse first: a corrupt/missing target must never make the user abandon
        # their dirty current document or remove its recovery file.
        try:
            document = load_document(path)
        except (DocumentError, OSError) as exc:
            self._show_error(tr('无法打开白板'), str(exc))
            return False
        was_dirty = self.is_dirty
        if not self._maybe_leave_document():
            return False
        # Saving the current document during this prompt can change the same
        # file that was validated above. Reload it before replacing the canvas.
        if was_dirty and not self.is_dirty and self.current_path == path.resolve():
            try:
                document = load_document(path)
            except (DocumentError, OSError) as exc:
                self._show_error(tr('无法打开白板'), str(exc))
                return False
        if not self._discard_recovery():
            return False
        self._replace_document(document, path.resolve())
        self._remember_file(path)
        self._toast(tr('白板已打开'))
        return True

    def _replace_document(self, document, path, *, recovered=False):
        self._loading = True
        try:
            self.canvas.clear_selection()
            self.scene.reset(document)
            self.canvas.load_view_from_document()
            self.current_path = Path(path) if path else None
            self._external_dirty = recovered
            self.scene.undo_stack.setClean()
            self._recovery_saved = recovered
            self._recovery_error = False
            if self.recovery:
                self.recovery.source_path = str(self.current_path) if self.current_path else None
        finally:
            self._loading = False
        self._update_title()
        self.canvas.setFocus()

    def save_document(self, *_):
        self.canvas.finish_interaction()
        if self.current_path is None:
            return self.save_as_document()
        return self._save_path(self.current_path)

    def save_as_document(self, *_):
        self.canvas.finish_interaction()
        suggested = str(self.current_path) if self.current_path else str(Path(self._default_directory()) / tr('白板.qboard'))
        path, _ = QFileDialog.getSaveFileName(self, tr('保存白板'), suggested, tr('{p0} 白板 (*.qboard)', p0=APP_DISPLAY_NAME))
        if not path:
            return False
        target = self._normalized_save_target(path, ".qboard")
        if target is None:
            return False
        return self._save_path(target)

    def _save_path(self, path):
        if self._is_reserved_recovery_path(path):
            self._show_error(tr('请选择其他保存位置'), tr('此路径保留用于应用的自动恢复副本。\n请另存为其他位置的 .qboard 文件。'))
            return False
        self.canvas.sync_view_to_document()
        try:
            save_document(path, self.scene.document)
        except (DocumentError, OSError) as exc:
            self._show_error(tr('无法保存白板'), str(exc))
            return False
        self.current_path = Path(path).resolve()
        self._external_dirty = False
        self.scene.undo_stack.setClean()
        self._remember_file(self.current_path)
        self._discard_recovery()
        if self.recovery:
            self.recovery.source_path = str(self.current_path)
        self._update_title()
        self._toast(tr('已保存到本机'))
        return True

    def export_document(self, *_):
        return self._export_current_view("png", choose_format=True)

    def export_png(self, *_):
        """Compatibility entry point for callers explicitly requesting PNG."""
        return self._export_current_view("png", choose_format=False)

    def export_pdf(self, *_):
        return self._export_current_view("pdf", choose_format=False)

    def _export_current_view(self, default_format, choose_format):
        from .exporting import export_pdf, export_png

        self.canvas.finish_interaction()
        if not self.scene.document.strokes:
            self._toast(tr('白板还是空的，写几笔后再导出'))
            return False
        filters = {"png": tr('PNG 图片 (*.png)'), "pdf": tr('PDF 文档 (*.pdf)')}
        filename = (self.current_path.stem if self.current_path else tr('白板')) + "." + default_format
        path, selected_filter = QFileDialog.getSaveFileName(
            self, tr('导出当前视图'), str(Path(self._default_directory()) / filename),
            ";;".join(filters.values()) if choose_format else filters[default_format],
            filters[default_format], options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return False
        # The chosen file type controls the encoder and matching extension.
        # Replace a prior PNG/PDF extension when the type changes; preserve
        # an unrelated user-supplied suffix by appending the selected extension.
        file_format = "pdf" if choose_format and "*.pdf" in selected_filter.lower() else default_format
        target = Path(path)
        suffix = "." + file_format
        if target.suffix.lower() in {".png", ".pdf"}:
            target = target.with_suffix(suffix)
        elif target.suffix.lower() != suffix:
            target = Path(str(target) + suffix)
        if target.exists():
            box = QMessageBox(self)
            box.setWindowTitle(tr('替换现有文件？'))
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(tr('文件“{p0}”已存在。', p0=target.name))
            box.setInformativeText(tr('导出位置：\n{p0}\n\n是否替换该文件？', p0=target))
            replace_button = box.addButton(tr('替换'), QMessageBox.ButtonRole.DestructiveRole)
            cancel = box.addButton(tr('取消'), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(cancel)
            box.setEscapeButton(cancel)
            box.exec()
            if box.clickedButton() is not replace_button:
                return False
        try:
            arguments = (target, self.scene, self.canvas.size(), self.canvas.view_scale, self.canvas.view_offset)
            if file_format == "pdf":
                export_pdf(*arguments, title=self.current_path.stem if self.current_path else tr('白板当前视图'))
            else:
                export_png(*arguments, dpr=self.canvas.devicePixelRatioF())
        except (DocumentError, OSError, ValueError, MemoryError) as exc:
            self._show_error(tr('导出失败'), str(exc))
            return False
        self._toast(tr('{p0} 已导出 · 可编辑白板仍需单独保存', p0=file_format.upper()))
        return True

    @staticmethod
    def _canonical_path(path):
        """Resolve aliases and compare Windows path spellings consistently."""
        value = str(Path(path).resolve()).replace("/", "\\")
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return value.casefold()

    def _is_reserved_recovery_path(self, path):
        reserved = self.recovery.path if self.recovery else app_data_directory() / "recovery.qboard"
        if self._canonical_path(path) == self._canonical_path(reserved):
            return True
        try:
            return Path(path).samefile(reserved)
        except (FileNotFoundError, OSError):
            return False

    def _normalized_save_target(self, raw_path, suffix):
        raw = Path(raw_path)
        target = raw if raw.suffix.lower() == suffix else Path(str(raw) + suffix)
        # QFileDialog only confirmed the selected spelling. If we append an
        # extension, an entirely different existing file could be overwritten.
        if target.exists() and self._canonical_path(target) != self._canonical_path(raw):
            box = QMessageBox(self)
            box.setWindowTitle(tr('替换现有文件？'))
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(tr('文件“{p0}”已存在。', p0=target.name))
            box.setInformativeText(tr('添加文件后缀后的保存位置是：\n{p0}\n\n是否替换该文件？', p0=target))
            replace_button = box.addButton(tr('替换'), QMessageBox.ButtonRole.DestructiveRole)
            cancel = box.addButton(tr('取消'), QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(cancel)
            box.setEscapeButton(cancel)
            box.exec()
            if box.clickedButton() is not replace_button:
                return None
        return target

    def clear_document(self, *_):
        self.canvas.finish_interaction()
        if not self.scene.document.strokes:
            return
        result = QMessageBox.question(self, tr('清空白板'), tr('清空当前白板上的全部笔迹？\n此操作可以撤销。'),
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Cancel)
        if result == QMessageBox.StandardButton.Yes:
            self.canvas.clear_selection()
            self.scene.clear()

    def _default_directory(self):
        if self.current_path:
            return str(self.current_path.parent)
        if self.settings.recent_files:
            parent = Path(self.settings.recent_files[0]).parent
            if parent.is_dir():
                return str(parent)
        from PySide6.QtCore import QStandardPaths
        return QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)

    def _remember_file(self, path):
        self.settings.add_recent(path)
        self._save_settings()
        self._refresh_recent_menu()

    def _refresh_recent_menu(self):
        self.recent_menu.clear()
        if not self.settings.recent_files:
            empty = self.recent_menu.addAction(tr('暂无最近文件'))
            empty.setEnabled(False)
            return
        for raw_path in self.settings.recent_files[:10]:
            path = Path(raw_path)
            # Bound long names before Qt lays out the menu. Keep the full path
            # in its tooltip and callback; '&' in a filename is not a mnemonic.
            self.recent_menu.ensurePolished()
            name_width = min(360, self.screen().availableGeometry().width() - 100)
            name = self.recent_menu.fontMetrics().elidedText(
                path.name, Qt.TextElideMode.ElideMiddle, max(80, name_width))
            action = self.recent_menu.addAction(name.replace("&", "&&"))
            action.setToolTip(str(path))
            action.triggered.connect(lambda checked=False, p=path: self._load_path(p))

    def _recovery_prompt(self):
        if self.recovery is None or not self.recovery.available():
            return "none"
        box = QMessageBox(self)
        box.setWindowTitle(tr('发现未保存的白板'))
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(tr('恢复上次未保存的笔迹？'))
        box.setInformativeText(tr('恢复副本仅保存在本机。选择“稍后处理”会保留副本，并暂停新的自动恢复保存。'))
        restore = box.addButton(tr('恢复白板'), QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton(tr('丢弃副本'), QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(tr('稍后处理'), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(restore)
        box.setEscapeButton(cancel)
        box.exec()
        if box.clickedButton() is restore:
            try:
                document = self.recovery.recover()
                source = self.recovery.source_path
            except DocumentError as exc:
                self._show_error(tr('无法恢复白板'), str(exc))
                self._recovery_blocked = True
                self._update_title()
                return "cancel"
            self._recovery_blocked = False
            self._replace_document(document, source, recovered=True)
            self._toast(tr('笔迹已恢复，请保存白板'))
            return "restored"
        if box.clickedButton() is discard:
            try:
                self.recovery.discard()
            except DocumentError as exc:
                self._show_error(tr('无法丢弃恢复副本'), str(exc))
                self._recovery_blocked = True
                self._update_title()
                return "cancel"
            self._recovery_blocked = False
            self.recovery.source_path = str(self.current_path) if self.current_path else None
            if self.is_dirty:
                self.recovery.schedule()
            self._update_title()
            return "discarded"
        self._recovery_blocked = True
        self._update_title()
        return "cancel"

    def _show_recovery(self):
        if not self._maybe_leave_document():
            return
        self._recovery_prompt()

    def _undo(self, *_):
        self.canvas.finish_interaction()
        self.canvas.clear_selection()
        self.scene.undo_stack.undo()

    def _redo(self, *_):
        self.canvas.finish_interaction()
        self.canvas.clear_selection()
        self.scene.undo_stack.redo()

    def toggle_fullscreen(self, checked=None):
        if self.isFullScreen():
            self.showNormal()
            if self._normal_geometry is not None:
                self.restoreGeometry(self._normal_geometry)
            self.fullscreen_action.setChecked(False)
        else:
            self._normal_geometry = self.saveGeometry()
            self.showFullScreen()
            self.fullscreen_action.setChecked(True)

    def _escape(self):
        if self.canvas.selection_ids:
            self.canvas.clear_selection()
        elif self.isFullScreen():
            self.toggle_fullscreen()

    def _toggle_diagnostics(self, enabled):
        if not hasattr(self, "diagnostics"):
            return
        self.diagnostics.setVisible(enabled)
        self.canvas.set_diagnostics_enabled(enabled)
        self._layout_overlays()

    def _export_input_diagnostics(self):
        self.canvas.finish_interaction()
        path, _ = QFileDialog.getSaveFileName(self, tr('导出输入记录'), tr('白板输入记录.json'), "JSON (*.json)")
        if not path:
            return
        try:
            self.canvas.input_diagnostics.export_json(path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, tr('导出失败'), str(error))

    def _show_help(self):
        QMessageBox.information(
            self, tr('快捷键与操作'),
            tr('画笔 1–6\u3000·\u3000荧光笔 H\u3000·\u3000橡皮 E\u3000·\u3000套索 L\u3000·\u3000移动 V\n再次点按当前画笔或橡皮，可调整工具设置。\n\n手指移动画布，双指缩放；也可切换移动工具拖动。\n翻转 Surface Pen 临时擦除，笔靠近时优先书写。\n套索按逻辑整笔选择，局部擦除后的残片一起移动。\n\nCtrl+N 新建\u3000\u3000Ctrl+O 打开\u3000\u3000Ctrl+S 保存\nCtrl+Shift+S 另存为\u3000\u3000Ctrl+Shift+E 导出 PNG / PDF\nCtrl+Z 撤销\u3000\u3000Ctrl+Y 重做\u3000\u3000Delete 删除所选\nCtrl+0 原始大小\u3000\u3000Ctrl+1 适应全部笔迹\nF11 全屏\u3000\u3000F12 输入诊断'))

    def _toast(self, text, duration=3500):
        self._toast_text = str(text)
        self.toast_label.setText(self._toast_text)
        self.toast_label.setToolTip(self._toast_text)
        self.toast_label.setAccessibleName(self._toast_text)
        self.toast_label.setWordWrap(True)
        self.toast_label.show()
        self._layout_overlays()
        self._toast_timer.start(duration)

    def _layout_toast(self):
        label = self.toast_label
        width_limit = max(1, self.host.width() - 64)
        label.setMaximumWidth(width_limit)
        label.ensurePolished()
        margins = label.contentsMargins()
        text_width = max(1, width_limit - margins.left() - margins.right() - 2 * label.margin())

        def fit(text):
            # QLabel only wraps at word boundaries. A single long path/token
            # needs its own elision or it would be silently clipped sideways.
            text = re.sub(r"\S+", lambda match: label.fontMetrics().elidedText(
                match.group(), Qt.TextElideMode.ElideMiddle, text_width), text)
            label.setText(text)
            label.setWordWrap(False)
            width = min(width_limit, label.sizeHint().width())
            label.setWordWrap(True)
            label.resize(width, label.heightForWidth(width))

        fit(self._toast_text)
        bottom = self.tools_card.y() - 16
        top = 68
        for panel in (self.diagnostics, self.selection_card):
            if panel.isVisible():
                top = max(top, panel.geometry().bottom() + 16)
        available_height = max(1, bottom - top)
        if label.height() > available_height:
            # A pathological path/error must not hide the header or toolbar.
            # Keep the complete message available to accessibility clients.
            low, high = 0, len(self._toast_text)
            while low < high:
                middle = (low + high + 1) // 2
                fit(self._toast_text[:middle] + "…")
                if label.height() <= available_height:
                    low = middle
                else:
                    high = middle - 1
            fit(self._toast_text[:low] + "…")
        label.move((self.host.width() - label.width()) // 2, bottom - label.height())

    def _show_error(self, title, message):
        QMessageBox.critical(self, title, message)

    def closeEvent(self, event: QCloseEvent):
        if self._closing:
            event.accept()
            return
        if not self._maybe_leave_document():
            event.ignore()
            return
        # An unresolved old recovery must survive this empty/new window closing.
        if not self._discard_recovery():
            event.ignore()
            return
        self._settings_timer.stop()
        self._save_settings()
        if self.recovery:
            self.recovery.shutdown()
        self._closing = True
        event.accept()
