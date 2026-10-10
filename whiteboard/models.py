"""Serializable document values. Geometry and Qt objects live outside the model."""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4


@dataclass(slots=True)
class InkSample:
    x: float
    y: float
    t: float = 0.0
    pressure: float = 1.0
    tilt_x: float = 0.0
    tilt_y: float = 0.0


@dataclass(slots=True)
class Brush:
    color: str = "#222222"
    width: float = 4.0
    kind: str = "pen"
    pressure_enabled: bool = True
    sensitivity: float = 1.0
    opacity: float = 1.0
    # A saved stroke owns its rendering algorithm; preferences must not change
    # how old raw samples are replayed after an application upgrade.
    render_profile: str = "legacy-v1"
    # Logical screen distance per world unit at pen-down; replay must not use
    # the document's current zoom for a stroke's pressure filter.
    input_scale: float = 1.0


@dataclass(slots=True)
class EraseMask:
    points: list[tuple[float, float]]
    radius: float


@dataclass(slots=True)
class Stroke:
    samples: list[InkSample]
    brush: Brush
    id: str = field(default_factory=lambda: str(uuid4()))
    offset_x: float = 0.0
    offset_y: float = 0.0
    erase_masks: list[EraseMask] = field(default_factory=list)


@dataclass(slots=True)
class BoardImage:
    """A self-contained PNG placed in logical world coordinates."""

    png_data: bytes
    x: float
    y: float
    width: float
    height: float
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(slots=True)
class BoardDocument:
    strokes: list[Stroke] = field(default_factory=list)
    view_scale: float = 1.0
    view_offset_x: float = 0.0
    view_offset_y: float = 0.0
    background: str = "#FFFFFF"
    images: list[BoardImage] = field(default_factory=list)
