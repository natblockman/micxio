import string
import unittest

from audio_converter.i18n import LANGUAGES, TRANSLATIONS, translate


class TranslationTests(unittest.TestCase):
    def test_required_languages_are_available_and_english_is_first(self):
        self.assertEqual(tuple(LANGUAGES), ("en", "ru", "zh", "ja"))

    def test_every_language_contains_the_same_keys(self):
        english_keys = set(TRANSLATIONS["en"])
        for language in LANGUAGES:
            self.assertEqual(set(TRANSLATIONS[language]), english_keys, language)

    def test_every_translation_can_format_its_placeholders(self):
        formatter = string.Formatter()
        for language, translations in TRANSLATIONS.items():
            for key, value in translations.items():
                fields = {field for _, field, _, _ in formatter.parse(value) if field}
                parameters = {field: 1 for field in fields}
                with self.subTest(language=language, key=key):
                    translate(language, key, **parameters)


if __name__ == "__main__":
    unittest.main()
