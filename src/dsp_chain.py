"""
Global studio voice chain for TikTok LIVE Battle VoiceMod.

Conditions the microphone signal before the battle effect (noise gate, high-pass,
presence EQ and broadcast compressor) and protects the final output after the
soundboard mix (peak limiter). Every stage is vectorized in NumPy/SciPy with
persistent filter state, so it runs safely inside the real-time audio callback.

Chain order:
    mic -> NoiseGate -> HighPass -> StudioEQ -> Compressor -> [battle effect]
        -> + soundboard -> Limiter -> output
"""

import numpy as np
import scipy.signal as signal
from typing import Optional


def _db_to_lin(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def _lin_to_db(x: np.ndarray) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(x, 1e-9))


def _peaking_eq(f0: float, gain_db: float, q: float, sample_rate: int) -> np.ndarray:
    """RBJ audio cookbook peaking EQ biquad, returned as an SOS section."""
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * f0 / sample_rate
    alpha = np.sin(w0) / (2.0 * q)
    cos_w0 = np.cos(w0)

    b0 = 1.0 + alpha * a
    b1 = -2.0 * cos_w0
    b2 = 1.0 - alpha * a
    a0 = 1.0 + alpha / a
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha / a

    return np.array([[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]])


class OnePole:
    """Single-pole smoothing filter with persistent state."""

    def __init__(self, time_ms: float, sample_rate: int):
        tau_samples = max(1.0, sample_rate * (time_ms / 1000.0))
        self.alpha = float(np.exp(-1.0 / tau_samples))
        self.zi = np.zeros(1, dtype=np.float64)

    def process(self, x: np.ndarray) -> np.ndarray:
        y, self.zi = signal.lfilter([1.0 - self.alpha], [1.0, -self.alpha], x, zi=self.zi)
        return y

    def reset(self):
        self.zi = np.zeros(1, dtype=np.float64)


class AsymmetricEnvelope:
    """
    Fast-attack / slow-release envelope follower.

    Built as the pointwise maximum of a fast and a slow single-pole filter:
    on a rising edge the fast pole wins, on a falling edge the slow pole wins.
    This gives correct attack/release behaviour while staying fully vectorized,
    with no per-sample Python loop inside the audio callback.
    """

    def __init__(self, attack_ms: float, release_ms: float, sample_rate: int):
        self.fast = OnePole(attack_ms, sample_rate)
        self.slow = OnePole(release_ms, sample_rate)

    def process(self, x: np.ndarray) -> np.ndarray:
        return np.maximum(self.fast.process(x), self.slow.process(x))

    def reset(self):
        self.fast.reset()
        self.slow.reset()


class StereoDetector:
    """Derives a single stereo-linked control signal from a stereo block."""

    @staticmethod
    def peak(audio: np.ndarray) -> np.ndarray:
        if audio.shape[1] == 1:
            return np.abs(audio[:, 0])
        return np.maximum(np.abs(audio[:, 0]), np.abs(audio[:, 1]))


class SosFilter:
    """Stereo SOS (biquad cascade) filter with persistent per-channel state."""

    def __init__(self, sos: np.ndarray, channels: int = 2):
        self.sos = np.asarray(sos, dtype=np.float64)
        self.channels = 0
        self.zi: Optional[np.ndarray] = None
        self._allocate(channels)

    def _allocate(self, channels: int):
        # Zero state: the filter has never seen a signal, so silence in stays silence out.
        # (sosfilt_zi would pre-load the step-response steady state and ring on silence.)
        n_sections = self.sos.shape[0]
        self.zi = np.zeros((n_sections, 2, channels), dtype=np.float64)
        self.channels = channels

    def process(self, audio: np.ndarray) -> np.ndarray:
        if audio.shape[1] != self.channels:
            self._allocate(audio.shape[1])
        y, self.zi = signal.sosfilt(self.sos, audio, axis=0, zi=self.zi)
        return y

    def reset(self):
        self._allocate(self.channels)


class NoiseGate:
    """
    Removes room noise, keyboard clicks and fan hum before the battle effect
    can amplify them. Gain is a smooth curve of the level, so there is no
    hard on/off click when the gate opens or closes.
    """

    def __init__(self, sample_rate: int, threshold_db: float = -40.0,
                 softness_ratio: float = 0.5, attack_ms: float = 3.0,
                 release_ms: float = 140.0):
        self.sample_rate = sample_rate
        self.enabled = True
        self.threshold = _db_to_lin(threshold_db)
        self.softness_ratio = softness_ratio
        self.detector = AsymmetricEnvelope(attack_ms, release_ms, sample_rate)

    def set_threshold_db(self, threshold_db: float):
        self.threshold = _db_to_lin(threshold_db)

    def process(self, audio: np.ndarray) -> np.ndarray:
        envelope = self.detector.process(StereoDetector.peak(audio))
        softness = max(self.threshold * self.softness_ratio, 1e-7)
        x = np.clip((envelope - self.threshold) / softness, 0.0, 1.0)
        gain = x * x * (3.0 - 2.0 * x)
        return audio * gain[:, np.newaxis]

    def reset(self):
        self.detector.reset()


class HighPassFilter:
    """Removes sub-vocal rumble (desk thumps, plosives, HVAC) below the cutoff."""

    def __init__(self, sample_rate: int, cutoff: float = 80.0, order: int = 2):
        self.enabled = True
        sos = signal.butter(order, cutoff / (sample_rate / 2.0), btype='high', output='sos')
        self.filter = SosFilter(sos)

    def process(self, audio: np.ndarray) -> np.ndarray:
        return self.filter.process(audio).astype(np.float32)

    def reset(self):
        self.filter.reset()


