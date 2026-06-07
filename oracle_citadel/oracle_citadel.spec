# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Oracle AI: Project Citadel.

Build (on Windows, where the .exe must be produced):

    pip install -r requirements.txt pyinstaller
    pyinstaller --noconfirm oracle_citadel.spec

Output: dist\\OracleAI.exe  (single-file, windowed, branded icon + splash).
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

# These packages ship data files / dynamically imported submodules that
# PyInstaller's static analysis misses. collect_all pulls them in wholesale.
for pkg in ("customtkinter", "sklearn", "MetaTrader5"):
    _d, _b, _h = collect_all(pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

hiddenimports += collect_submodules("sklearn")
hiddenimports += ["pandas", "numpy"]

# Bundle branding assets next to the frozen app.
datas += [("assets/oracle.ico", "assets"), ("assets/oracle.png", "assets")]

block_cipher = None

a = Analysis(
    ["oracle_citadel.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Native splash shown while the one-file bundle unpacks. The app closes it via
# `import pyi_splash; pyi_splash.close()` once the real UI is ready.
splash = Splash(
    "assets/oracle.png",
    binaries=a.binaries,
    datas=a.datas,
    text_pos=None,
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    splash.binaries,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="OracleAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,            # windowed app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/oracle.ico",
)
