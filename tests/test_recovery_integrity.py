"""Regression tests for real-world recovery integrity issues found on Windows."""
from __future__ import annotations

from src.core.models import RecoveryStatus, ScoreBreakdown
from src.core.recovery_validation import validate_recovered_output
from src.core.session_mgr import SessionManager
from src.core.models import ScanMode
from src.core.signatures import signature_check


def test_short_output_is_partial_not_success(tmp_path, make_df):
    df = make_df(
        name="notes.txt", file_type_by_ext="txt", file_type_by_magic="",
        size=1000, header_bytes=b"hello",
    )
    out = tmp_path / "notes.txt"
    out.write_bytes(b"hello")
    result = validate_recovered_output(df, str(out), 5)
    assert result.status == RecoveryStatus.PARTIAL
    assert "5 / 1000" in result.note


def test_pdf_without_pdf_signature_is_corrupt(tmp_path, make_df):
    df = make_df(
        name="deleted.pdf", file_type_by_ext="pdf", file_type_by_magic="",
        size=309 * 1024, header_bytes=b"X" * 512,
    )
    out = tmp_path / "deleted.pdf"
    payload = b"NOT-A-PDF" + b"\x00" * 100
    out.write_bytes(payload)
    result = validate_recovered_output(df, str(out), len(payload))
    assert result.status == RecoveryStatus.CORRUPT
    assert "signature PDF" in result.note


def test_empty_output_for_nonempty_source_is_failed(tmp_path, make_df):
    df = make_df(size=500)
    out = tmp_path / "photo.jpg"
    out.write_bytes(b"")
    result = validate_recovered_output(df, str(out), 0)
    assert result.status == RecoveryStatus.FAILED


def test_signature_check_pdf_allows_header_within_first_1024_bytes():
    header = b"junk" * 10 + b"%PDF-1.7\n"
    assert signature_check(header, "pdf") is True
    assert signature_check(b"not a pdf at all", "pdf") is False


def test_score_breakdown_confidence_cap():
    score = ScoreBreakdown(
        metadata_score=40, cluster_score=25, header_score=15,
        size_score=10, preview_score=5, confidence_cap=39,
    )
    assert score.total == 39
    assert score.label == "Partiel"


def test_ntfs_score_caps_known_extension_with_wrong_magic(make_df):
    from src.core.fs.ntfs import NTFSAdapter
    df = make_df(
        name="old.pdf", file_type_by_ext="pdf", file_type_by_magic="",
        header_bytes=b"\x00" * 512, data_runs_count=1,
    )
    adapter = NTFSAdapter.__new__(NTFSAdapter)
    score = adapter._compute_score(df)
    assert score.total <= 39


def test_session_preserves_ntfs_stream_context(tmp_path, make_df):
    mgr = SessionManager(tmp_path)
    session = SessionManager.new_session(r"\\.\PhysicalDrive1", ScanMode.QUICK)
    df = make_df(
        data_runs=[(0, 1234, 8, 0), (8, 9000, 2, 0)],
        data_attr_type=0x80,
        data_attr_id=7,
        data_attr_flags=2,
        data_attr_size=250_000,
        meta_seq=42,
    )
    session.results.append(df)
    mgr.save(session)
    loaded = mgr.load(session.session_id)
    got = loaded.results[0]
    assert got.data_runs == [(0, 1234, 8, 0), (8, 9000, 2, 0)]
    assert got.data_attr_type == 0x80
    assert got.data_attr_id == 7
    assert got.data_attr_flags == 2
    assert got.data_attr_size == 250_000
    assert got.meta_seq == 42


def test_ntfs_raw_runs_are_partition_relative():
    from src.core.fs.ntfs import NTFSAdapter

    class Img:
        def __init__(self):
            self.calls = []
        def read(self, offset, length):
            self.calls.append((offset, length))
            return bytes(range(offset, offset + length))

    adapter = NTFSAdapter.__new__(NTFSAdapter)
    adapter._img = Img()
    adapter._offset = 100
    adapter._block_size = 4
    data = b"".join(adapter._read_raw_runs([(0, 2, 2, 0)], 8, 8))
    assert adapter._img.calls == [(108, 8)]
    assert data == bytes(range(108, 116))
