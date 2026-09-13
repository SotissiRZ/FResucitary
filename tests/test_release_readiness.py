"""Release-readiness tests that do not require PyQt6 or pytsk3."""
from __future__ import annotations

from pathlib import Path

from src.app.meta import APP_NAME, APP_VERSION
from src.security.safe import _physical_drive_number


def test_release_version():
    assert APP_NAME == "FResucitary Pro"
    assert APP_VERSION == "2.1.3"


def test_physical_drive_parser():
    assert _physical_drive_number(r"\\.\PhysicalDrive0") == 0
    assert _physical_drive_number(r"\\.\PhysicalDrive31") == 31
    assert _physical_drive_number(r"C:\image.dd") is None


def test_production_build_files_exist():
    root = Path(__file__).resolve().parents[1]
    assert (root / "FResucitary.spec").is_file()
    assert (root / "BUILD_WINDOWS.bat").is_file()
    assert (root / "packaging" / "build_release.ps1").is_file()
    assert (root / "packaging" / "installer.nsi").is_file()
    assert (root / "packaging" / "version_info.txt").is_file()


def test_end_user_runtime_does_not_require_admin_manifest():
    root = Path(__file__).resolve().parents[1]
    spec = (root / "FResucitary.spec").read_text(encoding="utf-8")
    assert "uac_admin=False" in spec
    assert "COLLECT(" in spec


def test_installer_targets_x64_program_files():
    root = Path(__file__).resolve().parents[1]
    nsis = (root / "packaging" / "installer.nsi").read_text(encoding="utf-8")
    assert "$PROGRAMFILES64" in nsis
    assert "RequestExecutionLevel admin" in nsis
    assert "RunningX64" in nsis
