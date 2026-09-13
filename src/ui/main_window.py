"""
FResucitary — Main application window.
Material You layout: sidebar / action bar / splitter (table + preview) / log / status.
Wires ScanWorker, RecoveryWorker, PreviewPanel, ReportGenerator.
"""
from __future__ import annotations
import logging
import os
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QCloseEvent, QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QSplitter,
    QStatusBar, QVBoxLayout, QWidget,
)

from src.core.models import (
    DeletedFile, FileCategory, RecoveryStatus,
    RecoveryTask, ScanMode, ScanSession,
)
from src.core.scan_worker    import ScanWorker
from src.core.recovery_worker import RecoveryWorker
from src.core.session_mgr    import SessionManager
from src.security.safe       import ReadOnlyGuard, is_admin, request_elevation
from src.reports.pdf         import ReportGenerator
from src.ui.results_table    import ResultsTableWidget
from src.ui.preview          import PreviewPanel
from src.ui.style            import apply_dark, apply_light
from src.app.meta            import APP_NAME, APP_VERSION
from src.app.diagnostics     import runtime_report, log_dir

log = logging.getLogger(__name__)



def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if value < 1024:
            return f"{value:.0f} {unit}"
        value /= 1024
    return f"{value:.1f} Po"


def _restart_as_admin() -> bool:
    """Start an elevated copy and close this process cleanly through Qt."""
    if not request_elevation():
        return False
    app = QApplication.instance()
    if app is not None:
        QTimer.singleShot(0, app.quit)
    return True


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._scan_worker:     Optional[ScanWorker]     = None
        self._recover_worker:  Optional[RecoveryWorker] = None
        self._imager_thread:    Optional[_ImagerThread]  = None
        self._session:         Optional[ScanSession]    = None
        self._session_mgr                               = SessionManager()
        self._dark_mode                                 = True
        self._source_path                               = ""
        self._stats: dict                               = {}

        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(1440, 900)
        self.setMinimumSize(900, 600)

        self._build_ui()
        self._build_menu()
        self._check_admin()
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_dark(app)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Sidebar ───────────────────────────────────────────────────
        sidebar = self._build_sidebar()
        root.addWidget(sidebar)

        # ── Main area (vertical stack) ────────────────────────────────
        main_area = QVBoxLayout()
        main_area.setContentsMargins(0, 0, 0, 0)
        main_area.setSpacing(0)
        root.addLayout(main_area)

        # Action bar (source + scan controls)
        action_bar = self._build_action_bar()
        main_area.addWidget(action_bar)

        # Create the results table exactly once. Filters, selection signals and
        # the visible splitter must all target the same model/view instance.
        self._table = ResultsTableWidget()
        self._table.file_selected.connect(self._on_file_selected)
        self._table.selection_count.connect(self._on_selection_count)

        # Filter bar
        filter_bar = self._build_filter_bar()
        main_area.addWidget(filter_bar)

        # Splitter: table | preview
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._table)

        # Right panel: detail info + preview stacked
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0,0,0,0)
        right_layout.setSpacing(0)

        # Detail info card
        self._detail_card = QFrame()
        self._detail_card.setObjectName("DetailCard")
        self._detail_card.setFixedHeight(130)
        detail_layout = QVBoxLayout(self._detail_card)
        detail_layout.setContentsMargins(12, 8, 12, 8)
        detail_layout.setSpacing(3)
        self._detail_name  = QLabel("— Sélectionnez un fichier —")
        self._detail_name.setStyleSheet("font-weight:bold;font-size:13px;")
        self._detail_score = QLabel("")
        self._detail_path  = QLabel("")
        self._detail_path.setStyleSheet("color:#90CAF9;font-size:11px;")
        self._detail_meta  = QLabel("")
        self._detail_meta.setStyleSheet("color:#90CAF9;font-size:11px;")
        self._detail_score.setStyleSheet("font-size:12px;")
        for w in (self._detail_name, self._detail_score, self._detail_path, self._detail_meta):
            w.setWordWrap(True)
            detail_layout.addWidget(w)
        right_layout.addWidget(self._detail_card)

        self._preview = PreviewPanel()
        right_layout.addWidget(self._preview, stretch=1)

        splitter.addWidget(right_panel)
        splitter.setSizes([960, 360])
        main_area.addWidget(splitter, stretch=1)

        # Log panel (collapsible)
        self._log_panel = QPlainTextEdit()
        self._log_panel.setObjectName("LogPanel")
        self._log_panel.setReadOnly(True)
        self._log_panel.setMaximumHeight(120)
        self._log_panel.setPlaceholderText("Journal des opérations…")
        main_area.addWidget(self._log_panel)

        # Status bar
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._lbl_count   = QLabel("0 fichier(s)")
        self._lbl_speed   = QLabel("")
        self._lbl_eta     = QLabel("")
        self._lbl_admin   = QLabel("")
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedWidth(200)
        self._progress_bar.setVisible(False)
        self._status_bar.addWidget(self._lbl_count)
        self._status_bar.addPermanentWidget(self._lbl_speed)
        self._status_bar.addPermanentWidget(self._lbl_eta)
        self._status_bar.addPermanentWidget(self._progress_bar)
        self._status_bar.addPermanentWidget(self._lbl_admin)

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # App title
        title = QLabel(f" 🗂 {APP_NAME}")
        title.setObjectName("SidebarHeader")
        layout.addWidget(title)

        # Source info
        self._lbl_source_info = QLabel("  Aucune source")
        self._lbl_source_info.setStyleSheet(
            "color:#90CAF9;font-size:10px;padding:4px 12px;"
            "background:#0A1929;border-bottom:1px solid #1A3A6B;"
        )
        self._lbl_source_info.setWordWrap(True)
        layout.addWidget(self._lbl_source_info)

        # Stats dashboard
        stats_area = self._build_stats_dashboard()
        layout.addWidget(stats_area)

        layout.addStretch()

        # Theme toggle
        btn_theme = QPushButton("🌙  Mode sombre")
        btn_theme.setObjectName("NavButton")
        btn_theme.setCheckable(True)
        btn_theme.setChecked(True)
        btn_theme.clicked.connect(self._toggle_theme)
        layout.addWidget(btn_theme)

        # About
        btn_about = QPushButton("ℹ  À propos")
        btn_about.setObjectName("NavButton")
        btn_about.clicked.connect(self._show_about)
        layout.addWidget(btn_about)

        return sidebar

    def _build_stats_dashboard(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(8)

        label = QLabel("Statistiques")
        label.setStyleSheet("color: #5C8BC7; font-size: 11px; font-weight: bold;")
        layout.addWidget(label)

        self._stat_widgets: dict[str, tuple[QLabel, QLabel]] = {}
        for key, title in [
            ("total_found", "Fichiers trouvés"),
            ("high_score",  "Score excellent"),
            ("carved",      "Carvés"),
            ("images",      "Images"),
            ("videos",      "Vidéos"),
            ("documents",   "Documents"),
        ]:
            row = QHBoxLayout()
            val = QLabel("0")
            val.setObjectName("StatValue")
            val.setStyleSheet("font-size: 20px; font-weight: bold; color: #90CAF9;")
            lbl = QLabel(title)
            lbl.setObjectName("StatLabel")
            lbl.setStyleSheet("font-size: 10px; color: #5C8BC7;")
            col = QVBoxLayout()
            col.addWidget(val)
            col.addWidget(lbl)
            row.addLayout(col)
            self._stat_widgets[key] = (val, lbl)
            layout.addLayout(row)

        return container

    def _build_action_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("ActionBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(8)

        # Source selection
        self._source_edit = QLineEdit()
        self._source_edit.setPlaceholderText("Sélectionner un disque ou une image…")
        self._source_edit.setReadOnly(True)
        self._source_edit.setMinimumWidth(280)

        btn_source = QPushButton("📂 Source")
        btn_source.setProperty("class", "tonal")
        btn_source.clicked.connect(self._select_source)

        # Scan mode
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["⚡ Rapide (~2 min)", "🔍 Profond (~15 min)", "🔬 Forensique (~45 min)"])
        self._mode_combo.setToolTip(
            "Rapide : entrées MFT supprimées seulement\n"
            "Profond : MFT + répertoires + carving\n"
            "Forensique : Profond + journal MFT brut"
        )

        # Scan button
        self._btn_scan = QPushButton("▶  Lancer le scan")
        self._btn_scan.setObjectName("PrimaryButton")
        self._btn_scan.clicked.connect(self._start_scan)
        self._btn_scan.setEnabled(False)

        # Pause / Cancel
        self._btn_pause  = QPushButton("⏸")
        self._btn_pause.setToolTip("Pause")
        self._btn_pause.setVisible(False)
        self._btn_pause.clicked.connect(self._toggle_pause)

        self._btn_cancel = QPushButton("⏹")
        self._btn_cancel.setToolTip("Annuler")
        self._btn_cancel.setVisible(False)
        self._btn_cancel.clicked.connect(self._cancel_scan)

        # Recover
        self._btn_recover = QPushButton("💾 Récupérer")
        self._btn_recover.setObjectName("TonalButton")
        self._btn_recover.setEnabled(False)
        self._btn_recover.clicked.connect(self._start_recovery)

        # Report
        self._btn_report = QPushButton("📄 Rapport")
        self._btn_report.setEnabled(False)
        self._btn_report.clicked.connect(self._export_report)

        # Selection helpers
        self._btn_select_all = QPushButton("☑ Tout")
        self._btn_select_all.setToolTip("Sélectionner tout")
        self._btn_select_all.clicked.connect(lambda: self._table.view.selectAll())

        self._btn_deselect = QPushButton("☐ Aucun")
        self._btn_deselect.setToolTip("Désélectionner")
        self._btn_deselect.clicked.connect(lambda: self._table.view.clearSelection())

        layout.addWidget(self._source_edit, stretch=1)
        layout.addWidget(btn_source)
        layout.addWidget(self._mode_combo)
        layout.addWidget(self._btn_scan)
        layout.addWidget(self._btn_pause)
        layout.addWidget(self._btn_cancel)
        layout.addSpacing(8)
        layout.addWidget(self._btn_select_all)
        layout.addWidget(self._btn_deselect)
        layout.addSpacing(8)
        layout.addWidget(self._btn_recover)
        layout.addWidget(self._btn_report)
        return bar

    def _build_filter_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("FilterBar")
        bar.setFixedHeight(44)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(8)

        # Text search
        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍 Rechercher par nom ou chemin…")
        self._search.setMaximumWidth(320)
        self._search.textChanged.connect(self._table.proxy.set_text)

        # Category filter
        self._cat_combo = QComboBox()
        self._cat_combo.addItem("Toutes catégories", None)
        for cat in FileCategory:
            self._cat_combo.addItem(cat.name.capitalize(), cat)
        self._cat_combo.currentIndexChanged.connect(
            lambda i: self._table.proxy.set_category(self._cat_combo.currentData())
        )

        # Score slider
        score_label = QLabel("Score min :")
        self._score_slider = QSlider(Qt.Orientation.Horizontal)
        self._score_slider.setRange(0, 100)
        self._score_slider.setValue(0)
        self._score_slider.setMaximumWidth(140)
        self._score_lbl = QLabel("0%")
        self._score_lbl.setMinimumWidth(32)
        self._score_slider.valueChanged.connect(lambda v: (
            self._table.proxy.set_score_min(v),
            self._score_lbl.setText(f"{v}%"),
        ))

        # Carved only
        self._carved_check = QCheckBox("Carvés uniquement")
        self._carved_check.toggled.connect(self._table.proxy.set_carved_only)

        # Count label
        self._visible_count_lbl = QLabel()
        self._table.model.count_changed.connect(self._update_count_label)

        layout.addWidget(self._search)
        layout.addWidget(self._cat_combo)
        layout.addWidget(score_label)
        layout.addWidget(self._score_slider)
        layout.addWidget(self._score_lbl)
        layout.addWidget(self._carved_check)
        layout.addStretch()
        layout.addWidget(self._visible_count_lbl)
        return bar

    def _build_menu(self) -> None:
        mb = self.menuBar()
        if mb is None:
            return

        # Fichier
        file_menu = mb.addMenu("Fichier")
        assert file_menu is not None
        act_open  = QAction("Ouvrir session…", self)
        act_open.triggered.connect(self._load_session)
        act_csv   = QAction("Exporter CSV…", self)
        act_csv.triggered.connect(self._export_csv)
        act_quit  = QAction("Quitter", self)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_open)
        file_menu.addAction(act_csv)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

        # Disque
        disk_menu   = mb.addMenu("Disque")
        assert disk_menu is not None
        act_image   = QAction("Créer image disque (.dd)…", self)
        act_image.triggered.connect(self._create_disk_image)
        disk_menu.addAction(act_image)

        # Aide
        help_menu = mb.addMenu("Aide")
        assert help_menu is not None
        act_diag = QAction("Diagnostic système…", self)
        act_diag.triggered.connect(self._show_diagnostics)
        help_menu.addAction(act_diag)
        help_menu.addSeparator()
        act_about = QAction(f"À propos de {APP_NAME}", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    # ------------------------------------------------------------------
    # Source selection
    # ------------------------------------------------------------------

    def _select_source(self) -> None:
        dlg = _SourceDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._source_path = dlg.selected_path
            self._source_edit.setText(self._source_path)
            self._btn_scan.setEnabled(bool(self._source_path))
            if self._source_path:
                name = self._source_path.split("\\")[-1] or self._source_path
                self._lbl_source_info.setText(f"  💽 {name}")

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    def _start_scan(self) -> None:
        if not self._source_path:
            return

        # Physical devices require elevation, while image files intentionally
        # remain usable in standard-user mode.
        if self._source_path.startswith("\\\\.\\") and not is_admin():
            reply = QMessageBox.warning(
                self, "Accès administrateur requis",
                "L'analyse directe d'un disque physique nécessite les droits "
                "administrateur Windows.\n\n"
                "Les images .dd/.img peuvent être analysées sans élévation.\n\n"
                "Relancer FResucitary en administrateur ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Yes:
                _restart_as_admin()
            return

        # Safety check
        guard = ReadOnlyGuard(self._source_path)
        try:
            guard.assert_safe()
        except PermissionError as exc:
            reply = QMessageBox.warning(
                self, "⚠️ Protection source", str(exc),
                QMessageBox.StandardButton.Ignore | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Ignore:
                return

        mode_map = {0: ScanMode.QUICK, 1: ScanMode.DEEP, 2: ScanMode.FORENSIC}
        mode = mode_map[self._mode_combo.currentIndex()]

        self._table.clear()
        self._preview.clear()
        self._log("=" * 60)
        self._log(f"Démarrage scan {mode.name} — {self._source_path}")

        self._btn_scan.setEnabled(False)
        self._btn_pause.setVisible(True)
        self._btn_cancel.setVisible(True)
        self._btn_recover.setEnabled(False)
        self._btn_report.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, 0)  # indeterminate

        self._scan_worker = ScanWorker(
            self._source_path, mode, self._session_mgr
        )
        self._scan_worker.file_found.connect(self._on_file_found)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.stats_update.connect(self._on_stats_update)
        self._scan_worker.log_message.connect(self._log)
        self._scan_worker.scan_finished.connect(self._on_scan_finished)
        self._scan_worker.scan_error.connect(self._on_scan_error)
        self._scan_worker.start()

    def _toggle_pause(self) -> None:
        # ScanWorker doesn't pause (disk streaming), but RecoveryWorker does
        if self._recover_worker:
            if self._recover_worker._paused:
                self._recover_worker.resume()
                self._btn_pause.setText("⏸")
            else:
                self._recover_worker.pause()
                self._btn_pause.setText("▶")

    def _cancel_scan(self) -> None:
        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_worker.cancel()
        if self._recover_worker and self._recover_worker.isRunning():
            self._recover_worker.cancel()

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def _start_recovery(self) -> None:
        files = self._table.selected_files()
        if not files:
            files = self._table.all_files()

        if not files:
            QMessageBox.information(self, "Récupération", "Aucun fichier à récupérer.")
            return

        # Destination
        out_dir = QFileDialog.getExistingDirectory(
            self, "Choisir le dossier de destination"
        )
        if not out_dir:
            return

        # Output safety
        warning = ReadOnlyGuard.safe_output_warning(self._source_path, out_dir)
        if warning:
            reply = QMessageBox.warning(
                self, "⚠️ Attention", warning,
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Ok:
                return

        tasks = [RecoveryTask(file=f, output_dir=out_dir) for f in files]

        self._btn_recover.setEnabled(False)
        self._btn_pause.setVisible(True)
        self._btn_cancel.setVisible(True)
        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, len(tasks))

        self._recover_worker = RecoveryWorker(tasks, self._source_path, out_dir)
        self._recover_worker.file_recovered.connect(self._on_file_recovered)
        self._recover_worker.file_failed.connect(self._on_file_failed)
        self._recover_worker.progress.connect(self._on_recovery_progress)
        self._recover_worker.log_message.connect(self._log)
        self._recover_worker.recovery_finished.connect(self._on_recovery_finished)
        self._recover_worker.start()

    # ------------------------------------------------------------------
    # Slots — scan
    # ------------------------------------------------------------------

    @pyqtSlot(object)
    def _on_file_found(self, df: DeletedFile) -> None:
        self._table.add_file(df)

    @pyqtSlot(int, int, int)
    def _on_scan_progress(self, current: int, total: int, eta: int) -> None:
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
        else:
            self._progress_bar.setRange(0, 0)
        if eta > 0:
            m, s = divmod(eta, 60)
            self._lbl_eta.setText(f"ETA {m}m{s:02d}s")
        elif current > 0:
            self._lbl_eta.setText("")
        self._status_bar.showMessage(f"Scan… {current} fichiers trouvés")

    @pyqtSlot(dict)
    def _on_stats_update(self, stats: dict) -> None:
        self._stats = stats
        for key, (val_lbl, _) in self._stat_widgets.items():
            val_lbl.setText(str(stats.get(key, 0)))
        t = stats.get("throughput", 0)
        self._lbl_speed.setText(f"{t} fichiers/s")

    @pyqtSlot(object)
    def _on_scan_finished(self, session: ScanSession) -> None:
        self._session = session
        n = len(session.results)
        self._log(f"✅ Scan terminé : {n} fichiers trouvés — durée {session.duration_str()}")
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(1)
        self._btn_scan.setEnabled(True)
        self._btn_pause.setVisible(False)
        self._btn_cancel.setVisible(False)
        self._btn_recover.setEnabled(n > 0)
        self._btn_report.setEnabled(n > 0)
        self._status_bar.showMessage(f"Scan terminé — {n} fichiers détectés")

    @pyqtSlot(str)
    def _on_scan_error(self, msg: str) -> None:
        QMessageBox.critical(self, "Erreur de scan", msg)
        self._reset_scan_ui()

    # ------------------------------------------------------------------
    # Slots — recovery
    # ------------------------------------------------------------------

    @pyqtSlot(object)
    def _on_file_recovered(self, df: DeletedFile) -> None:
        self._table.refresh_file(df)

    @pyqtSlot(object, str)
    def _on_file_failed(self, df: DeletedFile, error: str) -> None:
        self._table.refresh_file(df)

    @pyqtSlot(int, int, float)
    def _on_recovery_progress(self, done: int, total: int, speed: float) -> None:
        self._progress_bar.setValue(done)
        self._status_bar.showMessage(
            f"Récupération {done}/{total} — {speed:.1f} MB/s"
        )

    @pyqtSlot(int, int, int, int)
    def _on_recovery_finished(
        self, intact: int, partial: int, corrupt: int, failed: int
    ) -> None:
        self._log(
            f"✅ Récupération terminée : {intact} intact(s), {partial} partiel(s), "
            f"{corrupt} corrompu(s), {failed} échec(s)"
        )
        self._reset_scan_ui()
        self._btn_recover.setEnabled(True)
        msg = (
            "Récupération terminée\n\n"
            f"✅ Intacts : {intact}\n"
            f"⚠️ Partiels : {partial}\n"
            f"⛔ Corrompus : {corrupt}\n"
            f"❌ Échecs : {failed}"
        )
        if partial or corrupt:
            msg += (
                "\n\nLes fichiers partiels/corrompus sont conservés pour analyse, "
                "mais ne sont plus comptés comme des récupérations réussies."
            )
        QMessageBox.information(self, "Récupération terminée", msg)

    # ------------------------------------------------------------------
    # Slots — table
    # ------------------------------------------------------------------

    def _on_file_selected(self, df: DeletedFile) -> None:
        import datetime
        # Fill detail card
        self._detail_name.setText(f"📄 {df.name}")
        score = df.recovery_score
        color = df.score.color_hex
        self._detail_score.setText(
            f'<span style="color:{color};font-weight:bold">{score}% — {df.score.label}</span>'
            f'  &nbsp; Type: <b>{df.display_type.upper() or "?"}</b>'
            f'  &nbsp; Taille: <b>{_human_size(df.size)}</b>'
            f'  &nbsp; {"🗑 Corbeille" if df.in_recycle_bin else ""}'
            f'  {"⛏ Carvé" if df.carved else ""}'
        )
        self._detail_score.setTextFormat(Qt.TextFormat.RichText)
        self._detail_path.setText(f"📁 {df.path}")
        ts = datetime.datetime.fromtimestamp(df.deleted_at).strftime("%Y-%m-%d %H:%M") if df.deleted_at else "—"
        recovery_info = ""
        if df.recovery_note:
            recovery_info = f"  |  Récupération: {df.recovery_note}"
        self._detail_meta.setText(
            f"Inode: {df.inode}  |  Supprimé: {ts}  |  "
            f"Fragments: {df.data_runs_count or '?'}  |  "
            f"SHA-256: {df.sha256[:16]+'…' if df.sha256 else '(non calculé)'}"
            f"{recovery_info}"
        )

        def read_fn(inode: int, size: int) -> bytes:
            from src.core.fs.image import DiskSource
            from src.core.fs.ntfs import NTFSAdapter
            from src.core.fs.fat32 import FAT32Adapter

            source = None
            adapter = None
            try:
                source = DiskSource(self._source_path)

                if df.carved:
                    if not df.clusters:
                        return df.header_bytes or b""
                    abs_offset = int(df.source_offset or 0) + int(df.clusters[0])
                    return bytes(source.img.read(abs_offset, size))

                fs_type = df.source_fs
                offset = int(df.source_offset or 0)
                if not fs_type:
                    parts = source.detect_partitions()
                    part = next((p for p in parts if p.offset_bytes == offset), None)
                    if part is None:
                        part = next((p for p in parts if p.fs_type == "NTFS"), parts[0] if parts else None)
                    if part is not None:
                        fs_type = part.fs_type
                        offset = part.offset_bytes

                if fs_type == "NTFS":
                    adapter = NTFSAdapter(source.img, offset)
                elif fs_type in ("FAT12", "FAT16", "FAT32", "exFAT"):
                    adapter = FAT32Adapter(source.img, offset)
                else:
                    return df.header_bytes or b""

                return b"".join(adapter.read_file_data(
                    inode,
                    size,
                    chunk_size=max(1, size),
                    attr_type=df.data_attr_type,
                    attr_id=df.data_attr_id,
                    meta_seq=df.meta_seq,
                    data_runs=df.data_runs,
                    data_attr_flags=df.data_attr_flags,
                ))
            except Exception:
                return df.header_bytes or b""
            finally:
                if adapter is not None:
                    try:
                        adapter.close()
                    except Exception:
                        pass
                if source is not None:
                    source.close()
        self._preview.load_file(df, read_fn)
        # Detail in status bar
        self._status_bar.showMessage(
            f"{df.name}  |  {df.display_type.upper()}  |  "
            f"Score {df.recovery_score}%  |  {df.score.label}"
        )

    def _on_selection_count(self, count: int) -> None:
        self._btn_recover.setEnabled(
            count > 0 or self._table.model.rowCount() > 0
        )

    # ------------------------------------------------------------------
    # Reports / CSV
    # ------------------------------------------------------------------

    def _export_report(self) -> None:
        if not self._session:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le rapport", "rapport_fresucitary.pdf", "PDF (*.pdf)"
        )
        if not path:
            return
        gen = ReportGenerator(self._session)
        ok  = gen.export_pdf(path)
        if ok:
            QMessageBox.information(self, "Rapport", f"Rapport PDF exporté :\n{path}")
        else:
            QMessageBox.warning(self, "Erreur", "Impossible de générer le rapport PDF.\n"
                                                "Vérifiez que reportlab est installé.")

    def _export_csv(self) -> None:
        if not self._session:
            QMessageBox.information(self, "CSV", "Aucune session active.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Exporter CSV", "fresucitary_resultats.csv", "CSV (*.csv)"
        )
        if not path:
            return
        gen = ReportGenerator(self._session)
        gen.export_csv(path)
        QMessageBox.information(self, "CSV", f"Exporté :\n{path}")

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def _load_session(self) -> None:
        sessions = self._session_mgr.list_sessions()
        if not sessions:
            QMessageBox.information(self, "Sessions", "Aucune session sauvegardée.")
            return
        # For simplicity, load the most recent session
        s = self._session_mgr.load(sessions[0]["session_id"])
        if s:
            self._session = s
            self._source_path = s.source_path
            self._source_edit.setText(s.source_path)
            self._table.load_session(s.results)
            self._log(f"Session chargée : {s.session_id} — {len(s.results)} fichiers")
            self._btn_recover.setEnabled(bool(s.results))
            self._btn_report.setEnabled(bool(s.results))

    # ------------------------------------------------------------------
    # Disk imaging
    # ------------------------------------------------------------------

    def _create_disk_image(self) -> None:
        if not self._source_path:
            QMessageBox.information(self, "Imagerie", "Sélectionnez d'abord une source.")
            return
        out, _ = QFileDialog.getSaveFileName(
            self, "Destination de l'image", "image.dd", "Image disque (*.dd *.img)"
        )
        if not out:
            return

        if self._source_path.startswith("\\\\.\\") and not is_admin():
            reply = QMessageBox.warning(
                self, "Accès administrateur requis",
                "La création d'une image depuis un disque physique nécessite "
                "les droits administrateur Windows.\n\nRelancer FResucitary en administrateur ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Yes:
                _restart_as_admin()
            return

        warning = ReadOnlyGuard.safe_output_warning(self._source_path, out)
        if warning:
            QMessageBox.critical(self, "Destination interdite", warning)
            return

        if self._imager_thread and self._imager_thread.isRunning():
            QMessageBox.information(self, "Imagerie", "Une imagerie est déjà en cours.")
            return

        self._log(f"Démarrage imagerie disque → {out}")
        self._imager_thread = _ImagerThread(self._source_path, out, self)
        self._imager_thread.result.connect(self._on_imaging_finished)
        self._imager_thread.start()
        self._status_bar.showMessage("Création de l'image disque en cours…")
        QMessageBox.information(
            self, "Imagerie en cours",
            f"L'image est en cours de création :\n{out}\n\n"
            "Vous pouvez suivre l'opération dans le journal."
        )

    @pyqtSlot(bool, str, str)
    def _on_imaging_finished(self, success: bool, error: str, output_path: str) -> None:
        if success:
            self._log(f"✅ Image disque créée : {output_path}")
            self._status_bar.showMessage("Image disque créée", 5000)
            QMessageBox.information(self, "Imagerie terminée", f"Image créée :\n{output_path}")
        else:
            msg = error or "L'imagerie a été annulée ou interrompue."
            self._log(f"❌ Échec imagerie : {msg}")
            self._status_bar.showMessage("Échec de l'imagerie", 5000)
            QMessageBox.critical(self, "Échec de l'imagerie", msg)
        if self._imager_thread:
            self._imager_thread.deleteLater()
        self._imager_thread = None

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _check_admin(self) -> None:
        if is_admin():
            self._lbl_admin.setText("🛡 Administrateur")
            self._lbl_admin.setStyleSheet("color: #4CAF50;")
            self._lbl_admin.setToolTip("Accès direct aux disques physiques autorisé")
        else:
            self._lbl_admin.setText("Mode standard")
            self._lbl_admin.setStyleSheet("color: #90A4AE;")
            self._lbl_admin.setToolTip(
                "Les images disque sont accessibles normalement. "
                "Windows demandera l'UAC uniquement pour un disque physique."
            )

    def _toggle_theme(self, checked: bool) -> None:
        self._dark_mode = checked
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        if checked:
            apply_dark(app)
        else:
            apply_light(app)

    def _show_diagnostics(self) -> None:
        report = runtime_report()
        box = QMessageBox(self)
        box.setWindowTitle("Diagnostic système — FResucitary Pro")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("Informations utiles au support :")
        box.setDetailedText(report)
        box.setInformativeText(f"Journal : {log_dir() / 'fresucitary.log'}")
        box.exec()

    def _show_about(self) -> None:
        QMessageBox.about(
            self, f"À propos — {APP_NAME}",
            f"<h2>{APP_NAME}</h2>"
            f"<p>Version {APP_VERSION}</p>"
            f"<p>Outil professionnel de récupération de données pour Windows.</p>"
            f"<p>Supporte NTFS, FAT32, exFAT et images disque (.img, .dd, .iso, .vhd).</p>"
            f"<p>Moteur embarqué : PyQt6 + pytsk3 — aucune installation Python requise.</p>"
            f"<hr/>"
            f"<p>Fonctionnement 100% local — aucune donnée transmise.</p>",
        )

    def _log(self, msg: str) -> None:
        self._log_panel.appendPlainText(msg)

    def _update_count_label(self, count: int) -> None:
        visible = self._table.visible_count()
        self._lbl_count.setText(f"{visible} / {count} fichier(s)")

    def _reset_scan_ui(self) -> None:
        self._btn_scan.setEnabled(bool(self._source_path))
        self._btn_pause.setVisible(False)
        self._btn_cancel.setVisible(False)
        self._progress_bar.setVisible(False)

    def closeEvent(self, a0: QCloseEvent | None) -> None:
        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_worker.cancel()
            self._scan_worker.wait(3000)
        if self._recover_worker and self._recover_worker.isRunning():
            self._recover_worker.cancel()
            self._recover_worker.wait(3000)
        if self._imager_thread and self._imager_thread.isRunning():
            self._imager_thread.cancel()
            self._imager_thread.wait(3000)
        if a0 is not None:
            a0.accept()


# ---------------------------------------------------------------------------
# Background disk imaging
# ---------------------------------------------------------------------------

class _ImagerThread(QThread):
    result = pyqtSignal(bool, str, str)

    def __init__(self, source_path: str, output_path: str, parent=None):
        super().__init__(parent)
        from src.core.fs.image import DiskImager
        self._output_path = output_path
        self._imager = DiskImager(source_path, output_path)

    def cancel(self) -> None:
        self._imager.cancel()

    def run(self) -> None:
        success, error = self._imager.run()
        self.result.emit(success, error, self._output_path)


# ---------------------------------------------------------------------------
# Source selection dialog
# ---------------------------------------------------------------------------

class _SourceDialog(QDialog):
    """Choose between a privileged physical device and a regular image file."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sélection de la source")
        self.setMinimumWidth(480)
        self.selected_path = ""
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(QLabel("<b>Lecteurs physiques détectés :</b>"))

        # Physical drives (Windows)
        self._drive_combo = QComboBox()
        self._populate_drives()
        layout.addWidget(self._drive_combo)

        self._btn_drive = QPushButton("Utiliser ce lecteur")
        self._btn_drive.setObjectName("PrimaryButton")
        self._btn_drive.clicked.connect(self._use_drive)
        layout.addWidget(self._btn_drive)

        if __import__("sys").platform == "win32" and not is_admin():
            self._btn_drive.setText("🛡 Relancer en administrateur pour les disques physiques")

        layout.addWidget(QLabel("<b>— ou — image disque :</b>"))

        btn_file = QPushButton("📂 Parcourir les fichiers image (.img .dd .iso .vhd)")
        btn_file.clicked.connect(self._use_file)
        layout.addWidget(btn_file)

    def _populate_drives(self) -> None:
        import sys
        if sys.platform != "win32":
            self._drive_combo.addItem("Les disques physiques sont disponibles sous Windows", "")
            return
        if not is_admin():
            self._drive_combo.addItem("Accès aux disques physiques : droits administrateur requis", "")
            return

        # Scan a practical range instead of assuming that only drives 0..7 exist.
        for i in range(32):
            path = f"\\\\.\\PhysicalDrive{i}"
            try:
                with open(path, "rb"):
                    self._drive_combo.addItem(f"Disque physique {i}  ({path})", path)
            except (FileNotFoundError, OSError, PermissionError):
                continue
        if self._drive_combo.count() == 0:
            self._drive_combo.addItem("Aucun disque physique accessible", "")

    def _use_drive(self) -> None:
        if not is_admin():
            _restart_as_admin()
            return
        path = self._drive_combo.currentData()
        if path:
            self.selected_path = path
            self.accept()

    def _use_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Ouvrir une image disque", "",
            "Images disque (*.img *.dd *.iso *.vhd *.vhdx *.raw);;Tous (*.*)"
        )
        if path:
            self.selected_path = path
            self.accept()
