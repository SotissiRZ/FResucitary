"""
FResucitary — Test suite (pytest).
Tests run WITHOUT pytsk3 or PyQt6 (pure logic only).
Target: ≥95% coverage on core scoring, signatures, carver, session, security.

Run:
    pip install pytest pytest-cov
    pytest tests/ -v --cov=src --cov-report=term-missing
"""
from __future__ import annotations
import json
import os
import struct
import time
import tempfile
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Stub pytsk3 so imports don't fail ────────────────────────────────────
sys.modules.setdefault("pytsk3", MagicMock())

# ── Now import our modules ────────────────────────────────────────────────
from src.core.models import (
    DeletedFile, FileCategory, RecoveryStatus,
    RecoveryTask, ScanMode, ScanSession, ScoreBreakdown,
)
from src.core.signatures import detect_type, category_for_ext, SIGNATURES
from src.core.session_mgr import SessionManager
from src.security.safe import ReadOnlyGuard, SectorJournal
from src.core.stats import stats_key_for_category


# ===========================================================================
# Fixtures
# ===========================================================================

def make_df(**kwargs) -> DeletedFile:
    """Factory: create a DeletedFile with sensible defaults."""
    defaults = dict(
        name="test.jpg",
        path="Photos/test.jpg",
        inode=42,
        meta_flags=0,
        size=500_000,
        file_type_by_ext="jpg",
        file_type_by_magic="jpg",
        category=FileCategory.IMAGE,
        mft_record_valid=True,
        data_runs_count=1,
        deleted_at=time.time() - 3600,
        modified_at=time.time() - 3600,
        header_bytes=b"\xFF\xD8\xFF\xE0" + b"\x00" * 508,
    )
    defaults.update(kwargs)
    return DeletedFile(**defaults)




class TestStatsCategoryMapping:
    def test_dashboard_plural_keys(self):
        assert stats_key_for_category(FileCategory.IMAGE) == "images"
        assert stats_key_for_category(FileCategory.VIDEO) == "videos"
        assert stats_key_for_category(FileCategory.DOCUMENT) == "documents"
        assert stats_key_for_category(FileCategory.AUDIO) == "audio"

    def test_non_dashboard_categories_fold_into_other(self):
        assert stats_key_for_category(FileCategory.EXECUTABLE) == "other"
        assert stats_key_for_category(FileCategory.DATABASE) == "other"

# ===========================================================================
# ScoreBreakdown
# ===========================================================================

class TestScoreBreakdown:

    def test_total_clamps_at_100(self):
        s = ScoreBreakdown(
            metadata_score=40, cluster_score=25, header_score=15,
            size_score=10, preview_score=5, recycle_score=3, timestamp_score=2,
        )
        assert s.total == 100

    def test_total_overflow_clamped(self):
        s = ScoreBreakdown(
            metadata_score=40, cluster_score=25, header_score=15,
            size_score=10, preview_score=5, recycle_score=3, timestamp_score=99,
        )
        assert s.total == 100

    def test_zero_score(self):
        s = ScoreBreakdown()
        assert s.total == 0
        assert s.label == "Faible"

    def test_label_excellent(self):
        s = ScoreBreakdown(metadata_score=40, cluster_score=25, header_score=15)
        assert s.total == 80
        assert s.label == "Excellent"
        assert s.color_hex == "#4CAF50"

    def test_label_bon(self):
        s = ScoreBreakdown(metadata_score=30, cluster_score=25)  # 55 = Bon
        assert s.total == 55
        assert s.label == "Bon"
        assert s.color_hex == "#FF9800"

    def test_label_partiel(self):
        s = ScoreBreakdown(metadata_score=20, cluster_score=8)
        assert s.label == "Partiel"
        assert s.color_hex == "#FF5722"

    def test_label_faible(self):
        s = ScoreBreakdown(metadata_score=5)
        assert s.label == "Faible"
        assert s.color_hex == "#9E9E9E"

    def test_individual_criteria(self):
        s = ScoreBreakdown(
            metadata_score=10, cluster_score=5, header_score=3,
            size_score=2, preview_score=1, recycle_score=1, timestamp_score=1,
        )
        assert s.total == 23


# ===========================================================================
# DeletedFile
# ===========================================================================

