"""
FResucitary — File carving engine.
Scans raw disk blocks for file signatures (200+ types).
Does NOT require MFT metadata — recovers data even when entries are wiped.
No PyQt6 imports.
"""
from __future__ import annotations
import logging
import os
import time
from typing import Callable, Iterator, Optional

from src.core.models import DeletedFile, ScoreBreakdown
from src.core.signatures import SIGNATURES, Signature

log = logging.getLogger(__name__)

# Block size for raw scanning (512 KB = good balance of speed vs memory)
CARVE_BLOCK_SIZE  = 512 * 1024
# Overlap to catch signatures split across block boundaries
OVERLAP_SIZE      = 256
# Maximum number of carved files (prevent runaway on heavily zeroed disks)
MAX_CARVED_FILES  = 50_000


class CarvingEngine:
    """
    Stateless carver: takes an img reader and emits DeletedFile objects
    for every signature match found.

    Usage:
        carver = CarvingEngine(img_read_fn, disk_size)
        for df in carver.carve(progress_cb):
            ...
    """

    def __init__(
        self,
        read_fn: Callable[[int, int], bytes],
        disk_size: int,
        enabled_categories: Optional[set] = None,
    ):
        """
        read_fn(offset, length) -> bytes   — pure read, never writes
        disk_size                          — total bytes to scan
        enabled_categories                 — None = all categories
        """
        self._read          = read_fn
        self._disk_size     = disk_size
        self._enabled_cats  = enabled_categories
        self._cancelled     = False

        # Filter signatures to enabled categories
        self._sigs: list[Signature] = [
            s for s in SIGNATURES
            if (enabled_categories is None or s.category in enabled_categories)
            and s.offset == 0  # carver only handles offset-0 signatures for now
        ]
        # Build first-byte lookup for fast rejection
        self._fb_index: dict[int, list[Signature]] = {}
        for sig in self._sigs:
            if sig.magic:
                self._fb_index.setdefault(sig.magic[0], []).append(sig)

        log.info("Carver initialised: %d signatures, disk_size=%d MB",
                 len(self._sigs), disk_size // (1024*1024))

    def cancel(self) -> None:
        self._cancelled = True

    def carve_parallel(
        self,
        n_workers: int = 4,
        progress_cb=None,
    ) -> list:
        """
        Parallel carving: split disk into N slices, scan each in a thread.
        ~N× faster than single-threaded carve() on SSD/image files.
        Use for disks > 100 GB in DEEP/FORENSIC modes.
        """
        import concurrent.futures
        import threading

        slice_size = self._disk_size // max(n_workers, 1)
        slices = [
            (i * slice_size,
             (i + 1) * slice_size if i < n_workers - 1 else self._disk_size)
            for i in range(n_workers)
        ]

        all_results = []
        lock        = threading.Lock()
        done_bytes  = [0]

        def scan_slice(start, end):
            results, offset, prev_tail = [], start, b""
            while offset < end and not self._cancelled:
                to_read = min(CARVE_BLOCK_SIZE, end - offset)
                try:
                    block = self._read(offset, to_read)
                except Exception:
                    offset += CARVE_BLOCK_SIZE
                    continue
                if not block:
                    break
                combined = prev_tail + block
                base     = offset - len(prev_tail)
                for sig, abs_offset, header in self._scan_buffer(combined, base):
                    size = self._estimate_size(sig, abs_offset, header)
                    results.append(self._build_carved_file(sig, abs_offset, size, header, abs_offset))
                prev_tail = block[-OVERLAP_SIZE:] if len(block) >= OVERLAP_SIZE else block
                offset   += len(block)
                with lock:
                    done_bytes[0] += len(block)
                    if progress_cb:
                        progress_cb(done_bytes[0], self._disk_size)
            return results

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [pool.submit(scan_slice, s, e) for s, e in slices]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    all_results.extend(fut.result())
                except Exception as exc:
                    log.warning("Parallel carver slice failed: %s", exc)

        all_results.sort(key=lambda df: df.clusters[0] if df.clusters else 0)
        log.info("Parallel carver: %d files found across %d workers", len(all_results), n_workers)
        return all_results

    def carve(
        self,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> Iterator[DeletedFile]:
        """
        Scan the disk sequentially and yield carved DeletedFile objects.
        progress_cb(offset, total) called every block.
        """
        offset     = 0
        prev_tail  = b""
        found      = 0
        seen_offsets: set[int] = set()
        start_time = time.time()

        while offset < self._disk_size and not self._cancelled:
            # Read block + overlap prefix
            try:
                block = self._read(offset, CARVE_BLOCK_SIZE)
            except Exception as exc:
                log.debug("Carver read error @ %d: %s", offset, exc)
                offset += CARVE_BLOCK_SIZE
                continue

            if not block:
                break

            # ── Zero-block fast-skip ─────────────────────────────────
            # On real disks 80-99% of blocks are unallocated (all zeros).
            # Strategy: sample first byte + 3 positions — O(1), not O(n).
            # If all sampled bytes are zero, skip with high confidence.
            # Overlap tail still processed for cross-boundary signatures.
            blen = len(block)
            if not prev_tail and blen >= 64:
                # Sample 4 positions spread across the block
                s0 = block[0]
                s1 = block[blen >> 2]
                s2 = block[blen >> 1]
                s3 = block[-1]
                if s0 == 0 and s1 == 0 and s2 == 0 and s3 == 0:
                    # High probability zero block — verify first-byte of each sig
                    first_bytes_present = False
                    for fb in self._fb_index:
                        if block.find(bytes([fb])) != -1:
                            first_bytes_present = True
                            break
                    if not first_bytes_present:
                        offset += blen
                        if progress_cb:
                            progress_cb(offset, self._disk_size)
                        continue

            # Combined buffer: tail of previous block + current block
            combined = prev_tail + block

            # Scan combined buffer for signatures
            for match in self._scan_buffer(combined, offset - len(prev_tail)):
                sig, file_offset, header = match
                # The overlap tail is scanned twice by design; suppress the
                # second report of signatures already emitted in the previous
                # block.
                if file_offset in seen_offsets:
                    continue
                seen_offsets.add(file_offset)
                size = self._estimate_size(sig, file_offset, header)
                df   = self._build_carved_file(sig, file_offset, size, header, found)
                yield df
                found += 1
                if found >= MAX_CARVED_FILES:
                    log.warning("MAX_CARVED_FILES reached — stopping carver")
                    return

            # Retain tail for next iteration overlap
            prev_tail = block[-OVERLAP_SIZE:] if len(block) >= OVERLAP_SIZE else block
            offset   += len(block)

            if progress_cb:
                progress_cb(offset, self._disk_size)

        elapsed = time.time() - start_time
        log.info("Carver finished: %d files in %.1fs", found, elapsed)

    # ------------------------------------------------------------------
    # Internal: buffer scanning
    # ------------------------------------------------------------------

    def _scan_buffer(
        self, buf: bytes, base_offset: int
    ) -> Iterator[tuple[Signature, int, bytes]]:
        """
        Vectorized scan using bytes.find() per signature.
        ~200x faster than byte-by-byte iteration.
        Deduplicates hits: only the first matching signature per offset is yielded.
        """
        # Track offsets already claimed to avoid double-reporting overlapping magics
        claimed: set[int] = set()

        for sig in self._sigs:
            if sig.offset != 0:
                continue
            magic  = sig.magic
            mlen   = len(magic)
            cursor = 0
            while True:
                pos = buf.find(magic, cursor)
                if pos == -1:
                    break
                abs_offset = base_offset + pos
                if abs_offset >= 0 and abs_offset not in claimed:
                    claimed.add(abs_offset)
                    yield sig, abs_offset, buf[pos: pos + 512]
                cursor = pos + mlen

    # ------------------------------------------------------------------
    # Internal: size estimation
    # ------------------------------------------------------------------

    def _estimate_size(self, sig: Signature, offset: int, header: bytes) -> int:
        """
        Estimate the carved file size.
        1. Try footer search in the next `max_size` bytes.
        2. Fall back to inline size fields (where known).
        3. Fall back to sig.max_size.
        """
        # Format-specific inline size extraction
        inline = self._inline_size(sig, header)
        if inline and 0 < inline <= sig.max_size:
            return inline

        # Footer search
        if sig.footer:
            found_size = self._search_footer(sig, offset)
            if found_size:
                return found_size

        # Conservative fallback — avoid over-allocation
        return min(sig.max_size, 10 * 1024 * 1024)

    def _inline_size(self, sig: Signature, header: bytes) -> Optional[int]:
        """Extract size from format-specific header fields."""
        ext = sig.ext.lower()
        if ext == "bmp" and len(header) >= 6:
            return int.from_bytes(header[2:6], "little")
        if ext in ("wav", "avi") and len(header) >= 8:
            # RIFF chunk size at offset 4 + 8 bytes for RIFF header
            return int.from_bytes(header[4:8], "little") + 8
        return None

    def _search_footer(self, sig: Signature, start_offset: int) -> Optional[int]:
        """Search for footer marker within max_size bytes from start_offset."""
        footer = sig.footer
        if not footer:
            return None
        search_limit = min(sig.max_size, self._disk_size - start_offset)
        chunk_size   = min(CARVE_BLOCK_SIZE, search_limit)
        offset       = start_offset
        searched     = 0

        while searched < search_limit and not self._cancelled:
            try:
                block = self._read(offset, chunk_size)
            except Exception:
                break
            if not block:
                break
            pos = block.find(footer)
            if pos != -1:
                return (offset - start_offset) + pos + len(footer)
            offset   += len(block) - len(footer)  # overlap to avoid split footer
            searched += len(block)

        return None

    # ------------------------------------------------------------------
    # Build DeletedFile from carving hit
    # ------------------------------------------------------------------

    @staticmethod
    def _build_carved_file(
        sig: Signature,
        offset: int,
        size: int,
        header: bytes,
        index: int,
    ) -> DeletedFile:
        name = f"CARVED_{index:06d}.{sig.ext}"
        score = ScoreBreakdown(
            metadata_score  = 0,    # no MFT — no metadata score
            cluster_score   = 0,    # no cluster info
            header_score    = 15,   # magic confirmed — full header score
            size_score      = _size_score(size),
            preview_score   = _previewable(sig.ext),
            recycle_score   = 0,
            timestamp_score = 0,
        )
        df = DeletedFile(
            name=name,
            path=f"[CARVED@{offset:#010x}]/{name}",
            inode=0,
            meta_flags=0,
            size=size,
            file_type_by_magic=sig.ext,
            file_type_by_ext=sig.ext,
            category=sig.category,
            header_bytes=header[:64],
            carved=True,
        )
        df.score  = score
        # Store the disk offset in the first cluster slot (used by recoverer)
        df.clusters = [offset]
        return df


# ------------------------------------------------------------------
# Pure helpers (no I/O)
# ------------------------------------------------------------------

def _size_score(size: int) -> int:
    mb = size / (1024 * 1024)
    if mb < 1:   return 10
    if mb < 10:  return 8
    if mb < 100: return 5
    if mb < 500: return 2
    return 1


def _previewable(ext: str) -> int:
    previewable = {"jpg", "jpeg", "png", "gif", "bmp", "webp",
                   "pdf", "txt", "mp3", "flac"}
    return 5 if ext in previewable else 0
