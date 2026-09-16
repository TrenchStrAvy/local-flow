from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LOCALFLOW_SETTINGS"] = str(
            Path(self.tmp.name) / "nested" / "settings.json")
        import settings
        self.settings = importlib.reload(settings)

    def tearDown(self) -> None:
        os.environ.pop("LOCALFLOW_SETTINGS", None)
        self.tmp.cleanup()

    def test_defaults(self) -> None:
        s = self.settings
        self.assertEqual(s.get_language(), "en")
        self.assertEqual(s.get_languages(), ["en", "de", "fr"])
        self.assertEqual(s.get_quick_keys(), {"q": "de", "w": "fr", "e": "en"})
        self.assertEqual(s.quick_keys_by_keycode(), {12: "de", 13: "fr", 14: "en"})

    def test_language_round_trips_and_creates_parent_dir(self) -> None:
        self.settings.set_language("de")
        self.assertEqual(self.settings.get_language(), "de")
        self.assertTrue(self.settings.SETTINGS_PATH.exists())

    def test_selecting_unlisted_language_adds_it(self) -> None:
        self.settings.set_language("it")
        self.assertIn("it", self.settings.get_languages())

    def test_rejects_unknown_language(self) -> None:
        with self.assertRaises(ValueError):
            self.settings.set_language("xx")
        with self.assertRaises(ValueError):
            self.settings.add_language("xx")

    def test_add_and_remove_language(self) -> None:
        s = self.settings
        s.add_language("es")
        s.add_language("es")   # idempotent
        self.assertEqual(s.get_languages(), ["en", "de", "fr", "es"])
        s.set_quick_key("r", "es")
        s.set_language("es")
        s.remove_language("es")
        self.assertEqual(s.get_languages(), ["en", "de", "fr"])
        self.assertEqual(s.get_language(), "en")       # fell back to first
        self.assertNotIn("r", s.get_quick_keys())      # key cleared

    def test_cannot_remove_last_language(self) -> None:
        s = self.settings
        for code in ("de", "fr"):
            s.remove_language(code)
        s.remove_language("en")
        self.assertEqual(s.get_languages(), ["en"])

    def test_quick_key_is_unique_per_language(self) -> None:
        s = self.settings
        s.set_quick_key("r", "de")             # moves German from Q to R
        keys = s.get_quick_keys()
        self.assertEqual(keys["r"], "de")
        self.assertNotIn("q", keys)
        s.set_quick_key("r", None)
        self.assertNotIn("r", s.get_quick_keys())
        with self.assertRaises(ValueError):
            s.set_quick_key("z", "de")

    def test_corrupt_file_falls_back_to_default(self) -> None:
        self.settings.SETTINGS_PATH.parent.mkdir(parents=True)
        self.settings.SETTINGS_PATH.write_text("{not json")
        self.assertEqual(self.settings.get_language(), "en")
        self.assertEqual(self.settings.get_languages(), ["en", "de", "fr"])

    def test_model_for_language(self) -> None:
        m = self.settings.model_for_language
        self.assertEqual(m("small.en", "en"), "small.en")
        self.assertEqual(m("small.en", "de"), "small")
        self.assertEqual(m("medium.en", "fr"), "medium")
        self.assertEqual(m("small", "de"), "small")


class CatalogTests(unittest.TestCase):
    def test_catalog_is_complete_and_labelled(self) -> None:
        import languages
        self.assertGreaterEqual(len(languages.CATALOG), 99)
        self.assertEqual(languages.label("en"), "English")
        self.assertEqual(languages.label("de"), "Deutsch (German)")
        self.assertEqual(languages.sorted_codes()[0], "af")


if __name__ == "__main__":
    unittest.main()
