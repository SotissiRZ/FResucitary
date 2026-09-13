"""
FResucitary — NTFS filesystem adapter (v2 — robust Windows fix).
Deep MFT analysis: deleted entries, data runs, timestamps, bitmap checks.
Requires pytsk3. No PyQt6 imports.
"""
from __future__ import annotations
import logging
import os
import struct
import time
from typing import Iterator, Optional

try:
    import pytsk3
    _TSK_FS_META_FLAG_UNALLOC = pytsk3.TSK_FS_META_FLAG_UNALLOC
    _TSK_FS_NAME_FLAG_UNALLOC = pytsk3.TSK_FS_NAME_FLAG_UNALLOC
    _TSK_FS_META_TYPE_DIR     = pytsk3.TSK_FS_META_TYPE_DIR
    _TSK_VS_PART_FLAG_ALLOC   = getattr(pytsk3, 'TSK_VS_PART_FLAG_ALLOC', 1)
except ImportError:
    pytsk3 = None  # type: ignore
    _TSK_FS_META_FLAG_UNALLOC = 2
    _TSK_FS_NAME_FLAG_UNALLOC = 2
    _TSK_FS_META_TYPE_DIR     = 2
    _TSK_VS_PART_FLAG_ALLOC   = 1

from src.core.models import DeletedFile, FileCategory, ScoreBreakdown
from src.core.signatures import detect_type, category_for_ext, signature_check

log = logging.getLogger(__name__)

MFT_ENTRY_SIZE = 1024
ATTR_DATA          = 0x80
ATTR_FILE_NAME     = 0x30
ATTR_STANDARD_INFO = 0x10
ATTR_END           = 0xFFFFFFFF


def _filetime_to_unix(ft: int) -> float:
    """Normalize a TSK timestamp to Unix seconds.

    pytsk3 exposes filesystem timestamps as Unix seconds for normal FS_Info
    metadata. Some raw NTFS structures, however, use Windows FILETIME. The old
    implementation converted *every* value as FILETIME, turning valid modern
    Unix timestamps into dates centuries before 1970.
    """
    if not ft:
        return 0.0
    try:
        value = int(ft)
        # Unix seconds are ~1e9 today. Windows FILETIME is ~1e17.
        if abs(value) < 100_000_000_000:
            return float(value)
        EPOCH_DIFF = 11644473600
        return value / 10_000_000 - EPOCH_DIFF
    except Exception:
        return 0.0


def _decode_name(raw) -> str:
    """Safely decode a pytsk3 name field (bytes or str)."""
    if raw is None:
        return ""
    if isinstance(raw, (bytes, bytearray)):
        # Try UTF-8 first, then latin-1
        try:
            return raw.rstrip(b"\x00").decode("utf-8", errors="replace")
        except Exception:
            return raw.rstrip(b"\x00").decode("latin-1", errors="replace")
    return str(raw)


