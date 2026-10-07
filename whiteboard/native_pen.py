"""Windows pointer-contact lifecycle for the inverted eraser, independent of Qt buttons.

Implemented from the Windows SDK interfaces, without third-party application code.
MainWindow.nativeEvent calls handle_message before Qt translates WM_POINTER input.
All messages continue to Qt; Canvas suppresses duplicate edits for owned contacts.

https://learn.microsoft.com/windows/win32/api/winuser/ns-winuser-pointer_pen_info
https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-getpointerpeninfohistory
https://learn.microsoft.com/windows/win32/inputmsg/wm-pointercapturechanged
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass, replace
import math
import sys

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication


WM_POINTERUPDATE = 0x0245
WM_POINTERDOWN = 0x0246
WM_POINTERUP = 0x0247
WM_POINTERENTER = 0x0249
WM_POINTERLEAVE = 0x024A
WM_POINTERCAPTURECHANGED = 0x024C
PT_PEN = 3
POINTER_FLAG_INRANGE = 0x00000002
POINTER_FLAG_INCONTACT = 0x00000004
POINTER_FLAG_CANCELED = 0x00008000
POINTER_FLAG_DOWN = 0x00010000
POINTER_FLAG_UP = 0x00040000
PEN_FLAG_INVERTED = 0x00000002
PEN_FLAG_ERASER = 0x00000004
PEN_MASK_PRESSURE = 0x00000001
PEN_MASK_TILT_X = 0x00000004
PEN_MASK_TILT_Y = 0x00000008
MAX_HISTORY = 512

_MESSAGES = frozenset({WM_POINTERDOWN, WM_POINTERUP, WM_POINTERUPDATE,
                       WM_POINTERENTER, WM_POINTERLEAVE, WM_POINTERCAPTURECHANGED})
_HARD_ENDS = {WM_POINTERUP: "native_up", WM_POINTERLEAVE: "native_leave",
              WM_POINTERCAPTURECHANGED: "native_capture_lost"}


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class POINTER_INFO(ctypes.Structure):
    # Win32 LONG/DWORD stay 32 bit; HANDLE/HWND use native pointer alignment.
    _fields_ = [
        ("pointerType", ctypes.c_uint32), ("pointerId", ctypes.c_uint32),
        ("frameId", ctypes.c_uint32), ("pointerFlags", ctypes.c_uint32),
        ("sourceDevice", ctypes.c_void_p), ("hwndTarget", ctypes.c_void_p),
        ("ptPixelLocation", POINT), ("ptHimetricLocation", POINT),
        ("ptPixelLocationRaw", POINT), ("ptHimetricLocationRaw", POINT),
        ("dwTime", ctypes.c_uint32), ("historyCount", ctypes.c_uint32),
        ("InputData", ctypes.c_int32), ("dwKeyStates", ctypes.c_uint32),
        ("PerformanceCount", ctypes.c_uint64), ("ButtonChangeType", ctypes.c_uint32),
    ]


class POINTER_PEN_INFO(ctypes.Structure):
    _fields_ = [("pointerInfo", POINTER_INFO), ("penFlags", ctypes.c_uint32),
                ("penMask", ctypes.c_uint32), ("pressure", ctypes.c_uint32),
                ("rotation", ctypes.c_uint32), ("tiltX", ctypes.c_int32),
                ("tiltY", ctypes.c_int32)]


@dataclass(frozen=True, slots=True)
class PenSample:
    pointer_id: int
    source_device: int
    target_hwnd: int
    flags: int
    pen_flags: int
    x: int
    y: int
    timestamp: int
    frame_id: int = 0
    history_count: int = 1
    pen_mask: int = 0
    pressure: int = 0
    tilt_x: int = 0
    tilt_y: int = 0
    performance_count: int = 0
    button_change: int = 0
    pointer_type: int = PT_PEN

    @classmethod
    def from_native(cls, value: POINTER_PEN_INFO) -> "PenSample":
        p = value.pointerInfo
        return cls(int(p.pointerId), int(p.sourceDevice or 0), int(p.hwndTarget or 0),
                   int(p.pointerFlags), int(value.penFlags), int(p.ptPixelLocation.x),
                   int(p.ptPixelLocation.y), int(p.dwTime), int(p.frameId),
                   int(p.historyCount), int(value.penMask), int(value.pressure),
                   int(value.tiltX), int(value.tiltY), int(p.PerformanceCount),
                   int(p.ButtonChangeType), int(p.pointerType))


class NativeInputError(OSError):
    """A failed native read; current contact flags may still permit a fallback."""


class WindowsPointerApi:
    """Synchronous Win32 reads. Returned samples own their scalar data."""

    def __init__(self):
        dll = ctypes.WinDLL("user32", use_last_error=True)
        self._dll = dll
        self._type = dll.GetPointerType
        self._type.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
        self._type.restype = ctypes.c_int32
        self._info = dll.GetPointerPenInfo
        self._info.argtypes = [ctypes.c_uint32, ctypes.POINTER(POINTER_PEN_INFO)]
        self._info.restype = ctypes.c_int32
        self._history = dll.GetPointerPenInfoHistory
        self._history.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                                  ctypes.POINTER(POINTER_PEN_INFO)]
        self._history.restype = ctypes.c_int32
        self._screen_to_client = dll.ScreenToClient
        self._screen_to_client.argtypes = [ctypes.c_void_p, ctypes.POINTER(POINT)]
        self._screen_to_client.restype = ctypes.c_int32

    @staticmethod
    def _check(ok, name):
        if not ok:
            raise NativeInputError(f"{name} failed (Win32 error {ctypes.get_last_error()})")

    def pointer_type(self, pointer_id: int) -> int:
        value = ctypes.c_uint32()
        self._check(self._type(pointer_id, ctypes.byref(value)), "GetPointerType")
        return value.value

    def pen_info(self, pointer_id: int) -> PenSample:
        value = POINTER_PEN_INFO()
        self._check(self._info(pointer_id, ctypes.byref(value)), "GetPointerPenInfo")
        return PenSample.from_native(value)

    def pen_history(self, pointer_id: int, count: int) -> tuple[PenSample, ...]:
        if not 1 <= count <= MAX_HISTORY:
            raise NativeInputError("Pointer history exceeds the bounded input buffer")
        buffer = (POINTER_PEN_INFO * count)()
        available = ctypes.c_uint32(count)
        self._check(self._history(pointer_id, ctypes.byref(available), buffer),
                    "GetPointerPenInfoHistory")
        # Win32 can return only the newest entries when the supplied array is
        # too small. Never bridge across that unobserved portion of the stream.
        if available.value != count:
            raise NativeInputError("Pointer history changed during the native read")
        return tuple(PenSample.from_native(buffer[i]) for i in range(count))

    def screen_to_client(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        value = POINT(x, y)
        self._check(self._screen_to_client(hwnd, ctypes.byref(value)), "ScreenToClient")
        return value.x, value.y


class NativeTailEraser:
    """Own native tail contacts on bare Canvas; drain after outside edits.

    An injected API implements pointer_type(id), pen_info(id),
    pen_history(id, count) (newest first), and screen_to_client(hwnd, x, y).
    The adapter uses no event-time heuristics and never queries old frames later.
    """

    def __init__(self, window, canvas, api=None):
        self.window = window
        self.canvas = canvas
        self._api = api
        if api is None:
            app = QApplication.instance()
            if sys.platform == "win32" and app is not None and app.platformName() == "windows":
                try:
                    self._api = WindowsPointerApi()
                except (OSError, AttributeError):
                    pass  # Unsupported native API leaves the ordinary Qt path intact.
        self._pointer_id = None
        self._source_device = None
        self._draining = False
        self._last_sample = None
        self._generation = 0
        self._candidate = None

    def _qt_guard(self, guarded):
        self.canvas.set_native_tail_qt_guard(guarded)

    def _clear_candidate(self, reason, endpoint=None):
        self.canvas.clear_native_contact_candidate(reason, endpoint)
        self._candidate = None

    @property
    def enabled(self) -> bool:
        return self._api is not None

    @property
    def active_pointer_id(self) -> int | None:
        return self._pointer_id

    def _log(self, reason, **values):
        diagnostic = getattr(self.canvas, "input_diagnostics", None)
        if diagnostic is not None:
            diagnostic.record_boundary(reason, generation=self._generation, **values)

    def _log_sample(self, sample, message):
        self._log("native_pen_sample", message=int(message.message),
                  msg_time=int(message.time), wparam=int(message.wParam),
                  pointer_id=sample.pointer_id, source_device=sample.source_device,
                  target_hwnd=sample.target_hwnd, pointer_flags=sample.flags,
                  pen_flags=sample.pen_flags, pen_mask=sample.pen_mask,
                  frame_id=sample.frame_id, sample_time=sample.timestamp,
                  performance_count=sample.performance_count, history_count=sample.history_count,
                  button_change=sample.button_change, x=sample.x, y=sample.y,
                  pressure=sample.pressure, tilt_x=sample.tilt_x, tilt_y=sample.tilt_y,
                  draining=self._draining)

    def _finish(self, reason, *, release=False):
        # Canvas also clears proximity/cursor state after an external command
        # has already committed the gesture, without ending other input sources.
        self.canvas.end_native_tail(reason)
        self._draining = True
        self._log(reason, pointer_id=self._pointer_id, source_device=self._source_device)
        if release:
            self.canvas.set_native_tail_claimed(False)
            self._pointer_id = None
            self._source_device = None
            self._draining = False
            self._last_sample = None
            self._clear_candidate(reason)

    def _positions(self, hwnd, sample):
        x, y = self._api.screen_to_client(hwnd, sample.x, sample.y)
        dpr = float(self.window.devicePixelRatioF())
        if not math.isfinite(dpr) or dpr <= 0:
            raise NativeInputError("Invalid window device pixel ratio")
        in_window = QPointF(x / dpr, y / dpr)
        return in_window, self.canvas.mapFrom(self.window, in_window)

    @staticmethod
    def _brush_values(sample):
        pressure = min(1.0, max(0.0, sample.pressure / 1024.0)) if sample.pen_mask & PEN_MASK_PRESSURE else 0.0
        tilt_x = sample.tilt_x if sample.pen_mask & PEN_MASK_TILT_X else 0
        tilt_y = sample.tilt_y if sample.pen_mask & PEN_MASK_TILT_Y else 0
        return pressure, sample.timestamp, tilt_x, tilt_y

    def _start(self, msg, pointer_id, hwnd, *, late=False):
        try:
            if self._api.pointer_type(pointer_id) != PT_PEN:
                return False
            sample = self._api.pen_info(pointer_id)
            self._log_sample(sample, msg)
            if (sample.pointer_id != pointer_id or sample.pointer_type != PT_PEN
                    or sample.target_hwnd != hwnd
                    or not sample.flags & POINTER_FLAG_INCONTACT
                    or sample.flags & (POINTER_FLAG_CANCELED | POINTER_FLAG_UP)):
                return False
            in_window, position = self._positions(hwnd, sample)
        except (OSError, ValueError, OverflowError) as exc:
            self._log("native_start_unavailable", pointer_id=pointer_id, error=str(exc))
            return False
        if (not self.canvas.isVisible() or not self.canvas.isEnabled()
                or self.window.childAt(in_window.toPoint()) is not self.canvas
                or QApplication.activePopupWidget() is not None
                or QApplication.activeModalWidget() is not None):
            return False
        generation = self.canvas.input_boundary_generation
        identity = (pointer_id, sample.source_device, generation)
        if late:
            if self._candidate != identity:
                return False
        else:
            # Some drivers report INVERTED/ERASER after the initial DOWN.
            # Remember eligibility even while Qt handles the unclassified pen.
            self._candidate = identity
            self.canvas.set_native_contact_candidate(True)
        if not sample.pen_flags & (PEN_FLAG_INVERTED | PEN_FLAG_ERASER):
            return False
        self._generation += 1
        self._pointer_id = pointer_id
        self._source_device = sample.source_device
        self._draining = False
        self._last_sample = sample
        self.canvas.set_native_tail_claimed(True)
        self._qt_guard(True)
        if late:
            self.canvas.adopt_native_tail(position, *self._brush_values(sample))
        else:
            self.canvas.begin_native_tail(position, *self._brush_values(sample))
        self._log("native_tail_claimed", pointer_id=pointer_id, source_device=sample.source_device, late=late)
        return True

    def _observe(self, msg, pointer_id):
        try:
            if self._api.pointer_type(pointer_id) == PT_PEN:
                sample = self._api.pen_info(pointer_id)
                self._log_sample(sample, msg)
                if (self._candidate is not None and self._candidate[0] == pointer_id
                        and (sample.flags & (POINTER_FLAG_CANCELED | POINTER_FLAG_UP)
                             or not sample.flags & POINTER_FLAG_INCONTACT)):
                    self._clear_candidate("native_candidate_contact_ended")
                if (sample.pointer_id == pointer_id and sample.target_hwnd == int(msg.hWnd or 0)
                        and not sample.flags & (POINTER_FLAG_INCONTACT | POINTER_FLAG_CANCELED | POINTER_FLAG_UP)):
                    tail = bool(sample.pen_flags & (PEN_FLAG_INVERTED | PEN_FLAG_ERASER))
                    self._qt_guard(tail)
                    _, position = self._positions(int(msg.hWnd), sample)
                    self.canvas.observe_native_hover(position, tail, *self._brush_values(sample))
        except (OSError, ValueError, OverflowError) as exc:
            self._log("native_observation_unavailable", pointer_id=pointer_id, error=str(exc))

    def handle_message(self, nativeMSG) -> bool:
        if not self.enabled or int(nativeMSG.message) not in _MESSAGES:
            return False
        # ctypes.wintypes.MSG spells this field hWnd (not the SDK's hwnd).
        hwnd = int(nativeMSG.hWnd or 0)
        if hwnd != int(self.window.winId()):
            return False
        message = int(nativeMSG.message)
        pointer_id = int(nativeMSG.wParam) & 0xFFFF
        if self._pointer_id is None:
            if message == WM_POINTERDOWN:
                self._clear_candidate("native_new_down")
                self._qt_guard(False)
                return self._start(nativeMSG, pointer_id, hwnd)
            if (self._candidate is not None and self._candidate[0] == pointer_id
                    and (int(nativeMSG.wParam) >> 16) & POINTER_FLAG_CANCELED):
                # The message's cancellation is authoritative even when the
                # corresponding pen data has expired. Canvas commits a Qt tail
                # batch and guards its queued events; an ordinary tip remains
                # on Qt's own cancellation path instead of losing its Release.
                self._clear_candidate("native_candidate_cancel")
                return True
            if message in _HARD_ENDS:
                if self._candidate is not None and self._candidate[0] == pointer_id:
                    endpoint = None
                    if message == WM_POINTERUP:
                        try:
                            final = self._api.pen_info(pointer_id)
                            if (final.pointer_id == pointer_id and final.pointer_type == PT_PEN
                                    and final.source_device == self._candidate[1] and final.target_hwnd == hwnd
                                    and not final.flags & POINTER_FLAG_CANCELED):
                                _, endpoint = self._positions(hwnd, final)
                        except (OSError, ValueError, OverflowError):
                            pass
                    self._clear_candidate(_HARD_ENDS[message], endpoint)
                if message != WM_POINTERUP:
                    self._qt_guard(False)
                    self.canvas.end_native_tail(_HARD_ENDS[message])
                return False
            if message == WM_POINTERUPDATE and self._candidate is not None and self._candidate[0] == pointer_id:
                if self._candidate[2] != self.canvas.input_boundary_generation:
                    self._clear_candidate("native_candidate_external_end")
                    self._qt_guard(True)
                    return True
                if self._start(nativeMSG, pointer_id, hwnd, late=True):
                    return True
            self._observe(nativeMSG, pointer_id)
            return False
        if pointer_id != self._pointer_id:
            # Focus loss/suspend may end Canvas without a matching native UP.
            # A new pointer's explicit DOWN can retire that stale drain; an
            # actually active first pen must not be stolen by a second device.
            if message == WM_POINTERDOWN and not self.canvas.native_tail_active:
                self._finish("native_new_pointer_down", release=True)
                self._qt_guard(False)
                return self._start(nativeMSG, pointer_id, hwnd)
            return False
        # Unlike a Qt TabletPress inferred from changed buttons, a native DOWN
        # explicitly starts a contact. Even if an UP was lost, never bridge it.
        if message == WM_POINTERDOWN:
            self._finish("native_new_down", release=True)
            self._qt_guard(False)
            return self._start(nativeMSG, pointer_id, hwnd)
        # Capture notifications reuse old POINTER_INFO data; their message type
        # alone is authoritative and must not add that old position again.
        if message in (WM_POINTERCAPTURECHANGED, WM_POINTERLEAVE):
            self._finish(_HARD_ENDS[message], release=True)
            self._qt_guard(False)
            return True
        if ((int(nativeMSG.wParam) >> 16) & POINTER_FLAG_CANCELED):
            self._finish("native_cancel", release=message == WM_POINTERUP)
            return True
        if not self.canvas.native_tail_active:
            if not self._draining:
                self._log("native_external_end_drain", pointer_id=pointer_id)
            self._draining = True
        if self._draining:
            # No restarting on UPDATE after toolbar/save/focus/cancel actions.
            # UP can be handled even if GetPointerPenInfo would already fail.
            if message == WM_POINTERUP:
                self._finish("native_up", release=True)
                return True
            try:
                sample = self._api.pen_info(pointer_id)
                self._log_sample(sample, nativeMSG)
                if sample.flags & POINTER_FLAG_UP:
                    self._finish("native_up", release=True)
            except (OSError, ValueError, OverflowError):
                pass
            return True
        try:
            try:
                latest = self._api.pen_info(pointer_id)
            except (OSError, ValueError, OverflowError) as exc:
                latest = self._message_sample(nativeMSG, pointer_id, hwnd)
                self._log("native_message_fallback", pointer_id=pointer_id, error=str(exc))
            if (latest.pointer_id != pointer_id or latest.pointer_type != PT_PEN
                    or latest.source_device != self._source_device or latest.target_hwnd != hwnd):
                self._finish("native_identity_changed", release=message == WM_POINTERUP)
                return True
            if latest.flags & POINTER_FLAG_CANCELED:
                self._log_sample(latest, nativeMSG)
                self._finish("native_cancel", release=message == WM_POINTERUP)
                return True
            newest_first = self._history_or_latest(latest)
            for index, sample in enumerate(reversed(newest_first)):
                self._log_sample(sample, nativeMSG)
                if (sample.pointer_id != pointer_id or sample.pointer_type != PT_PEN
                        or sample.source_device != self._source_device or sample.target_hwnd != hwnd):
                    self._finish("native_identity_changed")
                    break
                if sample.flags & POINTER_FLAG_CANCELED:
                    self._finish("native_cancel")
                    break
                # Only the latest position on the actual UP message is an
                # endpoint. An older history UP terminates continuity instead.
                if message == WM_POINTERUP and index == len(newest_first) - 1:
                    _, position = self._positions(hwnd, sample)
                    self.canvas.move_native_tail(position, *self._brush_values(sample))
                    self._last_sample = sample
                    self._finish("native_up", release=True)
                    break
                if sample.flags & POINTER_FLAG_UP:
                    self._finish("native_history_up")
                    break
                if not sample.flags & POINTER_FLAG_INCONTACT:
                    self._finish("native_contact_lost")
                    break
                if sample != self._last_sample:
                    _, position = self._positions(hwnd, sample)
                    self.canvas.move_native_tail(position, *self._brush_values(sample))
                    self._last_sample = sample
        except (OSError, ValueError, OverflowError) as exc:
            self._log("native_frame_unavailable", pointer_id=pointer_id, error=str(exc))
            self._finish("native_data_lost")
        if message == WM_POINTERUP and self._pointer_id is not None:
            self._finish("native_up", release=True)
        return True

    def _history_or_latest(self, latest):
        try:
            if not 1 <= latest.history_count <= MAX_HISTORY:
                raise NativeInputError("Pointer history exceeds the bounded input buffer")
            history = (self._api.pen_history(latest.pointer_id, latest.history_count)
                       if latest.history_count > 1 else (latest,))
            if len(history) != latest.history_count:
                raise NativeInputError("Incomplete native pointer history")
            return history
        except (OSError, ValueError, OverflowError) as exc:
            # Coalesced intermediate moves are optional refinement. A current,
            # verified contact still gives a valid swept segment; missing that
            # refinement must not disable the remainder of the physical erase.
            if latest.flags & POINTER_FLAG_INCONTACT and not latest.flags & POINTER_FLAG_UP:
                self._log("native_history_fallback", pointer_id=latest.pointer_id, error=str(exc))
                return (latest,)
            raise

    def _message_sample(self, msg, pointer_id, hwnd):
        flags = (int(msg.wParam) >> 16) & 0xFFFF
        # Do not infer contact from cached pressure or a cursor icon. The
        # current Windows message itself must positively report INCONTACT.
        if (int(msg.message) != WM_POINTERUPDATE or self._last_sample is None
                or not flags & POINTER_FLAG_INCONTACT or flags & POINTER_FLAG_CANCELED):
            raise NativeInputError("No current native contact information")
        packed = int(msg.lParam)
        x = ctypes.c_int16(packed & 0xFFFF).value
        y = ctypes.c_int16((packed >> 16) & 0xFFFF).value
        return replace(self._last_sample, pointer_id=pointer_id, target_hwnd=hwnd,
                       flags=flags, x=x, y=y, timestamp=int(msg.time),
                       history_count=1, pen_mask=0, pressure=0, performance_count=0)
