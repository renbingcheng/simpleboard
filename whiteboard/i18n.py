"""Small offline translation service shared by the UI and user-facing errors.

Source strings are Simplified Chinese. Catalogs live in Python modules so source
archives, installed packages and frozen builds all use the same translations.
Only display text is translated; document keys and diagnostic records stay stable.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QTranslator

from .translations import EN

LANGUAGES = {"zh_CN": "简体中文", "en": "English"}
DEFAULT_LANGUAGE = "zh_CN"
_language = DEFAULT_LANGUAGE
_qt_translator = None
_local_language = ContextVar("translation_language", default=None)


def normalize_language(language: object) -> str:
    return language if isinstance(language, str) and language in LANGUAGES else DEFAULT_LANGUAGE


def language() -> str:
    return _language


def set_language(value: str) -> None:
    global _language, _qt_translator
    _language = normalize_language(value)
    app = QCoreApplication.instance()
    if app is None:
        return
    if _qt_translator is not None:
        app.removeTranslator(_qt_translator)
        _qt_translator.deleteLater()
        _qt_translator = None
    if _language == "zh_CN":
        translator = QTranslator(app)
        directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        if translator.load("qtbase_zh_CN", directory):
            app.installTranslator(translator)
            _qt_translator = translator
        else:
            translator.deleteLater()


def tr(source: str, **values) -> str:
    active = _local_language.get() or _language
    text = EN.get(source, source) if active == "en" else source
    return text.format(**values) if values else text


@contextmanager
def using_language(value: str):
    """Localize settings validation before the application language is installed."""
    token = _local_language.set(normalize_language(value))
    try:
        yield
    finally:
        _local_language.reset(token)


class TextBindings:
    """Explicit bindings for persistent widgets; never translate user filenames."""

    def __init__(self):
        self._items = []

    def bind(self, widget, property_name: str, source: str, **values):
        setter = getattr(widget, "set" + property_name[0].upper() + property_name[1:])
        self._items.append((setter, source, values))
        setter(tr(source, **values))
        return widget

    def refresh(self):
        for setter, source, values in self._items:
            setter(tr(source, **values))
