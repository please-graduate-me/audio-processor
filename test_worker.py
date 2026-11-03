import unittest
from unittest.mock import patch, Mock
from segmentation import split_transcript
from errors import ProcessingError
from video_validation import validate_metadata, youtube_metadata

class SegmentationTests(unittest.TestCase):
    def payload(self, words):
        return {"words": [{"word": word, "start": start, "end": end} for word, start, end in words]}

    def test_sentence_pause_and_absolute_times(self):
        result = split_transcript(self.payload([("Hello.", 10, 11), ("We", 11.1, 11.4), ("go", 11.4, 12), ("home", 13, 14)]), 20)
        self.assertEqual([s["text"] for s in result], ["Hello.", "We go", "home"])
        self.assertEqual(result[0]["start"], 10)
        self.assertEqual(result[-1]["end"], 14)

    def test_long_phrase_limits(self):
        result = split_transcript(self.payload([("word", i * .3, (i + 1) * .3) for i in range(60)]), 30)
        self.assertTrue(all(len(s["words"]) <= 25 and s["end"] - s["start"] <= 12 for s in result))
        result = split_transcript(self.payload([("word", i, i + .9) for i in range(40)]), 45)
        self.assertTrue(all(s["end"] - s["start"] <= 12 for s in result))

    def test_invalid_and_missing_timestamps(self):
        for payload in [{}, self.payload([("hello", 5, 4)]), self.payload([("hello", float("nan"), 4)])]:
            with self.assertRaises(ValueError):
                split_transcript(payload, 20)

    def test_preserves_punctuation_and_clamps_overlap(self):
        payload = self.payload([("Hello", 1, 2), ("world", 1.9, 3)])
        payload["text"] = "Hello world!"
        result = split_transcript(payload, 2.9)
        self.assertEqual(result[0]["text"], "Hello world!")
        self.assertEqual(result[0]["words"][1]["start"], 2)
        self.assertEqual(result[0]["end"], 2.9)

class MetadataTests(unittest.TestCase):
    def test_limits(self):
        self.assertEqual(validate_metadata({"duration": 900}), 900)
        for info in [{"duration": 901}, {"duration": 10, "is_live": True}, {"duration": 10, "age_limit": 18}, {"duration": None}, {"duration": 10, "availability": "private"}]:
            with self.assertRaises(ProcessingError):
                validate_metadata(info)

    @patch.dict("os.environ", {"YOUTUBE_API_KEY": "테스트용 키"})
    @patch("video_validation.httpx.get")
    def test_api_rejects_disabled_embeds(self, get):
        get.return_value = Mock(is_error=False)
        get.return_value.json.return_value = {"items": [{"status": {"embeddable": False}}]}
        with self.assertRaises(ProcessingError) as caught:
            youtube_metadata("JnfBXjWm7hc")
        self.assertEqual(caught.exception.code, "EMBED_DISABLED")

if __name__ == "__main__":
    unittest.main()
