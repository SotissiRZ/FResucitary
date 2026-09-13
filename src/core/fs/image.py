"""
FResucitary — Disk / image source opener.
Opens physical drives (\\\\.\\PhysicalDriveN) and image files.
Detects partition layout and selects the right offset for NTFS.
No PyQt6 imports.
"""
from __future__ import annotations
import logging
import os
import struct
from dataclasses import dataclass
from typing import Optional

try:
    import pytsk3
except ImportError:
    pytsk3 = None  # type: ignore

log = logging.getLogger(__name__)

# Sector size assumed for MBR/GPT parsing
SECTOR_SIZE = 512

# Known NTFS/FAT signatures
FS_NTFS  = b"NTFS    "   # at offset 3 in boot sector
FS_FAT32 = b"FAT32   "
FS_FAT16 = b"FAT16   "
FS_FAT12 = b"FAT12   "
FS_EXFAT = b"EXFAT   "


@dataclass
class PartitionInfo:
    index:       int
    offset_bytes: int
    size_bytes:  int
    fs_type:     str   # "NTFS", "FAT32", "exFAT", "Unknown"
    label:       str = ""

    @property
    def offset_sectors(self) -> int:
        return self.offset_bytes // SECTOR_SIZE

    def __str__(self) -> str:
        gb = self.size_bytes / (1024 ** 3)
        return f"Partition {self.index}: {self.fs_type} @ {self.offset_bytes} ({gb:.1f} GB)"


class DiskSource:
    """
    Unified source abstraction for both raw images and physical drives.
    Opens in strict READ-ONLY mode.
    """

    def __init__(self, path: str):
        if pytsk3 is None:
            raise RuntimeError("pytsk3 is not installed")
        self.path = path
        self._img: Optional["pytsk3.Img_Info"] = None
        self._open()

    def _open(self) -> None:
        is_physical = self.path.startswith("\\\\.\\")
        try:
            if is_physical:
                self._img = pytsk3.Img_Info(url=self.path)
            else:
                ext = os.path.splitext(self.path)[1].lower()
                if ext in (".img", ".dd", ".raw", ""):
                    self._img = pytsk3.Img_Info(url=self.path)
                elif ext in (".iso",):
                    self._img = pytsk3.Img_Info(url=self.path)
                elif ext in (".vhd", ".vhdx"):
                    # pytsk3 can open VHD via afflib if available; fallback to raw
                    self._img = pytsk3.Img_Info(url=self.path)
                else:
                    self._img = pytsk3.Img_Info(url=self.path)
            log.info("Opened source: %s", self.path)
        except Exception as exc:
            raise RuntimeError(f"Cannot open source '{self.path}': {exc}") from exc

    @property
    def img(self) -> "pytsk3.Img_Info":
        assert self._img is not None
        return self._img

    # ------------------------------------------------------------------
    # Partition detection
    # ------------------------------------------------------------------

    def detect_partitions(self) -> list[PartitionInfo]:
        """
        Detect partitions using pytsk3 volume system.
        Falls back to treating the whole image as a single partition.
        """
        partitions: list[PartitionInfo] = []
        try:
            vol = pytsk3.Volume_Info(self._img)
            block_size = int(getattr(vol.info, "block_size", 0) or SECTOR_SIZE)
            alloc_flag = int(pytsk3.TSK_VS_PART_FLAG_ALLOC)
            idx = 0
            for part in vol:
                # TSK flags are bit fields; equality misses combined flags on
                # some partition tables.
                if int(part.flags) & alloc_flag:
                    offset = int(part.start) * block_size
                    size   = int(part.len) * block_size
                    fs_type = self._probe_fs(offset)
                    partitions.append(PartitionInfo(
                        index=idx,
                        offset_bytes=offset,
                        size_bytes=size,
                        fs_type=fs_type,
                    ))
                    idx += 1
            log.info("Detected %d partition(s)", len(partitions))
        except Exception as exc:
            log.info("No usable partition table found (%s) — using offset 0", exc)

        # Raw filesystem images (and a few malformed partition tables) can
        # produce an empty Volume_Info without raising. Always provide a
        # whole-source fallback so a scan cannot silently do nothing.
        if not partitions:
            fs_type = self._probe_fs(0)
            partitions.append(PartitionInfo(
                index=0,
                offset_bytes=0,
                size_bytes=0,
                fs_type=fs_type,
            ))
        return partitions

    def _probe_fs(self, offset: int) -> str:
        """Read boot sector at offset and identify the filesystem."""
        try:
            sector = self._img.read(offset, SECTOR_SIZE)
            if len(sector) < 11:
                return "Unknown"
            oem = sector[3:11]
            if oem == FS_NTFS:
                return "NTFS"
            if oem == FS_EXFAT:
                return "exFAT"

            # FAT12/16/32 do not normally store the filesystem label in the
            # OEM field. FAT12/16 use bytes 54..61; FAT32 uses 82..89.
            fat_1216 = sector[54:62] if len(sector) >= 62 else b""
            fat_32   = sector[82:90] if len(sector) >= 90 else b""
            if fat_32 == FS_FAT32:
                return "FAT32"
            if fat_1216 == FS_FAT16:
                return "FAT16"
            if fat_1216 == FS_FAT12:
                return "FAT12"
            # Try EXT2/3/4 magic at +0x438
            if len(sector) >= 512:
                try:
                    ext_sector = self._img.read(offset + 1024, 512)
                    if ext_sector[56:58] == b"\x53\xEF":
                        return "EXT4"
                except Exception:
                    pass
        except Exception as exc:
            log.debug("probe_fs at %d: %s", offset, exc)
        return "Unknown"

    def close(self) -> None:
        self._img = None

    def __enter__(self) -> "DiskSource":
        return self

    def __exit__(self, *_) -> None:
        self.close()


