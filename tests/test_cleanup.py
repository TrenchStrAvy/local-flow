from __future__ import annotations

import unittest

import cleanup


class RuleCleanupTests(unittest.TestCase):
    def test_removes_fillers_and_closes_sentence(self) -> None:
        self.assertEqual(
            cleanup.rule_cleanup("um so let's do the cheap fix first uh and then test"),
            "So let's do the cheap fix first and then test.")

    def test_filler_glued_to_comma(self) -> None:
        self.assertEqual(cleanup.rule_cleanup("okay, um, we go"), "Okay, we go.")

    def test_capitalizes_after_sentence_end(self) -> None:
        self.assertEqual(
            cleanup.rule_cleanup("that is it. next point? yes!"),
            "That is it. Next point? Yes!")

    def test_german_fillers_only_in_german(self) -> None:
        self.assertEqual(cleanup.rule_cleanup("äh mach das ähm bitte", "de"),
                         "Mach das bitte.")
        # "hm" is a German filler but must not touch English text
        self.assertEqual(cleanup.rule_cleanup("hm is a syllable", "en"),
                         "Hm is a syllable.")

    def test_does_not_eat_words_containing_fillers(self) -> None:
        self.assertEqual(cleanup.rule_cleanup("the umbrella and the hummus"),
                         "The umbrella and the hummus.")

    def test_ellipsis_is_a_pause_not_a_sentence_end(self) -> None:
        self.assertEqual(cleanup.rule_cleanup("keep the same... aesthetics"),
                         "Keep the same... aesthetics.")

    def test_keeps_existing_punctuation(self) -> None:
        self.assertEqual(cleanup.rule_cleanup("Is it done?"), "Is it done?")

    def test_empty(self) -> None:
        self.assertEqual(cleanup.rule_cleanup("  "), "")
        self.assertEqual(cleanup.rule_cleanup("um"), "")


class WordDiffTests(unittest.TestCase):
    def test_marks_removed_and_changed(self) -> None:
        d = cleanup.word_diff("so um we go", "So, we went.")
        self.assertEqual(d, [("So,", "same"), ("um", "removed"),
                             ("we", "same"), ("go", "removed"),
                             ("went.", "changed")])

    def test_punctuation_only_is_same(self) -> None:
        d = cleanup.word_diff("hello world", "Hello, world.")
        self.assertTrue(all(style == "same" for _, style in d))


class FaithfulTests(unittest.TestCase):
    def test_accepts_light_edit(self) -> None:
        self.assertTrue(cleanup.faithful(
            "so um there's a bug where it does not transcribe",
            "So there's a bug where it does not transcribe."))

    def test_rejects_dropped_clause(self) -> None:
        raw = ("my goal is to see the words while I speak and then it cleans "
               "up and we see the corrections in front of our eyes")
        self.assertFalse(cleanup.faithful(
            raw, "My goal is to see the words while I speak."))

    def test_rejects_answer_instead_of_edit(self) -> None:
        self.assertFalse(cleanup.faithful("what time is it", "It is noon."))
        self.assertFalse(cleanup.faithful("keep it in english", ""))


class OllamaGuardTests(unittest.TestCase):
    def test_unreachable_server_returns_raw(self) -> None:
        old = cleanup.OLLAMA_URL
        cleanup.OLLAMA_URL = "http://127.0.0.1:9/api/chat"
        try:
            self.assertEqual(cleanup.ollama_cleanup("hi there", "en"),
                             "hi there")
        finally:
            cleanup.OLLAMA_URL = old


if __name__ == "__main__":
    unittest.main()


class TyperPlanTests(unittest.TestCase):
    def test_plan(self) -> None:
        import typer
        self.assertEqual(typer.plan("", "hello"), (0, "hello"))
        self.assertEqual(typer.plan("how it work", "how it works and"),
                         (0, "s and"))
        self.assertEqual(typer.plan("so um we go", "So we go"),
                         (11, "So we go"))
        self.assertEqual(typer.plan("same", "same"), (0, ""))
        self.assertEqual(typer.plan("abc", "ab"), (1, ""))
