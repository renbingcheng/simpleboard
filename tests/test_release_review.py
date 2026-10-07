"""Regressions confirmed while reviewing release input boundaries."""
from __future__ import annotations

import unittest

from PySide6.QtCore import QEvent, QPointF

from tests import test_tail_recovery as helpers
from whiteboard.geometry import visible_path
from whiteboard.native_pen import (
    NativeInputError, PEN_FLAG_ERASER, POINTER_FLAG_CANCELED, POINTER_FLAG_INCONTACT,
    WM_POINTERDOWN, WM_POINTERUPDATE, WM_POINTERUP,
)
from whiteboard.storage import document_to_dict


class ReleaseReviewTests(unittest.TestCase):
    def setUp(self):
        # Reuse actual MainWindow/MSG/QTabletEvent helpers without inheriting
        # the other suite or exposing its TestCase to this module's discovery.
        self.fixture = helpers.TailRecoveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_candidate_message_cancel_survives_missing_pen_info(self):
        t = self.fixture
        t.band()
        t.native(WM_POINTERDOWN, (100, 240), pen_flags=0)
        t.qt(QEvent.Type.TabletPress, (100, 240))
        t.qt(QEvent.Type.TabletMove, (200, 240))
        t.api.info_error = NativeInputError("ERROR_NO_DATA on canceled UPDATE")
        t.native(WM_POINTERUPDATE, (220, 240), pen_flags=0,
                 flags=POINTER_FLAG_INCONTACT | POINTER_FLAG_CANCELED)
        canceled = document_to_dict(t.scene.document)

        # Queued Qt events must not extend the canceled physical contact.
        t.qt(QEvent.Type.TabletPress, (400, 240))
        t.qt(QEvent.Type.TabletMove, (600, 240))
        t.qt(QEvent.Type.TabletRelease, (650, 240))
        t.api.info_error = None
        t.native(WM_POINTERUP, (650, 240), pen_flags=0)
        self.assertEqual(document_to_dict(t.scene.document), canceled)
        t.assert_band(erased=(100, 150, 200), retained=(300, 500, 600, 750))
        t.assert_one_undo_restores()

    def test_other_pointer_cancellation_does_not_end_candidate_tail(self):
        t = self.fixture
        t.band()
        t.native(WM_POINTERDOWN, (100, 240), pen_flags=0)
        t.qt(QEvent.Type.TabletPress, (100, 240))
        t.qt(QEvent.Type.TabletMove, (200, 240))
        t.api.info_error = NativeInputError("unrelated pointer's canceled data expired")
        t.native(WM_POINTERUPDATE, (700, 240), pen_flags=0, pointer_id=91,
                 flags=POINTER_FLAG_INCONTACT | POINTER_FLAG_CANCELED)
        t.api.info_error = None
        t.native(WM_POINTERUPDATE, (400, 240), pen_flags=PEN_FLAG_ERASER)
        t.native(WM_POINTERUP, (500, 240))
        t.assert_band(erased=range(100, 501, 20), retained=(80, 600, 750))
        t.assert_one_undo_restores()

    def test_candidate_tip_cancel_keeps_qt_release_available(self):
        t = self.fixture
        t.native(WM_POINTERDOWN, (100, 240), pen_flags=0)
        t.qt(QEvent.Type.TabletPress, (100, 240), tip=True)
        t.qt(QEvent.Type.TabletMove, (200, 240), tip=True)
        t.api.info_error = NativeInputError("canceled ordinary pen data expired")
        t.native(WM_POINTERUPDATE, (200, 240), pen_flags=0,
                 flags=POINTER_FLAG_INCONTACT | POINTER_FLAG_CANCELED)
        t.qt(QEvent.Type.TabletRelease, (200, 240), tip=True)
        self.assertEqual(t.canvas._source, "")
        self.assertEqual(t.scene.undo_stack.count(), 1)
        self.assertEqual(len(t.scene.document.strokes), 1)
        self.assertTrue(visible_path(t.scene.document.strokes[0]).contains(QPointF(150, 240)))


if __name__ == "__main__":
    unittest.main()
