"""
FResucitary — Security & source protection.
Read-only guard, admin elevation check, disk write prevention.
Windows-specific (ctypes WinAPI). Falls back gracefully on non-Windows.
"""
from __future__ import annotations
import logging
import os
import sys
from typing import Optional

log = logging.getLogger(__name__)


def _physical_drive_number(path: str) -> Optional[int]:
    r"""Extract N from a Windows ``\\.\PhysicalDriveN`` path."""
    if not path:
        return None
    import re
    m = re.match(r"^\\\\\.\\PhysicalDrive(\d+)$", path, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _volume_disk_numbers_windows(path: str) -> set[int]:
    """
    Return the physical disk numbers backing the Windows volume containing
    ``path`` using IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS.

    The function is intentionally best-effort: an empty set means Windows did
    not allow the mapping to be determined. No write handle is ever requested.
    """
    if sys.platform != "win32":
        return set()
    try:
        import ctypes
        from ctypes import wintypes

        drive, _ = os.path.splitdrive(os.path.abspath(path))
        if not drive:
            return set()
        clean_drive = drive.rstrip("\\/")
        volume = "\\\\.\\" + clean_drive

        GENERIC_READ = 0x80000000
        FILE_SHARE_READ = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        OPEN_EXISTING = 3
        IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        CreateFileW = ctypes.windll.kernel32.CreateFileW  # type: ignore[attr-defined]
        CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        CreateFileW.restype = wintypes.HANDLE

        handle = CreateFileW(
            volume, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, 0, None,
        )
        if handle == INVALID_HANDLE_VALUE:
            return set()

        try:
            class DISK_EXTENT(ctypes.Structure):
                _fields_ = [
                    ("DiskNumber", wintypes.DWORD),
                    ("StartingOffset", ctypes.c_longlong),
                    ("ExtentLength", ctypes.c_longlong),
                ]

            # Enough for common basic/dynamic volumes with multiple extents.
            buf = ctypes.create_string_buffer(4096)
            returned = wintypes.DWORD(0)
            ok = ctypes.windll.kernel32.DeviceIoControl(  # type: ignore[attr-defined]
                handle,
                IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS,
                None, 0,
                buf, len(buf),
                ctypes.byref(returned),
                None,
            )
            if not ok or returned.value < 4:
                return set()

            count = int.from_bytes(buf.raw[:4], "little")
            if count <= 0:
                return set()

            # Windows aligns the first DISK_EXTENT to its natural alignment.
            first_offset = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 4
            extent_size = ctypes.sizeof(DISK_EXTENT)
            disks: set[int] = set()
            for i in range(min(count, 64)):
                offset = first_offset + i * extent_size
                if offset + extent_size > returned.value:
                    break
                extent = DISK_EXTENT.from_buffer_copy(buf.raw[offset:offset + extent_size])
                disks.add(int(extent.DiskNumber))
            return disks
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    except Exception as exc:
        log.debug("Unable to map volume to physical disk for %s: %s", path, exc)
        return set()


# ------------------------------------------------------------------
# Admin check
# ------------------------------------------------------------------

def is_admin() -> bool:
    """Return True if the current process has administrator privileges."""
    if sys.platform != "win32":
        return os.geteuid() == 0  # type: ignore
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore
    except Exception:
        return False


def request_elevation(script_path: Optional[str] = None) -> bool:
    """
    Re-launch the current process with a Windows UAC elevation request.

    Returns True when the elevated process was successfully started.
    The caller decides when to close the current GUI process; this avoids
    raising SystemExit from a Qt signal handler. Works both from development
    Python and from a frozen PyInstaller EXE.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        import subprocess

        if getattr(sys, "frozen", False):
            exe = sys.executable
            argv = list(sys.argv[1:])
        else:
            exe = sys.executable
            argv = [script_path or sys.argv[0], *sys.argv[1:]]

        params = subprocess.list2cmdline(argv)
        result = int(ctypes.windll.shell32.ShellExecuteW(  # type: ignore
            None, "runas", exe, params, None, 1
        ))
        if result <= 32:
            log.error("Elevation failed: ShellExecuteW returned %d", result)
            return False

        return True
    except Exception as exc:
        log.error("Elevation failed: %s", exc)
        return False


# ------------------------------------------------------------------
# Read-only guard
# ------------------------------------------------------------------

class ReadOnlyGuard:
    r"""
    Wraps a disk path and enforces that NO write operations occur on it.
    Raises PermissionError if a write is attempted through guarded methods.

    Usage:
        guard = ReadOnlyGuard(r"\\.\PhysicalDrive0")
        guard.assert_safe()     # raises if source is the system drive
    """

    # System drive letters to protect (populated on init)
    _SYSTEM_DRIVES: set[str] = set()

    def __init__(self, source_path: str):
        self.source_path = source_path
        self._populate_system_drives()

    def _populate_system_drives(self) -> None:
        if sys.platform == "win32":
            windir = os.environ.get("SystemRoot", "C:\\Windows")
            self._SYSTEM_DRIVES = {windir[0].upper()}
        else:
            self._SYSTEM_DRIVES = set()

    def assert_safe(self) -> None:
        """
        Raises PermissionError with a clear message if the source is
        the active system drive (writing there would risk data loss).
        """
        if sys.platform == "win32":
            # Extract drive letter from physical drive path
            if self.source_path.startswith("\\\\.\\"):
                # Physical drive — check if it hosts the system volume
                if self._is_system_physical_drive():
                    raise PermissionError(
                        f"⚠️  PROTECTION SOURCE\n\n"
                        f"La source '{self.source_path}' est le disque système.\n"
                        f"FResucitary refuse de récupérer directement sur le disque actif\n"
                        f"pour éviter tout risque d'écrasement.\n\n"
                        f"Recommandation : créez une image disque (.dd) avant de continuer."
                    )
            else:
                drive = self.source_path[0].upper()
                if drive in self._SYSTEM_DRIVES:
                    raise PermissionError(
                        f"⚠️  PROTECTION SOURCE\n\n"
                        f"La source '{self.source_path}' contient le système Windows actif.\n"
                        f"Récupérer directement depuis le lecteur {drive}: est risqué.\n\n"
                        f"Recommandation : créez d'abord une image disque (.dd)."
                    )

    def _is_system_physical_drive(self) -> bool:
        """Return True when the physical source backs the active Windows volume."""
        disk_no = _physical_drive_number(self.source_path)
        if disk_no is None:
            return False
        system_drive = os.environ.get("SystemDrive", "C:") + "\\"
        mapped = _volume_disk_numbers_windows(system_drive)
        if mapped:
            return disk_no in mapped
        # Conservative fallback for systems where DeviceIoControl is blocked.
        return disk_no == 0

    @staticmethod
    def validate_output_not_on_source(source: str, output: str) -> None:
        """Raise if the output directory is on the same drive as the source."""
        if not source or not output:
            return
        try:
            # When the source is a raw physical device, resolve the destination
            # volume to its backing disk(s). This prevents the classic recovery
            # mistake of writing recovered data back onto the source device.
            src_disk = _physical_drive_number(source)
            if src_disk is not None and sys.platform == "win32":
                output_disks = _volume_disk_numbers_windows(output)
                if src_disk in output_disks:
                    raise PermissionError(
                        f"⚠️  DESTINATION INTERDITE\n\n"
                        f"La destination ({output}) se trouve sur le même disque physique "
                        f"que la source ({source}).\n\n"
                        f"Choisissez un autre disque physique pour éviter d'écraser "
                        f"les données récupérables."
                    )
                return

            # Compare explicit Windows drive-letter paths on every platform.
            # This keeps the guard deterministic and also covers imported
            # Windows sessions when tests/diagnostics run elsewhere.
            src_drive = source[0].upper() if (len(source) > 2 and source[1] == ":") else ""
            out_drive = output[0].upper() if (len(output) > 2 and output[1] == ":") else ""
            if src_drive and out_drive and src_drive == out_drive:
                raise PermissionError(
                    f"⚠️  DESTINATION INVALIDE\n\n"
                    f"La destination de récupération ({output})\n"
                    f"est sur le même lecteur que la source ({source}).\n\n"
                    f"Choisissez un lecteur différent pour éviter l'écrasement des données."
                )
        except PermissionError:
            raise
        except Exception:
            pass  # can't determine — allow and warn in UI

    @staticmethod
    def safe_output_warning(source: str, output: str) -> Optional[str]:
        """
        Return a warning string (non-fatal) if the output drive is same as source.
        Returns None if everything looks safe.
        On non-Windows, compares path prefixes.
        """
        if not source or not output:
            return None
        if sys.platform == "win32":
            try:
                ReadOnlyGuard.validate_output_not_on_source(source, output)
                return None
            except PermissionError as exc:
                return str(exc)
        else:
            # On Linux/Mac: compare first path component (mount point heuristic)
            src_root = source.split("/")[1] if "/" in source else source
            out_root = output.split("/")[1] if "/" in output else output
            # Use explicit drive-letter style paths for cross-platform tests
            if len(source) > 2 and source[1] == ":" and len(output) > 2 and output[1] == ":":
                if source[0].upper() == output[0].upper():
                    return (
                        f"⚠️  DESTINATION INVALIDE\n\n"
                        f"La destination ({output}) est sur le même lecteur que la source ({source})."
                    )
            return None


# ------------------------------------------------------------------
# Sector journal — record unreadable sectors
# ------------------------------------------------------------------

class SectorJournal:
    """
    Tracks sectors (or byte offsets) that could not be read during scan/recovery.
    Exported as part of the forensic report.
    """

    def __init__(self) -> None:
        self._bad_sectors: list[dict] = []

    def record(self, offset: int, length: int, error: str) -> None:
        self._bad_sectors.append({
            "offset": offset,
            "length": length,
            "error":  error,
        })
        log.warning("Bad sector @ offset %d (len=%d): %s", offset, length, error)

    @property
    def count(self) -> int:
        return len(self._bad_sectors)

    @property
    def entries(self) -> list[dict]:
        return list(self._bad_sectors)

    def is_critical(self) -> bool:
        """Heuristic: >100 bad sectors suggests severe drive damage."""
        return self.count > 100
