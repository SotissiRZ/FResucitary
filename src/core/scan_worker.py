"""
FResucitary — Scan worker (QThread).
Orchestrates NTFSAdapter + CarvingEngine across 3 scan modes.
Emits Qt signals to keep UI responsive — NEVER blocks event loop.
"""
from __future__ import annotations
import logging
import time
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from src.core.models import DeletedFile, ScanMode, ScanSession
from src.core.session_mgr import SessionManager

log = logging.getLogger(__name__)


class ScanWorker(QThread):
    """
    QThread that runs the full scan pipeline.

    Signals emitted (all cross-thread safe):
        file_found(DeletedFile)          — one file discovered
        progress(current, total, eta_s)  — progress update
        stats_update(dict)               — live statistics dict
        log_message(str)                 — log line for UI log panel
        scan_finished(ScanSession)       — scan complete with session object
        scan_error(str)                  — fatal error message
    """

    file_found   = pyqtSignal(object)       # DeletedFile
    progress     = pyqtSignal(int, int, int)  # current, total, eta_seconds
    stats_update = pyqtSignal(dict)
    log_message  = pyqtSignal(str)
    scan_finished = pyqtSignal(object)      # ScanSession
    scan_error   = pyqtSignal(str)

    def __init__(
        self,
        source_path: str,
        scan_mode: ScanMode,
        session_mgr: Optional[SessionManager] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.source_path  = source_path
        self.scan_mode    = scan_mode
        self.session_mgr  = session_mgr
        self._cancelled   = False

        # Live stats
        self._stats = {
            "total_found": 0,
            "images": 0, "videos": 0, "audio": 0, "documents": 0,
            "archives": 0, "other": 0,
            "carved": 0,
            "high_score": 0, "medium_score": 0, "low_score": 0,
            "elapsed_s": 0,
            "throughput": 0,  # files/sec
        }

    def cancel(self) -> None:
        self._cancelled = True
        self._log("Annulation demandée…")

    # ------------------------------------------------------------------
    # Thread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        from src.core.fs.image import DiskSource
        from src.core.fs.ntfs import NTFSAdapter
        from src.core.carver import CarvingEngine
        from src.core.models import FileCategory

        start = time.time()
        session = SessionManager.new_session(self.source_path, self.scan_mode)
        self._log(f"Démarrage scan {self.scan_mode.name} — source : {self.source_path}")

        try:
            source = DiskSource(self.source_path)
        except RuntimeError as exc:
            self.scan_error.emit(str(exc))
            return

        try:
            partitions = source.detect_partitions()
            self._log(f"{len(partitions)} partition(s) détectée(s)")

            total_files = 0

            for part in partitions:
                if self._cancelled:
                    break
                supported_fs = ("NTFS", "FAT12", "FAT16", "FAT32", "exFAT", "Unknown")
                if part.fs_type not in supported_fs:
                    if self.scan_mode == ScanMode.QUICK:
                        self._log(
                            f"  Partition {part.index}: métadonnées {part.fs_type} non prises en charge "
                            "en mode Rapide"
                        )
                        continue
                    self._log(
                        f"  Partition {part.index}: métadonnées {part.fs_type} non prises en charge — "
                        "carving brut uniquement"
                    )

                self._log(f"  Partition {part.index}: {part.fs_type} @ offset {part.offset_bytes}")

                # ── MFT / directory scan ──────────────────────────────
                if part.fs_type == "NTFS":
                    try:
                        adapter = NTFSAdapter(source.img, part.offset_bytes)
                        iterator = {
                            ScanMode.QUICK:    adapter.iter_deleted_quick,
                            ScanMode.DEEP:     adapter.iter_deleted_deep,
                            ScanMode.FORENSIC: adapter.iter_deleted_forensic,
                        }[self.scan_mode]()

                        part_count = 0
                        for df in iterator:
                            if self._cancelled:
                                break
                            df.source_offset = part.offset_bytes
                            df.source_fs = part.fs_type
                            self._emit_file(df, session, start)
                            total_files += 1
                            part_count  += 1
                            # Log first few files so user sees activity
                            if part_count <= 5:
                                self._log(f"    ✓ Trouvé : {df.name} ({df.score.label} {df.recovery_score}%)")

                        self._log(f"  → {part_count} fichier(s) supprimé(s) sur partition {part.index}")
                        adapter.close()
                    except Exception as exc:
                        log.exception("NTFS scan error")
                        self._log(f"  ⚠ Erreur NTFS partition {part.index}: {exc}")

                elif part.fs_type in ("FAT12", "FAT16", "FAT32", "exFAT"):
                    try:
                        from src.core.fs.fat32 import FAT32Adapter
                        adapter = FAT32Adapter(source.img, part.offset_bytes)
                        part_count = 0
                        for df in adapter.iter_deleted():
                            if self._cancelled:
                                break
                            df.source_offset = part.offset_bytes
                            df.source_fs = part.fs_type
                            self._emit_file(df, session, start)
                            total_files += 1
                            part_count  += 1
                        self._log(f"  → {part_count} fichier(s) sur partition FAT {part.index}")
                        adapter.close()
                    except Exception as exc:
                        self._log(f"  ⚠ Erreur FAT partition {part.index}: {exc}")

                # ── Carving pass (DEEP + FORENSIC) ────────────────────
                if self.scan_mode in (ScanMode.DEEP, ScanMode.FORENSIC) and not self._cancelled:
                    self._log("  Démarrage carving par signature…")

                    def read_fn(offset: int, length: int) -> bytes:
                        try:
                            return bytes(source.img.read(offset + part.offset_bytes, length))
                        except Exception:
                            return b""

                    part_size = part.size_bytes or (source.img.get_size() - part.offset_bytes)
                    carver = CarvingEngine(read_fn, part_size)

                    def carve_progress(done: int, total: int) -> None:
                        pct = int(done / total * 100) if total else 0
                        eta = self._estimate_eta(done, total, start)
                        self.progress.emit(pct, 100, eta)

                    for df in carver.carve(progress_cb=carve_progress):
                        if self._cancelled:
                            break
                        df.source_offset = part.offset_bytes
                        df.source_fs = part.fs_type
                        self._emit_file(df, session, start)
                        total_files += 1

            # ── Finalise ─────────────────────────────────────────────
            session.finished_at   = time.time()
            session.total_scanned = total_files
            session.completed     = not self._cancelled

            if self.session_mgr:
                self.session_mgr.save(session)

            elapsed = session.finished_at - start
            if total_files == 0:
                self._log("⚠  AUCUN fichier supprimé détecté.")
                self._log("   → Vérifiez que le disque contient bien des entrées supprimées.")
                self._log("   → Essayez le mode Profond ou Forensique pour une analyse plus complète.")
                self._log("   → Le mode Rapide ne scanne que le premier niveau de chaque dossier.")
            # Push one final exact statistics snapshot. During the scan,
            # updates are throttled to avoid flooding the UI; without this
            # final emission the dashboard can remain stuck on e.g. 80 while
            # the session actually contains 84 results.
            self._stats["elapsed_s"] = int(elapsed)
            self._stats["throughput"] = (
                int(total_files / elapsed) if elapsed > 0 else total_files
            )
            self.stats_update.emit(dict(self._stats))

            self._log(
                f"Scan terminé : {total_files} fichiers trouvés en "
                f"{int(elapsed//60)}m{int(elapsed%60):02d}s"
            )
            self.scan_finished.emit(session)

        except Exception as exc:
            log.exception("Scan fatal error")
            self.scan_error.emit(f"Erreur fatale : {exc}")
        finally:
            source.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit_file(self, df: DeletedFile, session: ScanSession, start: float) -> None:
        session.results.append(df)
        self.file_found.emit(df)
        self._update_stats(df, start)

    def _update_stats(self, df: DeletedFile, start: float) -> None:
        from src.core.stats import stats_key_for_category
        s = self._stats
        s["total_found"] += 1
        s["elapsed_s"] = int(time.time() - start)
        n = s["total_found"]
        s["throughput"] = n // max(1, s["elapsed_s"])

        if df.carved:
            s["carved"] += 1

        # UI keys are plural for some enum values (IMAGE -> images, etc.).
        s[stats_key_for_category(df.category)] += 1

        score = df.recovery_score
        # Keep dashboard thresholds aligned with ScoreBreakdown.label:
        # Excellent >= 75, Bon >= 50, everything below is non-excellent.
        if score >= 75:
            s["high_score"] += 1
        elif score >= 50:
            s["medium_score"] += 1
        else:
            s["low_score"] += 1

        # Emit frequently at start, then every 100 to avoid signal flood
        emit_every = 10 if n < 100 else (25 if n < 500 else 100)
        if n % emit_every == 0:
            self.stats_update.emit(dict(s))
            self.progress.emit(n, 0, 0)
        # Always emit individual file signal (already done in _emit_file)

    def _log(self, msg: str) -> None:
        log.info(msg)
        self.log_message.emit(msg)

    @staticmethod
    def _estimate_eta(done: int, total: int, start: float) -> int:
        if done == 0 or total == 0:
            return 0
        elapsed = time.time() - start
        rate = done / elapsed
        remaining = (total - done) / rate if rate > 0 else 0
        return int(remaining)
