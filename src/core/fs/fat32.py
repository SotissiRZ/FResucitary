"""
FResucitary — FAT32 / exFAT filesystem adapter.
Wraps pytsk3 for FAT-family volumes.
Deleted entries detection via directory entry flags.
No PyQt6 imports.
"""
from __future__ import annotations
import logging
import os
from typing import Iterator, Optional

try:
    import pytsk3
except ImportError:
    pytsk3 = None  # type: ignore

from src.core.models import DeletedFile, FileCategory, ScoreBreakdown
from src.core.signatures import detect_type, category_for_ext, signature_check

log = logging.getLogger(__name__)

# FAT directory entry: first byte 0xE5 = deleted
FAT_DELETED_MARKER = 0xE5


class FAT32Adapter:
    """
    Adapter for FAT12, FAT16, FAT32 and exFAT volumes via pytsk3.
    Simpler than NTFS: no MFT, directory entries mark deletion with 0xE5.
    """

    def __init__(self, img: "pytsk3.Img_Info", partition_offset: int = 0):
        if pytsk3 is None:
            raise RuntimeError("pytsk3 is not installed")
        self._img    = img
        self._offset = partition_offset
        self._fs: pytsk3.FS_Info = pytsk3.FS_Info(img, offset=partition_offset)
        self._block_size = self._fs.info.block_size or 512
        log.info("FAT32 adapter opened — offset=%d", partition_offset)

    def iter_deleted(self) -> Iterator[DeletedFile]:
        """Walk the entire directory tree and yield deleted entries."""
        yield from self._walk_dir(self._fs.open_dir(inode=self._fs.info.root_inum))

    def _walk_dir(
        self, directory: "pytsk3.Directory", depth: int = 0
    ) -> Iterator[DeletedFile]:
        if depth > 32:
            return
        try:
            for entry in directory:
                name = self._entry_name(entry)
                if not name or name in (".", ".."):
                    continue
                try:
                    meta = entry.info.meta
                    if meta is None:
                        continue
                    if meta.flags & pytsk3.TSK_FS_META_FLAG_UNALLOC:
                        df = self._build(entry, name)
                        if df:
                            yield df
                    # Recurse into allocated directories
                    if (meta.type == pytsk3.TSK_FS_META_TYPE_DIR
                            and meta.flags & pytsk3.TSK_FS_META_FLAG_ALLOC):
                        try:
                            sub = self._fs.open_dir(inode=meta.addr)
                            yield from self._walk_dir(sub, depth + 1)
                        except Exception:
                            pass
                except Exception as exc:
                    log.debug("FAT entry error: %s", exc)
        except Exception as exc:
            log.warning("FAT dir walk error: %s", exc)

    def _build(self, entry: "pytsk3.File", name: str) -> Optional[DeletedFile]:
        try:
            meta  = entry.info.meta
            size  = meta.size or 0
            inode = meta.addr

            header = self._read_header(inode, size)
            ext    = os.path.splitext(name)[1].lower()
            sig    = detect_type(header, ext) if header else None

            df = DeletedFile(
                name=name,
                path=name,
                inode=inode,
                meta_flags=int(meta.flags),
                size=size,
                file_type_by_ext=ext.lstrip("."),
                file_type_by_magic=sig.ext if sig else "",
                category=sig.category if sig else category_for_ext(ext),
                mft_record_valid=True,
                header_bytes=header,
            )
            df.score = self._score(df)
            return df
        except Exception as exc:
            log.debug("FAT build error: %s", exc)
            return None

    def _read_header(self, inode: int, size: int, length: int = 512) -> bytes:
        if size == 0:
            return b""
        try:
            f = self._fs.open_meta(inode=inode)
            return bytes(f.read_random(0, min(length, size)))
        except Exception:
            return b""

    @staticmethod
    def _entry_name(entry: "pytsk3.File") -> str:
        try:
            ni = entry.info.name
            if ni and ni.name:
                return ni.name.decode("utf-8", errors="replace")
        except Exception:
            pass
        return ""

    @staticmethod
    def _score(df: DeletedFile) -> ScoreBreakdown:
        """Simplified scoring for FAT — no cluster run analysis."""
        import time
        s = ScoreBreakdown()
        # Metadata: FAT entries are simpler — valid if inode > 0
        if df.inode > 0:
            s.metadata_score = 20
        if df.size > 0:
            s.metadata_score += 10
        # Header
        if df.header_bytes:
            if df.file_type_by_magic:
                s.header_score = 15 if df.file_type_by_magic == df.file_type_by_ext else 10
            else:
                s.header_score = 3
        # Cluster (no run info on FAT — assume worst case)
        s.cluster_score = 8
        # Size
        mb = df.size / (1024 * 1024)
        if df.size == 0:    s.size_score = 0
        elif mb < 1:        s.size_score = 10
        elif mb < 10:       s.size_score = 8
        elif mb < 100:      s.size_score = 5
        else:               s.size_score = 1
        # Preview
        if df.file_type_by_magic in {"jpg","png","pdf","txt","mp3","flac"}:
            s.preview_score = 5
        if signature_check(df.header_bytes, df.file_type_by_ext) is False:
            s.confidence_cap = min(s.confidence_cap, 39)
        return s

    def read_file_data(
        self, inode: int, size: int, chunk_size: int = 1024*1024, *,
        attr_type: int = 0, attr_id: int = -1, meta_seq: int = 0,
        data_runs=None, data_attr_flags: int = 0,
    ) -> Iterator[bytes]:
        try:
            f = self._fs.open_meta(inode=inode)
            offset, remaining = 0, size
            while remaining > 0:
                to_read = min(chunk_size, remaining)
                if attr_id >= 0:
                    chunk = f.read_random(offset, to_read, int(attr_type), int(attr_id))
                else:
                    chunk = f.read_random(offset, to_read)
                if not chunk:
                    break
                data = bytes(chunk)
                yield data
                offset += len(data)
                remaining -= len(data)
                if len(data) < to_read:
                    break
        except Exception as exc:
            log.warning("FAT read_file_data inode=%d: %s", inode, exc)

    def close(self) -> None:
        self._fs = None
