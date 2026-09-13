"""
FResucitary — Material You design system.
Provides QSS stylesheets for dark and light modes.
Primary: #1565C0 (violet) | Font: Segoe UI / system
"""
from __future__ import annotations
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor


DARK_QSS = """
/* ── Base ─────────────────────────────────────────────────────── */
QWidget {
    background-color: #1C1B1F;
    color: #E6E1E5;
    font-family: "Segoe UI", "Roboto", system-ui, sans-serif;
    font-size: 13px;
}

QMainWindow, QDialog {
    background-color: #1C1B1F;
}

/* ── Sidebar ───────────────────────────────────────────────────── */
#Sidebar {
    background-color: #0D1B2A;
    border-right: 1px solid #1E3A5F;
    min-width: 200px;
    max-width: 220px;
}

#SidebarHeader {
    background-color: #1565C0;
    color: white;
    font-size: 16px;
    font-weight: bold;
    padding: 16px 12px;
    border: none;
}

/* ── Navigation buttons ────────────────────────────────────────── */
#NavButton {
    background: transparent;
    color: #90A4AE;
    border: none;
    border-radius: 28px;
    padding: 10px 16px;
    text-align: left;
    font-size: 13px;
}
#NavButton:hover {
    background-color: rgba(21, 101, 192, 0.12);
    color: #E6E1E5;
}
#NavButton:checked {
    background-color: #1E3A5F;
    color: #90CAF9;
    font-weight: bold;
}

/* ── Table ─────────────────────────────────────────────────────── */
QTableView {
    background-color: #1C1B1F;
    alternate-background-color: #111827;
    border: none;
    gridline-color: transparent;
    selection-background-color: #1E3A5F;
    selection-color: #E6E1E5;
    outline: none;
}
QTableView::item {
    padding: 4px 8px;
    border: none;
}
QHeaderView::section {
    background-color: #0D1B2A;
    color: #90A4AE;
    padding: 6px 8px;
    border: none;
    border-right: 1px solid #1E3A5F;
    font-weight: bold;
    font-size: 12px;
}
QHeaderView::section:first {
    border-left: none;
}

/* ── Toolbar / action bar ──────────────────────────────────────── */
#ActionBar {
    background-color: #0D1B2A;
    border-bottom: 1px solid #1E3A5F;
    padding: 4px 8px;
    min-height: 52px;
    max-height: 52px;
}

/* ── Primary button ────────────────────────────────────────────── */
QPushButton#PrimaryButton, QPushButton.primary {
    background-color: #1565C0;
    color: #FFFFFF;
    border: none;
    border-radius: 20px;
    padding: 8px 24px;
    font-weight: bold;
    font-size: 13px;
}
QPushButton#PrimaryButton:hover, QPushButton.primary:hover {
    background-color: #1976D2;
}
QPushButton#PrimaryButton:pressed, QPushButton.primary:pressed {
    background-color: #0D47A1;
}
QPushButton#PrimaryButton:disabled, QPushButton.primary:disabled {
    background-color: #1E3A5F;
    color: #5C8BC7;
}

/* ── Tonal button ──────────────────────────────────────────────── */
QPushButton#TonalButton, QPushButton.tonal {
    background-color: #1E3A5F;
    color: #90CAF9;
    border: none;
    border-radius: 20px;
    padding: 8px 24px;
    font-size: 13px;
}
QPushButton#TonalButton:hover, QPushButton.tonal:hover {
    background-color: #234670;
}

/* ── Outlined button ───────────────────────────────────────────── */
QPushButton {
    background-color: transparent;
    color: #90CAF9;
    border: 1px solid #5C8BC7;
    border-radius: 20px;
    padding: 7px 20px;
    font-size: 13px;
}
QPushButton:hover {
    background-color: rgba(144, 202, 249, 0.08);
}
QPushButton:pressed {
    background-color: rgba(144, 202, 249, 0.12);
}

/* ── Combo box ─────────────────────────────────────────────────── */
QComboBox {
    background-color: #0D1B2A;
    color: #E6E1E5;
    border: 1px solid #5C8BC7;
    border-radius: 8px;
    padding: 6px 12px;
    min-width: 120px;
}
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background-color: #0D1B2A;
    color: #E6E1E5;
    border: 1px solid #1E3A5F;
    selection-background-color: #1E3A5F;
}

/* ── Line edit ─────────────────────────────────────────────────── */
QLineEdit {
    background-color: #0D1B2A;
    color: #E6E1E5;
    border: 1px solid #5C8BC7;
    border-radius: 8px;
    padding: 7px 12px;
    selection-background-color: #1E3A5F;
}
QLineEdit:focus {
    border: 2px solid #90CAF9;
    padding: 6px 11px;
}

/* ── Slider (score filter) ─────────────────────────────────────── */
QSlider::groove:horizontal {
    height: 4px;
    background: #1E3A5F;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 20px; height: 20px;
    background: #90CAF9;
    border-radius: 10px;
    margin: -8px 0;
}
QSlider::sub-page:horizontal {
    background: #1565C0;
    border-radius: 2px;
}

/* ── Progress bar ──────────────────────────────────────────────── */
QProgressBar {
    background-color: #1E3A5F;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background-color: #90CAF9;
    border-radius: 4px;
}

/* ── Scroll bars ───────────────────────────────────────────────── */
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #1E3A5F;
    border-radius: 4px;
    min-height: 32px;
}
QScrollBar::handle:vertical:hover { background: #1565C0; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QScrollBar:horizontal {
    background: transparent;
    height: 8px;
}
QScrollBar::handle:horizontal {
    background: #1E3A5F;
    border-radius: 4px;
    min-width: 32px;
}
QScrollBar::handle:horizontal:hover { background: #1565C0; }

/* ── Preview panel ─────────────────────────────────────────────── */
#PreviewPanel {
    background-color: #111827;
    border-left: 1px solid #1E3A5F;
}
#PreviewHeader {
    background-color: #0D1B2A;
    color: #90A4AE;
    font-size: 12px;
    padding: 8px;
    border-bottom: 1px solid #1E3A5F;
}
#PreviewText, #PreviewHex {
    background-color: #1C1B1F;
    color: #A8C7FA;
    border: none;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 11px;
}
#PreviewPlaceholder {
    color: #1565C0;
    font-size: 13px;
}

/* ── Log panel ─────────────────────────────────────────────────── */
#LogPanel {
    background-color: #14121A;
    color: #A8C7FA;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 11px;
    border: none;
    border-top: 1px solid #1E3A5F;
}

/* ── Status bar ────────────────────────────────────────────────── */
QStatusBar {
    background-color: #0D1B2A;
    color: #90A4AE;
    font-size: 11px;
}

/* ── Tool tip ──────────────────────────────────────────────────── */
QToolTip {
    background-color: #0D1B2A;
    color: #E6E1E5;
    border: 1px solid #1565C0;
    padding: 4px 8px;
    border-radius: 4px;
}

/* ── Group box ─────────────────────────────────────────────────── */
QGroupBox {
    border: 1px solid #1E3A5F;
    border-radius: 8px;
    margin-top: 12px;
    padding: 12px 8px 8px 8px;
    color: #90A4AE;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: #90CAF9;
}

/* ── Labels ────────────────────────────────────────────────────── */
QLabel#StatValue {
    font-size: 28px;
    font-weight: bold;
    color: #90CAF9;
}
QLabel#StatLabel {
    font-size: 11px;
    color: #5C8BC7;
}

/* ── Splitter ──────────────────────────────────────────────────── */
QSplitter::handle {
    background-color: #1E3A5F;
    width: 1px;
    height: 1px;
}

/* ── Check box ─────────────────────────────────────────────────── */
QCheckBox {
    color: #E6E1E5;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 18px; height: 18px;
    border: 2px solid #5C8BC7;
    border-radius: 3px;
}
QCheckBox::indicator:checked {
    background-color: #1565C0;
    border-color: #1565C0;
    image: url(:/icons/check_white.png);
}

/* ── Tab bar ───────────────────────────────────────────────────── */
QTabBar::tab {
    background: transparent;
    color: #90A4AE;
    padding: 8px 16px;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:selected {
    color: #90CAF9;
    border-bottom: 2px solid #90CAF9;
}
QTabWidget::pane { border: none; }
/* ── Detail card ───────────────────────────────────────────────── */
#DetailCard {
    background-color: #0D1B2A;
    border-bottom: 1px solid #1E3A5F;
}

"""


