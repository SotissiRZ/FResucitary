"""
FResucitary — Professional forensic report generator.
Produces a structured PDF (ReportLab) with scan stats, file table, hashes,
bad sectors and recovery summary. Suitable for technical/legal use.
No PyQt6.
"""
from __future__ import annotations
import datetime
import logging
import os
from typing import Optional

from src.core.models import DeletedFile, RecoveryStatus, ScanSession
from src.app.meta import APP_NAME, APP_VERSION

log = logging.getLogger(__name__)

# ReportLab is optional — degrade gracefully
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm, mm
    from reportlab.platypus import (
        HRFlowable, Image, PageBreak, Paragraph, SimpleDocTemplate,
        Spacer, Table, TableStyle,
    )
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

# Brand colours (Material You violet)
COLOR_PRIMARY   = "#6750A4"
COLOR_SECONDARY = "#625B71"
COLOR_SUCCESS   = "#4CAF50"
COLOR_WARNING   = "#FF9800"
COLOR_ERROR     = "#F44336"
COLOR_LIGHT_BG  = "#F3EDF7"


class ReportGenerator:
    """
    Generates a professional PDF report from a completed ScanSession.
    """

    def __init__(self, session: ScanSession, bad_sectors: Optional[list[dict]] = None):
        self.session     = session
        self.bad_sectors = bad_sectors or []

    def export_pdf(self, output_path: str) -> bool:
        if not HAS_REPORTLAB:
            log.error("reportlab not installed — PDF export unavailable")
            return False
        try:
            self._build(output_path)
            log.info("PDF report exported: %s", output_path)
            return True
        except Exception as exc:
            log.exception("PDF export failed: %s", exc)
            return False

    def export_csv(self, output_path: str) -> bool:
        """Export enriched CSV (all metadata + score + hash)."""
        import csv
        try:
            with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Nom", "Chemin", "Taille (octets)", "Type (magic)",
                    "Type (extension)", "Catégorie", "Score (%)",
                    "Niveau", "Fragmenté", "Corbeille",
                    "Supprimé le", "Inode", "Hash SHA-256",
                    "Statut récupération", "Chemin récupéré", "Carved",
                ])
                for df in self.session.results:
                    writer.writerow([
                        df.name,
                        df.path,
                        df.size,
                        df.file_type_by_magic,
                        df.file_type_by_ext,
                        df.category.name,
                        df.recovery_score,
                        df.score.label,
                        "Oui" if df.fragmented else "Non",
                        "Oui" if df.in_recycle_bin else "Non",
                        _fmt_ts(df.deleted_at),
                        df.inode,
                        df.sha256,
                        df.status.name,
                        df.output_path,
                        "Oui" if df.carved else "Non",
                    ])
            log.info("CSV exported: %s", output_path)
            return True
        except Exception as exc:
            log.exception("CSV export failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # PDF builder
    # ------------------------------------------------------------------

    def _build(self, path: str) -> None:
        doc = SimpleDocTemplate(
            path,
            pagesize=A4,
            leftMargin=2*cm, rightMargin=2*cm,
            topMargin=2*cm, bottomMargin=2*cm,
            title="Rapport FResucitary",
            author="FResucitary Pro",
        )
        styles = self._styles()
        story  = []

        # ── Cover ────────────────────────────────────────────────────
        story.append(Spacer(1, 2*cm))
        story.append(Paragraph("FResucitary", styles["cover_title"]))
        story.append(Paragraph("Rapport d'analyse forensique", styles["cover_sub"]))
        story.append(Spacer(1, 0.5*cm))
        story.append(HRFlowable(width="100%", thickness=2, color=COLOR_PRIMARY))
        story.append(Spacer(1, 0.5*cm))

        # Meta table
        s = self.session
        meta = [
            ["Source analysée",  s.source_path],
            ["Mode de scan",     s.scan_mode.name],
            ["Date de début",    _fmt_ts(s.started_at)],
            ["Durée",            s.duration_str()],
            ["Généré le",        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
            ["Version",          f"{APP_NAME} {APP_VERSION}"],
        ]
        story.append(self._two_col_table(meta, styles))
        story.append(Spacer(1, 1*cm))

        # ── Statistics ───────────────────────────────────────────────
        story.append(Paragraph("Statistiques du scan", styles["h2"]))
        story.append(self._stats_table(styles))
        story.append(Spacer(1, 0.5*cm))

        # ── Score distribution ───────────────────────────────────────
        story.append(Paragraph("Distribution des scores de récupérabilité", styles["h2"]))
        story.append(self._score_dist_table(styles))
        story.append(Spacer(1, 0.5*cm))

        # ── File table ───────────────────────────────────────────────
        story.append(PageBreak())
        story.append(Paragraph("Fichiers détectés (extrait 500 premiers)", styles["h2"]))
        story.append(Spacer(1, 0.3*cm))
        story.append(self._file_table(styles))
        story.append(Spacer(1, 0.5*cm))

        # ── Bad sectors ──────────────────────────────────────────────
        if self.bad_sectors:
            story.append(PageBreak())
            story.append(Paragraph("Secteurs défaillants", styles["h2"]))
            story.append(self._bad_sectors_table(styles))
            story.append(Spacer(1, 0.5*cm))

        # ── Recovery summary ─────────────────────────────────────────
        recovered = [
            df for df in s.results
            if df.status in (RecoveryStatus.RECOVERED, RecoveryStatus.PARTIAL, RecoveryStatus.CORRUPT)
        ]
        if recovered:
            story.append(PageBreak())
            story.append(Paragraph("Sorties de récupération — hashes SHA-256", styles["h2"]))
            story.append(self._hash_table(recovered[:500], styles))

        # ── Footer disclaimer ────────────────────────────────────────
        story.append(Spacer(1, 1*cm))
        story.append(HRFlowable(width="100%", thickness=1, color=COLOR_SECONDARY))
        story.append(Spacer(1, 0.3*cm))
        story.append(Paragraph(
            "Ce rapport a été généré automatiquement par FResucitary Pro. "
            "Les résultats sont fournis à titre indicatif. "
            "L'intégrité des fichiers récupérés doit être vérifiée par les hashes SHA-256 fournis.",
            styles["footer"],
        ))

        doc.build(story, onFirstPage=self._header_footer, onLaterPages=self._header_footer)

    # ------------------------------------------------------------------
    # Table builders
    # ------------------------------------------------------------------

    def _stats_table(self, styles):
        results = self.session.results
        cats = {}
        for df in results:
            cats[df.category.name] = cats.get(df.category.name, 0) + 1

        carved  = sum(1 for df in results if df.carved)
        recycle = sum(1 for df in results if df.in_recycle_bin)
        frag    = sum(1 for df in results if df.fragmented)

        rows = [
            ["Métrique", "Valeur"],
            ["Total fichiers détectés",  str(len(results))],
            ["Dont fichiers carvés",      str(carved)],
            ["Dont corbeille",            str(recycle)],
            ["Dont fragmentés",           str(frag)],
        ]
        for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
            rows.append([f"  Catégorie : {cat}", str(count)])

        return self._styled_table(rows, col_widths=[10*cm, 5*cm])

    def _score_dist_table(self, styles):
        results = self.session.results
        high   = sum(1 for df in results if df.recovery_score >= 75)
        medium = sum(1 for df in results if 50 <= df.recovery_score < 75)
        partial = sum(1 for df in results if 25 <= df.recovery_score < 50)
        low    = sum(1 for df in results if df.recovery_score < 25)
        total  = max(len(results), 1)

        rows = [
            ["Niveau", "Nombre", "Pourcentage"],
            ["Excellent (≥75%)",  str(high),    f"{high/total*100:.1f}%"],
            ["Bon (50-74%)",       str(medium),  f"{medium/total*100:.1f}%"],
            ["Partiel (25-49%)",   str(partial), f"{partial/total*100:.1f}%"],
            ["Faible (<25%)",      str(low),     f"{low/total*100:.1f}%"],
        ]
        return self._styled_table(rows, col_widths=[7*cm, 4*cm, 4*cm])

    def _file_table(self, styles):
        rows = [["Nom", "Type", "Taille", "Score", "Statut"]]
        for df in self.session.results[:500]:
            rows.append([
                df.name[:50],
                df.display_type[:12],
                _human_size(df.size),
                f"{df.recovery_score}%",
                df.status.name,
            ])
        return self._styled_table(rows, col_widths=[6*cm, 2.5*cm, 2*cm, 2*cm, 2.5*cm])

    def _bad_sectors_table(self, styles):
        rows = [["Offset (hex)", "Longueur", "Erreur"]]
        for entry in self.bad_sectors[:200]:
            rows.append([
                f"0x{entry['offset']:08X}",
                _human_size(entry['length']),
                entry['error'][:60],
            ])
        return self._styled_table(rows, col_widths=[4*cm, 3*cm, 8*cm])

    def _hash_table(self, files: list[DeletedFile], styles):
        rows = [["Nom", "SHA-256", "Taille"]]
        for df in files:
            rows.append([
                df.name[:40],
                df.sha256[:32] + "…" if len(df.sha256) > 32 else df.sha256,
                _human_size(df.size),
            ])
        return self._styled_table(rows, col_widths=[5*cm, 8.5*cm, 1.5*cm])

    def _two_col_table(self, data: list[list], styles):
        from reportlab.platypus import Table as RTable, TableStyle as RTS
        t = RTable(data, colWidths=[5*cm, 12*cm])
        t.setStyle(RTS([
            ("FONTNAME",    (0,0), (-1,-1), "Helvetica"),
            ("FONTSIZE",    (0,0), (-1,-1), 9),
            ("FONTNAME",    (0,0), (0,-1),  "Helvetica-Bold"),
            ("TEXTCOLOR",   (0,0), (0,-1),  colors.HexColor(COLOR_PRIMARY)),
            ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.white, colors.HexColor(COLOR_LIGHT_BG)]),
            ("GRID",        (0,0), (-1,-1), 0.3, colors.lightgrey),
            ("TOPPADDING",  (0,0), (-1,-1), 4),
            ("BOTTOMPADDING",(0,0),(-1,-1), 4),
        ]))
        return t

    def _styled_table(self, rows, col_widths=None):
        from reportlab.platypus import Table as RTable, TableStyle as RTS
        t = RTable(rows, colWidths=col_widths, repeatRows=1)
        t.setStyle(RTS([
            # Header
            ("BACKGROUND",  (0,0), (-1,0), colors.HexColor(COLOR_PRIMARY)),
            ("TEXTCOLOR",   (0,0), (-1,0), colors.white),
            ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",    (0,0), (-1,0), 9),
            # Body
            ("FONTNAME",    (0,1), (-1,-1), "Helvetica"),
            ("FONTSIZE",    (0,1), (-1,-1), 8),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor(COLOR_LIGHT_BG)]),
            ("GRID",        (0,0), (-1,-1), 0.3, colors.lightgrey),
            ("TOPPADDING",  (0,0), (-1,-1), 3),
            ("BOTTOMPADDING",(0,0),(-1,-1), 3),
            ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ]))
        return t

    # ------------------------------------------------------------------
    # Page template
    # ------------------------------------------------------------------

    @staticmethod
    def _header_footer(canvas, doc):
        canvas.saveState()
        W, H = A4
        # Header bar
        canvas.setFillColor(colors.HexColor(COLOR_PRIMARY))
        canvas.rect(0, H - 1.2*cm, W, 1.2*cm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(2*cm, H - 0.8*cm, "FResucitary Pro — Rapport confidentiel")
        canvas.drawRightString(W - 2*cm, H - 0.8*cm, f"Page {doc.page}")
        canvas.restoreState()

    @staticmethod
    def _styles():
        base = getSampleStyleSheet()
        return {
            "cover_title": ParagraphStyle(
                "ct", parent=base["Title"],
                fontSize=28, textColor=colors.HexColor(COLOR_PRIMARY),
                spaceAfter=6, alignment=TA_CENTER,
            ),
            "cover_sub": ParagraphStyle(
                "cs", parent=base["Normal"],
                fontSize=14, textColor=colors.HexColor(COLOR_SECONDARY),
                spaceAfter=12, alignment=TA_CENTER,
            ),
            "h2": ParagraphStyle(
                "h2", parent=base["Heading2"],
                fontSize=12, textColor=colors.HexColor(COLOR_PRIMARY),
                spaceBefore=10, spaceAfter=6,
            ),
            "footer": ParagraphStyle(
                "footer", parent=base["Normal"],
                fontSize=7, textColor=colors.grey,
                alignment=TA_CENTER,
            ),
        }


# ------------------------------------------------------------------
# Utility
# ------------------------------------------------------------------

def _fmt_ts(ts: float) -> str:
    if not ts:
        return "—"
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def _human_size(size: int) -> str:
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} Po"
