from __future__ import annotations
import sys
import os
import io
import csv
import zipfile
import threading
import traceback
import time
import ctypes
from dataclasses import dataclass
from typing import List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QMessageBox, QTableWidgetItem,
    QLabel, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt

# Third-party libs
import pytsk3
from PIL import Image
import pandas as pd
import humanize

APP_NAME = "FResucitary"

# -------------------- Utilities --------------------

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def list_windows_drives() -> List[str]:
    drives = []
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{c}:\\"
        if os.path.exists(root):
            drives.append(f"{c}:")
    return drives

def raw_device_path_from_letter(letter: str) -> str:
    return rf"\\.\{letter}:"  # Format correct pour Windows

def format_bytes(n: int) -> str:
    try:
        return humanize.naturalsize(n, binary=True)
    except Exception:
        return str(n)

def estimate_recovery_probability(df: DeletedFile) -> str:
    if df.size == 0:
        return "low"
    if df.size < 1024 * 1024 * 2:  # < 2MB
        return "high"
    if df.size < 1024 * 1024 * 100:  # < 100MB
        return "medium"
    return "low"

def try_open_image(source_path: str | None):
    if not source_path:
        return None
    try:
        if len(source_path) == 2 and source_path[1] == ":":
            source_path = raw_device_path_from_letter(source_path[0])
        return pytsk3.Img_Info(source_path)
    except IOError as e:
        if "accès" in str(e).lower() or "access" in str(e).lower():
            raise IOError(f"Accès refusé à {source_path}. Exécutez en tant qu'administrateur.")
        else:
            raise IOError(f"Impossible d'ouvrir {source_path}: {e}")
    except Exception as e:
        raise Exception(f"Erreur inattendue : {e}")

# -------------------- Data model --------------------
@dataclass
class DeletedFile:
    name: str
    path: str
    size: int
    inode: int
    meta_flags: int
    recovered: bool = False
    probability: str = "unknown"
    error: Optional[str] = None

# -------------------- Worker Threads --------------------
class ScanWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(int)
    finished_scan = QtCore.pyqtSignal(list)
    error = QtCore.pyqtSignal(str)
    log = QtCore.pyqtSignal(str)

    def __init__(self, source_path: str):
        super().__init__()
        self.source_path = source_path
        self._stop = False

    def run(self):
        try:
            img = try_open_image(self.source_path)
            if img is None:
                self.error.emit(f"Impossible d'ouvrir la source: {self.source_path}")
                return

            fs = pytsk3.FS_Info(img)
            fs_type = fs.info.ftype
            self.log.emit(f"Type de système de fichiers : {fs_type} ({'NTFS' if fs_type == 1 else 'FAT32' if fs_type == 3 else 'inconnu'})")

            root = fs.open_dir(path="/")
            results: List[DeletedFile] = []
            stack = [(root, "\\")]
            processed = 0
            unalloc_count = 0

            while stack and not self._stop:
                directory, parent = stack.pop()
                for entry in directory:
                    try:
                        if self._stop:
                            break
                        name_obj = entry.info.name
                        if not name_obj:
                            continue
                        name = name_obj.name.decode('utf-8', 'ignore')
                        if name in ('.', '..'):
                            continue
                        meta = entry.info.meta
                        if not meta:
                            continue
                        current_path = os.path.join(parent, name).replace('/', '\\')

                        # Dossier ?
                        if meta.type == pytsk3.TSK_FS_META_TYPE_DIR:
                            try:
                                sub = entry.as_directory()
                                stack.append((sub, current_path))
                            except Exception:
                                pass
                            continue

                        # Fichier supprimé (non alloué) ?
                        if meta.flags & pytsk3.TSK_FS_META_FLAG_UNALLOC:
                            unalloc_count += 1
                            inode = int(meta.addr) if meta.addr is not None else -1
                            size = int(meta.size) if meta.size is not None else 0
                            df = DeletedFile(
                                name=name,
                                path=current_path,
                                size=size,
                                inode=inode,
                                meta_flags=int(meta.flags)
                            )
                            df.probability = estimate_recovery_probability(df)
                            results.append(df)

                        # Fichier dans la corbeille (même alloué) ?
                        elif "$Recycle.Bin" in current_path or "RECYCLER" in current_path:
                            inode = int(meta.addr) if meta.addr is not None else -1
                            size = int(meta.size) if meta.size is not None else 0
                            df = DeletedFile(
                                name=name,
                                path=current_path,
                                size=size,
                                inode=inode,
                                meta_flags=int(meta.flags)
                            )
                            df.probability = "high"
                            df.error = "Dans la corbeille"
                            results.append(df)

                        processed += 1
                        if processed % 100 == 0:
                            self.progress.emit(processed)

                    except Exception:
                        continue

            self.log.emit(f"Fichiers non alloués trouvés : {unalloc_count}")
            if self._stop:
                self.error.emit("Scan interrompu par l'utilisateur")
            else:
                self.finished_scan.emit(results)

        except IOError as e:
            self.error.emit(str(e))
        except Exception as e:
            tb = traceback.format_exc()
            self.error.emit(f"Erreur interne : {e}\n{tb}")

    def stop(self):
        self._stop = True


class RecoverWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, int)
    finished = QtCore.pyqtSignal(list, list)
    error = QtCore.pyqtSignal(str)

    def __init__(self, source_path: str, files: List[DeletedFile], output_dir: str):
        super().__init__()
        self.source_path = source_path
        self.files = files
        self.output_dir = output_dir

    def run(self):
        ok, fail = [], []
        try:
            img = try_open_image(self.source_path)
            if img is None:
                self.error.emit("Impossible d'ouvrir la source")
                return
            fs = pytsk3.FS_Info(img)
            total = len(self.files)

            for i, f in enumerate(self.files):
                try:
                    file_obj = fs.open_meta(inode=f.inode)
                    safe_name = f.name if f.name else f'inode_{f.inode}'
                    safe_name = "".join(c for c in safe_name if c.isalnum() or c in "._- ")
                    out_path = os.path.join(self.output_dir, safe_name)

                    base, ext = os.path.splitext(out_path)
                    counter = 1
                    while os.path.exists(out_path):
                        out_path = f"{base}_recovered_{counter}{ext}"
                        counter += 1

                    with open(out_path, 'wb') as out:
                        offset = 0
                        remaining = f.size
                        while remaining > 0:
                            chunk = file_obj.read_random(offset, min(1024*1024, remaining))
                            if not chunk:
                                break
                            out.write(chunk)
                            offset += len(chunk)
                            remaining -= len(chunk)

                    ok.append((f, out_path))
                except Exception as e:
                    f.error = str(e)
                    fail.append((f, str(e)))
                self.progress.emit(i + 1, total)

            self.finished.emit(ok, fail)
        except Exception as e:
            tb = traceback.format_exc()
            self.error.emit(f"Erreur : {e}\n{tb}")


