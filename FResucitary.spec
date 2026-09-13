# -*- mode: python ; coding: utf-8 -*-
# Production Windows build: onedir bundle for fast startup and reliable DLL loading.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH)

pdf_datas, pdf_binaries, pdf_hiddenimports = collect_all('pypdfium2')

a = Analysis(
    [str(ROOT / 'main.py')],
    pathex=[str(ROOT)],
    binaries=pdf_binaries,
    datas=pdf_datas,
    hiddenimports=[
        'pytsk3',
        'PyQt6.QtCore', 'PyQt6.QtGui', 'PyQt6.QtWidgets',
        'reportlab', 'reportlab.platypus', 'reportlab.lib',
        'PIL', 'PIL.Image',
        *pdf_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'notebook', 'pytest'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FResucitary',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    version=str(ROOT / 'packaging' / 'version_info.txt'),
    uac_admin=False,
    uac_uiaccess=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='FResucitary',
)
