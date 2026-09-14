from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .effects import CustomEffect, EFFECTS, SoundAdjustments
from .recording import InputDevice, OutputDevice, RecordingError, _sounddevice


class StreamingPitchShifter:
    """Low-latency dual-tap delay pitch shifter with crossfaded read heads."""

    def __init__(self, sample_rate: int, pitch: float) -> None:
        self.pitch = pitch
        self.grain = max(512, int(sample_rate * 0.045))
        self.minimum_delay = max(96, int(sample_rate * 0.006))
        self.ring_size = 1
        while self.ring_size < self.grain * 8:
            self.ring_size *= 2
        self.ring = np.zeros(self.ring_size, dtype=np.float32)
        self.total_written = 0
        self.phase = 0.0

    def process(self, samples: np.ndarray) -> np.ndarray:
        source = np.asarray(samples, dtype=np.float32).reshape(-1)
        count = len(source)
        if count == 0 or abs(self.pitch - 1.0) < 0.001:
            return source.copy()

        absolute = self.total_written + np.arange(count, dtype=np.int64)
        self.ring[np.mod(absolute, self.ring_size)] = source
        phase_step = 1.0 - self.pitch
        phases = np.mod(self.phase + np.arange(count, dtype=np.float64) * phase_step, self.grain)
        other_phases = np.mod(phases + self.grain / 2.0, self.grain)
        read_one = absolute.astype(np.float64) - self.minimum_delay - phases
        read_two = absolute.astype(np.float64) - self.minimum_delay - other_phases
        first = self._interpolate(read_one)
        second = self._interpolate(read_two)
        weight = (0.5 - 0.5 * np.cos(2.0 * math.pi * phases / self.grain)).astype(np.float32)
        output = first * weight + second * (1.0 - weight)
        self.phase = float((self.phase + count * phase_step) % self.grain)
        self.total_written += count
        return output.astype(np.float32, copy=False)

    def _interpolate(self, positions: np.ndarray) -> np.ndarray:
        lower = np.floor(positions).astype(np.int64)
        fraction = (positions - lower).astype(np.float32)
        left = self.ring[np.mod(lower, self.ring_size)]
        right = self.ring[np.mod(lower + 1, self.ring_size)]
        return left + (right - left) * fraction


