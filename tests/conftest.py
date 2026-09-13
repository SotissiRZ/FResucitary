"""
FResucitary — pytest conftest.py
Shared fixtures, stubs and helpers for the entire test suite.
"""
from __future__ import annotations
import sys
import time
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Stub heavy native modules before any import ───────────────────────────
for mod in [
    "pytsk3",
    "PyQt6", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets",
    "PyQt6.QtCore.QThread", "PyQt6.QtCore.pyqtSignal",
    "reportlab", "reportlab.lib", "reportlab.platypus",
    "reportlab.lib.pagesizes", "reportlab.lib.styles",
    "reportlab.lib.units", "reportlab.lib.enums",
    "pypdfium2",
    "PIL", "PIL.Image",
]:
    sys.modules.setdefault(mod, MagicMock())

# ── Now our modules are safe to import ───────────────────────────────────
from src.core.models import (
    DeletedFile, FileCategory, RecoveryStatus,
    RecoveryTask, ScanMode, ScanSession, ScoreBreakdown,
)
from src.core.session_mgr import SessionManager
from src.core.signatures import detect_type


# ── Factories ─────────────────────────────────────────────────────────────

@pytest.fixture
def make_df():
    """Factory fixture: returns a callable that creates DeletedFile instances."""
    def _factory(**kwargs) -> DeletedFile:
        defaults = dict(
            name="photo.jpg",
            path="Photos/photo.jpg",
            inode=100,
            meta_flags=0,
            size=250_000,
            file_type_by_ext="jpg",
            file_type_by_magic="jpg",
            category=FileCategory.IMAGE,
            mft_record_valid=True,
            data_runs_count=1,
            in_recycle_bin=False,
            deleted_at=time.time() - 3600,
            modified_at=time.time() - 3600,
            created_at=time.time() - 86400,
            header_bytes=b"\xFF\xD8\xFF\xE0" + b"\x00" * 508,
        )
        defaults.update(kwargs)
        return DeletedFile(**defaults)
    return _factory


@pytest.fixture
def make_session(make_df):
    """Factory: creates a ScanSession with N files."""
    def _factory(n: int = 5, mode: ScanMode = ScanMode.QUICK) -> ScanSession:
        s = SessionManager.new_session("/dev/sda", mode)
        for i in range(n):
            df = make_df(name=f"file_{i:04d}.jpg", inode=1000 + i, size=i * 10_000 + 1)
            df.score = ScoreBreakdown(
                metadata_score=30 + (i % 10),
                cluster_score=20,
                header_score=15,
            )
            s.results.append(df)
        s.completed = True
        s.finished_at = s.started_at + 120.0
        return s
    return _factory


@pytest.fixture
def tmp_session_dir(tmp_path):
    """Temporary directory for session files."""
    d = tmp_path / "sessions"
    d.mkdir()
    return d


@pytest.fixture
def session_mgr(tmp_session_dir):
    """SessionManager backed by a temporary directory."""
    return SessionManager(tmp_session_dir)


@pytest.fixture
def synthetic_disk():
    """
    Returns a bytes object that looks like a small disk image
    containing several embedded file signatures (for carver tests).
    Layout:
        [0    ] 512 bytes zeros (boot sector placeholder)
        [512  ] JPEG header
        [1024 ] 512 bytes zeros
        [1536 ] PDF header + %%EOF footer
        [2048 ] PNG header
        [4096 ] RAR header
        [8192 ] ZIP header
    Total: 16 KB
    """
    disk = bytearray(16 * 1024)
    # JPEG at 512
    disk[512:516]  = b"\xFF\xD8\xFF\xE0"
    disk[600:602]  = b"\xFF\xD9"  # JPEG footer
    # PDF at 1536
    disk[1536:1541] = b"%PDF-"
    disk[1600:1606] = b"%%EOF\n"
    # PNG at 2048
    disk[2048:2056] = b"\x89PNG\r\n\x1a\n"
    # RAR at 4096
    disk[4096:4103] = b"Rar!\x1A\x07\x00"
    # ZIP at 8192
    disk[8192:8196] = b"PK\x03\x04"
    return bytes(disk)


@pytest.fixture
def make_carver(synthetic_disk):
    """Returns a CarvingEngine scanning synthetic_disk."""
    from src.core.carver import CarvingEngine
    data = synthetic_disk

    def read_fn(offset: int, length: int) -> bytes:
        return data[offset:offset + length]

    return CarvingEngine(read_fn, len(data))


@pytest.fixture
def score_computer():
    """Returns a function that computes score via NTFSAdapter._compute_score."""
    from unittest.mock import patch
    from src.core.fs.ntfs import NTFSAdapter

    def _compute(df: DeletedFile) -> ScoreBreakdown:
        with patch.object(NTFSAdapter, "__init__", return_value=None):
            adapter = NTFSAdapter.__new__(NTFSAdapter)
        return adapter._compute_score(df)

    return _compute


# ── Helpers ───────────────────────────────────────────────────────────────

def assert_score_range(score: ScoreBreakdown, lo: int, hi: int) -> None:
    """Assert score.total is within [lo, hi]."""
    assert lo <= score.total <= hi, (
        f"Expected score in [{lo}, {hi}], got {score.total} "
        f"(metadata={score.metadata_score}, cluster={score.cluster_score}, "
        f"header={score.header_score})"
    )
