"""
Soundboard manager for TikTok LIVE Battle VoiceMod.
Supports MP3, WAV, OGG and FLAC audio files using soundfile.
Starts clean/empty by default, allows loading optional battle sounds,
deactivating or deleting any audio, and remembers settings via JSON.
Thread-safe execution for real-time audio callback and UI access.
"""

import os
import json
import threading
import numpy as np
import soundfile as sf
from typing import Dict, Optional, Callable, List


class SoundboardManager:
    SUPPORTED_EXTENSIONS = (".mp3", ".wav", ".ogg", ".flac")

    def __init__(self, sounds_dir: str, sample_rate: int = 48000):
        self.sounds_dir = sounds_dir
        self.sample_rate = sample_rate
        self.config_path = os.path.join(os.path.dirname(sounds_dir), "soundboard_config.json")
        
        self._lock = threading.Lock()
        self.sounds: Dict[str, np.ndarray] = {}
        self.sound_enabled: Dict[str, bool] = {}  # name -> is_active
        self.currently_playing: Dict[str, int] = {}  # name -> playback position
        self.on_sound_finished: Optional[Callable[[str], None]] = None

        os.makedirs(sounds_dir, exist_ok=True)
        self._load_config()
        self.reload_sounds()

    def _load_config(self):
        """Loads enabled/disabled status from soundboard_config.json."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    with self._lock:
                        self.sound_enabled = loaded
            except Exception as e:
                print(f"Erro ao carregar soundboard_config.json: {e}")
                with self._lock:
                    self.sound_enabled = {}

    def save_config(self):
        """Saves current enabled/disabled state to disk."""
        try:
            with self._lock:
                to_save = dict(self.sound_enabled)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(to_save, f, indent=2)
        except Exception as e:
            print(f"Erro ao salvar soundboard_config.json: {e}")

    def reload_sounds(self):
        """Reloads all supported audio files (MP3, WAV, OGG, FLAC) from sounds_dir."""
        if not os.path.exists(self.sounds_dir):
            with self._lock:
                self.sounds.clear()
            return

        loaded_sounds = {}
        new_enabled_keys = []

        for f in os.listdir(self.sounds_dir):
            ext = os.path.splitext(f)[1].lower()
            if ext in self.SUPPORTED_EXTENSIONS:
                name = os.path.splitext(f)[0]
                filepath = os.path.join(self.sounds_dir, f)
                try:
                    # Read using soundfile (supports MP3, WAV, etc.)
                    data, sr = sf.read(filepath, dtype='float32')

                    # Convert mono to stereo if needed
                    if data.ndim == 1:
                        data = np.column_stack([data, data])
                    elif data.ndim == 2 and data.shape[1] > 2:
                        data = data[:, :2]

                    # Resample if sample rate doesn't match engine sample rate (48000 Hz)
                    if sr != self.sample_rate:
                        num_samples = int(len(data) * self.sample_rate / sr)
                        data = np.apply_along_axis(lambda ch: np.interp(
                            np.linspace(0, len(ch), num_samples),
                            np.arange(len(ch)),
                            ch
                        ), axis=0, arr=data).astype(np.float32)

                    loaded_sounds[name] = data
                    new_enabled_keys.append(name)
                except Exception as e:
                    print(f"Erro ao carregar áudio {f}: {e}")

        with self._lock:
            self.sounds = loaded_sounds
            for name in new_enabled_keys:
                if name not in self.sound_enabled:
                    self.sound_enabled[name] = True

        self.save_config()

    def set_sound_active(self, sound_name: str, active: bool):
        """Enables or disables a sound without deleting the file."""
        with self._lock:
            self.sound_enabled[sound_name] = active
            if not active and sound_name in self.currently_playing:
                self.currently_playing.pop(sound_name, None)
        self.save_config()

    def delete_sound(self, sound_name: str) -> bool:
        """Deletes sound file (MP3/WAV) and removes from soundboard."""
        with self._lock:
            self.currently_playing.pop(sound_name, None)
            self.sounds.pop(sound_name, None)
            self.sound_enabled.pop(sound_name, None)
        self.save_config()

        # Check all supported extensions to remove the file from disk
        for ext in self.SUPPORTED_EXTENSIONS:
            target_file = os.path.join(self.sounds_dir, f"{sound_name}{ext}")
            if os.path.exists(target_file):
                try:
                    os.remove(target_file)
                    return True
                except Exception as e:
                    print(f"Erro ao excluir arquivo {target_file}: {e}")
        return False

    def play(self, sound_name: str):
        """Starts playback if sound exists and is enabled."""
        with self._lock:
            if sound_name in self.sounds and self.sound_enabled.get(sound_name, True):
                self.currently_playing[sound_name] = 0

    def stop(self, sound_name: Optional[str] = None):
        """Stops playing sound(s)."""
        with self._lock:
            if sound_name:
                self.currently_playing.pop(sound_name, None)
            else:
                self.currently_playing.clear()

    def get_mix(self, num_samples: int, out: Optional[np.ndarray] = None) -> np.ndarray:
        """Mixes currently playing sounds safely across threads.

        Writes into `out` when provided, so the audio callback can reuse a
        preallocated buffer instead of allocating a new one every block.
        """
        if out is None or out.shape[0] < num_samples or out.shape[1] != 2:
            out = np.zeros((num_samples, 2), dtype=np.float32)
        mix = out[:num_samples]
        mix.fill(0.0)
        finished = []

        with self._lock:
            for name, pos in list(self.currently_playing.items()):
                sound_data = self.sounds.get(name)
                if sound_data is None or not self.sound_enabled.get(name, True):
                    finished.append(name)
                    continue

                available = len(sound_data) - pos
                chunk_len = min(num_samples, available)
                mix[:chunk_len] += sound_data[pos:pos + chunk_len]

                new_pos = pos + chunk_len
                if new_pos >= len(sound_data):
                    finished.append(name)
                else:
                    self.currently_playing[name] = new_pos

            for name in finished:
                self.currently_playing.pop(name, None)

        if self.on_sound_finished and finished:
            for name in finished:
                try:
                    self.on_sound_finished(name)
                except Exception:
                    pass

        np.clip(mix, -1.0, 1.0, out=mix)
        return mix

    def load_sample_sounds(self, overwrite: bool = False):
        """
        Generates the 6 optional battle sample sounds (WAV) if user requests.
        Preserves existing/edited audio files unless overwrite is True.
        """
        import scipy.io.wavfile as wavfile
        sr = self.sample_rate

        # 1. Airhorn
        airhorn_path = os.path.join(self.sounds_dir, "Airhorn.wav")
        if overwrite or not os.path.exists(airhorn_path):
            duration = 0.8
            t = np.linspace(0, duration, int(sr * duration), endpoint=False)
            freqs = [233.08, 311.13, 349.23, 466.16]
            sig = np.zeros_like(t)
            for f in freqs:
                sig += 0.25 * np.sign(np.sin(2 * np.pi * f * t))
            envelope = np.minimum(t / 0.05, 1.0) * np.maximum(0.0, 1.0 - (t / duration) ** 2)
            wavfile.write(airhorn_path, sr, (sig * envelope * 0.7 * 32767).astype(np.int16))

        # 2. Victory Fanfare
        victory_path = os.path.join(self.sounds_dir, "Victory.wav")
        if overwrite or not os.path.exists(victory_path):
            notes = [(440, 0.15), (554.37, 0.15), (659.25, 0.15), (880, 0.45)]
            sig_list = []
            for freq, dur in notes:
                t = np.linspace(0, dur, int(sr * dur), endpoint=False)
                wave = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t)
                env = np.linspace(1.0, 0.1, len(t))
                sig_list.append(wave * env)
            wavfile.write(victory_path, sr, (np.concatenate(sig_list) * 0.6 * 32767).astype(np.int16))

        # 3. Buzzer / Erro
        buzzer_path = os.path.join(self.sounds_dir, "Buzzer_Erro.wav")
        if overwrite or not os.path.exists(buzzer_path):
            duration = 0.6
            t = np.linspace(0, duration, int(sr * duration), endpoint=False)
            f = 130.0
            sig = 0.6 * np.sign(np.sin(2 * np.pi * f * t)) + 0.3 * np.sin(2 * np.pi * (f * 1.41) * t)
            env = np.maximum(0.0, 1.0 - t / duration)
            wavfile.write(buzzer_path, sr, (sig * env * 0.7 * 32767).astype(np.int16))

        # 4. 8-Bit Coin
        coin_path = os.path.join(self.sounds_dir, "Coin_Presente.wav")
        if overwrite or not os.path.exists(coin_path):
            t1 = np.linspace(0, 0.08, int(sr * 0.08), endpoint=False)
            t2 = np.linspace(0, 0.35, int(sr * 0.35), endpoint=False)
            w1 = np.sign(np.sin(2 * np.pi * 987.77 * t1))
            w2 = np.sign(np.sin(2 * np.pi * 1318.51 * t2))
            sig = np.concatenate([w1 * 0.5, w2 * np.linspace(1.0, 0.0, len(t2)) * 0.5])
            wavfile.write(coin_path, sr, (sig * 32767).astype(np.int16))

        # 5. Risada
        laugh_path = os.path.join(self.sounds_dir, "Risada.wav")
        if overwrite or not os.path.exists(laugh_path):
            bursts = []
            for i in range(5):
                dur = 0.12
                t = np.linspace(0, dur, int(sr * dur), endpoint=False)
                f = 350.0 - i * 20.0
                wave = np.sin(2 * np.pi * f * t) * (1.0 - t / dur)
                bursts.append(wave)
                bursts.append(np.zeros(int(sr * 0.05)))
            wavfile.write(laugh_path, sr, (np.concatenate(bursts) * 0.6 * 32767).astype(np.int16))

        # 6. Impacto Batalha
        impact_path = os.path.join(self.sounds_dir, "Impacto_Batalha.wav")
        if overwrite or not os.path.exists(impact_path):
            duration = 1.0
            t = np.linspace(0, duration, int(sr * duration), endpoint=False)
            pitch_env = 180.0 * np.exp(-t * 12.0) + 45.0
            phase = np.cumsum(2 * np.pi * pitch_env / sr)
            drum = np.sin(phase)
            noise = np.random.uniform(-1, 1, len(t)) * np.exp(-t * 18.0)
            sig = (drum * 0.7 + noise * 0.3) * np.exp(-t * 4.0)
            wavfile.write(impact_path, sr, (sig * 0.8 * 32767).astype(np.int16))

        self.reload_sounds()
