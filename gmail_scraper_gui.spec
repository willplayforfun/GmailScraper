# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for GmailScraper GUI.
#
# Release build (default):
#   pyinstaller gmail_scraper_gui.spec
#
# Debug build (console window enabled, used by debug-build.yml):
#   DEBUG_BUILD=1 pyinstaller gmail_scraper_gui.spec
#   (on Windows: set DEBUG_BUILD=1 && pyinstaller gmail_scraper_gui.spec)

import os
from PyInstaller.utils.hooks import collect_data_files

is_debug = bool(os.environ.get("DEBUG_BUILD"))
exe_name = "GmailScraper-debug" if is_debug else "GmailScraper"

datas = collect_data_files("customtkinter")

a = Analysis(
    ["gui_main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "googleapiclient",
        "googleapiclient.discovery",
        "googleapiclient.http",
        "googleapiclient.errors",
        "google.auth",
        "google.auth.transport.requests",
        "google.oauth2.credentials",
        "google_auth_oauthlib",
        "google_auth_oauthlib.flow",
        "customtkinter",
        "darkdetect",
        "pkg_resources",
        "packaging",
        "packaging.version",
        "packaging.specifiers",
        "packaging.requirements",
        # sqlite3 is stdlib but sometimes missed
        "_sqlite3",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=exe_name,
    debug=is_debug,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=is_debug,          # True only in DEBUG_BUILD; always False for releases
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="gui/assets/icon.ico" if os.path.exists("gui/assets/icon.ico") else None,
    onefile=True,
)