# ------------------------------------------------------------------
# Disk imager — create .dd image before recovery on physical drives
# ------------------------------------------------------------------

class DiskImager:
    """
    Creates a raw sector-by-sector .dd image of a source device.
    Used to protect the source before any recovery attempt.
    Pure read operations only.
    """

    def __init__(self, source_path: str, output_path: str, chunk_size: int = 4 * 1024 * 1024):
        self.source_path  = source_path
        self.output_path  = output_path
        self.chunk_size   = chunk_size
        self._cancelled   = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self, progress_cb=None) -> tuple[bool, str]:
        """
        Run imaging. progress_cb(bytes_done, bytes_total) called periodically.
        Returns (success, error_message).
        """
        try:
            src = DiskSource(self.source_path)
            img = src.img
            # Get total size
            try:
                total = img.get_size()
            except Exception:
                total = 0

            written = 0
            with open(self.output_path, "wb") as out:
                offset = 0
                while not self._cancelled:
                    if total and offset >= total:
                        break
                    to_read = self.chunk_size if not total else min(self.chunk_size, total - offset)
                    try:
                        chunk = img.read(offset, to_read)
                    except Exception as exc:
                        if not total:
                            # Without a known device size, synthesising endless
                            # zero chunks would create an infinite image. Abort
                            # instead and report the failing offset.
                            raise RuntimeError(
                                f"Lecture impossible à l'offset {offset} et taille source inconnue: {exc}"
                            ) from exc
                        # Preserve exact alignment for a known-size image.
                        chunk = b"\x00" * to_read
                    if not chunk:
                        break
                    if total and len(chunk) > to_read:
                        chunk = chunk[:to_read]
                    out.write(chunk)
                    written += len(chunk)
                    offset  += len(chunk)
                    if progress_cb and total:
                        progress_cb(written, total)

            src.close()
            if self._cancelled:
                return False, "Imaging annulée par l'utilisateur."
            return True, ""
        except Exception as exc:
            return False, str(exc)
