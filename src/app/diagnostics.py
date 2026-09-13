"""Runtime diagnostics used by support and the About/Diagnostics UI."""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

from src.app.meta import APP_NAME, APP_VERSION
from src.security.safe import is_admin


def data_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else None
    if root:
        return Path(root) / "FResucitary"
    return Path.home() / ".fresucitary"


def log_dir() -> Path:
    return data_dir() / "Logs"


def session_dir() -> Path:
    return data_dir() / "Sessions"


def runtime_report() -> str:
    frozen = bool(getattr(sys, "frozen", False))
    return "\n".join([
        f"Application: {APP_NAME} {APP_VERSION}",
        f"OS: {platform.platform()}",
        f"Architecture: {platform.machine()}",
        f"Python runtime: {platform.python_version()} ({'embarqué' if frozen else 'développement'})",
        f"Exécutable: {sys.executable}",
        f"Mode administrateur: {'Oui' if is_admin() else 'Non'}",
        f"Journal: {log_dir() / 'fresucitary.log'}",
    ])