class LiveVoiceProcessor:
    def __init__(self, effect: str, intensity: float, sample_rate: int,
                 custom_effect: CustomEffect | None = None,
                 adjustments: SoundAdjustments | None = None) -> None:
        if effect not in EFFECTS and custom_effect is None:
            raise ValueError(f"Unknown voice effect: {effect}")
        self.effect = effect
        self.custom_effect = custom_effect
        self.adjustments = adjustments or SoundAdjustments()
        self.amount = max(0.0, min(1.0, intensity / 100.0))
        self.sample_rate = sample_rate
        self.oscillator_phase = 0.0
        self.lowpass_state = 0.0
        self.highpass_state = 0.0
        self.highpass_input = 0.0
        self.bass_state = 0.0
        self.echo_buffer = np.zeros(max(1, int(sample_rate * 0.55)), dtype=np.float32)
        self.echo_index = 0
        pitch = self._pitch_for_effect()
        self.pitch_shifter = StreamingPitchShifter(sample_rate, pitch) if pitch else None

    def _pitch_for_effect(self) -> float | None:
        base_pitch = 1.0
        if self.custom_effect:
            base_pitch = 2.0 ** (self.custom_effect.pitch_semitones / 12.0)
        elif self.effect == "female":
            base_pitch = 1.0 + 0.24 * self.amount
        elif self.effect == "male":
            base_pitch = 1.0 - 0.20 * self.amount
        elif self.effect == "child":
            base_pitch = 1.0 + 0.42 * self.amount
        elif self.effect == "child_boy":
            base_pitch = 1.0 + 0.28 * self.amount
        elif self.effect == "child_girl":
            base_pitch = 1.0 + 0.46 * self.amount
        elif self.effect == "elder_male":
            base_pitch = 1.0 - 0.22 * self.amount
        elif self.effect == "elder_female":
            base_pitch = 1.0 - 0.11 * self.amount
        elif self.effect == "monster":
            base_pitch = 1.0 - 0.34 * self.amount
        elif self.effect == "alien":
            base_pitch = 1.0 + 0.12 * self.amount
        pitch = base_pitch * (2.0 ** (self.adjustments.pitch_semitones / 12.0))
        return pitch if abs(pitch - 1.0) >= 0.001 else None

    def set_adjustments(self, adjustments: SoundAdjustments) -> None:
        """Update the live fine-tune values without restarting the stream."""
        self.adjustments = adjustments
        pitch = self._pitch_for_effect()
        if pitch is None:
            self.pitch_shifter = None
        elif self.pitch_shifter:
            self.pitch_shifter.pitch = pitch
        else:
            self.pitch_shifter = StreamingPitchShifter(self.sample_rate, pitch)

    def process(self, samples: np.ndarray) -> np.ndarray:
        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        if self.pitch_shifter:
            audio = self.pitch_shifter.process(audio)
        if self.custom_effect:
            if self.custom_effect.echo_percent >= 1:
                audio = self._echo(audio, 0.16, 0.08 + 0.62 * (self.custom_effect.echo_percent / 100.0))
            audio = self._bass(audio, self.custom_effect.bass_db)
            audio = self._tone(audio, self.custom_effect.tone_db)
        elif self.effect == "robot":
            frequency = 35.0 + 55.0 * self.amount
            modulated = self._ring_modulate(audio, frequency)
            audio = audio * (0.18 - 0.10 * self.amount) + modulated * (0.82 + 0.10 * self.amount)
            audio = self._echo(audio, 0.025, 0.14 + 0.12 * self.amount)
        elif self.effect == "monster":
            audio = np.tanh(audio * (1.5 + 2.3 * self.amount)).astype(np.float32)
            audio = self._echo(audio, 0.075, 0.18 + 0.25 * self.amount)
        elif self.effect == "alien":
            modulated = self._ring_modulate(audio, 7.0 + 13.0 * self.amount)
            audio = audio * 0.62 + modulated * (0.18 + 0.20 * self.amount)
            audio = self._echo(audio, 0.045, 0.12 + 0.18 * self.amount)
        elif self.effect == "radio":
            audio = self._band_limit(audio, 320.0, 4200.0 - 1100.0 * self.amount)
            audio = np.tanh(audio * (1.5 + self.amount)).astype(np.float32)
        elif self.effect == "echo":
            audio = self._echo(audio, 0.10 + 0.28 * self.amount, 0.20 + 0.50 * self.amount)
        elif self.effect == "elder_male":
            audio = self._age_tremble(audio, 4.0 + 1.5 * self.amount, 0.025 + 0.065 * self.amount)
        elif self.effect == "elder_female":
            audio = self._age_tremble(audio, 4.8 + 1.5 * self.amount, 0.020 + 0.055 * self.amount)
        if self.adjustments.reverb_percent >= 1:
            audio = self._echo(audio, 0.16, 0.08 + 0.62 * (self.adjustments.reverb_percent / 100.0))
        audio = self._bass(audio, self.adjustments.bass_db)
        audio = self._tone(audio, self.adjustments.treble_db)
        return np.clip(audio, -0.98, 0.98).astype(np.float32, copy=False)

    def _tone(self, audio: np.ndarray, gain_db: float) -> np.ndarray:
        """Apply a gentle high-frequency brightness adjustment in real time."""
        if abs(gain_db) < 0.1:
            return audio
        low = self._band_limit(audio, 0.0, 3000.0)
        high = audio - low
        gain = 10.0 ** (gain_db / 20.0)
        return low + high * gain

    def _bass(self, audio: np.ndarray, gain_db: float) -> np.ndarray:
        """Apply a gentle low-frequency shelf without adding live latency."""
        if abs(gain_db) < 0.1:
            return audio
        alpha = 1.0 - math.exp(-2.0 * math.pi * 180.0 / self.sample_rate)
        low = np.empty_like(audio)
        state = self.bass_state
        for index, sample in enumerate(audio):
            state += alpha * (float(sample) - state)
            low[index] = state
        self.bass_state = state
        gain = 10.0 ** (gain_db / 20.0)
        return audio + (gain - 1.0) * low

    def _ring_modulate(self, audio: np.ndarray, frequency: float) -> np.ndarray:
        count = len(audio)
        phase_step = 2.0 * math.pi * frequency / self.sample_rate
        phases = self.oscillator_phase + np.arange(count, dtype=np.float32) * phase_step
        self.oscillator_phase = float((self.oscillator_phase + count * phase_step) % (2.0 * math.pi))
        return audio * np.sin(phases).astype(np.float32)

    def _age_tremble(self, audio: np.ndarray, frequency: float, depth: float) -> np.ndarray:
        count = len(audio)
        phase_step = 2.0 * math.pi * frequency / self.sample_rate
        phases = self.oscillator_phase + np.arange(count, dtype=np.float32) * phase_step
        self.oscillator_phase = float((self.oscillator_phase + count * phase_step) % (2.0 * math.pi))
        return audio * (1.0 + depth * np.sin(phases).astype(np.float32))

    def _echo(self, audio: np.ndarray, delay_seconds: float, feedback: float) -> np.ndarray:
        delay = min(len(self.echo_buffer) - 1, max(1, int(self.sample_rate * delay_seconds)))
        output = np.empty_like(audio)
        for index, sample in enumerate(audio):
            read_index = (self.echo_index - delay) % len(self.echo_buffer)
            delayed = self.echo_buffer[read_index]
            output[index] = sample + delayed * feedback
            self.echo_buffer[self.echo_index] = sample + delayed * min(0.72, feedback * 0.7)
            self.echo_index = (self.echo_index + 1) % len(self.echo_buffer)
        return output

    def _band_limit(self, audio: np.ndarray, highpass: float, lowpass: float) -> np.ndarray:
        low_alpha = 1.0 - math.exp(-2.0 * math.pi * lowpass / self.sample_rate)
        high_alpha = math.exp(-2.0 * math.pi * highpass / self.sample_rate)
        output = np.empty_like(audio)
        low_state = self.lowpass_state
        high_state = self.highpass_state
        previous_input = self.highpass_input
        for index, sample in enumerate(audio):
            low_state += low_alpha * (float(sample) - low_state)
            high_state = high_alpha * (high_state + low_state - previous_input)
            previous_input = low_state
            output[index] = high_state
        self.lowpass_state = low_state
        self.highpass_state = high_state
        self.highpass_input = previous_input
        return output