# -------------------- Main Window --------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1200, 750)
        self.setStyleSheet("""
            QWidget {
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 10pt;
                background-color: #f8f9fa;
            }
            QPushButton {
                background-color: #0d6efd;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0b5ed7;
            }
            QPushButton:pressed {
                background-color: #0a58ca;
            }
            QPushButton:disabled {
                background-color: #6ea8fe;
            }
            QComboBox, QLineEdit, QTableWidget {
                padding: 5px;
                border: 1px solid #ced4da;
                border-radius: 4px;
            }
            QHeaderView::section {
                background-color: #0d6efd;
                color: white;
                font-weight: bold;
                padding: 6px;
                border: none;
            }
            QTableWidget {
                gridline-color: #dee2e6;
                selection-background-color: #d1e7ff;
            }
            QLabel {
                color: #212529;
            }
            QProgressBar {
                height: 10px;
                border: 1px solid #dee2e6;
                border-radius: 5px;
            }
            QProgressBar::chunk {
                background-color: #0d6efd;
                border-radius: 5px;
            }
        """)
        self.source_path: Optional[str] = None
        self.deleted_files: List[DeletedFile] = []
        self.scan_worker: Optional[ScanWorker] = None
        self.recover_worker: Optional[RecoverWorker] = None

        self._build_ui()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        main_layout = QtWidgets.QVBoxLayout(central)

        # Title
        title = QLabel(f"<h2 style='color:#0d6efd'>{APP_NAME}</h2>")
        subtitle = QLabel("Récupération de fichiers supprimés – Scan et restauration depuis disque ou image")
        subtitle.setStyleSheet("color: #495057; font-size: 9pt;")

        main_layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(subtitle, alignment=Qt.AlignmentFlag.AlignCenter)
        main_layout.addSpacing(20)

        # Controls
        controls = QtWidgets.QHBoxLayout()
        self.source_combo = QtWidgets.QComboBox()
        self.source_combo.setEditable(True)
        self.source_combo.addItems(list_windows_drives())
        self.source_combo.setFixedWidth(180)

        btn_refresh = QtWidgets.QPushButton('🔄 Actualiser')
        btn_browse = QtWidgets.QPushButton('📁 Parcourir')
        btn_scan = QtWidgets.QPushButton('🔍 Lancer le scan')
        btn_stop = QtWidgets.QPushButton('⏹️ Arrêter')

        controls.addWidget(QtWidgets.QLabel('Source :'))
        controls.addWidget(self.source_combo)
        controls.addWidget(btn_refresh)
        controls.addWidget(btn_browse)
        controls.addWidget(btn_scan)
        controls.addWidget(btn_stop)
        controls.addStretch()

        main_layout.addLayout(controls)

        # Search & Filter
        filter_layout = QtWidgets.QHBoxLayout()
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("Rechercher par nom...")
        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.addItems(['Tous', 'Images', 'Documents', 'Vidéos', 'Audio', 'Autres'])
        self.btn_filter = QtWidgets.QPushButton('Filtrer')
        self.btn_export_csv = QtWidgets.QPushButton('📤 Exporter CSV')

        filter_layout.addWidget(self.search_input)
        filter_layout.addWidget(self.filter_combo)
        filter_layout.addWidget(self.btn_filter)
        filter_layout.addWidget(self.btn_export_csv)

        main_layout.addLayout(filter_layout)

        # Table
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['Nom', 'Chemin', 'Taille', 'Inode', 'Probabilité', 'État'])
        header = self.table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        else:
            # Optionnel : log un avertissement (utile en debug)
            self._log("Attention : impossible d'accéder au header du tableau")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        main_layout.addWidget(self.table)

        # Actions
        actions_layout = QtWidgets.QHBoxLayout()
        self.btn_recover = QtWidgets.QPushButton('💾 Récupérer sélection')
        self.btn_recover_all = QtWidgets.QPushButton('💾 Récupérer tout')
        self.btn_show_log = QtWidgets.QPushButton('📋 Voir log')
        actions_layout.addWidget(self.btn_recover)
        actions_layout.addWidget(self.btn_recover_all)
        actions_layout.addWidget(self.btn_show_log)
        actions_layout.addStretch()

        main_layout.addLayout(actions_layout)

        # Footer
        footer = QtWidgets.QHBoxLayout()
        self.status_label = QLabel('Prêt')
        self.status_label.setStyleSheet("color: #6c757d;")
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        footer.addWidget(self.status_label)
        footer.addWidget(self.progress_bar)
        main_layout.addLayout(footer)

        self.setCentralWidget(central)

        # Connections
        btn_refresh.clicked.connect(self._refresh_drives)
        btn_browse.clicked.connect(self._browse_image)
        btn_scan.clicked.connect(self._start_scan)
        btn_stop.clicked.connect(self._stop_scan)
        self.btn_filter.clicked.connect(self._apply_filter)
        self.btn_export_csv.clicked.connect(self._export_csv)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.doubleClicked.connect(self._on_table_double_click)
        self.btn_recover.clicked.connect(self._recover_selected)
        self.btn_recover_all.clicked.connect(self._recover_all)
        self.btn_show_log.clicked.connect(self._show_log)

        self._log_lines: List[str] = []

    def _log(self, msg: str):
        t = time.strftime('%H:%M:%S')
        line = f"[{t}] {msg}"
        self._log_lines.append(line)
        self.status_label.setText(msg)

    def _refresh_drives(self):
        self.source_combo.clear()
        self.source_combo.addItems(list_windows_drives())
        self._log('Lecteurs actualisés')

    def _browse_image(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Sélectionner une image disque', filter="Images (*.img *.dd *.iso *.vhd)")
        if path:
            self.source_combo.setEditText(path)
            self._log(f'Source sélectionnée : {path}')

    def _set_progress(self, val: int, maximum: Optional[int] = None):
        if maximum is not None:
            self.progress_bar.setMaximum(maximum)
        self.progress_bar.setValue(val)

    def _start_scan(self):
        src = self.source_combo.currentText().strip()
        if not src:
            QMessageBox.warning(self, 'Erreur', 'Veuillez choisir une source (C:, D: ou un fichier image).')
            return

        # Demander admin si disque système
        if len(src) == 2 and src[1] == ":" and not is_admin():
            reply = QMessageBox.question(self, "Admin requis",
                "Le scan de disque brut nécessite les droits administrateur. Continuer ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.No:
                return

        self.source_path = src
        self._log(f"Scan démarré sur {src}...")
        self.table.setRowCount(0)
        self.deleted_files.clear()

        self.scan_worker = ScanWorker(src)
        self.scan_worker.progress.connect(lambda v: self._set_progress(v, 0))
        self.scan_worker.finished_scan.connect(self._on_scan_finished)
        self.scan_worker.error.connect(self._on_scan_error)
        self.scan_worker.log.connect(self._log)
        self.scan_worker.start()
        self._set_progress(0, 0)

    def _stop_scan(self):
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.stop()
            self._log("Arrêt demandé...")

    def _on_scan_finished(self, results: List[DeletedFile]):
        self.deleted_files = results
        self._populate_table(results)
        if len(results) == 0:
            self._log("⚠️ Aucun fichier supprimé trouvé.")
            self._log("💡 Conseils :")
            self._log("   - Lancez en admin pour C:/D:")
            self._log("   - Ne videz pas la corbeille")
            self._log("   - Évitez d'écrire sur le disque après suppression")
            self._log("   - Testez avec un fichier récemment supprimé")
        else:
            self._log(f"✅ Scan terminé : {len(results)} fichier(s) trouvé(s)")
        self._set_progress(0)

    def _on_scan_error(self, err: str):
        QMessageBox.critical(self, 'Erreur', err)
        self._log(f"❌ Erreur : {err}")
        self._set_progress(0)

    def _populate_table(self, items: List[DeletedFile]):
        self.table.setRowCount(0)
        for f in items:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(f.name))
            self.table.setItem(r, 1, QTableWidgetItem(f.path))
            self.table.setItem(r, 2, QTableWidgetItem(format_bytes(f.size)))
            self.table.setItem(r, 3, QTableWidgetItem(str(f.inode)))
            self.table.setItem(r, 4, QTableWidgetItem(f.probability.capitalize()))
            state = '✅ Récupéré' if f.recovered else ('❌ Erreur' if f.error else '🟢 Disponible')
            self.table.setItem(r, 5, QTableWidgetItem(state))

    def _apply_filter(self):
        q = self.search_input.text().lower()
        cat = self.filter_combo.currentText()
        ext_map = {
            'Images': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff'],
            'Documents': ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt'],
            'Vidéos': ['.mp4', '.avi', '.mkv', '.mov'],
            'Audio': ['.mp3', '.wav', '.flac'],
        }
        filtered = [
            f for f in self.deleted_files
            if (not q or q in f.name.lower() or q in f.path.lower())
            and (cat == 'Tous' or 
                 (cat in ext_map and os.path.splitext(f.name)[1].lower() in ext_map[cat]) or
                 (cat == 'Autres' and os.path.splitext(f.name)[1] == ''))
        ]
        self._populate_table(filtered)
        self._log(f"Filtre appliqué : {len(filtered)} résultats")

    def _export_csv(self):
        if not self.deleted_files:
            QMessageBox.information(self, 'Vide', 'Aucun fichier à exporter.')
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Exporter en CSV', filter='CSV (*.csv)')
        if path:
            df = pd.DataFrame([{
                'name': f.name, 'path': f.path, 'size': f.size,
                'inode': f.inode, 'probability': f.probability, 'error': f.error
            } for f in self.deleted_files])
            df.to_csv(path, index=False)
            self._log(f"Exporté : {path}")

    def _on_selection_changed(self):
        count = len(self.table.selectedItems()) // 6
        self.btn_recover.setText(f"💾 Récupérer ({count})") if count else self.btn_recover.setText("💾 Récupérer sélection")

    def _on_table_double_click(self):
        self._recover_selected()

    def _recover_selected(self):
        rows = {item.row() for item in self.table.selectedItems()}
        if not rows:
            QMessageBox.information(self, 'Sélection', 'Veuillez sélectionner des fichiers.')
            return
        files = []
        for r in rows:
            item = self.table.item(r, 1)
            if not item:
                continue
            path = item.text()
            f = next((x for x in self.deleted_files if x.path == path), None)
            if f:
                files.append(f)
        self._start_recovery(files)

    def _recover_all(self):
        if not self.deleted_files:
            QMessageBox.information(self, 'Vide', 'Aucun fichier à récupérer.')
            return
        self._start_recovery(self.deleted_files)

    def _start_recovery(self, files: List[DeletedFile]):
        out = QFileDialog.getExistingDirectory(self, 'Dossier de récupération')
        if not out:
            return
        if not self.source_path:
            QMessageBox.critical(self, 'Erreur', 'Source non définie.')
            return
        self._log(f"Récupération de {len(files)} fichier(s)...")
        self.recover_worker = RecoverWorker(self.source_path, files, out)
        self.recover_worker.progress.connect(self._set_progress)
        self.recover_worker.finished.connect(self._on_recover_finished)
        self.recover_worker.error.connect(self._on_recover_error)
        self.recover_worker.start()

    def _on_recover_finished(self, ok_list, fail_list):
        QMessageBox.information(self, 'Terminé', f"Récupérés : {len(ok_list)}, Échecs : {len(fail_list)}")
        self._log(f"✅ Récupération terminée : {len(ok_list)} OK, {len(fail_list)} échec")
        for f, _ in ok_list:
            f.recovered = True
        self._populate_table(self.deleted_files)

    def _on_recover_error(self, err: str):
        QMessageBox.critical(self, 'Erreur', err)
        self._log(f"❌ Erreur : {err}")

    def _show_log(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Journal")
        dlg.resize(700, 500)
        layout = QtWidgets.QVBoxLayout(dlg)
        text = QtWidgets.QTextEdit('\n'.join(self._log_lines))
        text.setReadOnly(True)
        layout.addWidget(text)
        dlg.exec()


# -------------------- Entrypoint --------------------
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()