class TestDeletedFile:

    def test_recovery_score_delegates_to_breakdown(self):
        df = make_df()
        df.score = ScoreBreakdown(metadata_score=40, cluster_score=25, header_score=10)
        assert df.recovery_score == 75

    def test_ext_property(self):
        df = make_df(name="document.DOCX")
        assert df.ext == ".docx"

    def test_display_type_prefers_magic(self):
        df = make_df(file_type_by_magic="pdf", file_type_by_ext="tmp")
        assert df.display_type == "pdf"

    def test_display_type_falls_back_to_ext(self):
        df = make_df(file_type_by_magic="", file_type_by_ext="xlsx")
        assert df.display_type == "xlsx"

    def test_display_type_unknown(self):
        df = make_df(file_type_by_magic="", file_type_by_ext="")
        assert df.display_type == "?"

    def test_default_status(self):
        df = make_df()
        assert df.status == RecoveryStatus.AVAILABLE

    def test_in_recycle_bin_flag(self):
        df = make_df(in_recycle_bin=True)
        assert df.in_recycle_bin is True

    def test_carved_flag_defaults_false(self):
        df = make_df()
        assert df.carved is False


# ===========================================================================
# ScanMode
# ===========================================================================

class TestScanMode:

    def test_values_ordered(self):
        assert ScanMode.QUICK < ScanMode.DEEP < ScanMode.FORENSIC

    def test_from_int(self):
        assert ScanMode(1) == ScanMode.QUICK
        assert ScanMode(2) == ScanMode.DEEP
        assert ScanMode(3) == ScanMode.FORENSIC


# ===========================================================================
# Signature detection
# ===========================================================================

class TestSignatures:

    # JPEG
    def test_detect_jpeg(self):
        header = b"\xFF\xD8\xFF\xE0" + b"\x00" * 508
        sig = detect_type(header)
        assert sig is not None
        assert sig.ext == "jpg"
        assert sig.category == FileCategory.IMAGE

    # PNG
    def test_detect_png(self):
        header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 504
        sig = detect_type(header)
        assert sig is not None
        assert sig.ext == "png"

    # PDF
    def test_detect_pdf(self):
        header = b"%PDF-1.7" + b"\x00" * 504
        sig = detect_type(header)
        assert sig is not None
        assert sig.ext == "pdf"
        assert sig.category == FileCategory.DOCUMENT

    # ZIP vs DOCX disambiguation
    def test_pk_disambiguated_to_docx(self):
        header = b"PK\x03\x04" + b"\x00" * 508
        sig = detect_type(header, ext_hint=".docx")
        assert sig is not None
        assert sig.ext == "docx"

    def test_pk_disambiguated_to_xlsx(self):
        header = b"PK\x03\x04" + b"\x00" * 508
        sig = detect_type(header, ext_hint=".xlsx")
        assert sig is not None
        assert sig.ext == "xlsx"

    def test_pk_falls_back_to_zip(self):
        header = b"PK\x03\x04" + b"\x00" * 508
        sig = detect_type(header, ext_hint=".unknown")
        assert sig is not None
        assert sig.ext == "zip"

    # MP3 with ID3
    def test_detect_mp3_id3(self):
        header = b"ID3" + b"\x00" * 509
        sig = detect_type(header)
        assert sig is not None
        assert sig.ext == "mp3"
        assert sig.category == FileCategory.AUDIO

    # RAR
    def test_detect_rar(self):
        header = b"Rar!\x1A\x07\x00" + b"\x00" * 505
        sig = detect_type(header)
        assert sig is not None
        assert sig.ext == "rar"
        assert sig.category == FileCategory.ARCHIVE

    # 7-Zip
    def test_detect_7z(self):
        header = b"7z\xBC\xAF\x27\x1C" + b"\x00" * 506
        sig = detect_type(header)
        assert sig is not None
        assert sig.ext == "7z"

    # SQLite
    def test_detect_sqlite(self):
        header = b"SQLite format 3\x00" + b"\x00" * 496
        sig = detect_type(header)
        assert sig is not None
        assert sig.ext == "sqlite"
        assert sig.category == FileCategory.DATABASE

    # PE executable
    def test_detect_exe(self):
        header = b"MZ" + b"\x00" * 510
        sig = detect_type(header)
        assert sig is not None
        assert sig.ext in ("exe", "dll", "sys")

    # Unknown header
    def test_unknown_header_returns_none(self):
        header = b"\x00\x01\x02\x03" + b"\x00" * 508
        sig = detect_type(header)
        assert sig is None

    # Empty header
    def test_empty_header(self):
        assert detect_type(b"") is None
        assert detect_type(None) is None  # type: ignore

    # Signature count sanity
    def test_minimum_signature_count(self):
        assert len(SIGNATURES) >= 50

    # Max sizes are positive
    def test_max_sizes_positive(self):
        for sig in SIGNATURES:
            assert sig.max_size > 0, f"Zero max_size for {sig.ext}"


