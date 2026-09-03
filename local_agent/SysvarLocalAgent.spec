# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys


block_cipher = None
root = Path(SPECPATH)
venv_site_packages = root / ".venv" / "Lib" / "site-packages"
site_packages = venv_site_packages if venv_site_packages.exists() else Path(sys.prefix) / "Lib" / "site-packages"
pywin32_paths = [
    site_packages / "win32",
    site_packages / "win32" / "lib",
    site_packages / "pythonwin",
]


a = Analysis(
    [str(root / "sysvar_agent" / "service_entry.py")],
    pathex=[str(root), *[str(path) for path in pywin32_paths if path.exists()]],
    binaries=[],
    datas=[],
    hiddenimports=[
        "servicemanager",
        "win32event",
        "win32evtlog",
        "win32evtlogutil",
        "win32service",
        "win32serviceutil",
        "pywintypes",
        "pythoncom",
    ],
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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SysvarLocalAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SysvarLocalAgent",
)
