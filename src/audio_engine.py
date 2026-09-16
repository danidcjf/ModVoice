"""
Core Real-Time Audio Engine for TikTok LIVE Battle VoiceMod.
Captures physical microphone with low latency, applies the battle voice effects
(megaphone, walkie-talkie, panic and a manual pitch control), mixes soundboard
audio, and routes to Virtual Mic (for TikTok) + Headphones (for monitoring).

Decoupled stream architecture:
- InputStream: captures from physical microphone.
- OutputStream (Virtual): broadcasts to TikTok LIVE Studio via Virtual Cable.
- OutputStream (Monitor): plays back locally to headphones without blocking.
- Inter-stream ring buffers eliminate cross-HostAPI incompatibilities and clock drift.
"""

import time
import numpy as np
import scipy.signal as signal
import sounddevice as sd
from typing import Optional, List, Dict, Tuple

from .dsp_chain import StudioChain, _peaking_eq


class FastAudioRingBuffer:
    """
    Lock-free single-producer / single-consumer circular buffer for passing audio
    between two real-time streams without ever blocking a callback.

    Only one thread may call `write()` and only one (different) thread may call
    `read()`. The producer never touches the consumer index and vice versa, so no
    mutex is needed: each index update is a single integer store, which is atomic
    under CPython. The producer publishes the new write index only after the data
    copy has completed, so the reader can never observe a half-written block.

    Latency is bounded on the reader side: if the producer has run ahead by more
    than `max_buffered` samples, the reader discards the excess before reading.
    Doing this on the read side (instead of the producer overwriting unread audio)
    is what keeps the buffer safe for a single producer.
    """
    def __init__(self, capacity: int = 8192, channels: int = 2, max_buffered: int = 1536):
        self.capacity = int(capacity)
        self.channels = int(channels)
        self.max_buffered = int(max_buffered)  # ~32ms at 48kHz
        self.buffer = np.zeros((self.capacity, self.channels), dtype=np.float32)
        # Monotonically increasing counters (never wrapped): the buffer position is
        # `pos % capacity`. Keeping them monotonic makes "full" distinguishable from
        # "empty", which a modulo index cannot express without a shared size counter.
        self._write_pos = 0   # written by the producer only
        self._read_pos = 0    # written by the consumer only
        self.overflows = 0    # blocks discarded because the consumer stalled
        self.dropped = 0      # samples discarded by the reader to bound latency

    @property
    def size(self) -> int:
        return self._write_pos - self._read_pos

    def write(self, data: np.ndarray):
        n = len(data)
        if n == 0:
            return
        if n > self.capacity:
            data = data[-self.capacity:]
            n = self.capacity

        # Never overwrite audio the consumer has not read yet: that would require
        # touching the consumer index and break the single-producer contract.
        if n > self.capacity - self.size:
            self.overflows += 1
            return

        w = self._write_pos
        start = w % self.capacity
        first = min(n, self.capacity - start)
        self.buffer[start:start + first] = data[:first]
        if n > first:
            self.buffer[0:n - first] = data[first:]
        self._write_pos = w + n

    def read(self, num_samples: int, out: Optional[np.ndarray] = None) -> np.ndarray:
        if out is None or out.shape[0] < num_samples:
            out = np.zeros((num_samples, self.channels), dtype=np.float32)
        else:
            out = out[:num_samples]

        size = self.size
        if size < 0:
            # A concurrent clear() moved the consumer index past the producer.
            self._read_pos = self._write_pos
            size = 0
        if size > self.max_buffered:
            skip = size - self.max_buffered
            self._read_pos += skip
            self.dropped += skip
            size = self.max_buffered

        avail = min(num_samples, size)
        if avail == 0:
            out.fill(0.0)
            return out

        r = self._read_pos
        start = r % self.capacity
        first = min(avail, self.capacity - start)
        out[:first] = self.buffer[start:start + first]
        if avail > first:
            out[first:avail] = self.buffer[0:avail - first]
        if avail < num_samples:
            out[avail:].fill(0.0)
        self._read_pos = r + avail
        return out

    def clear(self):
        # Discards everything without ever moving an index backwards, so a racing
        # reader can at worst see a smaller size — never stale memory.
        self._read_pos = self._write_pos