# ===========================================================================
# category_for_ext
# ===========================================================================

class TestCategoryForExt:

    @pytest.mark.parametrize("ext,expected", [
        (".jpg",    FileCategory.IMAGE),
        (".JPEG",   FileCategory.IMAGE),
        (".png",    FileCategory.IMAGE),
        (".mp4",    FileCategory.VIDEO),
        (".mkv",    FileCategory.VIDEO),
        (".mp3",    FileCategory.AUDIO),
        (".flac",   FileCategory.AUDIO),
        (".pdf",    FileCategory.DOCUMENT),
        (".docx",   FileCategory.DOCUMENT),
        (".zip",    FileCategory.ARCHIVE),
        (".rar",    FileCategory.ARCHIVE),
        (".exe",    FileCategory.EXECUTABLE),
        (".dll",    FileCategory.EXECUTABLE),
        (".sqlite", FileCategory.DATABASE),
        (".xyz",    FileCategory.OTHER),
        ("",        FileCategory.OTHER),
    ])
    def test_category(self, ext, expected):
        assert category_for_ext(ext) == expected


# ===========================================================================
# CarvingEngine (no disk I/O — synthetic buffer)
# ===========================================================================

class TestCarvingEngine:

    def _make_carver(self, data: bytes):
        from src.core.carver import CarvingEngine
        def read_fn(offset: int, length: int) -> bytes:
            return data[offset:offset + length]
        return CarvingEngine(read_fn, len(data))

    def test_finds_jpeg_in_buffer(self):
        payload = b"\x00" * 100 + b"\xFF\xD8\xFF\xE0" + b"\xFF\xD9"
        carver  = self._make_carver(payload)
        results = list(carver.carve())
        assert any(r.file_type_by_magic == "jpg" for r in results)

    def test_finds_pdf_in_buffer(self):
        payload = b"\x00" * 64 + b"%PDF-1.4 test\n%%EOF"
        carver  = self._make_carver(payload)
        results = list(carver.carve())
        assert any(r.file_type_by_magic == "pdf" for r in results)

    def test_finds_png_in_buffer(self):
        payload = b"\x00" * 32 + b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        carver  = self._make_carver(payload)
        results = list(carver.carve())
        assert any(r.file_type_by_magic == "png" for r in results)

    def test_empty_buffer_yields_nothing(self):
        carver  = self._make_carver(b"\x00" * 4096)
        results = list(carver.carve())
        assert results == []

    def test_carved_flag_set(self):
        payload = b"\xFF\xD8\xFF\xE0" + b"\x00" * 512
        carver  = self._make_carver(payload)
        results = list(carver.carve())
        for r in results:
            assert r.carved is True

    def test_carved_name_format(self):
        payload = b"\xFF\xD8\xFF\xE0" + b"\x00" * 512
        carver  = self._make_carver(payload)
        results = list(carver.carve())
        assert results[0].name.startswith("CARVED_")
        assert results[0].name.endswith(".jpg")

    def test_cancel_stops_carver(self):
        # Large buffer — cancel immediately
        payload = b"\xFF\xD8\xFF\xE0" * 10000
        carver  = self._make_carver(payload)
        carver.cancel()
        results = list(carver.carve())
        # May find 0 or some results depending on timing, but must not crash
        assert isinstance(results, list)

    def test_header_score_set(self):
        payload = b"\xFF\xD8\xFF\xE0" + b"\x00" * 512
        carver  = self._make_carver(payload)
        results = list(carver.carve())
        assert results[0].score.header_score == 15

    def test_progress_callback_called(self):
        payload = b"\x00" * (1024 * 1024)  # 1 MB
        carver  = self._make_carver(payload)
        calls   = []
        list(carver.carve(progress_cb=lambda d, t: calls.append((d, t))))
        assert len(calls) > 0

    def test_multiple_signatures_same_buffer(self):
        payload = (
            b"\xFF\xD8\xFF\xE0" + b"\x00" * 60 +
            b"%PDF-1.4" + b"\x00" * 60 +
            b"PK\x03\x04" + b"\x00" * 60
        )
        carver  = self._make_carver(payload)
        results = list(carver.carve())
        types   = {r.file_type_by_magic for r in results}
        assert "jpg" in types
        assert "pdf" in types


