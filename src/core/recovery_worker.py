"""
FResucitary — Recovery worker (QThread).
Priority queue recovery: high-score files first.
SHA-256 hash verification, arborescence preservation, conflict handling.
NEVER writes to source disk.
"""
from __future__ import annotations
import hashlib
import heapq
import logging
import os
import re
import time
from typing import Optional

from PyQt6.QtCore import QMutex, QThread, QWaitCondition, pyqtSignal

from src.core.models import DeletedFile, RecoveryStatus, RecoveryTask

log = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024  # 1 MB read chunks


class RecoveryWorker(QThread):
    """
    QThread consuming a priority queue of RecoveryTask objects.

    Signals:
        file_recovered(DeletedFile)      — one file done
        file_failed(DeletedFile, str)    — one file failed
        progress(done, total, speed_mb)  — speed in MB/s
        log_message(str)
        recovery_finished(int, int, int, int) — (intact, partial, corrupt, failed)
    """

    file_recovered   = pyqtSignal(object)        # DeletedFile
    file_failed      = pyqtSignal(object, str)   # DeletedFile, error
    progress         = pyqtSignal(int, int, float)  # done, total, MB/s
    log_message      = pyqtSignal(str)
    recovery_finished = pyqtSignal(int, int, int, int)

    def __init__(
        self,
        tasks: list[RecoveryTask],
        source_path: str,
        output_dir: str,
        compute_hash: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.source_path  = source_path
        self.output_dir   = output_dir
        self.compute_hash = compute_hash
        self._cancelled   = False
        self._paused      = False

        # Build max-heap (heapq is min-heap, RecoveryTask.__lt__ inverts priority)
        self._queue: list[RecoveryTask] = []
        for task in tasks:
            task.priority = task.file.recovery_score
            heapq.heappush(self._queue, task)

        self._total   = len(self._queue)
        self._done    = 0
        self._mutex   = QMutex()
        self._paused_cond = QWaitCondition()

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        self._cancelled = True
        self._paused_cond.wakeAll()

    def pause(self) -> None:
        self._paused = True
        self._log("Récupération en pause…")

    def resume(self) -> None:
        self._paused = False
        self._paused_cond.wakeAll()
        self._log("Récupération reprise.")

    def add_tasks(self, new_tasks: list[RecoveryTask]) -> None:
        for task in new_tasks:
            task.priority = task.file.recovery_score
            heapq.heappush(self._queue, task)
        self._total += len(new_tasks)

    # ------------------------------------------------------------------
    # Thread entry
    # ------------------------------------------------------------------

    def run(self) -> None:
        from src.core.fs.image import DiskSource
        from src.core.fs.ntfs import NTFSAdapter
        from src.core.fs.fat32 import FAT32Adapter
        from src.security.safe import ReadOnlyGuard

        recovered = 0
        partial   = 0
        corrupt   = 0
        failed    = 0
        start     = time.time()

        # Defense in depth: the worker itself enforces destination safety even
        # when it is invoked outside the main window.
        try:
            ReadOnlyGuard.validate_output_not_on_source(self.source_path, self.output_dir)
        except PermissionError as exc:
            self.log_message.emit(str(exc))
            self.recovery_finished.emit(0, 0, 0, self._total)
            return

        try:
            source = DiskSource(self.source_path)
            parts = source.detect_partitions()
        except Exception as exc:
            self.log_message.emit(f"Impossible d'ouvrir la source : {exc}")
            self.recovery_finished.emit(0, 0, 0, self._total)
            return

        # Cache one filesystem adapter per partition. A source can contain
        # several NTFS/FAT volumes and an inode is only meaningful inside the
        # volume it came from.
        adapters: dict[tuple[int, str], object] = {}

        def get_adapter(df: DeletedFile):
            fs_type = df.source_fs or ""
            offset = int(df.source_offset or 0)

            # Backward compatibility for sessions created before source context
            # was persisted: infer the partition by filesystem type.
            if not fs_type:
                inferred = next((p for p in parts if p.offset_bytes == offset), None)
                if inferred is None:
                    inferred = next((p for p in parts if p.fs_type == "NTFS"), None)
                if inferred is not None:
                    fs_type = inferred.fs_type
                    offset = inferred.offset_bytes

            key = (offset, fs_type)
            if key in adapters:
                return adapters[key]

            if fs_type == "NTFS":
                adapter = NTFSAdapter(source.img, offset)
            elif fs_type in ("FAT12", "FAT16", "FAT32", "exFAT"):
                adapter = FAT32Adapter(source.img, offset)
            else:
                raise RuntimeError(
                    f"Système de fichiers non récupérable via métadonnées : {fs_type or 'inconnu'}"
                )
            adapters[key] = adapter
            return adapter

        bytes_written = 0

        while self._queue and not self._cancelled:
            self._mutex.lock()
            while self._paused and not self._cancelled:
                self._paused_cond.wait(self._mutex)
            self._mutex.unlock()

            if self._cancelled:
                break

            task = heapq.heappop(self._queue)
            df   = task.file
            df.status = RecoveryStatus.RECOVERING

            try:
                out_path = self._resolve_output_path(task)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)

                if df.carved:
                    status, sha, size, note = self._recover_carved(df, out_path, source)
                else:
                    adapter = get_adapter(df)
                    status, sha, size, note = self._recover_metadata(df, out_path, adapter)

                df.sha256 = sha
                df.output_path = out_path if os.path.exists(out_path) else ""
                df.recovered_size = size
                df.recovery_note = note
                df.error = None if status == RecoveryStatus.RECOVERED else note
                df.status = status
                bytes_written += size

                if status == RecoveryStatus.RECOVERED:
                    recovered += 1
                    self._log(f"  ✓ {df.name}: intact — {note}")
                    self.file_recovered.emit(df)
                elif status == RecoveryStatus.PARTIAL:
                    partial += 1
                    self._log(f"  ⚠ {df.name}: partiel — {note}")
                    self.file_recovered.emit(df)
                elif status == RecoveryStatus.CORRUPT:
                    corrupt += 1
                    self._log(f"  ⛔ {df.name}: corrompu — {note}")
                    self.file_recovered.emit(df)
                else:
                    raise RuntimeError(note or "Aucune donnée lisible pour ce fichier")

            except Exception as exc:
                df.status = RecoveryStatus.FAILED
                df.error  = str(exc)
                failed   += 1
                self._log(f"  ✗ {df.name}: {exc}")
                self.file_failed.emit(df, str(exc))

            self._done += 1
            elapsed     = time.time() - start
            speed_mb    = (bytes_written / (1024 * 1024)) / max(elapsed, 0.001)
            self.progress.emit(self._done, self._total, speed_mb)

        for adapter in adapters.values():
            try:
                adapter.close()
            except Exception:
                pass
        source.close()

        self._log(
            f"Récupération terminée : {recovered} intact(s), {partial} partiel(s), "
            f"{corrupt} corrompu(s), {failed} échec(s)"
        )
        self.recovery_finished.emit(recovered, partial, corrupt, failed)

    # ------------------------------------------------------------------
    # Recovery implementations
    # ------------------------------------------------------------------

    def _recover_metadata(
        self,
        df: DeletedFile,
        out_path: str,
        adapter,
    ) -> tuple[RecoveryStatus, str, int, str]:
        """Recover a metadata-backed file and validate the resulting bytes."""
        if adapter is None:
            raise RuntimeError("Adaptateur de système de fichiers non disponible")

        from src.core.recovery_validation import validate_recovered_output

        hasher = hashlib.sha256() if self.compute_hash else None
        written = 0

        with open(out_path, "wb") as f:
            for chunk in adapter.read_file_data(
                df.inode,
                df.size,
                attr_type=df.data_attr_type,
                attr_id=df.data_attr_id,
                meta_seq=df.meta_seq,
                data_runs=df.data_runs,
                data_attr_flags=df.data_attr_flags,
            ):
                if not chunk:
                    continue
                f.write(chunk)
                written += len(chunk)
                if hasher:
                    hasher.update(chunk)

        sha = hasher.hexdigest() if hasher else ""
        result = validate_recovered_output(df, out_path, written)
        if result.status == RecoveryStatus.FAILED:
            try:
                os.remove(out_path)
            except OSError:
                pass
        return result.status, sha, written, result.note

    def _recover_carved(
        self,
        df: DeletedFile,
        out_path: str,
        source: "DiskSource",
    ) -> tuple[RecoveryStatus, str, int, str]:
        """Recover carved file and validate the resulting bytes."""
        if not df.clusters:
            raise RuntimeError("Offset de carving manquant")

        # Carver offsets are relative to the partition that was scanned.
        # Add the filesystem partition offset to reach the physical source.
        disk_offset = int(df.source_offset or 0) + int(df.clusters[0])
        total_size  = df.size
        hasher      = hashlib.sha256() if self.compute_hash else None
        written     = 0

        with open(out_path, "wb") as f:
            offset = disk_offset
            remaining = total_size
            while remaining > 0:
                to_read = min(CHUNK_SIZE, remaining)
                try:
                    chunk = bytes(source.img.read(offset, to_read))
                except Exception:
                    break
                if not chunk:
                    break
                f.write(chunk)
                written    += len(chunk)
                offset     += len(chunk)
                remaining  -= len(chunk)
                if hasher:
                    hasher.update(chunk)

        sha = hasher.hexdigest() if hasher else ""
        from src.core.recovery_validation import validate_recovered_output
        result = validate_recovered_output(df, out_path, written)
        if result.status == RecoveryStatus.FAILED:
            try:
                os.remove(out_path)
            except OSError:
                pass
        return result.status, sha, written, result.note

    # ------------------------------------------------------------------
    # Output path resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_component(value: str, fallback: str = "fichier") -> str:
        """Return a Windows-safe single path component."""
        # Control chars and characters illegal in Win32 file names.
        value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value or "")
        value = value.strip().rstrip(". ")
        if not value or value in (".", ".."): 
            value = fallback

        # Windows device names are reserved even when an extension is present.
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        stem = value.split(".", 1)[0].upper()
        if stem in reserved:
            value = "_" + value

        # Keep path lengths manageable for tools that are not long-path aware.
        return value[:180] or fallback

    def _resolve_output_path(self, task: RecoveryTask) -> str:
        df = task.file
        base = os.path.abspath(self.output_dir)
        safe_name = self._safe_component(df.name, "fichier_recupere")

        if task.preserve_tree and df.path and df.path != df.name:
            raw_parts = df.path.replace("\\", "/").split("/")
            parts: list[str] = []
            for raw in raw_parts:
                raw = raw.strip()
                if not raw or raw in (".", ".."):
                    continue
                # Drop drive/UNC syntax and sanitize each remaining component.
                if len(raw) == 2 and raw[1] == ":":
                    continue
                parts.append(self._safe_component(raw))
            if not parts or parts[-1].lower() != safe_name.lower():
                parts.append(safe_name)
            out = os.path.abspath(os.path.join(base, *parts))
        else:
            cat_dir = self._safe_component(df.category.name.capitalize(), "Autres")
            out = os.path.abspath(os.path.join(base, cat_dir, safe_name))

        # Defense in depth: the candidate must remain below output_dir.
        try:
            if os.path.commonpath([base, out]) != base:
                raise ValueError("Chemin de récupération hors destination")
        except ValueError:
            out = os.path.abspath(os.path.join(base, "Autres", safe_name))

        # Conflict resolution: append numeric suffix.
        if os.path.exists(out):
            stem, ext = os.path.splitext(out)
            counter = 1
            while os.path.exists(f"{stem}_{counter}{ext}"):
                counter += 1
            out = f"{stem}_{counter}{ext}"

        return out

    def _log(self, msg: str) -> None:
        log.info(msg)
        self.log_message.emit(msg)
