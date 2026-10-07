"""Application entry point. No network services or external runtime needed."""
from __future__ import annotations

from .i18n import LANGUAGES, set_language, tr

import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from PySide6.QtCore import QCoreApplication, QLockFile, QStandardPaths, QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_DISPLAY_NAME, DATA_APPLICATION_NAME, DATA_ORGANIZATION_NAME, __version__


def configure_application_identity(app: QApplication) -> None:
    """Set public branding without moving existing Qt application data."""
    app.setApplicationName(DATA_APPLICATION_NAME)
    app.setOrganizationName(DATA_ORGANIZATION_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setApplicationVersion(__version__)


def main(argv=None):
    # Read an explicit language before building help, without creating Qt UI.
    language_parser = argparse.ArgumentParser(add_help=False)
    language_parser.add_argument("--language", choices=tuple(LANGUAGES))
    language_args, _ = language_parser.parse_known_args(argv)
    if language_args.language:
        set_language(language_args.language)
    parser = argparse.ArgumentParser(prog=APP_DISPLAY_NAME, description=tr('{p0} · 本地离线手写', p0=APP_DISPLAY_NAME))
    parser.add_argument("--language", choices=tuple(LANGUAGES), help=tr("界面语言（zh_CN、en）"))
    parser.add_argument("--version", action="version", version=f"{APP_DISPLAY_NAME} {__version__}")
    parser.add_argument("document", nargs="?", help=tr('打开 .qboard 文件'))
    parser.add_argument("--no-recovery", action="store_true", help=tr('本次运行不使用恢复草稿'))
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-output", default="artifacts/smoke", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_CompressTabletEvents, False)
    # Standard buttons need Qt's touch-to-mouse synthesis. Canvas consumes its
    # own touch events and rejects synthetic mouse ink to prevent duplicates.
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_SynthesizeMouseForUnhandledTouchEvents, True)
    app = QApplication(sys.argv[:1])
    configure_application_identity(app)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    log_dir = Path(args.smoke_output) if args.smoke_test else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "whiteboard.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s")
    from .settings import AppSettings
    settings = AppSettings(log_dir / "smoke-settings.json") if args.smoke_test else AppSettings()
    if args.language:
        settings.language = args.language
    set_language(settings.language)
    lock = None
    if not args.smoke_test:
        lock = QLockFile(str(log_dir / "instance.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(100):
            QMessageBox.information(None, tr('{p0} 已经打开', p0=APP_DISPLAY_NAME), tr('已有一个 {p0} 窗口正在运行。请在该窗口中打开文件。', p0=APP_DISPLAY_NAME))
            return 1

    def report_exception(exc_type, exc, tb):
        logging.error("Unhandled application exception", exc_info=(exc_type, exc, tb))
        QMessageBox.critical(None, tr('{p0} 遇到问题', p0=APP_DISPLAY_NAME), tr('操作未完成。请尝试保存当前白板。\n详细信息已写入本地日志。\n\n') + str(exc))

    sys.excepthook = report_exception
    from .window import MainWindow
    window = MainWindow(initial_path=args.document, recovery_enabled=not (args.no_recovery or args.smoke_test), settings=settings)
    if args.smoke_test:
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    window.show()
    if args.smoke_test:
        from .smoke import run_smoke
        QTimer.singleShot(150, lambda: run_smoke(app, window, log_dir))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
