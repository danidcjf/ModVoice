"""
TikTok LIVE Studio - Battle VoiceMod & Soundboard
Main launcher application.
"""

import os
import sys

# When frozen by PyInstaller the bundled modules are already importable; this is
# only needed so that running from source can find the local `src` package.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import paths
from src.audio_engine import BattleAudioEngine
from src.soundboard import SoundboardManager
from src.hotkeys import GlobalHotkeyManager
from src.ui import BattleVoiceModApp


def selftest() -> int:
    """Headless check that everything the app needs is actually present.

    A frozen build fails silently when a native library is missing: the window
    simply never opens. This walks the pieces that need bundling (native audio
    backends, the driver package, the DSP engine) and writes a report to a file,
    because a windowed build has no console to print to.
    """
    import tempfile
    import traceback

    import numpy as np

    lines = []
    ok = True
    try:
        import numpy, scipy, sounddevice, soundfile, customtkinter, pynput  # noqa: F401
        lines.append(f"frozen        : {paths.is_frozen()}")
        lines.append(f"bundle dir    : {paths.bundle_dir()}")
        lines.append(f"user data dir : {paths.user_data_dir()}")
        lines.append(f"numpy {numpy.__version__} / scipy {scipy.__version__}")
        lines.append(f"sounddevice {sounddevice.__version__} / soundfile {soundfile.__version__}")

        drivers = paths.resource_path("drivers", "vbcable")
        installer = os.path.join(drivers, "VBCABLE_Setup_x64.exe")
        lines.append(f"driver package: {os.path.isdir(drivers)}")
        lines.append(f"driver setup  : {os.path.isfile(installer)}")
        ok = ok and os.path.isdir(drivers) and os.path.isfile(installer)

        engine = BattleAudioEngine(sample_rate=48000, block_size=256)
        block = np.zeros((256, 2), dtype=np.float32)
        for effect in ("normal", "megaphone", "walkie_talkie", "panic", "custom"):
            engine.set_effect(effect)
            out = engine._apply_dsp(block.copy())
            assert out.shape == (256, 2) and np.isfinite(out).all(), effect
        lines.append("dsp engine    : 5 effects ran")

        lines.append(f"audio hostapis: {len(sounddevice.query_hostapis())}")
        lines.append(f"audio devices : {len(sounddevice.query_devices())}")

        # soundfile must be able to reach its native libsndfile
        import soundfile
        lines.append("libsndfile    : " + getattr(soundfile, "__libsndfile_version__", "unknown"))
    except Exception:
        ok = False
        lines.append("EXCEPTION:\n" + traceback.format_exc())

    report = ("PASS\n" if ok else "FAIL\n") + "\n".join(lines) + "\n"
    try:
        out_path = os.path.join(tempfile.gettempdir(), "battle_voicemod_selftest.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"selftest report written to {out_path}")
    except OSError:
        pass
    print(report)
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())

    # Bring across settings and sounds from an older "next to the app" layout
    paths.migrate_legacy_data()

    # 1. Initialize Audio Engine & Soundboard (starts clean/empty by default)
    engine = BattleAudioEngine(sample_rate=48000, block_size=256)
    soundboard = SoundboardManager(sounds_dir=paths.user_sounds_dir(), sample_rate=48000)

    # 2. Setup Global Hotkeys for quick battle switching
    hotkey_mgr = GlobalHotkeyManager()
    
    # F1 -> Normal
    # F2 -> Megafone
    # F3 -> Walkie-Talkie
    # F4 -> Pânico
    # F7 -> Parar todos os sons da Soundboard
    hotkey_mgr.register_hotkey("<f1>", lambda: engine.set_effect("normal"))
    hotkey_mgr.register_hotkey("<f2>", lambda: engine.set_effect("megaphone"))
    hotkey_mgr.register_hotkey("<f3>", lambda: engine.set_effect("walkie_talkie"))
    hotkey_mgr.register_hotkey("<f4>", lambda: engine.set_effect("panic"))
    hotkey_mgr.register_hotkey("<f7>", lambda: soundboard.stop())

    hotkey_mgr.start()

    # 3. Launch UI
    app = BattleVoiceModApp(engine=engine, soundboard=soundboard)

    try:
        app.mainloop()
    finally:
        hotkey_mgr.stop()
        engine.stop()


if __name__ == "__main__":
    main()
