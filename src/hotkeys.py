"""
Global Hotkey Manager using pynput.
Allows triggering battle voices and soundboard effects with keyboard shortcuts
even when TikTok LIVE Studio or another window is in the foreground.
"""

from typing import Callable, Dict
from pynput import keyboard


class GlobalHotkeyManager:
    def __init__(self):
        self.hotkeys: Dict[str, Callable] = {}
        self.listener = None

    def register_hotkey(self, key_combo: str, callback: Callable):
        """Registers a key combination (e.g. '<f1>', '<f2>', '<ctrl>+1')."""
        self.hotkeys[key_combo] = callback

    def start(self):
        """Starts global keyboard listening in a background thread."""
        try:
            self.listener = keyboard.GlobalHotKeys(self.hotkeys)
            self.listener.start()
        except Exception as e:
            print(f"Aviso: Não foi possível registrar atalhos globais: {e}")

    def stop(self):
        """Stops the global keyboard listener."""
        if self.listener:
            try:
                self.listener.stop()
            except Exception:
                pass
            self.listener = None
