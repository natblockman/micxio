import tempfile
import unittest
from pathlib import Path

from audio_converter.core import (
    ConversionOptions,
    build_command,
    is_supported,
    unique_output_path,
)


class CoreTests(unittest.TestCase):
    def test_supported_extensions_are_case_insensitive(self):
        self.assertTrue(is_supported("voice.MP3"))
        self.assertTrue(is_supported("song.flac"))
        self.assertFalse(is_supported("notes.txt"))

    def test_lossy_command_includes_bitrate_and_selected_options(self):
        options = ConversionOptions(
            output_format="MP3",
            bitrate="256k",
            sample_rate="48000",
            channels="2",
            normalize=True,
            preserve_metadata=False,
        )
        command = build_command("ffmpeg", Path("input.wav"), Path("output.mp3"), options)
        joined = " ".join(command)
        self.assertIn("-c:a libmp3lame", joined)
        self.assertIn("-b:a 256k", joined)
        self.assertIn("-ar 48000", joined)
        self.assertIn("-ac 2", joined)
        self.assertIn("loudnorm=I=-16:LRA=11:TP=-1.5", joined)
        self.assertIn("-map_metadata -1", joined)

    def test_lossless_command_does_not_include_bitrate(self):
        options = ConversionOptions(output_format="FLAC", bitrate="320k")
        command = build_command("ffmpeg", Path("input.wav"), Path("output.flac"), options)
        self.assertNotIn("-b:a", command)
        self.assertIn("flac", command)

    def test_unique_output_never_overwrites_source_or_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "recording.wav"
            source.touch()
            first = unique_output_path(source, folder, "WAV")
            self.assertEqual(first.name, "recording_converted.wav")
            first.touch()
            second = unique_output_path(source, folder, "WAV")
            self.assertEqual(second.name, "recording_converted_2.wav")


if __name__ == "__main__":
    unittest.main()