@dataclass
class LiveVoiceEngine:
    input_device: InputDevice
    output_device: OutputDevice
    effect: str
    intensity: float
    custom_effect: CustomEffect | None = None
    adjustments: SoundAdjustments | None = None
    on_level: Callable[[float], None] | None = None
    on_error: Callable[[str], None] | None = None

    def __post_init__(self) -> None:
        self._stream = None
        self.active = False
        self.sample_rate = min(self.input_device.sample_rate, self.output_device.sample_rate)
        if self.sample_rate < 16000:
            self.sample_rate = 44100
        self.processor = LiveVoiceProcessor(
            self.effect, self.intensity, self.sample_rate, self.custom_effect, self.adjustments,
        )

    def start(self) -> None:
        if self.active:
            return
        sd = _sounddevice()

        def callback(indata, outdata, _frames, _time_info, status) -> None:
            try:
                source = np.asarray(indata[:, 0], dtype=np.float32)
                processed = self.processor.process(source)
                outdata[:, 0] = processed
                if self.on_level:
                    self.on_level(float(np.max(np.abs(processed), initial=0.0)))
                if status and self.on_error:
                    self.on_error(str(status))
            except Exception as error:
                outdata.fill(0)
                if self.on_error:
                    self.on_error(str(error))

        try:
            self._stream = sd.Stream(
                samplerate=self.sample_rate,
                blocksize=1024,
                device=(self.input_device.index, self.output_device.index),
                channels=(1, 1),
                dtype="float32",
                latency="low",
                callback=callback,
            )
            self._stream.start()
            self.active = True
        except Exception as error:
            self._stream = None
            raise RecordingError(str(error)) from error

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        self.active = False