LIGHT_QSS = """
QWidget {
    background-color: #FFFBFE;
    color: #1C1B1F;
    font-family: "Segoe UI", "Roboto", system-ui, sans-serif;
    font-size: 13px;
}
#Sidebar {
    background-color: #F7F2FA;
    border-right: 1px solid #90CAF9;
    min-width: 200px; max-width: 220px;
}
#SidebarHeader {
    background-color: #1565C0;
    color: white;
    font-size: 16px;
    font-weight: bold;
    padding: 16px 12px;
    border: none;
}
#NavButton {
    background: transparent;
    color: #1A3A6B;
    border: none;
    border-radius: 28px;
    padding: 10px 16px;
    text-align: left;
}
#NavButton:hover { background-color: rgba(21,101,192,0.08); }
#NavButton:checked {
    background-color: #BBDEFB;
    color: #0D47A1;
    font-weight: bold;
}
QTableView {
    background-color: #FFFBFE;
    alternate-background-color: #F7F2FA;
    border: none;
    selection-background-color: #BBDEFB;
    selection-color: #1C1B1F;
}
QHeaderView::section {
    background-color: #F3EDF7;
    color: #1A3A6B;
    padding: 6px 8px;
    border: none;
    border-right: 1px solid #90CAF9;
    font-weight: bold;
}
QPushButton#PrimaryButton, QPushButton.primary {
    background-color: #1565C0;
    color: white;
    border: none;
    border-radius: 20px;
    padding: 8px 24px;
    font-weight: bold;
}
QPushButton#PrimaryButton:hover { background-color: #1976D2; }
QPushButton {
    background: transparent;
    color: #1565C0;
    border: 1px solid #79747E;
    border-radius: 20px;
    padding: 7px 20px;
}
QPushButton:hover { background-color: rgba(21,101,192,0.08); }
QLineEdit {
    background-color: #F3EDF7;
    color: #1C1B1F;
    border: 1px solid #79747E;
    border-radius: 8px;
    padding: 7px 12px;
}
QLineEdit:focus { border: 2px solid #1565C0; }
QComboBox {
    background-color: #F3EDF7;
    color: #1C1B1F;
    border: 1px solid #79747E;
    border-radius: 8px;
    padding: 6px 12px;
}
QProgressBar {
    background-color: #BBDEFB;
    border-radius: 4px;
    height: 8px;
}
QProgressBar::chunk { background-color: #1565C0; border-radius: 4px; }
#PreviewPanel { background-color: #F7F2FA; border-left: 1px solid #90CAF9; }
#PreviewHeader { background-color: #F3EDF7; color: #1A3A6B; }
#LogPanel { background-color: #F3EDF7; color: #0D47A1; }
QStatusBar { background-color: #F7F2FA; color: #1A3A6B; }
"""


def apply_dark(app: QApplication) -> None:
    app.setStyleSheet(DARK_QSS)


def apply_light(app: QApplication) -> None:
    app.setStyleSheet(LIGHT_QSS)


def apply_auto(app: QApplication) -> None:
    """Apply dark or light based on system palette."""
    palette = app.palette()
    bg = palette.color(QPalette.ColorRole.Window)
    # If background luminance < 128 → dark mode already active
    lum = (bg.red() * 299 + bg.green() * 587 + bg.blue() * 114) // 1000
    if lum < 128:
        apply_dark(app)
    else:
        apply_light(app)