class VectorizedPitchShifter:
    """
    Dual read-head circular buffer pitch shifter fully vectorized in NumPy.
    Processes entire audio blocks with fractional linear interpolation and
    triangular crossfade weighting without per-sample Python loops.

    Every scratch array is preallocated, so a steady-state `process()` call only
    allocates the small fancy-index gathers. The returned array is an internal
    buffer owned by this instance and is valid only until the next `process()`.
    """
    def __init__(self, sample_rate: int = 48000, buf_size: int = 4096, max_block: int = 1024):
        self.sample_rate = sample_rate
        self.buf_size = buf_size
        self.buffer = np.zeros(buf_size, dtype=np.float32)
        self.in_ptr = 0
        self.out_ptr1 = 0.0
        self.out_ptr2 = float(buf_size // 2)
        self._max_block = 0
        self._alloc_scratch(max_block)

    def _alloc_scratch(self, n: int):
        self._max_block = int(n)
        self._arange_f = np.arange(self._max_block, dtype=np.float64)
        self._arange_i = np.arange(self._max_block, dtype=np.intp)
        self._op1 = np.empty(self._max_block, dtype=np.float64)
        self._op2 = np.empty(self._max_block, dtype=np.float64)
        self._wi = np.empty(self._max_block, dtype=np.intp)
        self._i1 = np.empty(self._max_block, dtype=np.intp)
        self._i1n = np.empty(self._max_block, dtype=np.intp)
        self._i2 = np.empty(self._max_block, dtype=np.intp)
        self._i2n = np.empty(self._max_block, dtype=np.intp)
        self._f1 = np.empty(self._max_block, dtype=np.float32)
        self._f2 = np.empty(self._max_block, dtype=np.float32)
        self._t = np.empty(self._max_block, dtype=np.float32)
        self._s1 = np.empty(self._max_block, dtype=np.float32)
        self._s2 = np.empty(self._max_block, dtype=np.float32)
        self._d = np.empty(self._max_block, dtype=np.float64)
        self._u = np.empty(self._max_block, dtype=np.float64)
        self._res = np.empty(self._max_block, dtype=np.float64)
        self._out = np.zeros((self._max_block, 2), dtype=np.float32)

    def reset(self):
        self.buffer.fill(0)
        self.in_ptr = 0
        self.out_ptr1 = 0.0
        self.out_ptr2 = float(self.buf_size // 2)

    def process(self, mono_in: np.ndarray, semitones: float) -> np.ndarray:
        n = len(mono_in)
        if n == 0:
            return self._out[:0]
        if n > self._max_block:
            self._alloc_scratch(n)

        rate = 2.0 ** (semitones / 12.0)
        w_size = self.buf_size // 2
        half_w = w_size / 2.0

        # 1. Vectorized circular write
        np.add(self._arange_i[:n], self.in_ptr, out=self._wi[:n])
        np.mod(self._wi[:n], self.buf_size, out=self._wi[:n])
        self.buffer[self._wi[:n]] = mono_in
        self.in_ptr = (self.in_ptr + n) % self.buf_size

        # 2. Vectorized read pointers
        np.multiply(self._arange_f[:n], rate, out=self._op1[:n])
        np.add(self._op1[:n], self.out_ptr1, out=self._op1[:n])
        np.mod(self._op1[:n], self.buf_size, out=self._op1[:n])

        np.multiply(self._arange_f[:n], rate, out=self._op2[:n])
        np.add(self._op2[:n], self.out_ptr2, out=self._op2[:n])
        np.mod(self._op2[:n], self.buf_size, out=self._op2[:n])

        self.out_ptr1 = (self.out_ptr1 + n * rate) % self.buf_size
        self.out_ptr2 = (self.out_ptr2 + n * rate) % self.buf_size

        # 3. Fractional linear interpolation for Head 1
        self._i1[:n] = self._op1[:n]
        np.add(self._i1[:n], 1, out=self._i1n[:n])
        np.mod(self._i1n[:n], self.buf_size, out=self._i1n[:n])
        np.subtract(self._op1[:n], self._i1[:n], out=self._f1[:n])

        np.subtract(1.0, self._f1[:n], out=self._t[:n])
        np.multiply(self._t[:n], self.buffer[self._i1[:n]], out=self._s1[:n])
        np.multiply(self._f1[:n], self.buffer[self._i1n[:n]], out=self._t[:n])
        np.add(self._s1[:n], self._t[:n], out=self._s1[:n])

        # 4. Fractional linear interpolation for Head 2
        self._i2[:n] = self._op2[:n]
        np.add(self._i2[:n], 1, out=self._i2n[:n])
        np.mod(self._i2n[:n], self.buf_size, out=self._i2n[:n])
        np.subtract(self._op2[:n], self._i2[:n], out=self._f2[:n])

        np.subtract(1.0, self._f2[:n], out=self._t[:n])
        np.multiply(self._t[:n], self.buffer[self._i2[:n]], out=self._s2[:n])
        np.multiply(self._f2[:n], self.buffer[self._i2n[:n]], out=self._t[:n])
        np.add(self._s2[:n], self._t[:n], out=self._s2[:n])

        # 5. Triangular crossfade weighting
        np.subtract(self._op1[:n], self._wi[:n], out=self._d[:n])
        np.mod(self._d[:n], w_size, out=self._d[:n])
        np.subtract(self._d[:n], half_w, out=self._d[:n])
        np.abs(self._d[:n], out=self._d[:n])
        np.divide(self._d[:n], half_w, out=self._d[:n])
        np.subtract(1.0, self._d[:n], out=self._d[:n])
        np.clip(self._d[:n], 0.0, 1.0, out=self._d[:n])

        np.multiply(self._s1[:n], self._d[:n], out=self._res[:n])
        np.subtract(1.0, self._d[:n], out=self._u[:n])
        np.multiply(self._u[:n], self._s2[:n], out=self._u[:n])
        np.add(self._res[:n], self._u[:n], out=self._res[:n])

        self._out[:n, 0] = self._res[:n]
        self._out[:n, 1] = self._res[:n]
        return self._out[:n]


class BattleAudioEngine:
    def __init__(self, sample_rate: int = 48000, block_size: int = 256,
                 latency: Optional[str] = 'low'):
        self.sample_rate = sample_rate
        self.block_size = block_size
        # PortAudio latency hint. 'low' cuts round-trip latency substantially;
        # streams fall back to the device default if a driver rejects it.
        self.latency = latency

        # Audio Devices
        self.input_device_id: Optional[int] = None
        self.virtual_output_id: Optional[int] = None
        self.monitor_output_id: Optional[int] = None
        self.in_channels = 2
        self.virt_channels = 2
        self.mon_channels = 2

        # Audio Stream states (Decoupled architecture)
        self.is_running = False
        self.input_stream: Optional[sd.InputStream] = None
        self.virtual_stream: Optional[sd.OutputStream] = None
        self.monitor_stream: Optional[sd.OutputStream] = None
        self.stream: Optional[sd.OutputStream] = None  # Backward-compatibility alias

        # Callback status counters (overflow / underflow), never printed from the
        # audio thread — the UI reads these to surface dropouts to the user.
        self.stream_errors: Dict[str, int] = {
            'input_overflow': 0,
            'input_underflow': 0,
            'output_overflow': 0,
            'output_underflow': 0,
        }

        # Decoupled Ring Buffers
        self.voice_ring_buffer = FastAudioRingBuffer(capacity=8192, channels=2, max_buffered=1536)
        self.monitor_ring_buffer = FastAudioRingBuffer(capacity=8192, channels=2, max_buffered=1536)

        # Preallocated per-block scratch, so no callback allocates in steady state.
        self._buf_frames = 0
        self._rng = np.random.default_rng()
        self._ensure_block_buffers(block_size)

        # Controls
        self.mic_volume = 1.0
        self.effect_volume = 1.0
        self.soundboard_volume = 1.0
        self.monitor_volume = 0.8
        self.hear_myself = True
        self.is_muted = False
        
        # Active Voice Effect
        self.active_effect = 'normal'
        self.custom_pitch_semitones = 0.0
        
        # External soundboard mixer hook
        self.soundboard_provider = None
        
        # Internal Metering (peak level [0.0 - 1.0])
        self.current_input_level = 0.0
        self.current_output_level = 0.0
        
        # DSP Buffers & State
        self._init_dsp()

    def _ensure_block_buffers(self, frames: int):
        """Preallocates every per-block scratch buffer sized to the callback block."""
        if frames <= self._buf_frames:
            return
        self._buf_frames = int(frames)
        self._mic_buf = np.zeros((frames, 2), dtype=np.float32)
        self._mono_buf = np.zeros(frames, dtype=np.float32)
        self._silence_buf = np.zeros((frames, 2), dtype=np.float32)
        self._voice_read_buf = np.zeros((frames, 2), dtype=np.float32)
        self._monitor_read_buf = np.zeros((frames, 2), dtype=np.float32)
        self._sb_buf = np.zeros((frames, 2), dtype=np.float32)
        self._mix_buf = np.zeros((frames, 2), dtype=np.float32)
        self._monitor_mix_buf = np.zeros((frames, 2), dtype=np.float32)
        # Metering scratch is per stream thread: input and output callbacks run
        # concurrently, so they must never share a scratch buffer.
        self._static_buf = np.empty((frames, 2), dtype=np.float32)
        self._meter_buf = np.zeros((frames, 2), dtype=np.float32)
        # Walkie-talkie radio noise scratch. These are float64 because
        # numpy's Generator.random(out=...) only writes float64.
        self._n_range = np.arange(frames, dtype=np.float64)
        self._lfo_buf = np.empty(frames, dtype=np.float64)
        self._rand_col = np.empty((frames, 1), dtype=np.float64)
        self._mask_col = np.zeros((frames, 1), dtype=bool)
        self._noise_f64 = np.empty((frames, 2), dtype=np.float64)
        self._hum_f64 = np.empty((frames, 1), dtype=np.float64)
        self._crackle_f64 = np.empty((frames, 1), dtype=np.float64)
        self._static_f64 = np.empty((frames, 2), dtype=np.float64)
        self._wt_out_buf = np.empty((frames, 2), dtype=np.float32)

    def _note_status(self, status):
        """Records PortAudio callback flags; safe to call from the audio thread."""
        if status is None:
            return
        if getattr(status, 'input_overflow', False):
            self.stream_errors['input_overflow'] += 1
        if getattr(status, 'input_underflow', False):
            self.stream_errors['input_underflow'] += 1
        if getattr(status, 'output_overflow', False):
            self.stream_errors['output_overflow'] += 1
        if getattr(status, 'output_underflow', False):
            self.stream_errors['output_underflow'] += 1

    def get_stream_errors(self) -> Dict[str, int]:
        """Returns a snapshot of the callback error counters."""
        return dict(self.stream_errors)

    def reset_stream_errors(self):
        for key in self.stream_errors:
            self.stream_errors[key] = 0

    def _open_stream(self, factory, **kwargs):
        """Opens a stream with the configured latency, falling back to the device default."""
        if self.latency is not None:
            try:
                return factory(latency=self.latency, **kwargs)
            except Exception:
                pass
        return factory(**kwargs)

    def _to_mono(self, audio: np.ndarray, n: int) -> np.ndarray:
        """Downmixes a stereo block into the preallocated mono scratch buffer."""
        if n > self._buf_frames:
            self._ensure_block_buffers(n)
        mono = self._mono_buf[:n]
        if audio.shape[1] == 1:
            mono[:] = audio[:, 0]
        else:
            np.add(audio[:, 0], audio[:, 1], out=mono)
            mono *= 0.5
        return mono

    def _init_dsp(self):
        sr = self.sample_rate
        nyq = sr / 2.0
        
        # Megaphone: band-pass 500 Hz - 4 kHz plus the horn's standing-wave
        # resonance around 2 kHz. The low-pass is applied AFTER the saturation
        # (see _apply_dsp): the clipper generates new harmonics above the pass
        # band, so filtering last is what keeps the result from turning fizzy.
        self.b_mega_hp, self.a_mega_hp = signal.butter(4, 500.0 / nyq, btype='high')
        self.base_zi_mega_hp = np.zeros((len(self.a_mega_hp) - 1, 2), dtype=np.float64)
        self.mega_hp_zi = self.base_zi_mega_hp.copy()

        self.b_mega_honk, self.a_mega_honk = signal.sos2tf(_peaking_eq(2000.0, 6.0, 2.5, sr))
        self.base_zi_mega_honk = np.zeros((len(self.a_mega_honk) - 1, 2), dtype=np.float64)
        self.mega_honk_zi = self.base_zi_mega_honk.copy()

        self.b_mega_lp, self.a_mega_lp = signal.butter(4, 4000.0 / nyq, btype='low')
        self.base_zi_mega_lp = np.zeros((len(self.a_mega_lp) - 1, 2), dtype=np.float64)
        self.mega_lp_zi = self.base_zi_mega_lp.copy()

        # Walkie-Talkie bandpass (400Hz - 2600Hz), RF dynamic noise & Roger Beep + Squelch Tail
        self.b_wt, self.a_wt = signal.butter(4, [400.0 / nyq, 2600.0 / nyq], btype='band')
        self.base_zi_wt = np.zeros((len(self.a_wt) - 1, 2), dtype=np.float64)
        self.wt_zi = self.base_zi_wt.copy()
        self.wt_hum_phase = 0.0
        self.wt_talking = False
        self.wt_silence_frames = 0
        
        # Pre-synthesize authentic dual-tone Roger Beep (1050Hz + 1400Hz) and squelch tail burst
        t_b1 = np.linspace(0, 0.035, int(sr * 0.035), endpoint=False)
        w_b1 = np.sin(np.pi * t_b1 / 0.035)
        b1 = (np.sin(2.0 * np.pi * 1050.0 * t_b1) * w_b1 * 0.18).astype(np.float32)

        t_b2 = np.linspace(0, 0.035, int(sr * 0.035), endpoint=False)
        w_b2 = np.sin(np.pi * t_b2 / 0.035)
        b2 = (np.sin(2.0 * np.pi * 1400.0 * t_b2) * w_b2 * 0.18).astype(np.float32)

        n_sq = int(sr * 0.05)
        t_sq = np.linspace(0, 1.0, n_sq)
        sq_env = np.exp(-6.0 * t_sq).astype(np.float32)
        noise_sq = np.random.uniform(-0.16, 0.16, n_sq).astype(np.float32) * sq_env
        total_roger = np.concatenate([b1, b2, noise_sq])
        self.wt_roger_beep = np.column_stack([total_roger, total_roger]).astype(np.float32)
        self.wt_roger_playback_idx = -1

        # Panic / Fear Tremor (6.2 Hz LFO pitch and tremolo)
        self.panic_lfo_phase = 0.0
        self.ps_panic = VectorizedPitchShifter(sr)

        # Manual pitch control ("Tom"), driven by the UI slider
        self.ps_custom = VectorizedPitchShifter(sr)

        # Global studio conditioning (gate, high-pass, EQ, compressor) + output limiter
        self.studio_chain = StudioChain(sr)

    def set_effect(self, effect_name: str, pitch_semitones: float = 0.0):
        """Changes active battle voice effect."""
        if effect_name != self.active_effect:
            self._reset_effect_state()
        self.active_effect = effect_name
        self.custom_pitch_semitones = pitch_semitones

    def _reset_effect_state(self):
        """Clears delay/reverb tails and filter states so switching effects never leaks the previous one."""
        self.mega_hp_zi = self.base_zi_mega_hp.copy()
        self.mega_honk_zi = self.base_zi_mega_honk.copy()
        self.mega_lp_zi = self.base_zi_mega_lp.copy()

        self.wt_zi = self.base_zi_wt.copy()
        self.wt_talking = False
        self.wt_silence_frames = 0
        self.wt_roger_playback_idx = -1

        self.panic_lfo_phase = 0.0
        self.ps_panic.reset()

        self.ps_custom.reset()

    def set_soundboard_provider(self, provider):
        """Sets soundboard manager that provides get_mix(num_samples)."""
        self.soundboard_provider = provider

    def _get_extra_settings(self, device_id: Optional[int]):
        """Returns WasapiSettings with auto_convert=True if device is on Windows WASAPI."""
        if device_id is None:
            return None
        try:
            dev = sd.query_devices(device_id)
            api = sd.query_hostapis(dev['hostapi'])
            if 'wasapi' in api['name'].lower():
                return sd.WasapiSettings(auto_convert=True)
        except Exception:
            pass
        return None

    def get_devices(self, include_all_apis: bool = False) -> Tuple[List[Dict], List[Dict]]:
        """
        Returns lists of available input and output devices.
        By default on Windows, prioritizes Windows WASAPI for lowest latency,
        clean full device names, native 48kHz, and no duplicates.
        Validates each device with an active probe to filter out invalid endpoints (e.g. -9996).
        Falls back to other APIs if WASAPI is unavailable or if include_all_apis is True.
        """
        input_devs = []
        output_devs = []
        try:
            all_devices = list(sd.query_devices())
            host_apis = list(sd.query_hostapis())

            wasapi_idx = None
            for idx, api in enumerate(host_apis):
                if 'wasapi' in api['name'].lower():
                    wasapi_idx = idx
                    break

            use_wasapi_only = (not include_all_apis) and (wasapi_idx is not None)
            if use_wasapi_only:
                has_wasapi_in = any(d['hostapi'] == wasapi_idx and d['max_input_channels'] > 0 for d in all_devices)
                has_wasapi_out = any(d['hostapi'] == wasapi_idx and d['max_output_channels'] > 0 for d in all_devices)
                if not (has_wasapi_in and has_wasapi_out):
                    use_wasapi_only = False

            for i, d in enumerate(all_devices):
                api_info = host_apis[d['hostapi']]
                api_name = api_info['name']

                # Filter out WDM-KS by default unless include_all_apis
                if not include_all_apis and 'wdm-ks' in api_name.lower():
                    continue

                if use_wasapi_only and d['hostapi'] != wasapi_idx:
                    continue

                # Filter out Windows generic mappers by default
                if not include_all_apis and ("mapeador de som" in d['name'].lower() or "driver de som prim" in d['name'].lower() or "driver de captura" in d['name'].lower()):
                    continue

                display_name = d['name'] if use_wasapi_only else f"{d['name']} ({api_name})"
                extra = self._get_extra_settings(i)

                # Validate and probe input device
                if d['max_input_channels'] > 0:
                    try:
                        ch = min(2, d['max_input_channels'])
                        s = sd.InputStream(
                            device=i,
                            samplerate=self.sample_rate,
                            blocksize=self.block_size,
                            channels=ch,
                            extra_settings=extra
                        )
                        s.close()
                        input_devs.append({
                            'id': i,
                            'name': display_name,
                            'raw_name': d['name'],
                            'api': api_name,
                            'channels': d['max_input_channels'],
                            'default_samplerate': d['default_samplerate']
                        })
                    except Exception:
                        pass

                # Validate and probe output device
                if d['max_output_channels'] > 0:
                    try:
                        ch = min(2, d['max_output_channels'])
                        s = sd.OutputStream(
                            device=i,
                            samplerate=self.sample_rate,
                            blocksize=self.block_size,
                            channels=ch,
                            extra_settings=extra
                        )
                        s.close()
                        output_devs.append({
                            'id': i,
                            'name': display_name,
                            'raw_name': d['name'],
                            'api': api_name,
                            'channels': d['max_output_channels'],
                            'default_samplerate': d['default_samplerate']
                        })
                    except Exception:
                        pass
        except Exception as e:
            print(f"Erro ao listar dispositivos: {e}")
        return input_devs, output_devs

    def _apply_dsp(self, audio: np.ndarray) -> np.ndarray:
        """Applies real-time voice modification fully vectorized in NumPy."""
        if self.active_effect == 'normal':
            return audio

        eff = self.active_effect
        n = len(audio)

        if eff == 'megaphone':
            # 1. High-pass at 500 Hz: a bullhorn driver cannot move enough air below
            #    this, so chest warmth disappears and the voice turns thin and forward.
            filtered, self.mega_hp_zi = signal.lfilter(
                self.b_mega_hp, self.a_mega_hp, audio, axis=0, zi=self.mega_hp_zi)

            # 2. The honk: the horn's standing-wave resonance around 2 kHz. This is the
            #    single feature that separates a megaphone from a plain telephone filter.
            honky, self.mega_honk_zi = signal.lfilter(
                self.b_mega_honk, self.a_mega_honk, filtered, axis=0, zi=self.mega_honk_zi)

            # 3. Underpowered horn driver: soft clipping adds the harmonics that make it
            #    sound driven rather than merely narrow-band, while staying intelligible.
            #    +12 dB into the saturator, mid-range of the usual 8-14 dB for a bullhorn.
            driven = np.tanh(honky * 4.0) * 0.85

            # 4. Low-pass at 4 kHz AFTER the clipper. The saturation generates new
            #    harmonics above the pass band, so filtering last is what keeps the
            #    result clean instead of fizzy.
            out, self.mega_lp_zi = signal.lfilter(
                self.b_mega_lp, self.a_mega_lp, driven, axis=0, zi=self.mega_lp_zi)

            return np.clip(out * 0.95, -1.0, 1.0).astype(np.float32)

        elif eff == 'walkie_talkie':
            # 1. Narrow transceiver bandpass (400Hz - 2600Hz)
            filtered, self.wt_zi = signal.lfilter(self.b_wt, self.a_wt, audio, axis=0, zi=self.wt_zi)
            
            # 2. Speaker saturation
            saturated = np.tanh(filtered * 3.8) * 0.86

            # 3. Dynamic radio static, speech detection & Roger Beep + Squelch
            voice_energy = float(np.max(np.abs(filtered)))
            if voice_energy > 0.02:
                self.wt_talking = True
                self.wt_silence_frames = 0
            elif self.wt_talking:
                self.wt_silence_frames += 1
                # If silent for ~130ms (about 25 frames of 256 samples), trigger Roger Beep!
                if self.wt_silence_frames >= 25:
                    self.wt_talking = False
                    self.wt_silence_frames = 0
                    self.wt_roger_playback_idx = 0

            if self.wt_talking:
                t = self._lfo_buf[:n]
                np.add(self._n_range[:n], self.wt_hum_phase, out=t)
                np.multiply(t, 1.0 / self.sample_rate, out=t)
                self.wt_hum_phase = (self.wt_hum_phase + n) % self.sample_rate

                hum = self._hum_f64[:n]
                np.multiply(t, 2.0 * np.pi * 120.0, out=t)
                np.sin(t, out=hum[:, 0])
                hum *= 0.02

                noise = self._noise_f64[:n]
                self._rng.random(out=noise)
                noise *= 0.09
                noise -= 0.045

                self._rng.random(out=self._rand_col[:n])
                np.greater(self._rand_col[:n], 0.982, out=self._mask_col[:n])
                np.multiply(self._rand_col[:n], 0.32, out=self._rand_col[:n])
                np.subtract(self._rand_col[:n], 0.16, out=self._rand_col[:n])
                np.multiply(self._mask_col[:n], self._rand_col[:n], out=self._crackle_f64[:n])

                blend = self._static_f64[:n]
                np.add(noise, self._crackle_f64[:n], out=blend)
                np.add(blend, hum, out=blend)
                blend *= min(voice_energy * 3.2, 1.0)
                out = self._wt_out_buf[:n]
                np.add(saturated, blend, out=out)
            else:
                out = self._wt_out_buf[:n]
                out[:] = saturated

            # Overlay Roger Beep / Squelch Tail if active
            if self.wt_roger_playback_idx >= 0:
                rem = len(self.wt_roger_beep) - self.wt_roger_playback_idx
                take = min(n, rem)
                out[:take] += self.wt_roger_beep[self.wt_roger_playback_idx:self.wt_roger_playback_idx + take]
                self.wt_roger_playback_idx += take
                if self.wt_roger_playback_idx >= len(self.wt_roger_beep):
                    self.wt_roger_playback_idx = -1

            np.clip(out, -1.0, 1.0, out=out)
            return out

        elif eff == 'panic':
            # 1. Panic/fear tremor LFO (6.2 Hz)
            t = (self.panic_lfo_phase + np.arange(n)) / self.sample_rate
            self.panic_lfo_phase = (self.panic_lfo_phase + n) % self.sample_rate

            mid_t = t[n // 2]
            pitch_mod = float(1.15 * np.sin(2.0 * np.pi * 6.2 * mid_t))

            mono_in = self._to_mono(audio, n)
            ps_out = self.ps_panic.process(mono_in, semitones=pitch_mod)

            # 2. Synchronized amplitude trembling
            tremolo = 1.0 - 0.22 * (np.sin(2.0 * np.pi * 6.2 * t)[:, np.newaxis] + 1.0) * 0.5
            out = ps_out * tremolo.astype(np.float32)

            return np.clip(out * 1.12, -1.0, 1.0).astype(np.float32)

        elif eff == 'custom':
            mono_in = self._to_mono(audio, n)
            return self.ps_custom.process(
                mono_in, semitones=self.custom_pitch_semitones).astype(np.float32)

        return audio

    def _input_callback(self, indata, frames, time_info, status):
        """Microphone capture callback: gains, studio processing, DSP and buffering."""
        self._note_status(status)

        if frames > self._buf_frames:
            self._ensure_block_buffers(frames)

        if self.is_muted:
            self.current_input_level = 0.0
            # Buffer silence so timing stays continuous
            self.voice_ring_buffer.write(self._silence_buf[:frames])
            return

        # 1. Convert input to stereo float32 (handles mono mic seamlessly)
        mic_stereo = self._mic_buf[:frames]
        if indata.shape[1] == 1:
            mic_stereo[:, 0] = indata[:, 0]
            mic_stereo[:, 1] = indata[:, 0]
        else:
            np.copyto(mic_stereo, indata[:, :2])

        # Input level metering (peak), computed without allocating
        np.abs(mic_stereo, out=self._static_buf[:frames])
        self.current_input_level = float(self._static_buf[:frames].max())

        # Apply mic gain
        mic_stereo *= self.mic_volume

        # 2. Studio conditioning (noise gate -> high-pass -> EQ -> compressor)
        mic_stereo = self.studio_chain.process_input(mic_stereo)

        # 3. Apply Battle DSP Effect (fully vectorized)
        fx_voice = self._apply_dsp(mic_stereo)
        fx_voice *= self.effect_volume

        # Route to voice ring buffer for the virtual output stream
        self.voice_ring_buffer.write(fx_voice)

    def _virtual_output_callback(self, outdata, frames, time_info, status):
        """Virtual output broadcast callback: reads voice, mixes soundboard, limiter, routes to TikTok."""
        self._note_status(status)

        if frames > self._buf_frames:
            self._ensure_block_buffers(frames)

        # 1. Read processed voice from ring buffer
        fx_voice = self.voice_ring_buffer.read(frames, out=self._voice_read_buf)

        # 2. Mix Soundboard audio
        sb_mix = self._sb_buf[:frames]
        sb_mix.fill(0.0)
        if self.soundboard_provider:
            self.soundboard_provider.get_mix(frames, out=sb_mix)
            sb_mix *= self.soundboard_volume

        # 3. Combined broadcast audio + output limiter
        combined = self._mix_buf[:frames]
        np.add(fx_voice, sb_mix, out=combined)
        np.clip(combined, -1.0, 1.0, out=combined)
        final_broadcast = self.studio_chain.process_output(combined)

        # 4. Output to virtual device (stereo or mono)
        if outdata.shape[1] == 1:
            outdata[:, 0] = (final_broadcast[:, 0] + final_broadcast[:, 1]) * 0.5
        else:
            outdata[:] = final_broadcast

        np.abs(final_broadcast, out=self._meter_buf[:frames])
        self.current_output_level = float(self._meter_buf[:frames].max())

        # 5. Route to Monitor Ring Buffer (for headphones)
        if self.monitor_stream and self.monitor_stream.active:
            monitor = self._monitor_mix_buf[:frames]
            if self.hear_myself:
                np.add(fx_voice, sb_mix, out=monitor)
            else:
                monitor[:] = sb_mix
            np.clip(monitor, -1.0, 1.0, out=monitor)
            self.monitor_ring_buffer.write(monitor)

    def set_hear_myself(self, enabled: bool):
        """Toggles real-time microphone return in headphones."""
        self.hear_myself = enabled

    def _monitor_callback(self, outdata, frames, time_info, status):
        """Asynchronous callback for Headphone Monitoring without blocking main audio."""
        self._note_status(status)

        if frames > self._buf_frames:
            self._ensure_block_buffers(frames)

        scaled = self.monitor_ring_buffer.read(frames, out=self._monitor_read_buf)
        scaled *= self.monitor_volume

        if outdata.shape[1] == 1:
            outdata[:, 0] = (scaled[:, 0] + scaled[:, 1]) * 0.5
        else:
            outdata[:] = scaled

    def start(self, input_id: int, virtual_output_id: int, monitor_output_id: Optional[int] = None):
        """Starts real-time decoupled audio pipeline with dynamic channel configuration."""
        self.stop()
        self.input_device_id = input_id
        self.virtual_output_id = virtual_output_id
        self.monitor_output_id = monitor_output_id

        self.voice_ring_buffer.clear()
        self.monitor_ring_buffer.clear()

        # Query input device
        in_dev = sd.query_devices(input_id)
        in_channels = min(2, max(1, in_dev.get('max_input_channels', 1)))
        self.in_channels = in_channels

        # Query virtual output device
        virt_dev = sd.query_devices(virtual_output_id)
        virt_channels = min(2, max(1, virt_dev.get('max_output_channels', 2)))
        self.virt_channels = virt_channels

        # 1. Setup Virtual Output Stream (TikTok LIVE Studio)
        virt_extra = self._get_extra_settings(virtual_output_id)
        self.virtual_stream = self._open_stream(
            sd.OutputStream,
            device=virtual_output_id,
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            channels=virt_channels,
            dtype='float32',
            callback=self._virtual_output_callback,
            extra_settings=virt_extra
        )
        self.virtual_stream.start()

        # 2. Setup Input Stream (Microphone)
        in_extra = self._get_extra_settings(input_id)
        self.input_stream = self._open_stream(
            sd.InputStream,
            device=input_id,
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            channels=in_channels,
            dtype='float32',
            callback=self._input_callback,
            extra_settings=in_extra
        )
        self.input_stream.start()

        # Backward compatibility alias
        self.stream = self.virtual_stream

        # 3. Setup Monitoring Stream (Headphones)
        if monitor_output_id is not None and monitor_output_id != virtual_output_id:
            try:
                mon_dev = sd.query_devices(monitor_output_id)
                mon_channels = min(2, max(1, mon_dev.get('max_output_channels', 2)))
                self.mon_channels = mon_channels
                self.monitor_ring_buffer.clear()

                mon_extra = self._get_extra_settings(monitor_output_id)
                self.monitor_stream = self._open_stream(
                    sd.OutputStream,
                    device=monitor_output_id,
                    samplerate=self.sample_rate,
                    blocksize=self.block_size,
                    channels=mon_channels,
                    dtype='float32',
                    callback=self._monitor_callback,
                    extra_settings=mon_extra
                )
                self.monitor_stream.start()
            except Exception as e:
                print(f"Erro ao iniciar monitoramento em fones: {e}")
                self.monitor_stream = None

        self.is_running = True

    def stop(self):
        """Stops all running audio streams."""
        self.is_running = False
        if self.input_stream:
            try:
                self.input_stream.stop()
                self.input_stream.close()
            except Exception:
                pass
            self.input_stream = None

        if self.virtual_stream:
            try:
                self.virtual_stream.stop()
                self.virtual_stream.close()
            except Exception:
                pass
            self.virtual_stream = None
        self.stream = None

        if self.monitor_stream:
            try:
                self.monitor_stream.stop()
                self.monitor_stream.close()
            except Exception:
                pass
            self.monitor_stream = None

        self.voice_ring_buffer.clear()
        self.monitor_ring_buffer.clear()
        self.studio_chain.reset()
