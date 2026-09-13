"""
FResucitary — Integration tests.
Tests the full pipeline: carver → scoring → session → CSV report.
Uses synthetic in-memory disk images (no real hardware needed).
"""
from __future__ import annotations
import csv
import os
import sys
import time
from unittest.mock import MagicMock, patch

# Stubs
for mod in [
    "pytsk3", "PyQt6", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets",
    "pypdfium2", "PIL", "PIL.Image",
]:
    sys.modules.setdefault(mod, MagicMock())

import pytest

from src.core.models import (
    DeletedFile, FileCategory, RecoveryStatus, RecoveryTask,
    ScanMode, ScanSession, ScoreBreakdown,
)
from src.core.carver import CarvingEngine
from src.core.session_mgr import SessionManager
from src.core.signatures import detect_type, SIGNATURES
from src.reports.pdf import ReportGenerator
from src.security.safe import ReadOnlyGuard, SectorJournal
from tests.conftest import assert_score_range


# ===========================================================================
# Integration: carver → detect type → build DeletedFile
# ===========================================================================

class TestCarverIntegration:
    """Full carver pipeline on synthetic disk images."""

    def _carver(self, data: bytes) -> CarvingEngine:
        def read_fn(o, l): return data[o:o+l]
        return CarvingEngine(read_fn, len(data))

    def test_synthetic_disk_finds_all_signatures(self, synthetic_disk):
        """The synthetic fixture has JPEG + PDF + PNG + RAR + ZIP."""
        carver  = self._carver(synthetic_disk)
        results = list(carver.carve())
        types   = {r.file_type_by_magic for r in results}
        assert "jpg" in types, f"JPEG not found, got {types}"
        assert "pdf" in types, f"PDF not found, got {types}"
        assert "png" in types, f"PNG not found, got {types}"
        assert "rar" in types, f"RAR not found, got {types}"

    def test_carved_offsets_are_correct(self, synthetic_disk):
        """JPEG is at byte 512 — cluster[0] must be 512."""
        carver  = self._carver(synthetic_disk)
        results = list(carver.carve())
        jpegs   = [r for r in results if r.file_type_by_magic == "jpg"]
        assert jpegs, "No JPEG carved"
        assert jpegs[0].clusters[0] == 512

    def test_all_carved_have_header_score_15(self, synthetic_disk):
        carver  = self._carver(synthetic_disk)
        results = list(carver.carve())
        for r in results:
            assert r.score.header_score == 15, f"{r.name} has header_score={r.score.header_score}"

    def test_progress_callback_receives_final_offset(self, synthetic_disk):
        carver  = self._carver(synthetic_disk)
        calls   = []
        list(carver.carve(progress_cb=lambda d, t: calls.append(d)))
        assert calls[-1] >= len(synthetic_disk) - 1024

    def test_category_set_correctly_on_carved(self, synthetic_disk):
        carver  = self._carver(synthetic_disk)
        results = list(carver.carve())
        by_type = {r.file_type_by_magic: r.category for r in results}
        assert by_type.get("jpg") == FileCategory.IMAGE
        assert by_type.get("pdf") == FileCategory.DOCUMENT
        assert by_type.get("png") == FileCategory.IMAGE
        assert by_type.get("rar") == FileCategory.ARCHIVE

    def test_cancel_mid_scan_no_crash(self, synthetic_disk):
        carver = self._carver(synthetic_disk)
        results = []
        gen = carver.carve()
        results.append(next(gen))  # get first result
        carver.cancel()
        # drain remaining without crash
        rest = list(gen)
        assert isinstance(results, list)

    def test_empty_disk_no_results(self):
        carver  = self._carver(b"\x00" * 65536)
        results = list(carver.carve())
        assert results == []

    def test_multiple_jpegs_in_disk(self):
        data = (b"\x00" * 64 + b"\xFF\xD8\xFF\xE0" + b"\xFF\xD9" + b"\x00" * 64) * 5
        carver  = self._carver(data)
        results = [r for r in carver.carve() if r.file_type_by_magic == "jpg"]
        assert len(results) >= 1  # at least one JPEG found


# ===========================================================================
# Integration: scoring pipeline end-to-end
# ===========================================================================

