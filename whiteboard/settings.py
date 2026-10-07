"""Small local settings file; invalid preferences fall back to usable defaults."""
from __future__ import annotations

from .i18n import DEFAULT_LANGUAGE, normalize_language, tr, using_language

from dataclasses import asdict
import json
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .models import Brush
from .storage import DocumentError, _atomic_write_bytes, _number, brush_from_dict


def app_data_directory() -> Path:
    # app.configure_application_identity keeps the legacy Qt data identity;
    # applicationDisplayName can change without moving settings or recovery.
    directory = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(directory) if directory else Path.home() / ".qboard"


class AppSettings:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else app_data_directory() / "settings.json"
        self.pens = [Brush(color=color, width=4) for color in (
            "#222222", "#2563EB", "#E5484D", "#16A36B", "#8B5CF6", "#F28C28",
        )]
        self.highlighter = Brush(color="#FFD84D", width=24, kind="highlighter", pressure_enabled=False, opacity=0.3)
        self.eraser_radius = 12.0
        self.eraser_whole = False
        self.pressure_enabled = True
        self.sensitivity = 1.0
        self.recent_files: list[str] = []
        self.language = DEFAULT_LANGUAGE
        self.load_error: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            if self.path.stat().st_size > 1024 * 1024:
                raise ValueError(tr('设置文件过大'))
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError(tr('设置格式不正确'))
            self.language = normalize_language(value.get("language"))
            with using_language(self.language):
                self._apply(value)
        except (OSError, ValueError, DocumentError, RecursionError) as exc:
            with using_language(self.language):
                self.load_error = tr('设置文件无法读取，已使用默认设置：{p0}', p0=exc)

    def _apply(self, value: dict) -> None:
        if "pens" in value:
            if not isinstance(value["pens"], list) or len(value["pens"]) != 6:
                raise ValueError(tr('画笔槽设置不正确'))
            pens = [brush_from_dict(brush) for brush in value["pens"]]
            if any(brush.kind != "pen" for brush in pens):
                raise ValueError(tr('画笔槽类型不正确'))
        else:
            pens = self.pens
        highlighter = brush_from_dict(value.get("highlighter", asdict(self.highlighter)))
        if highlighter.kind != "highlighter":
            raise ValueError(tr('荧光笔设置不正确'))
        eraser_radius = _number(value.get("eraser_radius", 12), tr('橡皮半径'), 1, 500)
        sensitivity = _number(value.get("sensitivity", 1), tr('压感灵敏度'), 0.05, 10)
        eraser_whole = value.get("eraser_whole", False)
        pressure_enabled = value.get("pressure_enabled", True)
        if not isinstance(eraser_whole, bool) or not isinstance(pressure_enabled, bool):
            raise ValueError(tr('工具设置不正确'))
        recent = value.get("recent_files", [])
        if not isinstance(recent, list) or any(not isinstance(path, str) for path in recent):
            raise ValueError(tr('最近文件列表不正确'))
        self.pens, self.highlighter = pens, highlighter
        self.eraser_radius, self.sensitivity = eraser_radius, sensitivity
        self.eraser_whole, self.pressure_enabled = eraser_whole, pressure_enabled
        self.recent_files = list(dict.fromkeys(recent))[:10]

    def save(self) -> None:
        value = {
            "version": 1,
            "language": self.language,
            "pens": [asdict(brush) for brush in self.pens],
            "highlighter": asdict(self.highlighter),
            "eraser_radius": self.eraser_radius,
            "eraser_whole": self.eraser_whole,
            "pressure_enabled": self.pressure_enabled,
            "sensitivity": self.sensitivity,
            "recent_files": self.recent_files[:10],
        }
        try:
            contents = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_bytes(self.path, contents)
        except (OSError, ValueError, TypeError) as exc:
            raise DocumentError(tr('无法保存设置：{p0}', p0=exc)) from exc

    def add_recent(self, path: Path | str) -> None:
        path = str(Path(path).resolve())
        self.recent_files = [path] + [existing for existing in self.recent_files if existing.casefold() != path.casefold()][:9]
