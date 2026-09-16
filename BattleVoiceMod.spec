# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Battle VoiceMod.

Built as onedir on purpose:

- a --onefile build would unpack the whole bundle into %TEMP% on every launch,
  which is slow for a numpy+scipy payload and is the shape antivirus heuristics
  dislike most;
- sounddevice and soundfile load native libraries, and onedir keeps them as
  plain files on disk instead of extracted temporaries.

Build with:
    .venv-build\\Scripts\\python.exe -m PyInstaller --noconfirm --clean BattleVoiceMod.spec
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

datas = []
binaries = []


def _collect(*names):
    """Collects data files and native libraries, ignoring packages that are absent."""
    for name in names:
        try:
            datas.extend(collect_data_files(name))
        except Exception:
            pass
        try:
            binaries.extend(collect_dynamic_libs(name))
        except Exception:
            pass


# Native audio backends. sounddevice is a single module that keeps PortAudio in a
# separate `_sounddevice_data` package, and soundfile keeps libsndfile under
# `soundfile/_soundfile_data` -- missing either one lets the window open and then
# fails the moment audio starts.
_collect("sounddevice", "_sounddevice_data")
_collect("soundfile", "_soundfile_data")

# CustomTkinter loads its theme JSON files at runtime.
_collect("customtkinter")

# The virtual audio cable is installed by the app itself, so it has to ship as data.
if os.path.isdir(os.path.join("drivers", "vbcable")):
    datas.append((os.path.join("drivers", "vbcable"), os.path.join("drivers", "vbcable")))

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        # pynput picks its backend by platform at runtime
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

icon_path = os.path.join("assets", "app.ico")

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BattleVoiceMod",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=icon_path if os.path.isfile(icon_path) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="BattleVoiceMod",
)