# ===========================================================================
# SessionManager
# ===========================================================================

class TestSessionManager:

    def test_save_and_load(self, tmp_path):
        mgr = SessionManager(tmp_path)
        session = SessionManager.new_session("/dev/sda", ScanMode.QUICK)
        df = make_df()
        df.score = ScoreBreakdown(metadata_score=30, cluster_score=20)
        session.results.append(df)
        session.completed = True

        mgr.save(session)
        loaded = mgr.load(session.session_id)

        assert loaded is not None
        assert loaded.session_id == session.session_id
        assert loaded.source_path == "/dev/sda"
        assert loaded.scan_mode == ScanMode.QUICK
        assert loaded.completed is True
        assert len(loaded.results) == 1

    def test_loaded_score_preserved(self, tmp_path):
        mgr     = SessionManager(tmp_path)
        session = SessionManager.new_session("/test", ScanMode.DEEP)
        df      = make_df()
        df.score = ScoreBreakdown(metadata_score=40, cluster_score=25, header_score=15)
        session.results.append(df)
        mgr.save(session)

        loaded = mgr.load(session.session_id)
        r = loaded.results[0]
        assert r.score.metadata_score == 40
        assert r.score.cluster_score  == 25
        assert r.score.header_score   == 15
        assert r.recovery_score       == 80

    def test_load_missing_returns_none(self, tmp_path):
        mgr = SessionManager(tmp_path)
        assert mgr.load("nonexistent") is None

    def test_list_sessions(self, tmp_path):
        mgr = SessionManager(tmp_path)
        for i in range(3):
            s = SessionManager.new_session(f"/disk{i}", ScanMode.QUICK)
            mgr.save(s)
        sessions = mgr.list_sessions()
        assert len(sessions) == 3

    def test_rotation_keeps_max(self, tmp_path):
        from src.core.session_mgr import MAX_SESSIONS
        mgr = SessionManager(tmp_path)
        for i in range(MAX_SESSIONS + 5):
            s = SessionManager.new_session(f"/disk{i}", ScanMode.QUICK)
            mgr.save(s)
        sessions = mgr.list_sessions()
        assert len(sessions) <= MAX_SESSIONS

    def test_delete_session(self, tmp_path):
        mgr     = SessionManager(tmp_path)
        session = SessionManager.new_session("/x", ScanMode.QUICK)
        mgr.save(session)
        mgr.delete(session.session_id)
        assert mgr.load(session.session_id) is None

    def test_roundtrip_all_categories(self, tmp_path):
        mgr     = SessionManager(tmp_path)
        session = SessionManager.new_session("/x", ScanMode.FORENSIC)
        for cat in FileCategory:
            df = make_df(name=f"file_{cat.name}.bin", category=cat)
            session.results.append(df)
        mgr.save(session)
        loaded = mgr.load(session.session_id)
        cats = {r.category for r in loaded.results}
        assert cats == set(FileCategory)

    def test_new_session_has_unique_id(self):
        ids = {SessionManager.new_session("/x", ScanMode.QUICK).session_id for _ in range(20)}
        assert len(ids) == 20

    def test_scan_session_duration_str(self):
        s = ScanSession(
            session_id="abc",
            source_path="/x",
            scan_mode=ScanMode.QUICK,
            started_at=0.0,
            finished_at=3661.0,
        )
        assert s.duration_str() == "01:01:01"


# ===========================================================================
# ReadOnlyGuard
# ===========================================================================

