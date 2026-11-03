import unittest
from unittest.mock import Mock, patch
import httpx
from video_validation import youtube_metadata


class VideoCategoryTests(unittest.TestCase):
    def video_response(self):
        return Mock(is_error=False, json=lambda: {"items": [{"status": {"embeddable": True}, "snippet": {"title": "학습 영상", "categoryId": "27"}, "contentDetails": {"duration": "PT2M"}}]})

    @patch.dict("os.environ", {"YOUTUBE_API_KEY": "테스트용 키"})
    @patch("video_validation.httpx.get")
    def test_category_title_and_id(self, get):
        get.side_effect = [self.video_response(), Mock(is_error=False, json=lambda: {"items": [{"snippet": {"title": "교육"}}]})]
        result = youtube_metadata("JnfBXjWm7hc")
        self.assertEqual(result["categoryId"], "27")
        self.assertEqual(result["category"], "교육")
        self.assertEqual(result["duration"], 120)

    @patch.dict("os.environ", {"YOUTUBE_API_KEY": "테스트용 키"})
    @patch("video_validation.httpx.get")
    def test_category_failure_does_not_block_video(self, get):
        get.side_effect = [self.video_response(), httpx.ConnectError("분류 서버 연결 실패")]
        result = youtube_metadata("JnfBXjWm7hc")
        self.assertIsNone(result["category"])
        self.assertEqual(result["categoryId"], "27")

    @patch.dict("os.environ", {"YOUTUBE_API_KEY": "", "YOUTUBE_DATA_API_v3": ""})
    @patch("video_validation.httpx.get")
    def test_no_key_uses_extractor(self, get):
        self.assertIsNone(youtube_metadata("JnfBXjWm7hc"))
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