class StudioEQ:
    """Fixed voice-shaping EQ: trims muddiness around 300 Hz, adds presence at 3 kHz."""

    def __init__(self, sample_rate: int, mud_hz: float = 300.0, mud_db: float = -3.0,
                 presence_hz: float = 3000.0, presence_db: float = 3.0):
        self.enabled = True
        self.mud = SosFilter(_peaking_eq(mud_hz, mud_db, 0.9, sample_rate))
        self.presence = SosFilter(_peaking_eq(presence_hz, presence_db, 0.8, sample_rate))

    def process(self, audio: np.ndarray) -> np.ndarray:
        out = self.mud.process(audio)
        return self.presence.process(out).astype(np.float32)

    def reset(self):
        self.mud.reset()
        self.presence.reset()


class Compressor:
    """
    Broadcast-style feed-forward compressor with soft knee and stereo-linked
    detection, so quiet battle callouts and loud reactions stay at a consistent
    level. Attack/release live in the level detector.
    """

    def __init__(self, sample_rate: int, threshold_db: float = -20.0, ratio: float = 3.0,
                 attack_ms: float = 10.0, release_ms: float = 100.0, knee_db: float = 6.0):
        self.sample_rate = sample_rate
        self.enabled = True
        self.threshold_db = threshold_db
        self.ratio = ratio
        self.knee_db = knee_db
        self.makeup_db = 0.0
        self.detector = AsymmetricEnvelope(attack_ms, release_ms, sample_rate)
        self._update_makeup()

    def set_amount(self, amount: float):
        """Maps 0.0-1.0 to compression strength (1:1 up to 6:1) with auto makeup."""
        amount = float(np.clip(amount, 0.0, 1.0))
        self.ratio = 1.0 + 5.0 * amount
        self._update_makeup()

    def _update_makeup(self):
        reduction_db = max(0.0, -self.threshold_db) * (1.0 - 1.0 / self.ratio)
        self.makeup_db = reduction_db * 0.5

    def process(self, audio: np.ndarray) -> np.ndarray:
        envelope = self.detector.process(StereoDetector.peak(audio))
        level_db = _lin_to_db(envelope)

        over = level_db - self.threshold_db
        slope = 1.0 - 1.0 / self.ratio
        knee = self.knee_db / 2.0

        reduction_db = np.zeros_like(over)
        if knee > 0.0:
            middle = (over > -knee) & (over < knee)
            reduction_db[middle] = slope * (over[middle] + knee) ** 2 / (4.0 * knee)
        reduction_db = np.where(over >= knee, slope * over, reduction_db)

        gain_db = -reduction_db + self.makeup_db
        gain = 10.0 ** (gain_db / 20.0)
        return (audio * gain[:, np.newaxis]).astype(np.float32)

    def reset(self):
        self.detector.reset()


class Limiter:
    """
    Peak limiter sitting after the soundboard mix, so voice + sound effects can
    never clip the virtual output that TikTok LIVE Studio receives.
    """

    def __init__(self, sample_rate: int, ceiling_db: float = -1.0,
                 attack_ms: float = 0.1, release_ms: float = 60.0):
        self.enabled = True
        self.ceiling_db = ceiling_db
        self.ceiling = _db_to_lin(ceiling_db)
        self.detector = AsymmetricEnvelope(attack_ms, release_ms, sample_rate)

    def set_ceiling_db(self, ceiling_db: float):
        self.ceiling_db = float(ceiling_db)
        self.ceiling = _db_to_lin(ceiling_db)

    def process(self, audio: np.ndarray) -> np.ndarray:
        envelope = self.detector.process(StereoDetector.peak(audio))
        gain = np.minimum(1.0, self.ceiling / np.maximum(envelope, 1e-9))
        out = audio * gain[:, np.newaxis]
        return np.clip(out, -self.ceiling, self.ceiling).astype(np.float32)

    def reset(self):
        self.detector.reset()


class StudioChain:
    """Owns every global stage and exposes the two integration points used by the engine."""

    def __init__(self, sample_rate: int = 48000):
        self.sample_rate = sample_rate
        self.gate = NoiseGate(sample_rate)
        self.highpass = HighPassFilter(sample_rate)
        self.eq = StudioEQ(sample_rate)
        self.compressor = Compressor(sample_rate)
        self.limiter = Limiter(sample_rate)
        self.enabled = True

    def process_input(self, audio: np.ndarray) -> np.ndarray:
        """Mic conditioning, applied before the battle effect."""
        if not self.enabled:
            return audio

        out = audio
        if self.gate.enabled:
            out = self.gate.process(out)
        if self.highpass.enabled:
            out = self.highpass.process(out)
        if self.eq.enabled:
            out = self.eq.process(out)
        if self.compressor.enabled:
            out = self.compressor.process(out)
        return out.astype(np.float32)

    def process_output(self, audio: np.ndarray) -> np.ndarray:
        """Output protection, applied after voice + soundboard are summed."""
        if not self.enabled:
            return audio
        if self.limiter.enabled:
            return self.limiter.process(audio)
        return audio

    def reset(self):
        self.gate.reset()
        self.highpass.reset()
        self.eq.reset()
        self.compressor.reset()
        self.limiter.reset()
