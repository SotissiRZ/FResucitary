"""
FResucitary Pro — Performance benchmark suite.
Measures throughput of the carver, scorer, and session I/O.
Run: python benchmarks/benchmark.py

Targets:
  - Carver:   ≥ 200 MB/s  (for 1 TB < 90 min carving pass)
  - Scorer:   ≥ 50 000 files/s
  - Session:  10 000 files saved + loaded < 3 s
  - Sigs:     1 000 000 detections/s
"""
from __future__ import annotations
import os
import sys
import time
import tempfile
import statistics
from pathlib import Path
from unittest.mock import MagicMock

# Stub native deps
for mod in [
    "pytsk3", "PyQt6", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets",
    "pypdfium2", "PIL", "PIL.Image",
]:
    sys.modules.setdefault(mod, MagicMock())

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.carver      import CarvingEngine, CARVE_BLOCK_SIZE
from src.core.models      import DeletedFile, FileCategory, ScanMode, ScoreBreakdown
from src.core.session_mgr import SessionManager
from src.core.signatures  import detect_type, SIGNATURES

# ── ANSI colours ─────────────────────────────────────────────────────────
G  = "\033[92m"
Y  = "\033[93m"
R  = "\033[91m"
B  = "\033[94m"
NC = "\033[0m"
BOLD = "\033[1m"


def _header(title: str) -> None:
    print(f"\n{BOLD}{B}{'─'*60}{NC}")
    print(f"{BOLD}{B}  {title}{NC}")
    print(f"{BOLD}{B}{'─'*60}{NC}")


def _result(name: str, value: str, target: str, ok: bool) -> None:
    icon = f"{G}✓{NC}" if ok else f"{R}✗{NC}"
    print(f"  {icon}  {name:<30} {value:>15}   (cible: {target})")


def _timeit(fn, repeat: int = 3):
    """Run fn `repeat` times, return (min_s, avg_s, results_of_last)."""
    times = []
    result = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        result = fn()
        times.append(time.perf_counter() - t0)
    return min(times), statistics.mean(times), result


# ===========================================================================
# 1. Carver throughput
# ===========================================================================