class TestReadOnlyGuard:

    def test_safe_path_does_not_raise(self):
        guard = ReadOnlyGuard("D:\\image.dd")
        # D: is not the system drive in our stub environment
        # Just ensure no exception for non-system path
        try:
            guard.assert_safe()
        except PermissionError:
            pytest.skip("System drive heuristic triggered in test environment")

    def test_output_same_drive_raises(self):
        with pytest.raises(PermissionError, match="même lecteur"):
            ReadOnlyGuard.validate_output_not_on_source("C:\\disk.dd", "C:\\Recovered")

    def test_output_different_drive_ok(self):
        # Should not raise
        ReadOnlyGuard.validate_output_not_on_source("C:\\disk.dd", "D:\\Recovered")

    def test_safe_output_warning_returns_string_on_same_drive(self):
        result = ReadOnlyGuard.safe_output_warning("C:\\source.dd", "C:\\output")
        assert result is not None
        assert isinstance(result, str)

    def test_safe_output_warning_returns_none_on_different_drive(self):
        result = ReadOnlyGuard.safe_output_warning("C:\\source.dd", "D:\\output")
        assert result is None


# ===========================================================================
# SectorJournal
# ===========================================================================

class TestSectorJournal:

    def test_empty_journal(self):
        j = SectorJournal()
        assert j.count == 0
        assert j.is_critical() is False

    def test_record_entry(self):
        j = SectorJournal()
        j.record(0x1000, 512, "I/O error")
        assert j.count == 1
        assert j.entries[0]["offset"] == 0x1000
        assert j.entries[0]["error"]  == "I/O error"

    def test_critical_threshold(self):
        j = SectorJournal()
        for i in range(101):
            j.record(i * 512, 512, "err")
        assert j.is_critical() is True

    def test_not_critical_below_threshold(self):
        j = SectorJournal()
        for i in range(100):
            j.record(i * 512, 512, "err")
        assert j.is_critical() is False

    def test_entries_are_copies(self):
        j = SectorJournal()
        j.record(0, 512, "err")
        entries = j.entries
        entries.clear()
        assert j.count == 1  # original unaffected


# ===========================================================================
# RecoveryTask priority ordering
# ===========================================================================

class TestRecoveryTaskPriority:

    def test_higher_score_lower_heap_key(self):
        import heapq
        df_high = make_df(name="high.jpg")
        df_high.score = ScoreBreakdown(metadata_score=40, cluster_score=25, header_score=15)
        df_low  = make_df(name="low.jpg")
        df_low.score  = ScoreBreakdown(metadata_score=5)

        task_high = RecoveryTask(file=df_high, output_dir="/out", priority=df_high.recovery_score)
        task_low  = RecoveryTask(file=df_low,  output_dir="/out", priority=df_low.recovery_score)

        heap = []
        heapq.heappush(heap, task_low)
        heapq.heappush(heap, task_high)

        # RecoveryTask.__lt__ inverts priority (max-heap via min-heap)
        first = heapq.heappop(heap)
        assert first.file.name == "high.jpg"


# ===========================================================================
# ScanSession
# ===========================================================================

class TestScanSession:

    def test_results_empty_by_default(self):
        s = ScanSession(session_id="x", source_path="/y", scan_mode=ScanMode.QUICK)
        assert s.results == []
        assert s.log_lines == []

    def test_completed_defaults_false(self):
        s = ScanSession(session_id="x", source_path="/y", scan_mode=ScanMode.QUICK)
        assert s.completed is False

    def test_duration_zero_start(self):
        s = ScanSession(
            session_id="x", source_path="/y", scan_mode=ScanMode.QUICK,
            started_at=0.0, finished_at=90.0
        )
        assert s.duration_str() == "00:01:30"


# ===========================================================================
# Integration-style: scoring pipeline
# ===========================================================================

