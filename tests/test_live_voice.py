import unittest
from unittest.mock import patch

import numpy as np

from audio_converter.effects import EFFECTS, SoundAdjustments
from audio_converter.live_voice import LiveVoiceEngine, LiveVoiceProcessor
from audio_converter.recording import InputDevice, OutputDevice


class FakeDuplexStream:
    def __init__(self, callback, **_options):
        self.callback = callback
        self.outputs = []

    def start(self):
        for _ in range(3):
            source = np.full((1024, 1), 0.2, dtype=np.float32)
            output = np.zeros((1024, 1), dtype=np.float32)
            self.callback(source, output, 1024, None, None)
            self.outputs.append(output)

    def stop(self):
        pass

    def close(self):
        pass


class FakeSoundDevice:
    latest_stream = None

    @classmethod
    def Stream(cls, **options):
        cls.latest_stream = FakeDuplexStream(**options)
        return cls.latest_stream


class LiveVoiceTests(unittest.TestCase):
    def test_all_effects_produce_safe_streaming_audio(self):
        source = (np.sin(2 * np.pi * 220 * np.arange(3072) / 44100) * 0.35).astype(np.float32)
        for effect in EFFECTS:
            with self.subTest(effect=effect):
                processor = LiveVoiceProcessor(effect, 65, 44100)
                output = np.concatenate([processor.process(source[:1024]), processor.process(source[1024:2048]),
                                         processor.process(source[2048:])])
                self.assertEqual(output.shape, source.shape)
                self.assertTrue(np.isfinite(output).all())
                self.assertLessEqual(float(np.abs(output).max()), 0.981)

    @patch("audio_converter.live_voice._sounddevice", return_value=FakeSoundDevice)
    def test_engine_starts_processes_and_stops(self, _mock):
        engine = LiveVoiceEngine(
            InputDevice(1, "Input", 44100, 1),
            OutputDevice(2, "Output", 44100, 2),
            "robot",
            60,
        )
        engine.start()
        self.assertTrue(engine.active)
        self.assertIsNotNone(FakeSoundDevice.latest_stream)
        self.assertTrue(any(np.any(block) for block in FakeSoundDevice.latest_stream.outputs))
        engine.stop()
        self.assertFalse(engine.active)

    def test_fine_tune_values_update_a_live_processor(self):
        processor = LiveVoiceProcessor("robot", 60, 44100, adjustments=SoundAdjustments(pitch_semitones=3))
        self.assertIsNotNone(processor.pitch_shifter)
        self.assertAlmostEqual(processor.pitch_shifter.pitch, 1.1892, places=4)
        processor.set_adjustments(SoundAdjustments(pitch_semitones=-3, bass_db=4, treble_db=-2, reverb_percent=40))
        self.assertIsNotNone(processor.pitch_shifter)
        self.assertAlmostEqual(processor.pitch_shifter.pitch, 0.8409, places=4)
        output = processor.process(np.full(1024, 0.15, dtype=np.float32))
        self.assertTrue(np.isfinite(output).all())


if __name__ == "__main__":
    unittest.main()
