"""FResucitary Pro — application entry point."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import traceback

from src.app.meta import APP_ID, APP_NAME, APP_PUBLISHER, APP_VERSION
from src.app.diagnostics import log_dir


def _setup_logging() -> logging.Logger:
    path = log_dir()
    path.mkdir(parents=True, exist_ok=True)
    logfile = path / "fresucitary.log"

    handlers: list[logging.Handler] = [
        RotatingFileHandler(logfile, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    ]
    if not getattr(sys, "frozen", False):
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )
    return logging.getLogger("fresucitary")


log = _setup_logging()


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)  # type: ignore[attr-defined]
    except Exception:
        log.debug("Unable to set Windows AppUserModelID", exc_info=True)


def _install_exception_hook() -> None:
    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log.critical("Unhandled exception\n%s", text)
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None,
                    "FResucitary Pro — erreur inattendue",
                    "Une erreur inattendue est survenue.\n\n"
                    "Aucune écriture n'a été effectuée sur la source.\n"
                    f"Consultez le journal :\n{log_dir() / 'fresucitary.log'}",
                )
        except Exception:
            pass
    sys.excepthook = hook


def _self_test() -> int:
    """Load critical runtime modules without opening the GUI."""
    try:
        import PyQt6  # noqa: F401
        import pytsk3  # type: ignore # noqa: F401
        import reportlab  # noqa: F401
        import PIL  # noqa: F401
        import pypdfium2  # noqa: F401
        log.info("Runtime self-test OK")
        return 0
    except Exception:
        log.exception("Runtime self-test FAILED")
        return 10


def main() -> int:
    if "--self-test" in sys.argv:
        return _self_test()
    if "--version" in sys.argv:
        # Useful in developer consoles; frozen windowed builds still return 0.
        print(f"{APP_NAME} {APP_VERSION}")
        return 0

    _set_windows_app_id()
    _install_exception_hook()

    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_PUBLISHER)
    app.setOrganizationDomain("fresucitary.local")

    font = QFont("Segoe UI", 10)
    app.setFont(font)

    from src.ui.main_window import MainWindow
    window = MainWindow()
    window.show()

    log.info("%s %s started (PID %d, frozen=%s)", APP_NAME, APP_VERSION, os.getpid(), bool(getattr(sys, "frozen", False)))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