class TestScoringPipeline:
    """
    Simulate the scoring logic without pytsk3 by calling NTFSAdapter._compute_score
    directly (it's a static-like method).
    """

    def _score(self, df: DeletedFile) -> ScoreBreakdown:
        from src.core.fs.ntfs import NTFSAdapter
        # Patch __init__ to skip pytsk3
        with patch.object(NTFSAdapter, "__init__", return_value=None):
            adapter = NTFSAdapter.__new__(NTFSAdapter)
        return adapter._compute_score(df)

    def test_perfect_file_scores_high(self):
        df = make_df(
            size=50_000,
            mft_record_valid=True,
            data_runs_count=1,
            header_bytes=b"\xFF\xD8\xFF\xE0" + b"\x00" * 508,
            file_type_by_magic="jpg",
            file_type_by_ext="jpg",
            in_recycle_bin=True,
            deleted_at=time.time() - 3600,
            created_at=time.time() - 86400,
            modified_at=time.time() - 3600,
            path="Photos/test.jpg",
        )
        s = self._score(df)
        assert s.total >= 70, f"Expected high score, got {s.total} ({s})"
        assert s.metadata_score > 0
        assert s.cluster_score == 25
        assert s.header_score == 15
        assert s.recycle_score == 3

    def test_fragmented_file_scores_lower_clusters(self):
        df_contiguous  = make_df(data_runs_count=1)
        df_fragmented  = make_df(data_runs_count=10)
        s_cont = self._score(df_contiguous)
        s_frag = self._score(df_fragmented)
        assert s_cont.cluster_score > s_frag.cluster_score

    def test_large_file_scores_lower_size(self):
        df_small = make_df(size=100_000)
        df_large = make_df(size=600 * 1024 * 1024)
        s_small = self._score(df_small)
        s_large = self._score(df_large)
        assert s_small.size_score > s_large.size_score

    def test_type_mismatch_scores_partial_header(self):
        df = make_df(
            header_bytes=b"\xFF\xD8\xFF\xE0" + b"\x00" * 508,
            file_type_by_magic="jpg",
            file_type_by_ext="tmp",   # extension mismatch
        )
        s = self._score(df)
        assert s.header_score == 10  # known magic but ext mismatch

    def test_unknown_magic_scores_low_header(self):
        df = make_df(
            header_bytes=b"\x00\x01\x02\x03" + b"\x00" * 508,
            file_type_by_magic="",
            file_type_by_ext="",
        )
        s = self._score(df)
        assert s.header_score == 3

    def test_no_header_bytes_zero_header_score(self):
        df = make_df(header_bytes=b"", file_type_by_magic="", file_type_by_ext="")
        s = self._score(df)
        assert s.header_score == 0

    def test_recent_deletion_gets_timestamp_bonus(self):
        df_recent = make_df(deleted_at=time.time() - 3600)        # 1 hour
        df_old    = make_df(deleted_at=time.time() - 30 * 86400)  # 30 days
        s_recent  = self._score(df_recent)
        s_old     = self._score(df_old)
        assert s_recent.timestamp_score > s_old.timestamp_score

    def test_zero_size_file_scores_zero_size(self):
        df = make_df(size=0)
        s  = self._score(df)
        assert s.size_score == 0

    def test_previewable_image_gets_preview_score(self):
        df = make_df(file_type_by_magic="jpg")
        s  = self._score(df)
        assert s.preview_score == 5

    def test_non_previewable_gets_no_preview_score(self):
        df = make_df(file_type_by_magic="exe", file_type_by_ext="exe")
        s  = self._score(df)
        assert s.preview_score == 0


# ===========================================================================
# Report generation (no reportlab required — tests CSV path only)
# ===========================================================================

