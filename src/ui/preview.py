"""
FResucitary — Preview panel widget.
Displays image thumbnails, PDF first page, text excerpts,
audio metadata and hex dump. Reads from in-memory bytes only
(never writes intermediate temp files to source drive).
"""
from __future__ import annotations
import logging
import struct
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QPixmap, QFont, QColor
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
    QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from src.core.models import DeletedFile

log = logging.getLogger(__name__)

THUMB_SIZE = QSize(400, 300)


# ---------------------------------------------------------------------------
# Async loader
# ---------------------------------------------------------------------------

class PreviewLoader(QThread):
    """Load preview data in background to avoid blocking the UI."""
    preview_ready = pyqtSignal(str, bytes, object)   # (kind, raw_bytes, extra)
    preview_error = pyqtSignal(str)

    def __init__(self, df: DeletedFile, read_fn, parent=None):
        super().__init__(parent)
        self.df      = df
        self.read_fn = read_fn   # Callable[[inode, size], bytes]

    def run(self) -> None:
        df = self.df
        try:
            # Most previews only need a small prefix. PDF renderers, however,
            # normally need the xref/trailer near EOF, so read the whole file
            # up to a conservative memory cap.
            ftype = df.file_type_by_magic or df.file_type_by_ext
            if ftype == "pdf":
                if df.size > 64 * 1024 * 1024:
                    self.preview_error.emit(
                        "PDF trop volumineux pour l’aperçu (> 64 Mo). "
                        "Récupérez-le d’abord pour le vérifier."
                    )
                    return
                preview_size = df.size
            else:
                preview_size = min(df.size, 2 * 1024 * 1024)

            raw = self.read_fn(df.inode, preview_size)
            if not raw:
                self.preview_error.emit("Aucune donnée lisible")
                return

            if ftype in ("jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "tiff"):
                self.preview_ready.emit("image", raw, None)

            elif ftype == "pdf":
                # Never display binary PDF bytes as text. If the signature is
                # gone, the deleted file content has probably been overwritten
                # or the MFT entry points to incomplete/fragmented data.
                if not raw.startswith(b"%PDF-"):
                    self.preview_error.emit(
                        "PDF non prévisualisable : signature %PDF absente. "
                        "Le contenu est probablement partiel ou écrasé."
                    )
                    return

                try:
                    import pypdfium2 as pdfium  # type: ignore
                    doc  = pdfium.PdfDocument(raw)
                    page = doc[0]
                    bmp  = page.render(scale=1.5)
                    img  = bmp.to_pil()
                    import io
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    self.preview_ready.emit("image", buf.getvalue(), None)
                except Exception as exc:
                    log.debug("PDF preview rendering failed: %s", exc)
                    self.preview_error.emit(
                        "PDF détecté, mais la première page ne peut pas être rendue. "
                        "Le fichier peut être fragmenté ou endommagé."
                    )

            elif ftype in ("txt", "xml", "html", "htm", "csv", "log", "py", "js",
                           "json", "md", "ini", "cfg", "bat", "ps1"):
                self.preview_ready.emit("text", raw[:8192], None)

            elif ftype in ("mp3", "flac", "ogg", "m4a", "wav", "aac", "wma"):
                meta = _parse_audio_meta(raw, ftype)
                self.preview_ready.emit("audio", raw[:256], meta)

            else:
                # Hex dump for unknown / binary
                self.preview_ready.emit("hex", raw[:512], None)

        except Exception as exc:
            log.debug("Preview load error: %s", exc)
            self.preview_error.emit(str(exc))


# ---------------------------------------------------------------------------
# Preview panel widget
# ---------------------------------------------------------------------------

class PreviewPanel(QFrame):
    """
    Right-side panel showing file preview.
    Slot: load_file(DeletedFile, read_fn)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PreviewPanel")
        self.setMinimumWidth(320)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._setup_ui()
        self._loader: Optional[PreviewLoader] = None

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        self._header = QLabel("Aperçu")
        self._header.setObjectName("PreviewHeader")
        self._header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._header.setFixedHeight(36)
        root.addWidget(self._header)

        # Stack: image / text / audio / hex / placeholder
        self._stack = QStackedWidget()
        root.addWidget(self._stack)

        # — Placeholder —
        self._placeholder = QLabel("Sélectionnez un fichier\npour voir l'aperçu")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setObjectName("PreviewPlaceholder")
        self._stack.addWidget(self._placeholder)   # index 0

        # — Image —
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setWidget(self._img_label)
        self._stack.addWidget(scroll)               # index 1

        # — Text —
        self._text_view = QPlainTextEdit()
        self._text_view.setReadOnly(True)
        self._text_view.setObjectName("PreviewText")
        font = QFont("Consolas", 9)
        self._text_view.setFont(font)
        self._stack.addWidget(self._text_view)      # index 2

        # — Audio meta —
        self._audio_widget = _AudioMetaWidget()
        self._stack.addWidget(self._audio_widget)   # index 3

        # — Hex dump —
        self._hex_view = QPlainTextEdit()
        self._hex_view.setReadOnly(True)
        self._hex_view.setObjectName("PreviewHex")
        self._hex_view.setFont(font)
        self._stack.addWidget(self._hex_view)       # index 4

        # — Loading —
        self._loading = QLabel("Chargement…")
        self._loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack.addWidget(self._loading)        # index 5

        self._stack.setCurrentIndex(0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_file(self, df: DeletedFile, read_fn) -> None:
        self._header.setText(f"Aperçu — {df.name}")
        self._stack.setCurrentIndex(5)  # loading

        # Cancel previous loader
        if self._loader and self._loader.isRunning():
            self._loader.terminate()

        self._loader = PreviewLoader(df, read_fn)
        self._loader.preview_ready.connect(self._on_preview_ready)
        self._loader.preview_error.connect(self._on_preview_error)
        self._loader.start()

    def clear(self) -> None:
        self._header.setText("Aperçu")
        self._stack.setCurrentIndex(0)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_preview_ready(self, kind: str, raw: bytes, extra) -> None:
        if kind == "image":
            px = QPixmap()
            if px.loadFromData(raw):
                scaled = px.scaled(
                    THUMB_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._img_label.setPixmap(scaled)
                self._stack.setCurrentIndex(1)
            else:
                self._on_preview_error("Format image non supporté")

        elif kind == "text":
            try:
                text = raw.decode("utf-8", errors="replace")
            except Exception:
                text = raw.decode("latin-1", errors="replace")
            self._text_view.setPlainText(text)
            self._stack.setCurrentIndex(2)

        elif kind == "audio":
            self._audio_widget.display(extra or {})
            self._stack.setCurrentIndex(3)

        elif kind == "hex":
            self._hex_view.setPlainText(_hex_dump(raw))
            self._stack.setCurrentIndex(4)

        else:
            self._stack.setCurrentIndex(0)

    def _on_preview_error(self, msg: str) -> None:
        self._placeholder.setText(f"Aperçu indisponible\n\n{msg}")
        self._stack.setCurrentIndex(0)


# ---------------------------------------------------------------------------
# Audio metadata widget
# ---------------------------------------------------------------------------

class _AudioMetaWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setObjectName("AudioMeta")
        layout.addWidget(QLabel("🎵 Métadonnées audio"))
        layout.addWidget(self._label)

    def display(self, meta: dict) -> None:
        lines = []
        for k, v in meta.items():
            lines.append(f"<b>{k}:</b> {v}")
        self._label.setText("<br>".join(lines) if lines else "Métadonnées non disponibles")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hex_dump(data: bytes, width: int = 16) -> str:
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        asc_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        lines.append(f"{i:08X}  {hex_part:<{width*3}}  {asc_part}")
    return "\n".join(lines)


def _parse_audio_meta(data: bytes, ftype: str) -> dict:
    """Extract basic audio metadata from header bytes without external libs."""
    meta: dict = {"Format": ftype.upper()}
    try:
        if ftype == "mp3" and data[:3] == b"ID3":
            # ID3v2 tag — extract title/artist if present
            tag_size = (
                (data[6] & 0x7F) << 21 |
                (data[7] & 0x7F) << 14 |
                (data[8] & 0x7F) << 7  |
                (data[9] & 0x7F)
            )
            meta["Tag"] = f"ID3v2 ({tag_size} octets)"
        elif ftype == "wav" and data[:4] == b"RIFF":
            file_size = struct.unpack_from("<I", data, 4)[0]
            meta["Taille RIFF"] = f"{file_size} octets"
        elif ftype == "flac" and data[:4] == b"fLaC":
            meta["Codec"] = "FLAC (Lossless)"
    except Exception:
        pass
    return meta