class NTFSAdapter:
    """Wraps a pytsk3 FS_Info for an NTFS volume."""

    def __init__(self, img: "pytsk3.Img_Info", partition_offset: int = 0):
        if pytsk3 is None:
            raise RuntimeError("pytsk3 is not installed")
        self._img    = img
        self._offset = partition_offset
        self._fs: pytsk3.FS_Info = pytsk3.FS_Info(img, offset=partition_offset)
        self._block_size: int = self._fs.info.block_size or 4096
        log.info("NTFS adapter opened — block_size=%d offset=%d", self._block_size, partition_offset)

    # ------------------------------------------------------------------
    # Public iterators
    # ------------------------------------------------------------------

    def iter_deleted_quick(self) -> Iterator[DeletedFile]:
        """
        QUICK mode: three-pronged approach.
        1. Recursive directory walk (catches most deleted files in folder tree)
        2. pytsk3 orphan virtual directory (inode 3)
        3. $Recycle.Bin explicit walk
        """
        seen: set[int] = set()

        # 1. Recursive tree walk — MUST be recursive to find files in subfolders
        try:
            root = self._fs.open_dir(inode=5)
            for df in self._walk(root, depth=0, max_depth=8, recurse_allocated=True):
                if df.inode not in seen:
                    seen.add(df.inode)
                    yield df
        except Exception as exc:
            log.warning("Quick walk failed: %s", exc)

        # 2. Orphan virtual dir (inode 3 in pytsk3 NTFS)
        try:
            orphan_dir = self._fs.open_dir(inode=3)
            for entry in orphan_dir:
                name = self._entry_name(entry)
                if not name or name in (".", ".."):
                    continue
                try:
                    meta = entry.info.meta
                    if meta and meta.addr not in seen:
                        df = self._build(entry, name)
                        if df:
                            seen.add(df.inode)
                            yield df
                except Exception:
                    pass
        except Exception:
            pass  # orphan dir not always accessible

        # 3. Recycle Bin
        try:
            for df in self._scan_recycle(seen):
                yield df
        except Exception as exc:
            log.debug("Recycle scan: %s", exc)

    def iter_deleted_deep(self) -> Iterator[DeletedFile]:
        """DEEP mode: full recursive walk + orphan MFT."""
        seen: set[int] = set()
        try:
            root = self._fs.open_dir(inode=5)
            for df in self._walk(root, depth=0, max_depth=64, recurse_allocated=True):
                if df.inode not in seen:
                    seen.add(df.inode)
                    yield df
        except Exception as exc:
            log.warning("Deep walk failed: %s", exc)
        yield from self._iter_orphan_mft(seen)

    def iter_deleted_forensic(self) -> Iterator[DeletedFile]:
        """FORENSIC mode: deep walk + raw $MFT scan."""
        seen: set[int] = set()
        for df in self.iter_deleted_deep():
            seen.add(df.inode)
            yield df
        yield from self._iter_mft_raw(seen)

    # ------------------------------------------------------------------
    # Core walker — handles BOTH flag types
    # ------------------------------------------------------------------

    def _walk(
        self,
        directory: "pytsk3.Directory",
        depth: int,
        max_depth: int,
        recurse_allocated: bool,
    ) -> Iterator[DeletedFile]:
        """
        Walk a directory recursively.
        Yields deleted files found at any depth.
        """
        if depth > max_depth:
            return

        try:
            for entry in directory:
                # Get name safely
                name = self._entry_name(entry)
                if not name or name in (".", ".."):
                    continue

                try:
                    meta = entry.info.meta
                    ni   = entry.info.name
                    if meta is None:
                        continue

                    # Check deletion via BOTH independent flags (OR logic)
                    meta_del = bool(int(meta.flags) & _TSK_FS_META_FLAG_UNALLOC)
                    name_del = False
                    if ni is not None:
                        try:
                            name_del = bool(int(ni.flags) & _TSK_FS_NAME_FLAG_UNALLOC)
                        except Exception:
                            pass

                    is_deleted = meta_del or name_del

                    # Determine if directory
                    try:
                        is_dir = (meta.type == _TSK_FS_META_TYPE_DIR)
                    except Exception:
                        is_dir = False

                    if is_deleted and not is_dir:
                        df = self._build(entry, name)
                        if df:
                            yield df

                    elif is_dir:
                        # Recurse into both deleted and allocated directories
                        if recurse_allocated or is_deleted:
                            try:
                                sub = self._fs.open_dir(inode=meta.addr)
                                yield from self._walk(sub, depth + 1, max_depth, recurse_allocated)
                            except Exception:
                                pass

                except Exception as exc:
                    log.debug("Entry error (depth=%d, name=%s): %s", depth, name, exc)

        except Exception as exc:
            log.debug("Directory iteration error depth=%d: %s", depth, exc)

    # ------------------------------------------------------------------
    # Orphan MFT and raw MFT
    # ------------------------------------------------------------------

    def _iter_orphan_mft(self, seen: set) -> Iterator[DeletedFile]:
        try:
            # pytsk3 exposes orphans via the root dir listing with special inode
            root = self._fs.open_dir(inode=self._fs.info.root_inum)
            for entry in root:
                name = self._entry_name(entry)
                if "$OrphanFiles" in (name or ""):
                    try:
                        orphan_sub = self._fs.open_dir(inode=entry.info.meta.addr)
                        for child in orphan_sub:
                            cname = self._entry_name(child)
                            if not cname or cname in (".", ".."):
                                continue
                            try:
                                meta = child.info.meta
                                if meta and meta.addr not in seen:
                                    df = self._build(child, cname)
                                    if df:
                                        seen.add(df.inode)
                                        yield df
                            except Exception:
                                pass
                    except Exception:
                        pass
        except Exception as exc:
            log.debug("Orphan MFT: %s", exc)

    def _iter_mft_raw(self, seen: set) -> Iterator[DeletedFile]:
        try:
            mft_file = self._fs.open_meta(inode=0)
            size     = mft_file.info.meta.size
            offset   = 0
            while offset < size:
                try:
                    read = mft_file.read_random(offset, MFT_ENTRY_SIZE)
                    if not read or len(read) < 4:
                        break
                    if read[:4] != b"FILE":
                        offset += MFT_ENTRY_SIZE
                        continue
                    flags      = struct.unpack_from("<H", read, 22)[0]
                    is_deleted = not (flags & 0x01)
                    is_dir     = bool(flags & 0x02)
                    rec_idx    = offset // MFT_ENTRY_SIZE
                    if is_deleted and not is_dir and rec_idx not in seen:
                        df = self._parse_raw_mft_record(read, rec_idx)
                        if df:
                            seen.add(rec_idx)
                            yield df
                except Exception:
                    pass
                offset += MFT_ENTRY_SIZE
        except Exception as exc:
            log.warning("Raw MFT: %s", exc)

    def _scan_recycle(self, seen: set) -> Iterator[DeletedFile]:
        for rname in ("$Recycle.Bin", "$RECYCLER", "RECYCLED"):
            try:
                root = self._fs.open_dir(inode=5)
                for entry in root:
                    if self._entry_name(entry).upper() == rname.upper():
                        rdir = self._fs.open_dir(inode=entry.info.meta.addr)
                        for sub in rdir:
                            sname = self._entry_name(sub)
                            if not sname or sname in (".", ".."):
                                continue
                            try:
                                sid_dir = self._fs.open_dir(inode=sub.info.meta.addr)
                                for fentry in sid_dir:
                                    fname = self._entry_name(fentry)
                                    if not fname or fname in (".", ".."):
                                        continue
                                    try:
                                        meta = fentry.info.meta
                                        if meta and meta.addr not in seen:
                                            df = self._build(fentry, fname)
                                            if df:
                                                df.in_recycle_bin = True
                                                seen.add(df.inode)
                                                yield df
                                    except Exception:
                                        pass
                            except Exception:
                                pass
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Build DeletedFile
    # ------------------------------------------------------------------

    def _build(self, entry: "pytsk3.File", name: str) -> Optional[DeletedFile]:
        try:
            meta = entry.info.meta
            if meta is None:
                return None
            try:
                if meta.type == _TSK_FS_META_TYPE_DIR:
                    return None
            except Exception:
                pass

            size  = int(meta.size) if meta.size else 0
            inode = int(meta.addr)

            # Capture the NTFS sequence number associated with the directory
            # name. It lets recovery detect an inode/MFT record that has been
            # reused after deletion instead of silently reading another file.
            meta_seq = 0
            try:
                ni = entry.info.name
                meta_seq = int(getattr(ni, "meta_seq", 0) or 0) if ni else 0
            except Exception:
                pass
            if not meta_seq:
                try:
                    meta_seq = int(getattr(meta, "seq", 0) or 0)
                except Exception:
                    meta_seq = 0

            # Select the unnamed $DATA attribute now, while we still have the
            # exact directory entry returned by TSK. Store its id and run map
            # so recovery does not have to guess a stream later by inode only.
            attr_type, attr_id, attr_flags, attr_size, data_runs = self._select_data_attribute(entry, size)
            if size <= 0 and attr_size > 0:
                size = attr_size

            try:
                mtime = _filetime_to_unix(int(meta.mtime)) if meta.mtime else 0.0
            except Exception:
                mtime = 0.0
            try:
                ctime = _filetime_to_unix(int(meta.crtime)) if meta.crtime else 0.0
            except Exception:
                ctime = 0.0

            in_recycle = ("$Recycle" in name or "$recycle" in name.lower() or
                          "$RECYCLE" in name)

            header = self._read_header(entry, inode, size, attr_type, attr_id, length=4096)
            ext    = os.path.splitext(name)[1].lower()
            sig    = detect_type(header, ext) if header else None

            clusters = [addr for _off, addr, run_len, _flags in data_runs if addr >= 0 and run_len > 0]
            runs_count = len([1 for _off, _addr, run_len, _flags in data_runs if run_len > 0])

            df = DeletedFile(
                name=name,
                path=name,
                inode=inode,
                meta_flags=int(meta.flags),
                size=size,
                file_type_by_ext=ext.lstrip("."),
                file_type_by_magic=sig.ext if sig else "",
                category=sig.category if sig else category_for_ext(ext),
                clusters=clusters[:32],
                fragmented=runs_count > 1,
                data_runs_count=runs_count,
                data_runs=data_runs,
                data_attr_type=attr_type,
                data_attr_id=attr_id,
                data_attr_flags=attr_flags,
                data_attr_size=attr_size,
                meta_seq=meta_seq,
                mft_record_valid=True,
                in_recycle_bin=in_recycle,
                deleted_at=mtime,
                modified_at=mtime,
                created_at=ctime,
                header_bytes=header,
            )
            df.score = self._compute_score(df)
            return df

        except Exception as exc:
            log.debug("_build failed name=%r: %s", name, exc)
            return None

    def _parse_raw_mft_record(self, record: bytes, idx: int) -> Optional[DeletedFile]:
        try:
            attr_off = struct.unpack_from("<H", record, 20)[0]
            name = ""; size = 0
            while attr_off + 8 < MFT_ENTRY_SIZE:
                attr_type = struct.unpack_from("<I", record, attr_off)[0]
                if attr_type == ATTR_END or attr_type == 0:
                    break
                attr_len = struct.unpack_from("<I", record, attr_off + 4)[0]
                if attr_len == 0 or attr_len > MFT_ENTRY_SIZE:
                    break
                if attr_type == ATTR_FILE_NAME and record[attr_off + 8] == 0:
                    co = struct.unpack_from("<H", record, attr_off + 20)[0]
                    ao = attr_off + co
                    if ao + 66 < MFT_ENTRY_SIZE:
                        nl = record[ao + 64]
                        ns = ao + 66
                        ne = ns + nl * 2
                        if ne <= MFT_ENTRY_SIZE:
                            name = record[ns:ne].decode("utf-16-le", errors="replace")
                elif attr_type == ATTR_DATA:
                    if record[attr_off + 8] == 0:
                        size = struct.unpack_from("<I", record, attr_off + 16)[0]
                    else:
                        try:
                            size = struct.unpack_from("<Q", record, attr_off + 48)[0]
                        except Exception:
                            size = 0
                attr_off += attr_len
            if not name or name.startswith("$"):
                return None
            ext = os.path.splitext(name)[1].lower()
            df  = DeletedFile(
                name=name, path=f"[MFT#{idx}]/{name}", inode=idx,
                meta_flags=0, size=size, file_type_by_ext=ext.lstrip("."),
                category=category_for_ext(ext), mft_record_valid=False,
            )
            df.score = self._compute_score(df)
            return df
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _attr_name(info) -> str:
        raw = getattr(info, "name", None)
        if not raw:
            return ""
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw).rstrip(b"\x00").decode("utf-8", errors="replace")
        return str(raw)

    def _select_data_attribute(
        self, entry: "pytsk3.File", meta_size: int
    ) -> tuple[int, int, int, int, list[tuple[int, int, int, int]]]:
        """Return the most likely primary (unnamed) NTFS $DATA stream."""
        candidates = []
        try:
            for attr in entry:
                info = getattr(attr, "info", None)
                if info is None:
                    continue
                try:
                    attr_type = int(info.type)
                except Exception:
                    continue
                if attr_type != ATTR_DATA:
                    continue
                attr_id = int(getattr(info, "id", -1) or 0)
                attr_flags = int(getattr(info, "flags", 0) or 0)
                attr_size = int(getattr(info, "size", 0) or 0)
                name = self._attr_name(info)
                runs: list[tuple[int, int, int, int]] = []
                try:
                    for run in attr:
                        runs.append((
                            int(getattr(run, "offset", 0) or 0),
                            int(getattr(run, "addr", -1)),
                            int(getattr(run, "len", 0) or 0),
                            int(getattr(run, "flags", 0) or 0),
                        ))
                except Exception:
                    pass
                # Primary file content is normally the unnamed DATA stream.
                rank = (
                    1 if not name else 0,
                    1 if meta_size > 0 and attr_size == meta_size else 0,
                    attr_size,
                )
                candidates.append((rank, attr_type, attr_id, attr_flags, attr_size, runs))
        except Exception:
            pass

        if not candidates:
            return ATTR_DATA, -1, 0, 0, []
        candidates.sort(key=lambda item: item[0], reverse=True)
        _rank, attr_type, attr_id, attr_flags, attr_size, runs = candidates[0]
        return attr_type, attr_id, attr_flags, attr_size, runs

    def _read_header(
        self,
        entry: "pytsk3.File",
        inode: int,
        size: int,
        attr_type: int,
        attr_id: int,
        length: int = 4096,
    ) -> bytes:
        if size == 0:
            return b""
        amount = min(length, size)
        try:
            if attr_id >= 0:
                data = entry.read_random(0, amount, attr_type, attr_id)
            else:
                data = entry.read_random(0, amount)
            return bytes(data) if data else b""
        except Exception:
            try:
                f = self._fs.open_meta(inode=inode)
                if attr_id >= 0:
                    data = f.read_random(0, amount, attr_type, attr_id)
                else:
                    data = f.read_random(0, amount)
                return bytes(data) if data else b""
            except Exception:
                return b""

    @staticmethod
    def _entry_name(entry) -> str:
        try:
            ni = entry.info.name
            if ni is None:
                return ""
            raw = ni.name
            return _decode_name(raw)
        except Exception:
            return ""

    def _compute_score(self, df: DeletedFile) -> ScoreBreakdown:
        s = ScoreBreakdown()
        if df.mft_record_valid:
            s.metadata_score += 20
        if df.size > 0:
            s.metadata_score += 10
        if df.created_at > 0 and df.modified_at > 0:
            s.metadata_score += 6
        if df.path and df.path != df.name:
            s.metadata_score += 4

        if df.data_runs_count == 1:   s.cluster_score = 25
        elif df.data_runs_count == 2: s.cluster_score = 18
        elif df.data_runs_count <= 4: s.cluster_score = 10
        elif df.data_runs_count > 0:  s.cluster_score = 4
        elif df.mft_record_valid:     s.cluster_score = 8

        if df.header_bytes:
            if df.file_type_by_magic:
                s.header_score = 15 if df.file_type_by_magic == df.file_type_by_ext else 10
            else:
                s.header_score = 3

        mb = df.size / (1024 * 1024)
        if df.size == 0:    s.size_score = 0
        elif mb < 1:        s.size_score = 10
        elif mb < 10:       s.size_score = 8
        elif mb < 100:      s.size_score = 5
        elif mb < 500:      s.size_score = 2
        else:               s.size_score = 1

        if df.file_type_by_magic in {"jpg","jpeg","png","gif","bmp","webp",
                                      "pdf","txt","xml","html","mp3","flac"}:
            s.preview_score = 5
        if df.in_recycle_bin:
            s.recycle_score = 3
        if df.deleted_at > 0:
            age = (time.time() - df.deleted_at) / 86400
            s.timestamp_score = 2 if age < 1 else 1 if age < 7 else 0

        # A known extension with contradictory magic bytes is strong evidence
        # that the deleted clusters have already been reused. Do not label such
        # a file "Bon" merely because the old metadata still looks coherent.
        sig_state = signature_check(df.header_bytes, df.file_type_by_ext)
        if sig_state is False:
            s.confidence_cap = min(s.confidence_cap, 39)

        # The directory metadata and the captured DATA stream should agree on
        # size. A disagreement is typical of a reused/stale MFT reference.
        if df.size > 0 and df.data_attr_size > 0 and df.size != df.data_attr_size:
            s.confidence_cap = min(s.confidence_cap, 49)

        return s

    # ------------------------------------------------------------------
    # Data streaming for recovery
    # ------------------------------------------------------------------

    def _read_raw_runs(
        self,
        data_runs: list[tuple[int, int, int, int]],
        size: int,
        chunk_size: int,
    ) -> Iterator[bytes]:
        """Reconstruct a non-resident stream directly from captured NTFS runs."""
        if not data_runs or size <= 0:
            return
        logical = 0
        for run_off, addr, run_len, run_flags in sorted(data_runs, key=lambda r: r[0]):
            file_start = int(run_off) * self._block_size
            run_bytes = int(run_len) * self._block_size
            if run_bytes <= 0 or file_start >= size:
                continue

            # Preserve logical holes. Sparse/deallocated runs can be represented
            # with a negative address by TSK.
            if file_start > logical:
                gap = min(size - logical, file_start - logical)
                while gap > 0:
                    n = min(chunk_size, gap)
                    yield b"\x00" * n
                    logical += n
                    gap -= n

            if logical >= size:
                break

            usable = min(run_bytes, size - file_start)
            # TSK run flag 0x02 denotes a sparse (logical zero) run.
            if addr < 0 or (int(run_flags) & 0x02):
                remaining = usable
                while remaining > 0:
                    n = min(chunk_size, remaining)
                    yield b"\x00" * n
                    remaining -= n
                    logical += n
                continue

            disk_offset = self._offset + int(addr) * self._block_size
            consumed = 0
            while consumed < usable:
                n = min(chunk_size, usable - consumed)
                try:
                    chunk = bytes(self._img.read(disk_offset + consumed, n))
                except Exception as exc:
                    log.warning("raw run read failed @%d: %s", disk_offset + consumed, exc)
                    return
                if not chunk:
                    return
                yield chunk
                consumed += len(chunk)
                logical = file_start + consumed
                if len(chunk) < n:
                    return

    def read_file_data(
        self,
        inode: int,
        size: int,
        chunk_size: int = 1024*1024,
        *,
        attr_type: int = 0,
        attr_id: int = -1,
        meta_seq: int = 0,
        data_runs: Optional[list[tuple[int, int, int, int]]] = None,
        data_attr_flags: int = 0,
    ) -> Iterator[bytes]:
        """
        Stream the deleted file using the exact $DATA attribute captured during
        scanning. For normal non-resident streams, prefer the captured run map:
        it survives MFT/inode reuse and avoids reading a different stream later.
        """
        # NTFS attribute flags: ENC=0x10, COMP=0x20. Raw run reconstruction
        # cannot transparently decrypt/decompress those streams, so let TSK read
        # them through the attribute API instead.
        run_flags = 0
        for _off, _addr, _len, flags in (data_runs or []):
            run_flags |= int(flags or 0)
        # Run flag 0x01=filler (unknown/lost), 0x04=encrypted. Avoid raw
        # reconstruction in those cases and let TSK handle the attribute.
        raw_safe = (
            bool(data_runs)
            and not (int(data_attr_flags or 0) & 0x30)
            and not (run_flags & 0x05)
        )
        if raw_safe:
            yield from self._read_raw_runs(list(data_runs or []), size, chunk_size)
            return

        try:
            f = self._fs.open_meta(inode=inode)
            current_seq = 0
            try:
                current_seq = int(getattr(f.info.meta, "seq", 0) or 0)
            except Exception:
                pass
            if meta_seq and current_seq and int(meta_seq) != current_seq:
                raise RuntimeError(
                    f"MFT réutilisée: séquence attendue {meta_seq}, actuelle {current_seq}"
                )

            offset = 0
            remaining = size
            while remaining > 0:
                to_read = min(chunk_size, remaining)
                if attr_id >= 0:
                    chunk = f.read_random(offset, to_read, int(attr_type or ATTR_DATA), int(attr_id))
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
            log.warning("read_file_data inode=%d: %s", inode, exc)

    def close(self) -> None:
        self._fs = None
