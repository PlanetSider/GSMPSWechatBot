# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the GSMPSWechatBot Windows portable (onedir) build.

Build from the repository root:

    pyinstaller packaging/app.spec --noconfirm --distpath dist --workpath build

Design notes
------------
* contents_directory='.' restores the flat PyInstaller-5 onedir layout so that
  ``sys._MEIPASS`` equals the directory holding ``GSMPSWechatBot.exe``.  The
  project resolves every data path from module ``__file__`` (PyInstaller pins
  frozen module ``__file__`` under ``_MEIPASS``), therefore
  ``BASE_DIR == exe directory`` holds without touching any project source.
* The project uses ``importlib.import_module("plugins.<name>")`` for its plugin
  system, which static analysis cannot see.  Every project package is therefore
  collected explicitly below.  Because ``collect_submodules`` pulls each
  module's third-party imports transitively, user-supplied plugins that reuse
  an already-collected library keep working; the real ``plugins/`` / ``auto/``
  directories are still shipped next to the exe (assemble_release.py) so the
  directory scan and future plugin additions keep functioning.
* Runtime resources (assets/, data/, weflow-core/, config template, scripts)
  are NOT bundled here; packaging/assemble_release.py places real copies next
  to the exe.  Only files that frozen code reads through frozen-module
  ``__file__`` are attached as datas below.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)

hiddenimports = ["main", "runpy", "tzdata"]
# tzdata: plugins/fx.py builds ZoneInfo objects at import time; CPython on
# Windows has no system tz database, so the tzdata package must be bundled
# (it is resolved via importlib.resources at runtime, invisible to static
# analysis -> must be listed here and installed in the build environment).
for _pkg in ("core", "llm", "plugins", "auto", "services", "utils", "anima"):
    hiddenimports += collect_submodules(_pkg)

a = Analysis(
    ["run_wrapper.py"],
    pathex=[ROOT],
    binaries=[],
    datas=[
        # Read through frozen-module __file__: anima_service.py -> ANIMA_DIR.
        (os.path.join(ROOT, "anima", "workflow.json"), "anima"),
        # services/music/sources/lx_daemon_client.py -> parents[1] / "lx_daemon.mjs".
        (os.path.join(ROOT, "services", "music", "lx_daemon.mjs"), "services/music"),
        (os.path.join(ROOT, "services", "music", "lx_runner.mjs"), "services/music"),
        (os.path.join(ROOT, "services", "music", "lx_runtime.mjs"), "services/music"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GSMPSWechatBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # keep the console: bot/weflow logs go to stdout
    icon=None,
    contents_directory=".",  # flat layout: _MEIPASS == exe directory
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="GSMPSWechatBot",
)
