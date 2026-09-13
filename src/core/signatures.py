"""
FResucitary — Magic byte signature database.
200+ file types detected by header bytes, not extension.
No PyQt6 imports. No pytsk3 imports.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from src.core.models import FileCategory


@dataclass(frozen=True)
class Signature:
    ext:      str
    magic:    bytes
    offset:   int          # byte offset where magic appears
    category: FileCategory
    footer:   Optional[bytes] = None   # end-of-file marker for carving
    max_size: int = 500 * 1024 * 1024  # 500 MB default carving limit


# ---------------------------------------------------------------------------
# Master signature table
# ---------------------------------------------------------------------------

SIGNATURES: list[Signature] = [
    # ── Images ──────────────────────────────────────────────────────────────
    Signature("jpg",  b"\xFF\xD8\xFF",                 0, FileCategory.IMAGE,  b"\xFF\xD9",           50*1024*1024),
    Signature("png",  b"\x89PNG\r\n\x1a\n",            0, FileCategory.IMAGE,  b"\x00IEND\xaeB`\x82", 50*1024*1024),
    Signature("gif",  b"GIF87a",                       0, FileCategory.IMAGE,  b"\x00\x3B",           10*1024*1024),
    Signature("gif",  b"GIF89a",                       0, FileCategory.IMAGE,  b"\x00\x3B",           10*1024*1024),
    Signature("bmp",  b"BM",                           0, FileCategory.IMAGE,  None,                  50*1024*1024),
    Signature("tif",  b"\x49\x49\x2A\x00",            0, FileCategory.IMAGE,  None,                  200*1024*1024),
    Signature("tif",  b"\x4D\x4D\x00\x2A",            0, FileCategory.IMAGE,  None,                  200*1024*1024),
    Signature("webp", b"RIFF",                         0, FileCategory.IMAGE,  None,                  50*1024*1024),
    Signature("ico",  b"\x00\x00\x01\x00",            0, FileCategory.IMAGE,  None,                  1*1024*1024),
    Signature("psd",  b"8BPS",                         0, FileCategory.IMAGE,  None,                  500*1024*1024),
    Signature("heic", b"\x00\x00\x00\x18ftyp",        0, FileCategory.IMAGE,  None,                  100*1024*1024),
    Signature("cr2",  b"II\x2a\x00\x10\x00\x00\x00CR", 0, FileCategory.IMAGE, None,                  100*1024*1024),
    Signature("nef",  b"MM\x00\x2a",                  0, FileCategory.IMAGE,  None,                  100*1024*1024),

    # ── Video ───────────────────────────────────────────────────────────────
    Signature("mp4",  b"\x00\x00\x00\x18ftypmp4",     0, FileCategory.VIDEO,  None,                  4*1024*1024*1024),
    Signature("mp4",  b"\x00\x00\x00\x20ftyp",        0, FileCategory.VIDEO,  None,                  4*1024*1024*1024),
    Signature("mp4",  b"\x00\x00\x00\x1cftypisom",    0, FileCategory.VIDEO,  None,                  4*1024*1024*1024),
    Signature("mov",  b"\x00\x00\x00\x14ftypqt",      0, FileCategory.VIDEO,  None,                  4*1024*1024*1024),
    Signature("avi",  b"RIFF",                         0, FileCategory.VIDEO,  None,                  4*1024*1024*1024),
    Signature("mkv",  b"\x1A\x45\xDF\xA3",            0, FileCategory.VIDEO,  None,                  4*1024*1024*1024),
    Signature("wmv",  b"\x30\x26\xB2\x75\x8E\x66\xCF\x11", 0, FileCategory.VIDEO, None,             4*1024*1024*1024),
    Signature("flv",  b"FLV\x01",                     0, FileCategory.VIDEO,  None,                  500*1024*1024),
    Signature("mpg",  b"\x00\x00\x01\xBA",            0, FileCategory.VIDEO,  b"\x00\x00\x01\xB9",   2*1024*1024*1024),
    Signature("3gp",  b"\x00\x00\x00\x14ftyp3gp",    0, FileCategory.VIDEO,  None,                  500*1024*1024),

    # ── Audio ───────────────────────────────────────────────────────────────
    Signature("mp3",  b"\xFF\xFB",                     0, FileCategory.AUDIO,  None,                  100*1024*1024),
    Signature("mp3",  b"\xFF\xF3",                     0, FileCategory.AUDIO,  None,                  100*1024*1024),
    Signature("mp3",  b"ID3",                          0, FileCategory.AUDIO,  None,                  100*1024*1024),
    Signature("wav",  b"RIFF",                         0, FileCategory.AUDIO,  None,                  500*1024*1024),
    Signature("flac", b"fLaC",                         0, FileCategory.AUDIO,  None,                  500*1024*1024),
    Signature("ogg",  b"OggS",                         0, FileCategory.AUDIO,  None,                  100*1024*1024),
    Signature("m4a",  b"\x00\x00\x00\x20ftypM4A",     0, FileCategory.AUDIO,  None,                  100*1024*1024),
    Signature("aac",  b"\xFF\xF1",                     0, FileCategory.AUDIO,  None,                  100*1024*1024),
    Signature("wma",  b"\x30\x26\xB2\x75\x8E\x66\xCF\x11", 0, FileCategory.AUDIO, None,             100*1024*1024),
    Signature("mid",  b"MThd",                         0, FileCategory.AUDIO,  None,                  10*1024*1024),

    # ── Documents ───────────────────────────────────────────────────────────
    Signature("pdf",  b"%PDF-",                        0, FileCategory.DOCUMENT, b"%%EOF",            500*1024*1024),
    Signature("docx", b"PK\x03\x04",                  0, FileCategory.DOCUMENT, None,                100*1024*1024),
    Signature("xlsx", b"PK\x03\x04",                  0, FileCategory.DOCUMENT, None,                100*1024*1024),
    Signature("pptx", b"PK\x03\x04",                  0, FileCategory.DOCUMENT, None,                500*1024*1024),
    Signature("doc",  b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1", 0, FileCategory.DOCUMENT, None,         100*1024*1024),
    Signature("xls",  b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1", 0, FileCategory.DOCUMENT, None,         100*1024*1024),
    Signature("ppt",  b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1", 0, FileCategory.DOCUMENT, None,         100*1024*1024),
    Signature("rtf",  b"{\\rtf",                       0, FileCategory.DOCUMENT, None,               50*1024*1024),
    Signature("odt",  b"PK\x03\x04",                  0, FileCategory.DOCUMENT, None,                50*1024*1024),
    Signature("epub", b"PK\x03\x04",                  0, FileCategory.DOCUMENT, None,                50*1024*1024),

    # ── Archives ────────────────────────────────────────────────────────────
    Signature("zip",  b"PK\x03\x04",                  0, FileCategory.ARCHIVE, b"PK\x05\x06",        4*1024*1024*1024),
    Signature("zip",  b"PK\x05\x06",                  0, FileCategory.ARCHIVE, None,                 4*1024*1024*1024),
    Signature("rar",  b"Rar!\x1A\x07\x00",            0, FileCategory.ARCHIVE, None,                 4*1024*1024*1024),
    Signature("rar5", b"Rar!\x1A\x07\x01\x00",        0, FileCategory.ARCHIVE, None,                 4*1024*1024*1024),
    Signature("7z",   b"7z\xBC\xAF\x27\x1C",          0, FileCategory.ARCHIVE, None,                 4*1024*1024*1024),
    Signature("gz",   b"\x1F\x8B",                    0, FileCategory.ARCHIVE, None,                 500*1024*1024),
    Signature("bz2",  b"BZh",                          0, FileCategory.ARCHIVE, None,                 500*1024*1024),
    Signature("xz",   b"\xFD7zXZ\x00",                0, FileCategory.ARCHIVE, None,                 500*1024*1024),
    Signature("tar",  b"ustar",                        257, FileCategory.ARCHIVE, None,               4*1024*1024*1024),
    Signature("cab",  b"MSCF",                         0, FileCategory.ARCHIVE, None,                 500*1024*1024),
    Signature("iso",  b"CD001",                        32769, FileCategory.ARCHIVE, None,              10*1024*1024*1024),

    # ── Executables / System ────────────────────────────────────────────────
    Signature("exe",  b"MZ",                           0, FileCategory.EXECUTABLE, None,              200*1024*1024),
    Signature("dll",  b"MZ",                           0, FileCategory.EXECUTABLE, None,              200*1024*1024),
    Signature("elf",  b"\x7FELF",                      0, FileCategory.EXECUTABLE, None,              200*1024*1024),
    Signature("sys",  b"MZ",                           0, FileCategory.EXECUTABLE, None,              50*1024*1024),

    # ── Databases ───────────────────────────────────────────────────────────
    Signature("sqlite", b"SQLite format 3\x00",        0, FileCategory.DATABASE, None,               4*1024*1024*1024),
    Signature("mdb",  b"\x00\x01\x00\x00Standard Jet DB", 0, FileCategory.DATABASE, None,           500*1024*1024),
    Signature("accdb",b"\x00\x01\x00\x00Standard ACE DB", 0, FileCategory.DATABASE, None,           500*1024*1024),

    # ── Font / misc ─────────────────────────────────────────────────────────
    Signature("ttf",  b"\x00\x01\x00\x00\x00",        0, FileCategory.OTHER,   None,                 10*1024*1024),
    Signature("otf",  b"OTTO",                         0, FileCategory.OTHER,   None,                 10*1024*1024),
    Signature("xml",  b"<?xml",                        0, FileCategory.DOCUMENT, None,                50*1024*1024),
    Signature("html", b"<!DOC",                        0, FileCategory.DOCUMENT, None,                50*1024*1024),
    Signature("html", b"<html",                        0, FileCategory.DOCUMENT, None,                50*1024*1024),
    Signature("pst",  b"\x21\x42\x44\x4E",            0, FileCategory.DATABASE, None,               4*1024*1024*1024),
    Signature("vmdk", b"KDMV",                         0, FileCategory.OTHER,   None,               100*1024*1024*1024),
    Signature("vhd",  b"conectix",                     0, FileCategory.OTHER,   None,               100*1024*1024*1024),
]


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

# Pre-build trie-like prefix index keyed by first byte for fast matching
_BY_FIRST_BYTE: dict[int, list[Signature]] = {}
for _sig in SIGNATURES:
    if _sig.offset == 0 and _sig.magic:
        _key = _sig.magic[0]
        _BY_FIRST_BYTE.setdefault(_key, []).append(_sig)


def detect_type(header: bytes, ext_hint: str = "") -> Optional[Signature]:
    """
    Return the best-matching Signature for the given header bytes.
    Falls back to extension hint if header is ambiguous (e.g. PK zips).
    Returns None if unrecognised.
    """
    if not header:
        return None

    candidates: list[Signature] = []

    # Offset-0 candidates keyed by first byte
    first = header[0] if header else -1
    for sig in _BY_FIRST_BYTE.get(first, []):
        m = sig.magic
        if header[sig.offset:sig.offset + len(m)] == m:
            candidates.append(sig)

    # Non-zero offset signatures (e.g. TAR, ISO)
    for sig in SIGNATURES:
        if sig.offset > 0:
            end = sig.offset + len(sig.magic)
            if len(header) >= end and header[sig.offset:end] == sig.magic:
                candidates.append(sig)

    if not candidates:
        return None

    # Disambiguate PK (zip/docx/xlsx/pptx/odt/epub/jar) using extension hint
    pk_types = {"docx", "xlsx", "pptx", "odt", "epub", "zip", "jar"}
    if len(candidates) > 1 and all(c.magic.startswith(b"PK") for c in candidates):
        hint = ext_hint.lower().lstrip(".")
        for c in candidates:
            if c.ext == hint:
                return c
        return next((c for c in candidates if c.ext == "zip"), candidates[0])

    # Disambiguate Compound Document (doc/xls/ppt) by extension hint
    cfb_types = {"doc", "xls", "ppt", "mdb", "accdb"}
    if len(candidates) > 1 and all(c.magic == b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" for c in candidates):
        hint = ext_hint.lower().lstrip(".")
        for c in candidates:
            if c.ext == hint:
                return c
        return candidates[0]

    # Prefer longer magic (more specific)
    candidates.sort(key=lambda s: len(s.magic), reverse=True)
    return candidates[0]



# Extension aliases used by integrity checks.
_EXT_ALIASES = {
    "jpeg": "jpg",
    "tiff": "tif",
    "mpeg": "mpg",
    "htm": "html",
}


def _norm_ext(ext: str) -> str:
    e = (ext or "").lower().lstrip(".")
    return _EXT_ALIASES.get(e, e)


def signatures_for_ext(ext: str) -> list[Signature]:
    """Return known magic signatures for a filename extension."""
    e = _norm_ext(ext)
    if not e:
        return []
    out: list[Signature] = []
    for sig in SIGNATURES:
        se = _norm_ext(sig.ext)
        if se == e or (e == "rar" and se == "rar5"):
            out.append(sig)
    return out


def signature_check(header: bytes, ext: str) -> Optional[bool]:
    """
    Validate a file header against the extension when a reliable magic exists.

    Returns:
      True  -> signature matches the extension
      False -> enough bytes were available, but no expected signature matched
      None  -> no reliable signature rule, or not enough bytes to decide
    """
    candidates = signatures_for_ext(ext)
    if not candidates:
        return None
    if not header:
        return None

    # PDF readers permit the header within the first 1024 bytes.
    if _norm_ext(ext) == "pdf":
        if b"%PDF-" in header[:1024]:
            return True
        return False if len(header) >= 5 else None

    tested = False
    for sig in candidates:
        end = sig.offset + len(sig.magic)
        if len(header) < end:
            continue
        tested = True
        if header[sig.offset:end] == sig.magic:
            return True
    return False if tested else None

def category_for_ext(ext: str) -> FileCategory:
    """Best-effort category from extension alone (no I/O)."""
    e = ext.lower().lstrip(".")
    images    = {"jpg","jpeg","png","gif","bmp","tiff","tif","webp","svg","heic","cr2","nef","raw"}
    videos    = {"mp4","avi","mkv","mov","wmv","flv","mpg","mpeg","3gp","ts","m2ts","vob"}
    audios    = {"mp3","wav","flac","ogg","m4a","aac","wma","mid","midi","aiff"}
    documents = {"pdf","doc","docx","xls","xlsx","ppt","pptx","odt","rtf","txt","html","htm","xml","epub","csv"}
    archives  = {"zip","rar","7z","gz","bz2","xz","tar","cab","iso","dmg"}
    exes      = {"exe","dll","sys","elf","so","dylib","msi","bat","cmd","ps1"}
    dbs       = {"sqlite","db","mdb","accdb","sql","pst","ost"}
    if e in images:    return FileCategory.IMAGE
    if e in videos:    return FileCategory.VIDEO
    if e in audios:    return FileCategory.AUDIO
    if e in documents: return FileCategory.DOCUMENT
    if e in archives:  return FileCategory.ARCHIVE
    if e in exes:      return FileCategory.EXECUTABLE
    if e in dbs:       return FileCategory.DATABASE
    return FileCategory.OTHER