class TestScoringIntegration:

    def _score(self, df):
        from src.core.fs.ntfs import NTFSAdapter
        with patch.object(NTFSAdapter, "__init__", return_value=None):
            adapter = NTFSAdapter.__new__(NTFSAdapter)
        return adapter._compute_score(df)

    def test_ideal_jpeg_scores_above_70(self, make_df):
        df = make_df(
            size=150_000,
            mft_record_valid=True,
            data_runs_count=1,
            header_bytes=b"\xFF\xD8\xFF\xE0" + b"\x00" * 508,
            file_type_by_magic="jpg",
            file_type_by_ext="jpg",
            in_recycle_bin=True,
            deleted_at=time.time() - 1800,
            created_at=time.time() - 86400,
            modified_at=time.time() - 1800,
            path="Photos/holiday.jpg",
        )
        s = self._score(df)
        assert_score_range(s, 70, 100)

    def test_corrupted_file_scores_below_40(self, make_df):
        df = make_df(
            size=0,
            mft_record_valid=False,
            data_runs_count=0,
            header_bytes=b"",
            file_type_by_magic="",
            file_type_by_ext="",
            in_recycle_bin=False,
        )
        s = self._score(df)
        assert_score_range(s, 0, 39)

    def test_heavily_fragmented_penalised(self, make_df):
        df_clean = make_df(data_runs_count=1)
        df_frag  = make_df(data_runs_count=20)
        s_clean  = self._score(df_clean)
        s_frag   = self._score(df_frag)
        assert s_clean.cluster_score > s_frag.cluster_score

    def test_recycle_bin_bonus_applied(self, make_df):
        df_recycle = make_df(in_recycle_bin=True)
        df_normal  = make_df(in_recycle_bin=False)
        s_r = self._score(df_recycle)
        s_n = self._score(df_normal)
        assert s_r.recycle_score == 3
        assert s_n.recycle_score == 0

    def test_timestamp_bonus_within_24h(self, make_df):
        df = make_df(deleted_at=time.time() - 3600)   # 1 hour ago
        s  = self._score(df)
        assert s.timestamp_score == 2

    def test_timestamp_bonus_within_7d(self, make_df):
        df = make_df(deleted_at=time.time() - 4 * 86400)  # 4 days
        s  = self._score(df)
        assert s.timestamp_score == 1

    def test_no_timestamp_bonus_old_file(self, make_df):
        df = make_df(deleted_at=time.time() - 30 * 86400)
        s  = self._score(df)
        assert s.timestamp_score == 0

    def test_score_deterministic(self, make_df):
        """Same input → same score every time."""
        df = make_df()
        scores = [self._score(df).total for _ in range(10)]
        assert len(set(scores)) == 1


# ===========================================================================
# Integration: session save → load → report CSV
# ===========================================================================

