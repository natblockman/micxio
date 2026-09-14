import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from audio_converter.recording import InputDevice, MicrophoneRecorder, list_input_devices


class FakeStream:
    def __init__(self, callback, **_options):
        self.callback = callback

    def start(self):
        samples = (1000).to_bytes(2, "little", signed=True) * 8
        self.callback(samples, 8, None, None)

    def stop(self):
        pass

    def close(self):
        pass


class FakeSoundDevice:
    class _Default:
        device = (1, 2)

    default = _Default()

    @staticmethod
    def query_devices():
        return [
            {"name": "Output only", "max_input_channels": 0, "default_samplerate": 48000},
            {"name": "Test microphone", "max_input_channels": 2, "default_samplerate": 44100},
        ]

    @staticmethod
    def RawInputStream(**options):
        return FakeStream(**options)


class RecordingTests(unittest.TestCase):
    @patch("audio_converter.recording._sounddevice", return_value=FakeSoundDevice())
    def test_lists_only_input_devices_and_prioritizes_default(self, _mock):
        devices = list_input_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].name, "Test microphone")
        self.assertEqual(devices[0].sample_rate, 44100)

    @patch("audio_converter.recording._sounddevice", return_value=FakeSoundDevice())
    def test_records_valid_mono_wave_file(self, _mock):
        levels = []
        device = InputDevice(1, "Test microphone", 44100, 2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recording.wav"
            recorder = MicrophoneRecorder(device, on_level=levels.append)
            recorder.start(path)
            result = recorder.stop()
            self.assertEqual(result, path)
            with wave.open(str(path), "rb") as recorded:
                self.assertEqual(recorded.getnchannels(), 1)
                self.assertEqual(recorded.getsampwidth(), 2)
                self.assertEqual(recorded.getframerate(), 44100)
                self.assertEqual(recorded.getnframes(), 8)
            self.assertTrue(levels)


if __name__ == "__main__":
    unittest.main()
