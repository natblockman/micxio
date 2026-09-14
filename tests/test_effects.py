import tempfile
import unittest
from pathlib import Path

from audio_converter.effects import (
    EFFECTS,
    SoundAdjustments,
    build_adjustment_filter,
    build_custom_effect_filter,
    build_effect_filter,
    load_custom_effects,
    new_custom_effect,
    save_custom_effects,
    unique_effect_output_path,
)


class VoiceEffectTests(unittest.TestCase):
    def test_all_presets_build_a_filter(self):
        self.assertEqual(
            set(EFFECTS),
            {
                "female", "male", "robot", "child", "child_boy", "child_girl",
                "elder_male", "elder_female", "monster", "alien", "radio", "echo",
            },
        )
        for effect in EFFECTS:
            with self.subTest(effect=effect):
                self.assertTrue(build_effect_filter(effect, 65))

    def test_pitch_effects_change_with_intensity(self):
        self.assertNotEqual(build_effect_filter("female", 10), build_effect_filter("female", 90))
        self.assertNotEqual(build_effect_filter("male", 10), build_effect_filter("male", 90))

    def test_intensity_is_safely_clamped(self):
        self.assertEqual(build_effect_filter("robot", -20), build_effect_filter("robot", 0))
        self.assertEqual(build_effect_filter("echo", 120), build_effect_filter("echo", 100))

    def test_unknown_effect_is_rejected(self):
        with self.assertRaises(ValueError):
            build_effect_filter("unknown", 50)

    def test_custom_effects_are_saved_loaded_and_used(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "custom-effects.json"
            custom = new_custom_effect("My voice", 7.5, 40, -3, "custom-123456abcdef", bass_db=5)
            save_custom_effects({custom.key: custom}, path)
            loaded = load_custom_effects(path)
            self.assertEqual(loaded, {custom.key: custom})
            self.assertEqual(build_effect_filter(custom.key, 0, loaded), build_custom_effect_filter(custom))
            self.assertIn("pitch=1.5422", build_custom_effect_filter(custom))
            self.assertIn("f=180", build_custom_effect_filter(custom))

    def test_custom_effect_values_are_clamped(self):
        custom = new_custom_effect("Safe", 100, -5, -100, "custom-123456abcdef", bass_db=100)
        self.assertEqual((custom.pitch_semitones, custom.echo_percent, custom.bass_db, custom.tone_db),
                         (12, 0, 12, -12))

    def test_fine_tune_filter_supports_all_four_controls(self):
        filter_chain = build_adjustment_filter(SoundAdjustments(3, -4, 5, 60))
        self.assertIn("rubberband=pitch=1.1892", filter_chain)
        self.assertIn("f=180", filter_chain)
        self.assertIn("f=5000", filter_chain)
        self.assertIn("aecho=", filter_chain)

    def test_output_name_contains_effect_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "voice.wav"
            source.touch()
            first = unique_effect_output_path(source, folder, "robot", "MP3")
            self.assertEqual(first.name, "voice_robot.mp3")
            first.touch()
            second = unique_effect_output_path(source, folder, "robot", "MP3")
            self.assertEqual(second.name, "voice_robot_2.mp3")


if __name__ == "__main__":
    unittest.main()