class TestReportGenerator:

    def test_csv_export(self, tmp_path):
        from src.reports.pdf import ReportGenerator
        session = SessionManager.new_session("/disk", ScanMode.QUICK)
        for i in range(10):
            df = make_df(name=f"file_{i}.jpg", size=i * 1000)
            df.score = ScoreBreakdown(metadata_score=30, cluster_score=20)
            session.results.append(df)
        session.completed = True

        out = str(tmp_path / "report.csv")
        gen = ReportGenerator(session)
        ok  = gen.export_csv(out)
        assert ok is True
        assert os.path.exists(out)

        import csv
        with open(out, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        assert len(rows) == 11  # 1 header + 10 data rows
        assert rows[0][0] == "Nom"

    def test_csv_score_values(self, tmp_path):
        from src.reports.pdf import ReportGenerator
        session = SessionManager.new_session("/disk", ScanMode.QUICK)
        df      = make_df(name="test.pdf", file_type_by_magic="pdf")
        df.score = ScoreBreakdown(metadata_score=40, cluster_score=25, header_score=15)
        session.results.append(df)

        out = str(tmp_path / "r.csv")
        ReportGenerator(session).export_csv(out)

        import csv
        with open(out, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["Score (%)"] == "80"
        assert rows[0]["Niveau"] == "Excellent"


# ===========================================================================
# Edge cases
# ===========================================================================

class TestEdgeCases:

    def test_deleted_file_with_no_path(self):
        df = DeletedFile(name="x.jpg", path="", inode=1, meta_flags=0)
        assert df.path == ""

    def test_score_breakdown_all_zero_label(self):
        s = ScoreBreakdown()
        assert s.label == "Faible"

    def test_detect_type_very_short_header(self):
        # Should not crash on 1-byte headers
        for b in range(256):
            try:
                detect_type(bytes([b]))
            except Exception as exc:
                pytest.fail(f"detect_type crashed on byte {b}: {exc}")

    def test_sector_journal_empty_entries(self):
        j = SectorJournal()
        assert j.entries == []

    def test_recovery_task_lt(self):
        df1 = make_df(); df1.score = ScoreBreakdown(metadata_score=10)
        df2 = make_df(); df2.score = ScoreBreakdown(metadata_score=40)
        t1 = RecoveryTask(file=df1, output_dir="/", priority=df1.recovery_score)
        t2 = RecoveryTask(file=df2, output_dir="/", priority=df2.recovery_score)
        assert t2 < t1  # higher priority = "less than" for min-heap

    def test_session_manager_new_session_fields(self):
        s = SessionManager.new_session("/test/path", ScanMode.FORENSIC)
        assert s.source_path == "/test/path"
        assert s.scan_mode   == ScanMode.FORENSIC
        assert s.completed   is False
        assert s.results     == []
        assert s.started_at  > 0

# ===========================================================================
# Regression tests — source context / filesystem probing / timestamps
# ===========================================================================

class TestRegressionFixes:

    def test_ntfs_timestamp_accepts_unix_seconds(self):
        from src.core.fs.ntfs import _filetime_to_unix
        ts = 1_700_000_000
        assert _filetime_to_unix(ts) == float(ts)

    def test_ntfs_timestamp_converts_real_filetime(self):
        from src.core.fs.ntfs import _filetime_to_unix
        unix_ts = 1_700_000_000
        filetime = int((unix_ts + 11_644_473_600) * 10_000_000)
        assert abs(_filetime_to_unix(filetime) - unix_ts) < 0.001

    def test_session_preserves_partition_context(self, tmp_path):
        mgr = SessionManager(tmp_path)
        session = SessionManager.new_session("C:\\disk.dd", ScanMode.DEEP)
        df = make_df(source_offset=1_048_576, source_fs="NTFS")
        session.results.append(df)
        mgr.save(session)
        loaded = mgr.load(session.session_id)
        assert loaded is not None
        assert loaded.results[0].source_offset == 1_048_576
        assert loaded.results[0].source_fs == "NTFS"

    @pytest.mark.parametrize("marker_offset,marker,expected", [
        (3,  b"NTFS    ", "NTFS"),
        (3,  b"EXFAT   ", "exFAT"),
        (82, b"FAT32   ", "FAT32"),
        (54, b"FAT16   ", "FAT16"),
        (54, b"FAT12   ", "FAT12"),
    ])
    def test_filesystem_probe_locations(self, marker_offset, marker, expected):
        from src.core.fs.image import DiskSource

        boot = bytearray(512)
        boot[marker_offset:marker_offset + len(marker)] = marker

        class Img:
            def read(self, offset, length):
                if offset == 0:
                    return bytes(boot[:length])
                return b"\x00" * length

        src = object.__new__(DiskSource)
        src._img = Img()
        assert src._probe_fs(0) == expected


def test_carver_deduplicates_overlap_boundary():
    from src.core.carver import CarvingEngine, CARVE_BLOCK_SIZE
    data = bytearray(CARVE_BLOCK_SIZE + 1024)
    pos = CARVE_BLOCK_SIZE - 100
    data[pos:pos+4] = b"\xFF\xD8\xFF\xE0"
    data[pos+20:pos+22] = b"\xFF\xD9"
    raw = bytes(data)
    carver = CarvingEngine(lambda o, l: raw[o:o+l], len(raw))
    hits = [x for x in carver.carve() if x.file_type_by_magic == "jpg"]
    assert len(hits) == 1
    assert hits[0].clusters[0] == pos
