"""Debounced recovery saves with a single ordered background writer."""
from __future__ import annotations

from .i18n import tr

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from .models import BoardDocument
from .settings import app_data_directory
from .storage import DocumentError, document_from_dict, document_to_dict, load_snapshot, save_snapshot


def _write_captured(path: Path, capture: tuple[BoardDocument, str | None]) -> None:
    """Convert immutable scene values and perform compression on the worker."""
    document, source_path = capture
    snapshot = document_to_dict(document)
    snapshot["recovery"] = {"source_path": source_path}
    save_snapshot(path, snapshot)


class RecoveryManager(QObject):
    error = Signal(str)
    saved = Signal()
    _completed = Signal(object)

    def __init__(self, snapshot_provider: Callable[[], BoardDocument], parent=None, directory: Path | str | None = None):
        super().__init__(parent)
        self.path = (Path(directory) if directory is not None else app_data_directory()) / "recovery.qboard"
        self.source_path: str | None = None
        self._provider = snapshot_provider
        self._unresolved = self.path.exists()
        self._dirty = False
        self._future: Future | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qboard-recovery")
        self._closed = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(2000)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(10000)
        self._debounce.timeout.connect(self._start_write)
        self._deadline.timeout.connect(self._start_write)
        self._completed.connect(self._finish_write)

    def available(self) -> bool:
        return self.path.is_file()

    def recover(self) -> BoardDocument:
        snapshot = load_snapshot(self.path)
        document = document_from_dict(snapshot)
        recovery = snapshot.get("recovery", {})
        source = recovery.get("source_path") if isinstance(recovery, dict) else None
        self.source_path = source if isinstance(source, str) else None
        self._unresolved = False
        return document

    def schedule(self) -> None:
        if self._closed or self._unresolved:
            return
        self._dirty = True
        self._debounce.start()
        if not self._deadline.isActive():
            self._deadline.start()

    def _capture(self) -> tuple[BoardDocument, str | None]:
        # Scene treats committed strokes, their brushes and sample/mask lists as
        # immutable: editing replaces Stroke objects. Copying only the document
        # wrapper and ordered stroke list is therefore a stable GUI-thread
        # snapshot without a 200,000-sample deepcopy or serialization pause.
        # Providers must honor the same invariant while a save is in flight.
        document = self._provider()
        detached = replace(document, strokes=list(document.strokes))
        return detached, str(self.source_path) if self.source_path else None

    @Slot()
    def _start_write(self) -> None:
        if not self._dirty or self._unresolved or self._closed:
            return
        if self._future is not None:
            return
        self._debounce.stop()
        self._deadline.stop()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            capture = self._capture()
            self._dirty = False
            self._future = self._executor.submit(_write_captured, self.path, capture)
            self._future.add_done_callback(self._worker_done)
        except Exception as exc:
            self._dirty = True
            self.error.emit(tr('无法保存恢复副本：{p0}', p0=exc))

    def _worker_done(self, future: Future) -> None:
        # A Qt signal safely crosses from the executor thread to the GUI thread.
        try:
            self._completed.emit(future)
        except RuntimeError:
            # The application may already have destroyed the QObject at exit.
            pass

    @Slot(object)
    def _finish_write(self, future: Future) -> None:
        if future is not self._future:
            return
        self._future = None
        try:
            future.result()
        except Exception as exc:
            self._dirty = True
            self.error.emit(tr('无法保存恢复副本：{p0}', p0=exc))
            # Avoid an immediate retry loop when the disk is full or read-only.
            if not self._closed:
                self._deadline.start()
            return
        self.saved.emit()
        if self._dirty:
            self._start_write()

    def flush(self) -> bool:
        """Finish the ordered writer, then persist the newest dirty GUI snapshot."""
        self._debounce.stop()
        self._deadline.stop()
        if self._unresolved or self._closed:
            return True
        if self._future is not None:
            future, self._future = self._future, None
            try:
                future.result()
            except Exception:
                self._dirty = True
            else:
                self.saved.emit()
        if not self._dirty:
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _write_captured(self.path, self._capture())
            self._dirty = False
            self.saved.emit()
            return True
        except Exception as exc:
            self.error.emit(tr('无法保存恢复副本：{p0}', p0=exc))
            return False

    def discard(self) -> None:
        """Resolve or remove this app's recovery after the user saves/discards."""
        self._debounce.stop()
        self._deadline.stop()
        if self._future is not None:
            future, self._future = self._future, None
            try:
                future.result()
            except Exception:
                pass
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise DocumentError(tr('无法删除恢复副本：{p0}', p0=exc)) from exc
        self._unresolved = False
        self._dirty = False
        self.source_path = None

    def shutdown(self) -> bool:
        """Flush and release the worker before the owning window is destroyed."""
        result = self.flush()
        self._closed = True
        self._executor.shutdown(wait=True)
        return result
