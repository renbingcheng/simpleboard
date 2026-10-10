"""Pen, touch and mouse input, independent of the application chrome."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
import math
import time
from uuid import uuid4

from PySide6.QtCore import QEvent, QLineF, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF,
    QTabletEvent, QTransform,
)
from PySide6.QtWidgets import QApplication, QWidget

from .geometry import IncrementalStrokeBuilder, erase_path, visible_path
from .input_diagnostics import InputDiagnostics
from .models import BoardImage, Brush, EraseMask, InkSample, Stroke
from .images import (MAX_IMAGE_WORLD_SIZE, MIN_IMAGE_WORLD_SIZE, image_rect,
                     image_size, load_image, validate_images)
from .renderer import ImageRenderer, TileRenderer, paint_stroke


class Canvas(QWidget):
    view_changed = Signal(float)
    selection_changed = Signal(int)
    diagnostics_changed = Signal(dict)
    tool_override_changed = Signal(str)
    edit_in_progress = Signal()

    def __init__(self, scene, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.renderer = TileRenderer(scene)
        self.image_renderer = ImageRenderer()
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setTabletTracking(True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)
        self.view_scale = 1.0
        self.view_offset = QPointF()
        self.tool = "pen"
        self.brush = Brush()
        self.eraser_radius = 12.0
        self.eraser_whole = False
        self.selection_ids: set[str] = set()
        self._interaction = ""
        self._interaction_tool = ""
        self._source = ""
        self._tail_eraser_contact = False
        self._native_tail_claimed = False
        self._native_tail_qt_guard = False
        self.input_boundary_generation = 0
        self._native_candidate_generation = None
        self._builder = None
        self._active_brush = None
        self._active_stroke_id = ""
        self._last_recovery_notify = 0.0
        self._active_layer = QImage()
        self._last_pointer = QPointF(-100, -100)
        self._last_world = QPointF()
        self._last_pressure = 1.0
        self._pointer_inside = False
        self._pen_near = False
        self._suppress_mouse_until = 0.0
        self._temporary_tool = ""
        self._erase_points = []
        self._erase_radius_world = 12.0
        self._erase_processed = 0
        self._erase_preview: set[str] = set()
        self._erase_layer = QImage()
        self._lasso_points: list[QPointF] = []
        self._move_start = QPointF()
        self._move_delta = QPointF()
        self._resize_image_id = ""
        self._resize_rect = QRectF()
        self._resize_original = QRectF()
        self._resize_corner = 0
        self._resize_pointer_offset = QPointF()
        self._pan_start = QPointF()
        self._pan_offset = QPointF()
        self._touch_ids: tuple[int, ...] = ()
        self._touch_blocked = False
        self._touch_anchor = QPointF()
        self._touch_distance = 1.0
        self._touch_scale = 1.0
        self._touch_offset = QPointF()
        self._gesture_image = QImage()
        self._gesture_scale = 1.0
        self._gesture_offset = QPointF()
        self.grid_enabled = False
        self._grid_opacity = 0.0
        self._grid_fade_started = 0.0
        self._grid_timer = QTimer(self)
        self._grid_timer.setSingleShot(True)
        self._grid_timer.setInterval(1200)
        self._grid_timer.timeout.connect(self._start_grid_fade)
        self._grid_fade_timer = QTimer(self)
        self._grid_fade_timer.setInterval(16)
        self._grid_fade_timer.timeout.connect(self._fade_grid)
        self._diagnostics_enabled = False
        self.input_diagnostics = InputDiagnostics()
        self._last_diagnostics = 0.0
        self._paint_ms = deque(maxlen=600)
        self._input_ms = deque(maxlen=600)
        self.scene.changed.connect(self._scene_changed)
        QApplication.instance().installEventFilter(self)
        self.load_view_from_document()

    def screen_to_world(self, position: QPointF) -> QPointF:
        return (position - self.view_offset) / self.view_scale

    def world_to_screen(self, position: QPointF) -> QPointF:
        return position * self.view_scale + self.view_offset

    def _transform(self):
        return QTransform(self.view_scale, 0, 0, self.view_scale,
                          self.view_offset.x(), self.view_offset.y())

    def set_tool(self, tool):
        if tool not in {"pen", "highlighter", "eraser", "lasso", "select", "pan"}:
            raise ValueError("Unknown canvas tool")
        self.finish_interaction()
        self.tool = tool
        if tool in {"lasso", "select"}:
            self._filter_selection(tool)
        elif tool != "pan":
            self.clear_selection()
        self._set_temporary_tool("")
        self._update_cursor()
        self.update()

    def set_brush(self, brush):
        self.finish_interaction()
        self.brush = replace(brush)

    def set_eraser(self, radius, whole):
        self.finish_interaction()
        self.eraser_radius = max(2.0, min(100.0, float(radius)))
        self.eraser_whole = bool(whole)

    def insert_image_path(self, path):
        """Embed pixels and create one undoable image, initially in view."""
        data = load_image(path)
        size = image_size(data)
        factor = min(1.0, max(32, self.width() * 0.65) / size.width(),
                     max(32, self.height() * 0.65) / size.height())
        factor = max(factor, MIN_IMAGE_WORLD_SIZE * self.view_scale / min(size.width(), size.height()))
        width, height = size.width() * factor / self.view_scale, size.height() * factor / self.view_scale
        center = self.screen_to_world(QPointF(self.rect().center()))
        item = BoardImage(png_data=data, x=center.x() - width / 2, y=center.y() - height / 2,
                          width=width, height=height)
        # Validate budget before committing or finishing any active edit.
        validate_images([*self.scene.document.images, item])
        self.finish_interaction()
        self.scene.add_image(item)
        self.selection_ids = {item.id}
        self.selection_changed.emit(1)
        self.update()
        return item.id

    def _selected_image(self):
        if len(self.selection_ids) == 1:
            return self.scene.get_image(next(iter(self.selection_ids)))
        return None

    @staticmethod
    def _image_corners(rect):
        return (rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft())

    def _image_resize_handle(self, position):
        item = self._selected_image()
        if item is not None:
            for index, corner in enumerate(self._image_corners(image_rect(item))):
                delta = self.world_to_screen(corner) - position
                if abs(delta.x()) <= 11 and abs(delta.y()) <= 11:
                    return index
        return None

    def set_diagnostics_enabled(self, enabled):
        self._diagnostics_enabled = bool(enabled)
        self.input_diagnostics.set_enabled(enabled)

    def set_view(self, scale, offset):
        scale = max(0.1, min(8.0, float(scale)))
        changed_scale = not math.isclose(scale, self.view_scale, rel_tol=1e-9)
        self.view_scale = scale
        self.view_offset = QPointF(offset)
        if changed_scale:
            self._show_zoom_grid()
        self.sync_view_to_document()
        self.view_changed.emit(self.view_scale)
        self.update()

    def set_grid_enabled(self, enabled):
        """Persistent-on mode is a view aid, never part of the document."""
        self.grid_enabled = bool(enabled)
        self._grid_timer.stop()
        self._grid_fade_timer.stop()
        self._grid_opacity = 0.0
        self.update()

    def _show_zoom_grid(self):
        self._grid_opacity = 1.0
        self._grid_fade_timer.stop()
        self._grid_timer.start()
        self.update()

    def _start_grid_fade(self):
        self._grid_fade_started = time.monotonic()
        self._grid_fade_timer.start()

    def _fade_grid(self):
        self._grid_opacity = max(0.0, 1.0 - (time.monotonic()-self._grid_fade_started)/0.22)
        if self._grid_opacity == 0.0:
            self._grid_fade_timer.stop()
        self.update()

    def _grid_lines(self):
        # Nested world-space grids keep their origin while panning/zooming.
        # Drop overly dense levels so even 10% zoom has bounded drawing cost.
        step = 40.0
        while step*self.view_scale < 16:
            step *= 5
        while step*self.view_scale > 80:
            step /= 5
        spacing = step*self.view_scale
        minor, major = [], []
        for axis, length, offset in ((0, self.width(), self.view_offset.x()),
                                      (1, self.height(), self.view_offset.y())):
            start = math.ceil(-offset/spacing)
            end = math.floor((length-offset)/spacing)
            for index in range(start, end+1):
                position = offset+index*spacing
                line = (QLineF(position, 0, position, self.height()) if axis == 0 else
                        QLineF(0, position, self.width(), position))
                (major if index % 5 == 0 else minor).append(line)
        return minor, major

    def _paint_grid(self, painter):
        opacity = 1.0 if self.grid_enabled else self._grid_opacity
        if opacity <= 0:
            return
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setOpacity(opacity)
        minor, major = self._grid_lines()
        for lines, color in ((minor, "#EDF0F4"), (major, "#D9E0E9")):
            painter.setPen(QPen(QColor(color), 1.0))
            painter.drawLines(lines)
        painter.restore()

    def zoom_by(self, factor, anchor=None):
        self.finish_interaction()
        anchor = QPointF(anchor) if anchor is not None else QPointF(self.rect().center())
        world_anchor = self.screen_to_world(anchor)
        scale = max(0.1, min(8.0, self.view_scale * factor))
        self.set_view(scale, anchor - world_anchor * scale)

    def reset_zoom(self):
        self.zoom_by(1.0 / self.view_scale)

    def fit_all(self):
        self.finish_interaction()
        bounds = self.scene.bounds()
        if bounds.isEmpty():
            self.set_view(1.0, QPointF())
            return
        scale = max(0.1, min(8.0, min(max(100, self.width() - 160) / bounds.width(),
                                     max(100, self.height() - 220) / bounds.height())))
        self.set_view(scale, QPointF(self.rect().center()) - bounds.center() * scale)

    def sync_view_to_document(self):
        doc = self.scene.document
        doc.view_scale = self.view_scale
        doc.view_offset_x = self.view_offset.x()
        doc.view_offset_y = self.view_offset.y()

    @property
    def has_active_ink(self):
        return self._interaction == "ink" and self._builder is not None and bool(self._builder.samples)

    @property
    def has_active_edit(self):
        return self.has_active_ink or self._interaction in {"erase", "move", "resize-image"}

    def snapshot_document(self):
        """Capture the visible edit for recovery without splitting its undo step."""
        self.sync_view_to_document()
        strokes = list(self.scene.document.strokes)
        images = list(self.scene.document.images)
        if self.has_active_ink:
            strokes.append(Stroke(samples=list(self._builder.samples), brush=replace(self._active_brush),
                                  id=self._active_stroke_id))
        elif self._interaction == "erase":
            self._update_erase_preview()
            captured = []
            for stroke in strokes:
                if stroke.id in self._erase_preview:
                    if self.eraser_whole:
                        continue
                    mask = EraseMask([(x-stroke.offset_x, y-stroke.offset_y)
                                      for x, y in self._erase_points], self._erase_radius_world)
                    stroke = replace(stroke, erase_masks=[*stroke.erase_masks, mask])
                captured.append(stroke)
            strokes = captured
        elif self._interaction == "move":
            strokes = [replace(s, offset_x=s.offset_x+self._move_delta.x(),
                               offset_y=s.offset_y+self._move_delta.y())
                       if s.id in self.selection_ids else s for s in strokes]
            images = [replace(item, x=item.x + self._move_delta.x(), y=item.y + self._move_delta.y())
                      if item.id in self.selection_ids else item for item in images]
        elif self._interaction == "resize-image":
            rect = self._resize_rect
            images = [replace(item, x=rect.x(), y=rect.y(), width=rect.width(), height=rect.height())
                      if item.id == self._resize_image_id else item for item in images]
        return replace(self.scene.document, strokes=strokes, images=images)

    def load_view_from_document(self):
        self.finish_interaction()
        doc = self.scene.document
        self.set_view(doc.view_scale, QPointF(doc.view_offset_x, doc.view_offset_y))
        self.image_renderer.clear()
        self.clear_selection()

    def clear_selection(self):
        if self.selection_ids:
            self.selection_ids.clear()
            self.selection_changed.emit(0)
            self.update()

    def delete_selection(self):
        self.finish_interaction()
        if self.selection_ids:
            self.scene.delete_items(set(self.selection_ids))
            self.clear_selection()

    def _filter_selection(self, tool):
        """Keep image editing separate from ink, including temporary pen tools."""
        lookup = self.scene.get_image if tool == "select" else self.scene.get
        selected = {sid for sid in self.selection_ids if lookup(sid) is not None}
        if selected != self.selection_ids:
            self.selection_ids = selected
            self.selection_changed.emit(len(selected))
            self.update()

    def _selection_bounds(self):
        bounds = QRectF()
        for sid in self.selection_ids:
            stroke = self.scene.get(sid)
            if stroke is not None:
                bounds = bounds.united(visible_path(stroke).boundingRect())
            else:
                item = self.scene.get_image(sid)
                if item is not None:
                    bounds = bounds.united(image_rect(item))
        return bounds

    def _scene_changed(self, rect):
        if self.selection_ids:
            alive = {sid for sid in self.selection_ids
                     if self.scene.get(sid) is not None or self.scene.get_image(sid) is not None}
            if alive != self.selection_ids:
                self.selection_ids = alive
                self.selection_changed.emit(len(alive))
        if rect is None:
            self.update()
        else:
            self.update(self._transform().mapRect(rect).adjusted(-12, -12, 12, 12).toAlignedRect())

    def _new_layer(self):
        dpr = self.devicePixelRatioF()
        image = QImage(max(1, math.ceil(self.width() * dpr)),
                       max(1, math.ceil(self.height() * dpr)), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        return image

    def _append_sample(self, position, pressure, timestamp, tilt_x=0, tilt_y=0):
        if self._builder is None:
            return
        point = self.screen_to_world(position)
        if self._builder.samples:
            last = self._builder.samples[-1]
            if (math.hypot(point.x() - last.x, point.y() - last.y) < 0.025
                    and abs(pressure-last.pressure) < 0.005
                    and not self._builder.needs_filter_update(point.x(), point.y(), pressure)):
                return
        sample = InkSample(point.x(), point.y(), float(timestamp), float(pressure), float(tilt_x), float(tilt_y))
        affected = self._builder.add(sample)
        if affected.isEmpty():
            self._notify_active_edit()
            return
        painter = QPainter(self._active_layer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setTransform(self._transform())
        color = QColor(self._active_brush.color)
        color.setAlpha(255)
        painter.fillPath(self._builder.last_segment, color)
        painter.end()
        self.update(self._transform().mapRect(affected).adjusted(-3, -3, 3, 3).toAlignedRect())
        self._notify_active_edit()

    def _notify_active_edit(self):
        now = time.monotonic()
        if now - self._last_recovery_notify >= 0.5:
            self._last_recovery_notify = now
            self.edit_in_progress.emit()

    def _begin(self, position, source, tool, pressure=1.0, timestamp=0, tilt_x=0, tilt_y=0):
        self.finish_interaction(preserve_native_candidate=True)
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._source = source
        self._interaction_tool = tool
        self._last_recovery_notify = 0.0
        self._last_world = self.screen_to_world(position)
        if tool in {"pen", "highlighter", "lasso", "select"}:
            # Barrel-button overrides bypass set_tool. Filter at contact start
            # too, so lasso/ink can never drag a selected image.
            self._filter_selection(tool)
        if tool == "select":
            handle = self._image_resize_handle(position)
            if handle is not None:
                item = self._selected_image()
                self._interaction = "resize-image"
                self._resize_image_id = item.id
                self._resize_rect = self._resize_original = image_rect(item)
                self._resize_corner = handle
                self._resize_pointer_offset = self._last_world - self._image_corners(self._resize_original)[handle]
                self._notify_active_edit()
                return
        if (tool in {"pen", "highlighter", "lasso"} and self.selection_ids
                and self._selection_bounds().adjusted(-6/self.view_scale, -6/self.view_scale,
                                                       6/self.view_scale, 6/self.view_scale).contains(self._last_world)):
            # A barrel-button lasso returns to the pen after release. The active
            # selection must still be draggable with the tip without drawing on it.
            self._interaction = "move"
            self._move_start = QPointF(self._last_world)
            self._move_delta = QPointF()
            self._notify_active_edit()
            return
        if tool == "select":
            # Only the pointer tool can hit images. Ignore the ink above them
            # and choose the topmost image at the actual contact position.
            candidates = self.scene.query_images(QRectF(self._last_world.x() - 0.1,
                                                        self._last_world.y() - 0.1, 0.2, 0.2))
            for item in reversed(candidates):
                if image_rect(item).contains(self._last_world):
                    self.selection_ids = {item.id}
                    self.selection_changed.emit(1)
                    self._interaction = "move"
                    self._move_start = QPointF(self._last_world)
                    self._move_delta = QPointF()
                    self._notify_active_edit()
                    self.update()
                    return
            self.clear_selection()
            return
        if tool in {"pen", "highlighter"}:
            self.clear_selection()
            self._interaction = "ink"
            self._active_stroke_id = str(uuid4())
            self._last_recovery_notify = 0.0
            self._active_brush = replace(self.brush, kind=tool,
                                          pressure_enabled=self.brush.pressure_enabled and source == "pen",
                                          render_profile="pressure-v2", input_scale=self.view_scale)
            self._builder = IncrementalStrokeBuilder(self._active_brush)
            self._active_layer = self._new_layer()
            self._append_sample(position, pressure, timestamp, tilt_x, tilt_y)
        elif tool == "eraser":
            self.clear_selection()
            self._interaction = "erase"
            self._erase_radius_world = self.eraser_radius / self.view_scale
            self._erase_points = [(self._last_world.x(), self._last_world.y())]
            self._erase_processed = 0
            self._erase_preview = set()
            self._erase_layer = QImage()
            self._notify_active_edit()
            self.update()
        elif tool == "lasso":
            self.clear_selection()
            self._interaction = "lasso"
            self._lasso_points = [QPointF(self._last_world)]
        elif tool == "pan":
            self._interaction = "pan"
            self._pan_start = QPointF(position)
            self._pan_offset = QPointF(self.view_offset)
            self._capture_gesture_preview()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _move(self, position, pressure=1.0, timestamp=0, tilt_x=0, tilt_y=0):
        point = self.screen_to_world(position)
        if self._interaction == "ink":
            self._append_sample(position, pressure, timestamp, tilt_x, tilt_y)
        elif self._interaction == "erase":
            if not self._erase_points or math.hypot(point.x()-self._erase_points[-1][0], point.y()-self._erase_points[-1][1]) > 0.1/self.view_scale:
                previous = QPointF(*self._erase_points[-1]) if self._erase_points else point
                self._erase_points.append((point.x(), point.y()))
                self._notify_active_edit()
                margin = self._erase_radius_world + 3/self.view_scale
                dirty = QRectF(previous, point).normalized().adjusted(-margin, -margin, margin, margin)
                self.update(self._transform().mapRect(dirty).toAlignedRect())
        elif self._interaction == "lasso":
            if (point-self._lasso_points[-1]).manhattanLength() > 2/self.view_scale:
                self._lasso_points.append(point)
                self.update()
        elif self._interaction == "move":
            delta = point - self._move_start
            # Keep both live recovery snapshots and the eventual mixed-object
            # transaction inside the document's serializable coordinate range.
            selected = [item for item in self.scene.document.images if item.id in self.selection_ids]
            if selected:
                limit = MAX_IMAGE_WORLD_SIZE
                delta.setX(max(max(-limit-item.x for item in selected),
                               min(min(limit-item.x for item in selected), delta.x())))
                delta.setY(max(max(-limit-item.y for item in selected),
                               min(min(limit-item.y for item in selected), delta.y())))
            self._move_delta = delta
            self._notify_active_edit()
            self.update()
        elif self._interaction == "resize-image":
            original = self._resize_original
            anchor = self._image_corners(original)[(self._resize_corner + 2) % 4]
            sign_x = -1 if self._resize_corner in (0, 3) else 1
            sign_y = -1 if self._resize_corner in (0, 1) else 1
            target = point - self._resize_pointer_offset
            factor = max(sign_x * (target.x() - anchor.x()) / original.width(),
                         sign_y * (target.y() - anchor.y()) / original.height())
            minimum = MIN_IMAGE_WORLD_SIZE / min(original.width(), original.height())
            maximum = MAX_IMAGE_WORLD_SIZE / max(original.width(), original.height())
            for sign, coordinate, extent in ((sign_x, anchor.x(), original.width()),
                                              (sign_y, anchor.y(), original.height())):
                if sign < 0:
                    minimum = max(minimum, (coordinate - MAX_IMAGE_WORLD_SIZE) / extent)
                    maximum = min(maximum, (coordinate + MAX_IMAGE_WORLD_SIZE) / extent)
            minimum = max(minimum, min(maximum, 12 / self.view_scale / min(original.width(), original.height())))
            factor = max(min(minimum, maximum), min(maximum, factor))
            opposite = anchor + QPointF(sign_x * original.width() * factor,
                                         sign_y * original.height() * factor)
            self._resize_rect = QRectF(anchor, opposite).normalized()
            # Roundoff at large coordinates must not create a just-outside
            # position or a sub-minimum extent in the recovery snapshot.
            self._resize_rect = QRectF(
                max(-MAX_IMAGE_WORLD_SIZE, min(MAX_IMAGE_WORLD_SIZE, self._resize_rect.x())),
                max(-MAX_IMAGE_WORLD_SIZE, min(MAX_IMAGE_WORLD_SIZE, self._resize_rect.y())),
                max(MIN_IMAGE_WORLD_SIZE, min(MAX_IMAGE_WORLD_SIZE, self._resize_rect.width())),
                max(MIN_IMAGE_WORLD_SIZE, min(MAX_IMAGE_WORLD_SIZE, self._resize_rect.height())))
            self._notify_active_edit()
            self.update()
        elif self._interaction == "pan":
            self.set_view(self.view_scale, self._pan_offset + position - self._pan_start)
        self._last_world = point

    def finish_interaction(self, *, preserve_native_candidate=False):
        if not preserve_native_candidate:
            # Explicit save/undo/tool changes invalidate a pending native
            # contact, even when a Qt button transition has ended its edit.
            self.input_boundary_generation += 1
        if self._interaction:
            self.input_diagnostics.record_boundary("finish", source=self._source,
                                                   interaction=self._interaction,
                                                   erase_samples=len(self._erase_points))
        self._tail_eraser_contact = False
        had_gesture = not self._gesture_image.isNull()
        self._gesture_image = QImage()
        if self._touch_ids:
            # Toolbar/keyboard edits invalidate a frozen touch preview. Require
            # a fresh touch sequence instead of resuming from stale anchors.
            self._touch_blocked = True
        kind = self._interaction
        self._interaction = ""
        if kind == "ink" and self._builder is not None:
            builder, brush = self._builder, self._active_brush
            builder.finish()
            self._builder = None
            self._active_layer = QImage()
            if builder.samples:
                self.scene.add_stroke(Stroke(samples=list(builder.samples), brush=brush,
                                            id=self._active_stroke_id), cached_path=builder.path)
        elif kind == "erase":
            points = self._erase_points
            self._erase_points = []
            self._erase_preview = set()
            self._erase_layer = QImage()
            if points:
                self.scene.erase(points, self._erase_radius_world, self.eraser_whole)
        elif kind == "lasso":
            if len(self._lasso_points) >= 3:
                path = QPainterPath()
                path.addPolygon(QPolygonF(self._lasso_points))
                path.closeSubpath()
                self.selection_ids = {s.id for s in self.scene.query(path.boundingRect())
                                      if path.intersects(visible_path(s))}
                self.selection_changed.emit(len(self.selection_ids))
            self._lasso_points = []
        elif kind == "move":
            delta = self._move_delta
            self._move_delta = QPointF()
            if delta.manhattanLength() > 1e-8:
                self.scene.move_items(set(self.selection_ids), delta.x(), delta.y())
        elif kind == "resize-image":
            if self._resize_image_id:
                self.scene.resize_image(self._resize_image_id, self._resize_rect)
            self._resize_image_id = ""
        self._source = ""
        if kind or had_gesture:
            self._update_cursor()
            self.update()

    def cancel_input(self, reason="cancel"):
        # Commit samples already received; never bridge to an unrelated future press.
        self.input_diagnostics.record_boundary(reason, source=self._source, interaction=self._interaction)
        self.finish_interaction()
        self._touch_ids = ()
        self._touch_blocked = False
        self._gesture_image = QImage()
        self._pen_near = False
        self._set_temporary_tool("")
        self.update()

    def _set_temporary_tool(self, tool):
        if tool != self._temporary_tool:
            self._temporary_tool = tool
            self.tool_override_changed.emit(tool)
            self._update_cursor()
            self.update()

    def _update_cursor(self):
        tool = self._temporary_tool or self.tool
        handle = self._image_resize_handle(self._last_pointer) if tool == "select" else None
        if handle is not None:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if handle in (0, 2) else Qt.CursorShape.SizeBDiagCursor)
            return
        self.setCursor(Qt.CursorShape.OpenHandCursor if tool == "pan" else
                       Qt.CursorShape.CrossCursor if tool in {"lasso", "eraser"} else Qt.CursorShape.ArrowCursor)

    @property
    def native_tail_active(self):
        return self._source == "native-tail" and self._interaction == "erase"

    @property
    def native_tail_owned(self):
        return self._native_tail_claimed or self.native_tail_active

    def set_native_tail_claimed(self, claimed):
        # Ownership outlives the edit when undo/save ends it before native UP.
        # The adapter keeps consuming that contact without reopening an edit.
        self._native_tail_claimed = bool(claimed)

    def set_native_tail_qt_guard(self, guarded):
        # Keep queued Qt presses from re-opening a completed native tail edit.
        # A subsequent native tip/leave observation clears this, not a timer.
        self._native_tail_qt_guard = bool(guarded)

    def set_native_contact_candidate(self, eligible):
        self._native_candidate_generation = self.input_boundary_generation if eligible else None

    @property
    def native_contact_candidate(self):
        return self._native_candidate_generation == self.input_boundary_generation

    def clear_native_contact_candidate(self, reason, endpoint=None):
        if (self.native_contact_candidate and self._tail_eraser_contact
                and self._source == "pen" and self._interaction == "erase"):
            self.input_diagnostics.record_boundary(reason, source=self._source, interaction=self._interaction)
            if endpoint is not None:
                self._move(endpoint)
            self._native_tail_qt_guard = True
            self.finish_interaction(preserve_native_candidate=True)
        self._native_candidate_generation = None

    def observe_native_hover(self, position, tail, pressure=0, timestamp=0, tilt_x=0, tilt_y=0):
        if not self._source:
            self._set_temporary_tool("eraser" if tail else "")
            self._native_tail_position(position, pressure, tilt_x, tilt_y)

    def _native_tail_position(self, position, pressure, tilt_x, tilt_y):
        previous = QPointF(self._last_pointer)
        self._pen_near = True
        self._pointer_inside = self.rect().contains(position.toPoint())
        self._suppress_mouse_until = time.monotonic() + 0.2
        self._last_pointer = QPointF(position)
        if self._touch_ids:
            self._touch_blocked = True
            self._gesture_image = QImage()
        self._emit_diagnostics("Surface Pen · Windows Ink", pressure, tilt_x, tilt_y)
        self._update_eraser_cursor(previous)

    def begin_native_tail(self, position, pressure=1.0, timestamp=0, tilt_x=0, tilt_y=0):
        """One native DOWN owns one erase, independently of Qt button events."""
        self._begin(position, "native-tail", "eraser", pressure, timestamp, tilt_x, tilt_y)
        self._tail_eraser_contact = True
        self._set_temporary_tool("eraser")
        self._native_tail_position(position, pressure, tilt_x, tilt_y)

    def adopt_native_tail(self, position, pressure=1.0, timestamp=0, tilt_x=0, tilt_y=0):
        """Promote a Qt erase without committing or discarding its sweep."""
        if self._source == "pen" and self._interaction == "erase":
            self._source = "native-tail"
            self._tail_eraser_contact = True
            self._set_temporary_tool("eraser")
            self.move_native_tail(position, pressure, timestamp, tilt_x, tilt_y)
        else:
            self.begin_native_tail(position, pressure, timestamp, tilt_x, tilt_y)

    def move_native_tail(self, position, pressure=1.0, timestamp=0, tilt_x=0, tilt_y=0):
        if not self.native_tail_active:
            return
        started = time.perf_counter()
        self._move(position, pressure, timestamp, tilt_x, tilt_y)
        self._native_tail_position(position, pressure, tilt_x, tilt_y)
        self._input_ms.append((time.perf_counter() - started) * 1000)

    def end_native_tail(self, reason):
        self.input_diagnostics.record_boundary(reason, source=self._source,
                                               interaction=self._interaction)
        if self.native_tail_active:
            self.finish_interaction(preserve_native_candidate=True)
        # External commands can have already ended the contact. Do not finish
        # another input source, or resume the old sweep after an undo/save.
        if not self._source:
            self._pen_near = False
            self._set_temporary_tool("")
        self._suppress_mouse_until = time.monotonic() + 0.2

    def eventFilter(self, watched, event):
        et = event.type()
        if et == QEvent.Type.TabletEnterProximity:
            self._pen_near = True
            if self._touch_ids:
                self._touch_blocked = True
                self._gesture_image = QImage()
                self.update()
        elif et == QEvent.Type.TabletLeaveProximity:
            self.input_diagnostics.record_boundary("leave_proximity", source=self._source,
                                                   interaction=self._interaction)
            if self.native_tail_owned or self._native_tail_qt_guard or self.native_contact_candidate:
                # Queued Qt proximity/button events cannot end a contact owned
                # by WM_POINTER. Native leave/capture loss still cancels it.
                return False
            if self._source == "pen":
                self.finish_interaction()
            self._pen_near = False
            self._set_temporary_tool("")
        elif et == QEvent.Type.ApplicationDeactivate:
            self.cancel_input("application_deactivate")
        elif et in {QEvent.Type.WindowDeactivate, QEvent.Type.Hide} and watched is self.window():
            self.cancel_input(et.name)
        return super().eventFilter(watched, event)

    def tabletEvent(self, event: QTabletEvent):
        started = time.perf_counter()
        self.input_diagnostics.record_tablet(event, source=self._source, interaction=self._interaction,
                                             tool=self._interaction_tool,
                                             tail_contact=self._tail_eraser_contact,
                                             native_tail_owned=self.native_tail_owned,
                                             native_tail_guard=self._native_tail_qt_guard,
                                             native_candidate=self.native_contact_candidate,
                                             input_generation=self.input_boundary_generation)
        if self.native_tail_owned or self._native_tail_qt_guard:
            event.accept()
            return
        previous_pointer = QPointF(self._last_pointer)
        event.accept()
        self._pen_near = True
        self._suppress_mouse_until = time.monotonic() + 0.2
        self._pointer_inside = True
        self._last_pointer = event.position()
        if self._touch_ids:
            self._touch_blocked = True
            self._gesture_image = QImage()
        # Windows reports pen-tip and inverted-end devices separately. A
        # transient device/type change can therefore also produce another
        # TabletPress during one contact. Keep a tail erase continuous until
        # a real release/cancel; the selected drawing tool is restored later.
        is_tail = event.pointerType() == event.pointerType().Eraser
        continuing_tail = (self._tail_eraser_contact and self._source == "pen"
                           and self._interaction == "erase")
        tool = self.tool
        if is_tail or continuing_tail:
            tool = "eraser"
        elif (self.tool == "select" and event.type() == QEvent.Type.TabletMove
              and self._source == "pen" and self._interaction_tool == "lasso"):
            # Releasing the barrel button before lifting must not turn the
            # remainder of an ink lasso into a drag of the background image.
            tool = "lasso"
        elif event.buttons() & Qt.MouseButton.RightButton:
            tool = "lasso"
        self._set_temporary_tool(tool if tool != self.tool else "")
        pressure = max(0.0, min(1.0, event.pressure()))
        if event.type() == QEvent.Type.TabletPress:
            self._last_pressure = pressure
            if self._source == "pen" and self._interaction == "erase" and tool == "eraser":
                # Do not discard the previous endpoint on a duplicate press.
                self._move(event.position(), pressure, event.timestamp(), event.xTilt(), event.yTilt())
            else:
                self._begin(event.position(), "pen", tool, pressure, event.timestamp(), event.xTilt(), event.yTilt())
            self._tail_eraser_contact = continuing_tail or is_tail
        elif event.type() == QEvent.Type.TabletMove and self._source == "pen":
            if tool != self._interaction_tool:
                self._begin(event.position(), "pen", tool, pressure, event.timestamp(), event.xTilt(), event.yTilt())
            if is_tail and tool == "eraser":
                self._tail_eraser_contact = True
            self._last_pressure = pressure if pressure > 0 else self._last_pressure
            self._move(event.position(), pressure, event.timestamp(), event.xTilt(), event.yTilt())
        elif event.type() == QEvent.Type.TabletRelease:
            if self._source == "pen":
                if self._interaction == "ink":
                    # Release positions can already be above the screen. End
                    # at the last drawing sample instead of extending a thick
                    # tail using the previous pressure at a hover coordinate.
                    # Very short taps may supply pressure only on release;
                    # compensate at the down position, never at the up one.
                    if self._builder is not None and len(self._builder.samples) == 1:
                        first = self._builder.samples[0]
                        if pressure > first.pressure:
                            self._append_sample(self.world_to_screen(QPointF(first.x, first.y)),
                                                pressure, event.timestamp(), event.xTilt(), event.yTilt())
                else:
                    self._move(event.position(), pressure or self._last_pressure,
                               event.timestamp(), event.xTilt(), event.yTilt())
                if not (self.native_contact_candidate and self._tail_eraser_contact
                        and self._interaction == "erase"):
                    self.finish_interaction(preserve_native_candidate=True)
        self._emit_diagnostics("Surface Pen", pressure, event.xTilt(), event.yTilt())
        self._input_ms.append((time.perf_counter()-started)*1000)
        if tool == "eraser":
            self._update_eraser_cursor(previous_pointer)
        elif tool == "select" and not self._interaction:
            self._update_cursor()

    def _update_eraser_cursor(self, previous):
        margin = self.eraser_radius + 3
        for point in (previous, self._last_pointer):
            self.update(QRectF(point.x()-margin, point.y()-margin, margin*2, margin*2).toAlignedRect())

    def _ignore_mouse(self, event):
        return (event.source() != Qt.MouseEventSource.MouseEventNotSynthesized or self._pen_near
                or time.monotonic() < self._suppress_mouse_until)

    def mousePressEvent(self, event):
        if self._ignore_mouse(event):
            event.accept()
            return
        self._last_pointer = event.position()
        if event.button() in {Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton}:
            tool = "pan" if event.button() == Qt.MouseButton.MiddleButton else self.tool
            self._begin(event.position(), "mouse", tool, 1.0, event.timestamp())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._ignore_mouse(event):
            event.accept()
            return
        previous_pointer = QPointF(self._last_pointer)
        self._last_pointer = event.position()
        if self._source == "mouse":
            if event.buttons() & (Qt.MouseButton.LeftButton | Qt.MouseButton.MiddleButton):
                self._move(event.position(), 1.0, event.timestamp())
            else:
                self.finish_interaction()
        self._emit_diagnostics("mouse", 1.0)
        if self.tool == "eraser":
            self._update_eraser_cursor(previous_pointer)
        elif not self._interaction:
            self._update_cursor()
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._ignore_mouse(event):
            event.accept()
            return
        if self._source == "mouse":
            self._move(event.position(), 1.0, event.timestamp())
            self.finish_interaction()
        event.accept()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120
        if not steps and not event.pixelDelta().isNull():
            steps = event.pixelDelta().y()/80
        self.zoom_by(1.15 ** steps, event.position())
        event.accept()

    def enterEvent(self, event):
        self._pointer_inside = True
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._pointer_inside = False
        self.update()
        super().leaveEvent(event)

    def event(self, event):
        et = event.type()
        if et in {QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd, QEvent.Type.TouchCancel}:
            self._handle_touch(event)
            return True
        return super().event(event)

    def _handle_touch(self, event):
        event.accept()
        points = {p.id(): p.position() for p in event.points()
                  if p.state() != p.state().Released}
        if event.type() in {QEvent.Type.TouchEnd, QEvent.Type.TouchCancel} or not points:
            self._touch_ids = ()
            self._touch_blocked = False
            self._gesture_image = QImage()
            self.update()
            return
        if self._pen_near or self._source == "pen":
            self._touch_blocked = True
        if self._touch_blocked:
            self._touch_ids = tuple(sorted(points))
            return
        ids = tuple(sorted(points))[:2]
        positions = [points[i] for i in ids]
        center = sum(positions, QPointF()) / len(positions)
        distance = max(1.0, math.hypot(positions[1].x()-positions[0].x(),
                                     positions[1].y()-positions[0].y())) if len(ids) == 2 else 1.0
        if ids != self._touch_ids:
            if not self._touch_ids:
                self.finish_interaction()
                self._capture_gesture_preview()
            self._touch_ids = ids
            self._touch_anchor = center
            self._touch_distance = distance
            self._touch_scale = self.view_scale
            self._touch_offset = QPointF(self.view_offset)
            if len(ids) == 2:
                self._show_zoom_grid()
            return
        scale = max(0.1, min(8.0, self._touch_scale * distance/self._touch_distance))
        anchor_world = (self._touch_anchor - self._touch_offset) / self._touch_scale
        self.set_view(scale, center - anchor_world*scale)
        self._emit_diagnostics("pinch" if len(ids) == 2 else "touch_pan", 0)

    def _capture_gesture_preview(self):
        self._gesture_image = QImage()
        image = self._new_layer()
        painter = QPainter(image)
        # Capture only ink. The grid is drawn live in world coordinates beneath
        # this image instead of baking it into a stretched gesture preview.
        self.renderer.paint(painter, QRectF(self.rect()), self.view_scale, self.view_offset,
                            self.devicePixelRatioF(), background=False)
        painter.end()
        self._gesture_image = image
        self._gesture_scale = self.view_scale
        self._gesture_offset = QPointF(self.view_offset)

    def _update_erase_preview(self):
        if self._erase_processed >= len(self._erase_points):
            return
        if self._erase_layer.isNull():
            self._erase_layer = self._new_layer()
            painter = QPainter(self._erase_layer)
            self.renderer.paint(painter, QRectF(self.rect()), self.view_scale, self.view_offset,
                                self.devicePixelRatioF(), background=False)
            painter.end()
        dirty = QRectF()
        painter = QPainter(self._erase_layer)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setTransform(self._transform())
        # Rasterize only newly received spans. No growing vector difference or
        # replay of already touched ink belongs in the interactive paint path.
        while self._erase_processed < len(self._erase_points):
            end = min(len(self._erase_points), self._erase_processed + 32)
            points = self._erase_points[max(0, self._erase_processed-1):end]
            sweep = erase_path(points, self._erase_radius_world)
            self._erase_processed = end
            for stroke in self.scene.query(sweep.boundingRect()):
                if stroke.id in self._erase_preview:
                    continue
                if self.eraser_whole:
                    path = visible_path(stroke)
                    if not path.intersects(sweep):
                        continue
                    dirty = dirty.united(path.boundingRect())
                # Local recovery can keep a harmless non-intersecting mask;
                # exact hit testing is deferred to the document transaction.
                self._erase_preview.add(stroke.id)
            if not self.eraser_whole:
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
                painter.fillPath(sweep, Qt.GlobalColor.black)
        if self.eraser_whole and not dirty.isEmpty():
            dirty = dirty.adjusted(-2/self.view_scale, -2/self.view_scale,
                                   2/self.view_scale, 2/self.view_scale)
            painter.setClipRect(dirty)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(dirty, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            for stroke in self.scene.query(dirty):
                if stroke.id not in self._erase_preview:
                    self._paint_one(painter, stroke)
            self.update(self._transform().mapRect(dirty).toAlignedRect())
        painter.end()

    @staticmethod
    def _paint_one(painter, stroke, path=None):
        paint_stroke(painter, stroke, path)

    def _paint_document(self, painter, allow_preview=True):
        viewport = QRectF(self.rect())
        painter.fillRect(viewport, QColor(self.scene.document.background))
        if allow_preview:
            self._paint_grid(painter)
        # Pictures form a separate lower layer. Eraser previews remove only
        # ink, so they reveal the original picture without punching through it.
        painter.save()
        painter.setTransform(self._transform())
        world = self._transform().inverted()[0].mapRect(viewport)
        for item in self.scene.document.images:
            rect = image_rect(item)
            if allow_preview and item.id in self.selection_ids:
                if self._interaction == "move":
                    rect = rect.translated(self._move_delta)
                elif self._interaction == "resize-image" and item.id == self._resize_image_id:
                    rect = self._resize_rect
            if rect.intersects(world):
                self.image_renderer.paint(painter, item, rect)
        painter.restore()
        if allow_preview and self._interaction == "erase":
            self._update_erase_preview()
            painter.drawImage(QPointF(), self._erase_layer)
        elif allow_preview and not self._gesture_image.isNull():
            ratio = self.view_scale / self._gesture_scale
            offset = self.view_offset - self._gesture_offset * ratio
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.translate(offset)
            painter.scale(ratio, ratio)
            painter.drawImage(QPointF(), self._gesture_image)
            painter.restore()
        else:
            hidden = self.selection_ids if allow_preview and self._interaction == "move" else None
            self.renderer.paint(painter, viewport, self.view_scale, self.view_offset,
                                self.devicePixelRatioF(), exclude_ids=hidden, background=False)
        if not allow_preview:
            return
        if self._interaction == "ink" and not self._active_layer.isNull():
            painter.save()
            painter.setOpacity(self._active_brush.opacity)
            painter.drawImage(QPointF(), self._active_layer)
            painter.restore()
        if self._interaction == "move":
            painter.save()
            painter.setTransform(self._transform())
            painter.translate(self._move_delta)
            for stroke in self.scene.document.strokes:
                if stroke.id in self.selection_ids:
                    self._paint_one(painter, stroke)
            painter.restore()

    def paintEvent(self, event):
        started = time.perf_counter()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_document(painter)
        painter.save()
        painter.setTransform(self._transform())
        pen = QPen(QColor("#407BF0"), 1.4/self.view_scale, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        if self._lasso_points:
            path = QPainterPath()
            path.addPolygon(QPolygonF(self._lasso_points))
            painter.fillPath(path, QColor(64, 123, 240, 16))
            painter.drawPath(path)
        if self.selection_ids:
            bounds = self._selection_bounds().translated(self._move_delta if self._interaction == "move" else QPointF())
            if self._interaction == "resize-image":
                bounds = self._resize_rect
            painter.setBrush(QColor(64, 123, 240, 12))
            painter.drawRoundedRect(bounds.adjusted(-5/self.view_scale, -5/self.view_scale,
                                                    5/self.view_scale, 5/self.view_scale), 3, 3)
            if (self._temporary_tool or self.tool) == "select" and self._selected_image() is not None:
                painter.setPen(QPen(QColor("#407BF0"), 1.4/self.view_scale))
                painter.setBrush(QColor("#FFFFFF"))
                radius = 5 / self.view_scale
                for corner in self._image_corners(bounds):
                    painter.drawRect(QRectF(corner.x() - radius, corner.y() - radius,
                                            radius * 2, radius * 2))
        painter.restore()
        if (self._temporary_tool or self.tool) == "eraser" and self._pointer_inside:
            painter.setPen(QPen(QColor("#718096"), 1.2))
            painter.setBrush(QColor(255, 255, 255, 65))
            painter.drawEllipse(self._last_pointer, self.eraser_radius, self.eraser_radius)
        painter.end()
        self._paint_ms.append((time.perf_counter()-started)*1000)

    def _emit_diagnostics(self, source, pressure, tilt_x=0, tilt_y=0):
        if not self._diagnostics_enabled or time.monotonic()-self._last_diagnostics < 0.08:
            return
        self._last_diagnostics = time.monotonic()
        world = self.screen_to_world(self._last_pointer)
        self.diagnostics_changed.emit({
            "source": source, "pressure": round(pressure, 4),
            "tilt_x": tilt_x, "tilt_y": tilt_y, "tool": self._temporary_tool or self.tool,
            "x": round(world.x(), 2), "y": round(world.y(), 2),
            "samples": len(self._builder.samples) if self._builder else 0,
            "paint_ms": round(self._paint_ms[-1], 2) if self._paint_ms else 0,
            "cache_mb": round(self.renderer.cache_bytes / 1024**2, 2),
            "scale": round(self.view_scale, 3),
        })

    def export_png(self, path):
        self.finish_interaction()
        image = self._new_layer()
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_document(painter, allow_preview=False)
        painter.end()
        return image.save(str(path), "PNG")

    def resizeEvent(self, event):
        # Old layer is viewport-sized. Commit before a resize invalidates its coordinates.
        self.finish_interaction()
        self._gesture_image = QImage()
        super().resizeEvent(event)

    def focusOutEvent(self, event):
        self.cancel_input("focus_out")
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
            self.delete_selection()
            event.accept()
        elif event.key() == Qt.Key.Key_Escape:
            self.finish_interaction()
            self.clear_selection()
            event.ignore()  # MainWindow may also leave fullscreen.
        else:
            super().keyPressEvent(event)
