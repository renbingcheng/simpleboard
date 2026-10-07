"""Opt-in, bounded input evidence; no files are written until explicit export."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import time

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QIODevice, QSaveFile, qVersion

from . import __version__ as application_version


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _number(value):
    value = float(value)
    return value if math.isfinite(value) else None


def _enum_value(value) -> int:
    return int(value.value if hasattr(value, "value") else value)


def _state_values(values: dict) -> dict:
    # Callers supply a handful of scalar state fields, never document content.
    # Bound strings as well as event count, and keep the JSON independently owned.
    result = {}
    for key, value in list(values.items())[:24]:
        if value is None or isinstance(value, (bool, int)):
            converted = value
        elif isinstance(value, float):
            converted = _number(value)
        else:
            converted = str(value)[:160]
        result[str(key)[:48]] = converted
    return result


class InputDiagnostics:
    """Record Qt input before dispatch and named interaction boundaries.

    This is owned and called by the GUI thread. Enabling/disabling starts and
    ends recording segments without deleting the previous evidence. ``clear``
    is explicit. Native input details, if available, can be included as scalar
    fields in ``record_boundary``; Qt pressure alone does not prove contact.
    """

    def __init__(self, max_events: int = 512):
        if type(max_events) is not int or not 1 <= max_events <= 4096:
            raise ValueError("max_events must be an integer between 1 and 4096")
        self.capacity = max_events
        self.enabled = False
        self._events = deque(maxlen=max_events)
        self._sequence = 0
        self._dropped = 0
        self._started = time.monotonic()
        self._created_utc = _utc_now()

    @property
    def count(self) -> int:
        return len(self._events)

    @property
    def dropped(self) -> int:
        return self._dropped

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.enabled:
            return
        if enabled:
            self.enabled = True
            self.record_boundary("recording_enabled")
        else:
            self.record_boundary("recording_disabled")
            self.enabled = False

    def clear(self) -> None:
        self._events.clear()
        self._sequence = 0
        self._dropped = 0
        self._started = time.monotonic()
        self._created_utc = _utc_now()

    def _append(self, values: dict) -> None:
        if len(self._events) == self.capacity:
            self._dropped += 1
        self._sequence += 1
        self._events.append({
            "sequence": self._sequence,
            "elapsed_ms": round((time.monotonic() - self._started) * 1000, 3),
            **values,
        })

    def record_tablet(self, event, **state) -> None:
        if not self.enabled:
            return
        position = event.position()
        device = event.pointingDevice()
        unique_id = device.uniqueId()
        self._append({
            "kind": "tablet",
            "event_type": event.type().name,
            "event_type_value": _enum_value(event.type()),
            "pointer_type": event.pointerType().name,
            "pointer_type_value": _enum_value(event.pointerType()),
            "button": _enum_value(event.button()),
            "buttons": _enum_value(event.buttons()),
            "pressure": _number(event.pressure()),
            "timestamp_ms": int(event.timestamp()),
            "x": _number(position.x()),
            "y": _number(position.y()),
            "tilt_x": _number(event.xTilt()),
            "tilt_y": _number(event.yTilt()),
            "device": {
                "name": device.name()[:160],
                "system_id": int(device.systemId()),
                "unique_id": int(unique_id.numericId()) if unique_id.isValid() else None,
            },
            "state": _state_values(state),
        })

    def record_boundary(self, reason: str, **state) -> None:
        if not self.enabled:
            return
        self._append({"kind": "boundary", "reason": str(reason)[:160],
                      "state": _state_values(state)})

    def snapshot(self) -> dict:
        """Return detached JSON values, without sampling or disk I/O."""
        return {
            "schema": "local-whiteboard.input-diagnostics",
            "version": 1,
            "created_utc": self._created_utc,
            "exported_utc": _utc_now(),
            "enabled": self.enabled,
            "capacity": self.capacity,
            "recorded_total": self._sequence,
            "dropped": self._dropped,
            "environment": {
                "application": application_version,
                "qt": qVersion(),
                "pyside": pyside_version,
                "python": platform.python_version(),
                "platform": platform.platform(),
            },
            "events": deepcopy(list(self._events)),
        }

    def export_json(self, path) -> Path:
        """Atomically export only when requested; raise OSError on failure."""
        target = Path(path)
        data = (json.dumps(self.snapshot(), ensure_ascii=False, indent=2,
                           allow_nan=False) + "\n").encode("utf-8")
        output = QSaveFile(str(target))
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        if output.write(data) != len(data):
            error = output.errorString()
            output.cancelWriting()
            raise OSError(error)
        if not output.commit():
            raise OSError(output.errorString())
        return target
