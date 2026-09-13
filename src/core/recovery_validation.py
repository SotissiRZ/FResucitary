"""Post-recovery integrity validation.

A copied byte stream is not automatically a successfully recovered file.
This module classifies output as intact, partial, corrupt, or failed using
byte-count and format-aware checks.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import zipfile

from src.core.models import DeletedFile, RecoveryStatus
from src.core.signatures import signature_check


@dataclass(frozen=True)
class ValidationResult:
    status: RecoveryStatus
    note: str


_ZIP_EXTS = {"zip", "docx", "xlsx", "pptx", "odt", "epub"}
_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "webp", "ico"}


def _format_validation(path: Path, ext: str) -> tuple[bool | None, str]:
    """Return (valid?, explanation); None means no parser check available."""
    ext = (ext or "").lower().lstrip(".")

    if ext == "pdf":
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(str(path))
            try:
                pages = len(doc)
            finally:
                try:
                    doc.close()
                except Exception:
                    pass
            if pages <= 0:
                return False, "PDF sans page lisible"
            return True, f"PDF lisible ({pages} page(s))"
        except Exception as exc:
            return False, f"structure PDF illisible ({exc})"

    if ext in _ZIP_EXTS:
        try:
            if not zipfile.is_zipfile(path):
                return False, "conteneur ZIP/OOXML invalide"
            with zipfile.ZipFile(path, "r") as zf:
                bad = zf.testzip()
                if bad:
                    return False, f"archive endommagée ({bad})"
            return True, "conteneur ZIP/OOXML valide"
        except Exception as exc:
            return False, f"archive illisible ({exc})"

    if ext in _IMAGE_EXTS:
        try:
            from PIL import Image
            with Image.open(path) as im:
                im.verify()
            return True, "image décodable"
        except Exception as exc:
            return False, f"image illisible ({exc})"

    return None, ""


def validate_recovered_output(
    df: DeletedFile,
    output_path: str,
    written: int,
) -> ValidationResult:
    """Classify the recovered file using size, signature, and parser checks."""
    expected = max(0, int(df.size or 0))
    written = max(0, int(written or 0))
    path = Path(output_path)

    if expected > 0 and written == 0:
        return ValidationResult(RecoveryStatus.FAILED, "aucune donnée récupérée")

    # A zero-byte source file can legitimately recover to zero bytes.
    if expected == 0 and written == 0:
        return ValidationResult(RecoveryStatus.RECOVERED, "fichier vide récupéré intégralement")

    if not path.exists():
        return ValidationResult(RecoveryStatus.FAILED, "fichier de sortie absent")

    # Validate magic bytes against the claimed extension. Read enough for ISO
    # and other signatures with a non-zero offset.
    try:
        with path.open("rb") as fh:
            header = fh.read(64 * 1024)
    except OSError as exc:
        return ValidationResult(RecoveryStatus.FAILED, f"sortie illisible ({exc})")

    sig_state = signature_check(header, df.file_type_by_ext)
    if sig_state is False:
        ext = (df.file_type_by_ext or "fichier").upper()
        return ValidationResult(
            RecoveryStatus.CORRUPT,
            f"signature {ext} absente ou incohérente; les clusters ont probablement été réutilisés",
        )

    # A short stream is never reported as a full success. Keep the bytes for
    # forensic/salvage use, but label them explicitly as partial.
    if expected > 0 and written < expected:
        pct = (written / expected) * 100.0
        return ValidationResult(
            RecoveryStatus.PARTIAL,
            f"{written} / {expected} octets récupérés ({pct:.1f}%)",
        )

    # Exact byte count still does not guarantee a usable structured file.
    fmt_valid, fmt_note = _format_validation(path, df.file_type_by_ext)
    if fmt_valid is False:
        return ValidationResult(RecoveryStatus.CORRUPT, fmt_note)

    if expected > 0 and written > expected:
        return ValidationResult(
            RecoveryStatus.PARTIAL,
            f"taille inattendue: {written} octets récupérés pour {expected} attendus",
        )

    note = f"{written} octets récupérés"
    if fmt_valid is True and fmt_note:
        note += f"; {fmt_note}"
    elif sig_state is True:
        note += "; signature cohérente"
    return ValidationResult(RecoveryStatus.RECOVERED, note)
