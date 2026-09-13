"""
FResucitary — Session manager.
Saves/loads scan sessions to JSON for pause/resume across reboots.
No PyQt6. No pytsk3.
"""
from __future__ import annotations
import dataclasses
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from src.app.diagnostics import session_dir
from src.core.models import (
    DeletedFile, FileCategory, RecoveryStatus,
    ScanMode, ScanSession, ScoreBreakdown,
)

log = logging.getLogger(__name__)

SESSION_DIR = session_dir()
MAX_SESSIONS = 20   # rotate oldest


class SessionManager:
    """
    Manages the lifecycle of ScanSession objects on disk.

    Session files: %LOCALAPPDATA%/FResucitary/Sessions/<id>.json on Windows
    """

    def __init__(self, session_dir: Optional[Path] = None):
        self._dir = Path(session_dir) if session_dir else SESSION_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    @staticmethod
    def new_session(source_path: str, scan_mode: ScanMode) -> ScanSession:
        return ScanSession(
            session_id=str(uuid.uuid4())[:8],
            source_path=source_path,
            scan_mode=scan_mode,
        )

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------

    def save(self, session: ScanSession) -> Path:
        path = self._dir / f"{session.session_id}.json"
        data = {
            "session_id":    session.session_id,
            "source_path":   session.source_path,
            "scan_mode":     session.scan_mode.value,
            "started_at":    session.started_at,
            "finished_at":   session.finished_at,
            "total_scanned": session.total_scanned,
            "completed":     session.completed,
            "log_lines":     session.log_lines[-500:],   # keep last 500 lines
            "results":       [self._df_to_dict(df) for df in session.results],
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("Session saved: %s (%d results)", path.name, len(session.results))
        self._rotate()
        return path

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(self, session_id: str) -> Optional[ScanSession]:
        path = self._dir / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            session = ScanSession(
                session_id=data["session_id"],
                source_path=data["source_path"],
                scan_mode=ScanMode(data["scan_mode"]),
                started_at=data.get("started_at", 0.0),
                finished_at=data.get("finished_at", 0.0),
                total_scanned=data.get("total_scanned", 0),
                completed=data.get("completed", False),
                log_lines=data.get("log_lines", []),
                results=[self._dict_to_df(d) for d in data.get("results", [])],
            )
            log.info("Session loaded: %s (%d results)", session_id, len(session.results))
            return session
        except Exception as exc:
            log.warning("Failed to load session %s: %s", session_id, exc)
            return None

    def list_sessions(self) -> list[dict]:
        """Return list of session summary dicts, newest first."""
        summaries = []
        for path in sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                summaries.append({
                    "session_id":    data["session_id"],
                    "source_path":   data.get("source_path", "?"),
                    "scan_mode":     ScanMode(data.get("scan_mode", 1)).name,
                    "started_at":    data.get("started_at", 0.0),
                    "total_scanned": data.get("total_scanned", 0),
                    "completed":     data.get("completed", False),
                    "result_count":  len(data.get("results", [])),
                })
            except Exception:
                continue
        return summaries

    def delete(self, session_id: str) -> None:
        path = self._dir / f"{session_id}.json"
        if path.exists():
            path.unlink()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rotate(self) -> None:
        """Keep only the most recent MAX_SESSIONS sessions."""
        files = sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        while len(files) > MAX_SESSIONS:
            files.pop(0).unlink()

    @staticmethod
    def _df_to_dict(df: DeletedFile) -> dict:
        return {
            "name":               df.name,
            "path":               df.path,
            "inode":              df.inode,
            "meta_flags":         df.meta_flags,
            "size":               df.size,
            "file_type_by_ext":   df.file_type_by_ext,
            "file_type_by_magic": df.file_type_by_magic,
            "category":           df.category.value,
            "clusters":           df.clusters,
            "fragmented":         df.fragmented,
            "data_runs_count":    df.data_runs_count,
            "data_runs":          [list(r) for r in df.data_runs],
            "data_attr_type":     df.data_attr_type,
            "data_attr_id":       df.data_attr_id,
            "data_attr_flags":    df.data_attr_flags,
            "data_attr_size":     df.data_attr_size,
            "meta_seq":           df.meta_seq,
            "mft_record_valid":   df.mft_record_valid,
            "in_recycle_bin":     df.in_recycle_bin,
            "deleted_at":         df.deleted_at,
            "modified_at":        df.modified_at,
            "created_at":         df.created_at,
            "source_offset":      df.source_offset,
            "source_fs":          df.source_fs,
            "carved":             df.carved,
            "sha256":             df.sha256,
            "output_path":        df.output_path,
            "recovered_size":     df.recovered_size,
            "recovery_note":      df.recovery_note,
            "status":             df.status.value,
            "error":              df.error,
            "header_hex":         df.header_bytes.hex(),
            "score": {
                "metadata":  df.score.metadata_score,
                "cluster":   df.score.cluster_score,
                "header":    df.score.header_score,
                "size":      df.score.size_score,
                "preview":   df.score.preview_score,
                "recycle":   df.score.recycle_score,
                "timestamp": df.score.timestamp_score,
                "cap":       df.score.confidence_cap,
            },
        }

    @staticmethod
    def _dict_to_df(d: dict) -> DeletedFile:
        score_d = d.get("score", {})
        score = ScoreBreakdown(
            metadata_score  = score_d.get("metadata", 0),
            cluster_score   = score_d.get("cluster",  0),
            header_score    = score_d.get("header",   0),
            size_score      = score_d.get("size",     0),
            preview_score   = score_d.get("preview",  0),
            recycle_score   = score_d.get("recycle",  0),
            timestamp_score = score_d.get("timestamp",0),
            confidence_cap   = score_d.get("cap", 100),
        )
        df = DeletedFile(
            name=d.get("name", ""),
            path=d.get("path", ""),
            inode=d.get("inode", 0),
            meta_flags=d.get("meta_flags", 0),
            size=d.get("size", 0),
            file_type_by_ext=d.get("file_type_by_ext", ""),
            file_type_by_magic=d.get("file_type_by_magic", ""),
            category=FileCategory(d.get("category", FileCategory.OTHER.value)),
            clusters=[int(x) for x in d.get("clusters", [])],
            fragmented=d.get("fragmented", False),
            data_runs_count=d.get("data_runs_count", 0),
            data_runs=[
                (int(r[0]), int(r[1]), int(r[2]), int(r[3]) if len(r) > 3 else 0)
                for r in d.get("data_runs", [])
                if isinstance(r, (list, tuple)) and len(r) >= 3
            ],
            data_attr_type=d.get("data_attr_type", 0),
            data_attr_id=d.get("data_attr_id", -1),
            data_attr_flags=d.get("data_attr_flags", 0),
            data_attr_size=d.get("data_attr_size", 0),
            meta_seq=d.get("meta_seq", 0),
            mft_record_valid=d.get("mft_record_valid", False),
            in_recycle_bin=d.get("in_recycle_bin", False),
            deleted_at=d.get("deleted_at", 0.0),
            modified_at=d.get("modified_at", 0.0),
            created_at=d.get("created_at", 0.0),
            source_offset=d.get("source_offset", 0),
            source_fs=d.get("source_fs", ""),
            carved=d.get("carved", False),
            sha256=d.get("sha256", ""),
            output_path=d.get("output_path", ""),
            recovered_size=d.get("recovered_size", 0),
            recovery_note=d.get("recovery_note", ""),
            status=RecoveryStatus(d.get("status", RecoveryStatus.AVAILABLE.value)),
            error=d.get("error"),
            header_bytes=bytes.fromhex(d.get("header_hex", "")) if d.get("header_hex") else b"",
        )
        df.score = score
        return df