class TestSessionReportPipeline:

    def test_full_pipeline(self, tmp_path, make_session):
        """Save session → reload → export CSV → verify rows."""
        mgr     = SessionManager(tmp_path / "sessions")
        (tmp_path / "sessions").mkdir(exist_ok=True)
        session = make_session(n=20)

        # Save
        path = mgr.save(session)
        assert path.exists()

        # Reload
        loaded = mgr.load(session.session_id)
        assert loaded is not None
        assert len(loaded.results) == 20

        # CSV export
        csv_path = str(tmp_path / "out.csv")
        gen = ReportGenerator(loaded)
        ok  = gen.export_csv(csv_path)
        assert ok

        with open(csv_path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 20
        for row in rows:
            score = int(row["Score (%)"])
            assert 0 <= score <= 100
            assert row["Type (magic)"] == "jpg"

    def test_session_with_mixed_categories(self, tmp_path, make_df):
        """Session containing multiple file categories round-trips correctly."""
        mgr = SessionManager(tmp_path)
        s   = SessionManager.new_session("/disk", ScanMode.DEEP)

        categories = [
            (FileCategory.IMAGE,    "photo.jpg",  "jpg"),
            (FileCategory.VIDEO,    "video.mp4",  "mp4"),
            (FileCategory.DOCUMENT, "doc.pdf",    "pdf"),
            (FileCategory.ARCHIVE,  "data.zip",   "zip"),
            (FileCategory.AUDIO,    "song.mp3",   "mp3"),
        ]
        for cat, name, ext in categories:
            df = make_df(name=name, category=cat, file_type_by_magic=ext, file_type_by_ext=ext)
            s.results.append(df)

        mgr.save(s)
        loaded = mgr.load(s.session_id)
        loaded_cats = {r.category for r in loaded.results}
        assert loaded_cats == {c for c, _, _ in categories}

    def test_csv_columns_complete(self, tmp_path, make_session):
        """All expected columns present in exported CSV."""
        session  = make_session(n=3)
        csv_path = str(tmp_path / "out.csv")
        ReportGenerator(session).export_csv(csv_path)

        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            cols   = reader.fieldnames or []

        expected = [
            "Nom", "Chemin", "Taille (octets)", "Type (magic)",
            "Score (%)", "Niveau", "Hash SHA-256", "Statut récupération",
        ]
        for col in expected:
            assert col in cols, f"Missing column: {col}"

    def test_large_session_performance(self, tmp_path, make_df):
        """10 000-file session saves and loads in < 5 seconds."""
        mgr = SessionManager(tmp_path)
        s   = SessionManager.new_session("/disk", ScanMode.FORENSIC)
        for i in range(10_000):
            df = make_df(name=f"f{i}.jpg", inode=i)
            df.score = ScoreBreakdown(metadata_score=30, cluster_score=20)
            s.results.append(df)

        t0 = time.time()
        mgr.save(s)
        loaded = mgr.load(s.session_id)
        elapsed = time.time() - t0

        assert loaded is not None
        assert len(loaded.results) == 10_000
        assert elapsed < 5.0, f"Too slow: {elapsed:.2f}s for 10k file session"


# ===========================================================================
# Integration: RecoveryTask priority queue
# ===========================================================================

class TestRecoveryQueueIntegration:
    """Verify that high-score files are always recovered first."""

    def test_priority_order(self, make_df):
        import heapq
        scores = [90, 30, 75, 10, 55, 85, 20, 45]
        tasks  = []
        for score_val in scores:
            df = make_df(name=f"f{score_val}.jpg")
            df.score = ScoreBreakdown(metadata_score=min(score_val, 40))
            # Manually set recovery_score via mocking not needed — set score directly
            task = RecoveryTask(file=df, output_dir="/out", priority=score_val)
            heapq.heappush(tasks, task)

        order = []
        while tasks:
            order.append(heapq.heappop(tasks).priority)

        # Must be descending (highest first)
        assert order == sorted(scores, reverse=True)

    def test_equal_priority_no_crash(self, make_df):
        import heapq
        tasks = []
        for i in range(10):
            df   = make_df(name=f"f{i}.jpg")
            task = RecoveryTask(file=df, output_dir="/out", priority=50)
            heapq.heappush(tasks, task)
        results = []
        while tasks:
            results.append(heapq.heappop(tasks))
        assert len(results) == 10


# ===========================================================================
# Integration: SectorJournal → CSV report
# ===========================================================================

class TestSectorJournalReport:

    def test_bad_sectors_appear_in_report(self, tmp_path, make_session):
        session = make_session(n=5)
        journal = SectorJournal()
        for i in range(3):
            journal.record(i * 512, 512, f"I/O error sector {i}")

        gen = ReportGenerator(session, bad_sectors=journal.entries)
        # Verify entries accessible
        assert len(gen.bad_sectors) == 3
        assert gen.bad_sectors[0]["offset"] == 0
        assert "I/O error" in gen.bad_sectors[0]["error"]

    def test_critical_journal_flagged(self):
        j = SectorJournal()
        for i in range(150):
            j.record(i * 512, 512, "err")
        assert j.is_critical()
        assert j.count == 150


# ===========================================================================
# Integration: signature detection on realistic headers
# ===========================================================================

class TestRealisticHeaders:
    """Test magic detection on headers that match real file formats."""

    REALISTIC_HEADERS = {
        "jpg": (
            bytes.fromhex("FFD8FFE000104A46494600010100000100010000"),
            FileCategory.IMAGE,
        ),
        "png": (
            bytes.fromhex("89504E470D0A1A0A0000000D49484452"),
            FileCategory.IMAGE,
        ),
        "pdf": (
            b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog",
            FileCategory.DOCUMENT,
        ),
        "zip": (
            bytes.fromhex("504B03041400000008004B58"),
            FileCategory.ARCHIVE,
        ),
        "rar": (
            bytes.fromhex("526172211A0700"),
            FileCategory.ARCHIVE,
        ),
        "mp3": (
            bytes.fromhex("494433030000000000"),
            FileCategory.AUDIO,
        ),
        "exe": (
            bytes.fromhex("4D5A9000030000000400000000000000FFFF"),
            FileCategory.EXECUTABLE,
        ),
        "7z": (
            bytes.fromhex("377ABCAF271C"),
            FileCategory.ARCHIVE,
        ),
        "sqlite": (
            b"SQLite format 3\x00" + b"\x00" * 16,
            FileCategory.DATABASE,
        ),
        "gif": (
            b"GIF89a\x10\x00\x10\x00\x80\x00\x00",
            FileCategory.IMAGE,
        ),
    }

    @pytest.mark.parametrize("ext,header_cat", REALISTIC_HEADERS.items())
    def test_realistic_header(self, ext, header_cat):
        header, expected_cat = header_cat
        sig = detect_type(header + b"\x00" * max(0, 16 - len(header)), ext_hint=f".{ext}")
        assert sig is not None, f"Failed to detect {ext}"
        assert sig.category == expected_cat, (
            f"{ext}: expected {expected_cat.name}, got {sig.category.name}"
        )

    def test_all_signatures_have_unique_ext_or_disambiguated(self):
        """No two signatures with same magic and same ext (would cause ambiguity)."""
        seen = {}
        for sig in SIGNATURES:
            key = (sig.magic, sig.ext)
            assert key not in seen, f"Duplicate signature: ext={sig.ext} magic={sig.magic!r}"
            seen[key] = True
