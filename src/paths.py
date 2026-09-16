"""
Filesystem layout for the app, in one place.

The split matters once the app is frozen with PyInstaller:

- Bundled resources are read-only. A `--onefile` build unpacks them to a
  temporary directory (`sys._MEIPASS`) that is deleted when the program exits,
  and an installed copy under Program Files is not writable either.
- User data (settings and the sounds the user adds) lives in %APPDATA%, so it
  survives upgrades and works no matter where the program was installed.

Everything that reads a bundled file goes through `resource_path()`; everything
that reads or writes user files goes through `user_data_dir()`.
"""

import os
import shutil
import sys

APP_NAME = "BattleVoiceMod"

SOUND_EXTENSIONS = (".mp3", ".wav", ".ogg", ".flac")


def is_frozen() -> bool:
    """True when running from a PyInstaller build rather than from source."""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> str:
    """Directory holding the bundled, read-only resources."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    # src/paths.py -> project root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts: str) -> str:
    return os.path.join(bundle_dir(), *parts)


def user_data_dir() -> str:
    """Writable per-user folder, created on first use."""
    root = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(root, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def user_config_path(filename: str) -> str:
    return os.path.join(user_data_dir(), filename)


def user_sounds_dir() -> str:
    path = os.path.join(user_data_dir(), "sounds")
    os.makedirs(path, exist_ok=True)
    return path


def _legacy_assets_dir() -> str:
    """Location used by builds that kept everything next to the program."""
    if is_frozen():
        root = os.path.dirname(sys.executable)
    else:
        root = bundle_dir()
    return os.path.join(root, "assets")


def migrate_legacy_data() -> None:
    """One-time import of settings and sounds from the old "next to the app" layout.

    Safe to call on every start: everything is skipped when the destination
    already exists, so it never overwrites data the user has since changed.
    """
    legacy = _legacy_assets_dir()
    if not os.path.isdir(legacy) or os.path.abspath(legacy) == os.path.abspath(user_data_dir()):
        return

    for name in ("app_config.json", "soundboard_config.json"):
        source = os.path.join(legacy, name)
        target = user_config_path(name)
        if os.path.isfile(source) and not os.path.exists(target):
            try:
                shutil.copy2(source, target)
            except OSError as e:
                print(f"Nao foi possivel migrar {name}: {e}")

    legacy_sounds = os.path.join(legacy, "sounds")
    if not os.path.isdir(legacy_sounds):
        return

    target_sounds = user_sounds_dir()
    for entry in os.listdir(legacy_sounds):
        if not entry.lower().endswith(SOUND_EXTENSIONS):
            continue
        target = os.path.join(target_sounds, entry)
        if not os.path.exists(target):
            try:
                shutil.copy2(os.path.join(legacy_sounds, entry), target)
            except OSError as e:
                print(f"Nao foi possivel migrar {entry}: {e}")