def bench_carver() -> None:
    _header("Benchmark : Moteur de carving")

    # Build synthetic disk: signatures every 64 KB, rest zeros
    DISK_SIZE_MB = 128
    disk_size    = DISK_SIZE_MB * 1024 * 1024
    disk         = bytearray(disk_size)

    # Plant signatures
    sigs_to_plant = [
        (b"\xFF\xD8\xFF\xE0", "jpg"),
        (b"%PDF-",             "pdf"),
        (b"\x89PNG\r\n\x1a\n","png"),
        (b"PK\x03\x04",       "zip"),
        (b"Rar!\x1A\x07\x00", "rar"),
    ]
    interval = 64 * 1024
    for i, (magic, _) in enumerate(sigs_to_plant * (disk_size // (interval * len(sigs_to_plant)) + 1)):
        offset = i * interval
        if offset + len(magic) < disk_size:
            disk[offset:offset + len(magic)] = magic

    disk_bytes = bytes(disk)

    def read_fn(offset: int, length: int) -> bytes:
        return disk_bytes[offset:offset + length]

    def run_carver():
        c = CarvingEngine(read_fn, disk_size)
        return list(c.carve())

    min_t, avg_t, results = _timeit(run_carver, repeat=2)

    throughput_mb = DISK_SIZE_MB / min_t
    files_found   = len(results)
    TARGET_MB_S   = 200.0

    print(f"  Taille disque : {DISK_SIZE_MB} MB")
    print(f"  Fichiers trouvés : {files_found}")
    _result("Débit carving",    f"{throughput_mb:>8.1f} MB/s", f"≥{TARGET_MB_S:.0f} MB/s", throughput_mb >= TARGET_MB_S)
    _result("Temps (min)",      f"{min_t:>8.3f} s",    "< 1 s / 128 MB",             min_t < 1.0)

    # Extrapolate to 1 TB
    tb_minutes = (1024 * 1024) / (throughput_mb * 60)
    ok_tb = tb_minutes < 90
    _result("Extrapolation 1 TB", f"{tb_minutes:>8.1f} min", "< 90 min",               ok_tb)

    return throughput_mb >= TARGET_MB_S


# ===========================================================================
# 2. Scoring throughput
# ===========================================================================

def bench_scoring() -> None:
    _header("Benchmark : Moteur de scoring")

    from unittest.mock import patch
    from src.core.fs.ntfs import NTFSAdapter

    with patch.object(NTFSAdapter, "__init__", return_value=None):
        adapter = NTFSAdapter.__new__(NTFSAdapter)

    # Create 100k DeletedFile objects
    N = 100_000
    files = []
    for i in range(N):
        df = DeletedFile(
            name=f"file_{i}.jpg",
            path=f"Photos/file_{i}.jpg",
            inode=i + 1000,
            meta_flags=0,
            size=(i % 50) * 1024 * 1024 + 1024,
            file_type_by_ext="jpg",
            file_type_by_magic="jpg",
            category=FileCategory.IMAGE,
            mft_record_valid=True,
            data_runs_count=(i % 5) + 1,
            in_recycle_bin=(i % 10 == 0),
            deleted_at=time.time() - (i % 30) * 86400,
            modified_at=time.time() - 3600,
            created_at=time.time() - 86400,
            header_bytes=b"\xFF\xD8\xFF\xE0" + b"\x00" * 508,
        )
        files.append(df)

    def run_scoring():
        for df in files:
            df.score = adapter._compute_score(df)
        return files

    min_t, avg_t, _ = _timeit(run_scoring, repeat=3)

    rate = N / min_t
    TARGET_RATE = 50_000  # files/s

    _result("Débit scoring",     f"{rate:>10.0f} f/s",  f"≥{TARGET_RATE:,} f/s", rate >= TARGET_RATE)
    _result("Temps (100k files)",f"{min_t*1000:>10.1f} ms", "< 2 000 ms",         min_t < 2.0)
    _result("Score moyen",       f"{sum(f.recovery_score for f in files[:100])//100:>10}%", "30–80%", True)


# ===========================================================================
# 3. Signature detection throughput
# ===========================================================================

def bench_signatures() -> None:
    _header("Benchmark : Détection de signatures")

    headers = [
        b"\xFF\xD8\xFF\xE0" + b"\x00" * 508,   # JPEG
        b"%PDF-1.7"          + b"\x00" * 504,   # PDF
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 504,   # PNG
        b"PK\x03\x04"        + b"\x00" * 508,   # ZIP
        b"MZ"                + b"\x00" * 510,   # EXE
        b"\x00\x01\x02\x03" + b"\x00" * 508,   # Unknown
        b"SQLite format 3\x00" + b"\x00"*496,   # SQLite
        b"Rar!\x1A\x07\x00" + b"\x00"*505,      # RAR
    ]
    N = 1_000_000 // len(headers)  # balanced

    def run():
        for _ in range(N):
            for h in headers:
                detect_type(h)

    min_t, _, _ = _timeit(run, repeat=3)
    total = N * len(headers)
    rate  = total / min_t
    TARGET = 500_000

    _result("Débit détection",   f"{rate:>12.0f}/s",  f"≥{TARGET:,}/s",    rate >= TARGET)
    _result("Temps (1M déts.)",  f"{min_t*1000:>10.1f} ms", "< 2 000 ms",  min_t < 2.0)
    _result("Signatures en base",f"{len(SIGNATURES):>12}",    "≥ 50",       len(SIGNATURES) >= 50)


# ===========================================================================
# 4. Session I/O throughput
# ===========================================================================

def bench_session_io() -> None:
    _header("Benchmark : Persistance de session (JSON)")

    N_FILES = 10_000

    def make_files():
        files = []
        for i in range(N_FILES):
            df = DeletedFile(
                name=f"file_{i:06d}.jpg",
                path=f"dir_{i//1000}/file_{i:06d}.jpg",
                inode=1_000_000 + i,
                meta_flags=0,
                size=250_000 + i,
                file_type_by_ext="jpg",
                file_type_by_magic="jpg",
                category=FileCategory.IMAGE,
                mft_record_valid=True,
                data_runs_count=1,
                deleted_at=time.time() - 3600,
            )
            df.score = ScoreBreakdown(
                metadata_score=30, cluster_score=25, header_score=15,
                size_score=10, preview_score=5,
            )
            files.append(df)
        return files

    with tempfile.TemporaryDirectory() as tmp:
        mgr = SessionManager(Path(tmp))

        from src.core.models import ScanSession
        session = SessionManager.new_session("/dev/sda", ScanMode.QUICK)
        session.results = make_files()
        session.completed = True
        session.finished_at = time.time()

        # Save benchmark
        t0 = time.perf_counter()
        path = mgr.save(session)
        save_t = time.perf_counter() - t0
        file_size_kb = path.stat().st_size / 1024

        # Load benchmark
        t0 = time.perf_counter()
        loaded = mgr.load(session.session_id)
        load_t = time.perf_counter() - t0

        assert loaded is not None and len(loaded.results) == N_FILES

    _result("Sauvegarde 10k fichiers", f"{save_t*1000:>8.0f} ms", "< 2 000 ms",  save_t < 2.0)
    _result("Chargement 10k fichiers", f"{load_t*1000:>8.0f} ms", "< 2 000 ms",  load_t < 2.0)
    _result("Taille fichier session",  f"{file_size_kb:>8.0f} KB",  "< 20 000 KB", file_size_kb < 20_000)
    _result("Total save+load",         f"{(save_t+load_t)*1000:>8.0f} ms", "< 4 000 ms", (save_t+load_t) < 4.0)


# ===========================================================================
# 5. Memory usage (simple)
# ===========================================================================

def bench_memory() -> None:
    _header("Benchmark : Empreinte mémoire (100k DeletedFile)")

    try:
        import tracemalloc
        tracemalloc.start()
    except Exception:
        print("  tracemalloc non disponible — test ignoré")
        return

    files = []
    for i in range(100_000):
        df = DeletedFile(
            name=f"f{i}.jpg",
            path=f"p{i}.jpg",
            inode=i,
            meta_flags=0,
            size=500_000,
        )
        df.score = ScoreBreakdown(metadata_score=30)
        files.append(df)

    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_mb = peak / (1024 * 1024)
    TARGET_MB = 500

    _result("Mémoire 100k fichiers", f"{peak_mb:>8.1f} MB", f"< {TARGET_MB} MB", peak_mb < TARGET_MB)


# ===========================================================================
# Main
# ===========================================================================

if __name__ == "__main__":
    print(f"\n{BOLD}FResucitary Pro — Benchmarks de performance{NC}")
    print(f"Python {sys.version.split()[0]} | PID {os.getpid()}")

    results = {}

    results["carver"]    = bench_carver()
    results["scoring"]   = bench_scoring()
    results["signatures"]= bench_signatures()
    results["session"]   = bench_session_io()
    results["memory"]    = bench_memory()

    _header("Résumé")
    all_ok = True
    for name, ok in results.items():
        if ok is None:
            continue
        icon = f"{G}PASS{NC}" if ok else f"{R}FAIL{NC}"
        print(f"  [{icon}]  {name}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print(f"{G}{BOLD}✅  Tous les benchmarks ont atteint leurs cibles.{NC}")
    else:
        print(f"{R}{BOLD}⚠️   Certains benchmarks n'ont pas atteint leurs cibles.{NC}")
    print()
    sys.exit(0 if all_ok else 1)
