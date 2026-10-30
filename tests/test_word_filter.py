import unittest

from services.word_filter import contains_blocked_term, parse_word_list


class WordFilterTests(unittest.TestCase):
    def test_txt_list_is_validated_and_deduplicated(self):
        words = parse_word_list(
            b"\xef\xbb\xbf# comments are allowed\nBad phrase\nbad   phrase\n\xd8\xb3\xd9\x8a\xd8\xa6\n"
        )
        self.assertEqual(words, ["Bad phrase", "سيئ"])

    def test_invalid_upload_does_not_produce_a_replacement_list(self):
        for content in (
            b"",
            b"\xff",
            b"valid\n" + b"x" * 101,
            b"\x00",
        ):
            with self.subTest(content=content[:12]):
                with self.assertRaises(ValueError):
                    parse_word_list(content)

    def test_matching_uses_normalized_whole_terms_and_phrases(self):
        words = ["كَلِمَة", "bad phrase"]
        self.assertTrue(contains_blocked_term("هذه كلمة غير مقبولة", words))
        self.assertTrue(contains_blocked_term("This is a bad phrase.", words))
        self.assertFalse(contains_blocked_term("bad phrased", words))
        self.assertFalse(contains_blocked_term("all good", words))


if __name__ == "__main__":
    unittest.main()
