"""
FResucitary — Core data models.
All dataclasses used across modules are defined here.
No PyQt6 imports allowed in this file.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import IntEnum, auto
from typing import Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ScanMode(IntEnum):
    QUICK     = 1   # MFT deleted entries only — ~2 min on 1TB
    DEEP      = 2   # Full directory traversal + orphan detection — ~15 min
    FORENSIC  = 3   # Deep + full carving pass — ~45 min


class FileCategory(IntEnum):
    IMAGE    = auto()
    VIDEO    = auto()
    AUDIO    = auto()
    DOCUMENT = auto()
    ARCHIVE  = auto()
    EXECUTABLE = auto()
    DATABASE = auto()
    OTHER    = auto()


class RecoveryStatus(IntEnum):
    AVAILABLE  = auto()
    QUEUED     = auto()
    RECOVERING = auto()
    RECOVERED  = auto()
    FAILED     = auto()
    SKIPPED    = auto()
    # Appended after the legacy values to keep old session JSON compatible.
    PARTIAL    = auto()
    CORRUPT    = auto()


# ---------------------------------------------------------------------------
# Score breakdown — transparent, explainable
# ---------------------------------------------------------------------------

@dataclass
class ScoreBreakdown:
    """
    7-criteria scoring system (0-100).
    Weights: metadata(40) + clusters(25) + header(15) + size(10)
             + preview(5) + recycle(3) + timestamp(2)
    """
    metadata_score:  int = 0   # 0-40 : MFT coherence, timestamps, attributes
    cluster_score:   int = 0   # 0-25 : contiguous runs, bitmap state
    header_score:    int = 0   # 0-15 : magic bytes match, file type detection
    size_score:      int = 0   # 0-10 : size-based overwrite probability
    preview_score:   int = 0   # 0-5  : successfully parsed / thumbnailable
    recycle_score:   int = 0   # 0-3  : still in $Recycle.Bin (safer)
    timestamp_score: int = 0   # 0-2  : recent deletion (lower overwrite risk)
    # Hard ceiling applied when evidence contradicts the filename/metadata.
    # Example: a .pdf whose current bytes no longer contain a PDF signature.
    confidence_cap: int = 100

    @property
    def total(self) -> int:
        raw = (
            self.metadata_score +
            self.cluster_score  +
            self.header_score   +
            self.size_score     +
            self.preview_score  +
            self.recycle_score  +
            self.timestamp_score
        )
        return min(100, max(0, int(self.confidence_cap)), raw)

    @property
    def label(self) -> str:
        t = self.total
        if t >= 75: return "Excellent"
        if t >= 50: return "Bon"
        if t >= 25: return "Partiel"
        return "Faible"

    @property
    def color_hex(self) -> str:
        t = self.total
        if t >= 75: return "#4CAF50"
        if t >= 50: return "#FF9800"
        if t >= 25: return "#FF5722"
        return "#9E9E9E"


# ---------------------------------------------------------------------------
# DeletedFile — primary domain object
# ---------------------------------------------------------------------------

@dataclass
class DeletedFile:
    # — Identity —
    name:       str
    path:       str
    inode:      int
    meta_flags: int

    # — Size & type —
    size:               int  = 0
    file_type_by_ext:   str  = ""   # from name extension
    file_type_by_magic: str  = ""   # from first 16 bytes signature
    category:           FileCategory = FileCategory.OTHER

    # — Filesystem internals —
    clusters:           list[int] = field(default_factory=list)
    fragmented:         bool      = False
    data_runs_count:    int       = 0
    # Run map entries: (file_offset_blocks, disk_block, block_count, flags).
    # Keeping the run map allows recovery even if an inode is later reused.
    data_runs:          list[tuple[int, int, int, int]] = field(default_factory=list)
    data_attr_type:     int       = 0
    data_attr_id:       int       = -1
    data_attr_flags:    int       = 0
    data_attr_size:     int       = 0
    meta_seq:           int       = 0
    mft_record_valid:   bool      = False
    in_recycle_bin:     bool      = False

    # — Timestamps (Unix epoch floats) —
    deleted_at:  float = 0.0
    modified_at: float = 0.0
    created_at:  float = 0.0

    # — Scoring —
    score:              ScoreBreakdown = field(default_factory=ScoreBreakdown)
    header_bytes:       bytes          = field(default_factory=bytes, repr=False)

    # — Recovery state —
    status:     RecoveryStatus = RecoveryStatus.AVAILABLE
    sha256:     str = ""
    error:      Optional[str] = None
    output_path: str = ""
    recovered_size: int = 0
    recovery_note: str = ""

    # — Source context —
    # `source_offset` is the byte offset of the filesystem partition in the
    # source image/device. Carved offsets remain partition-relative in
    # `clusters[0]`; recovery combines both values.
    source_offset: int = 0
    source_fs: str = ""
    carved: bool = False

    @property
    def recovery_score(self) -> int:
        return self.score.total

    @property
    def ext(self) -> str:
        import os
        return os.path.splitext(self.name)[1].lower()

    @property
    def display_type(self) -> str:
        return self.file_type_by_magic or self.file_type_by_ext or "?"


# ---------------------------------------------------------------------------
# Scan session — persisted to disk for resume
# ---------------------------------------------------------------------------

@dataclass
class ScanSession:
    session_id:    str
    source_path:   str
    scan_mode:     ScanMode
    started_at:    float = field(default_factory=time.time)
    finished_at:   float = 0.0
    total_scanned: int   = 0
    results:       list[DeletedFile] = field(default_factory=list)
    log_lines:     list[str]         = field(default_factory=list)
    completed:     bool = False

    def duration_str(self) -> str:
        delta = (self.finished_at or time.time()) - self.started_at
        m, s = divmod(int(delta), 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# Recovery task
# ---------------------------------------------------------------------------

@dataclass
class RecoveryTask:
    file:        DeletedFile
    output_dir:  str
    priority:    int = 0   # higher = recovered first
    preserve_tree: bool = True

    def __lt__(self, other: "RecoveryTask") -> bool:
        return self.priority > other.priority   # reversed for heapq
