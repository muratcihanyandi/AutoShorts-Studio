import os
import sys
import unittest
from unittest.mock import patch

root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from app.services.hype_detector import hype_detector
from app.services.llm import generate_viral_moment_selection, _fallback_viral_moment


class TestHypeDetector(unittest.TestCase):
    def test_fallback_viral_moment(self):
        fb = _fallback_viral_moment(120.0, video_title="Korkunç Oyun", channel_name="Rraenee")
        self.assertIn("start_sec", fb)
        self.assertIn("end_sec", fb)
        self.assertIn("viral_hook_text", fb)
        self.assertIn("youtube_title", fb)
        self.assertIn("Rraenee", fb["viral_hook_text"])
        self.assertGreater(fb["end_sec"], fb["start_sec"])

    @patch("app.services.llm._generate_response")
    def test_generate_viral_moment_selection_success(self, mock_generate):
        mock_generate.return_value = """```json
        {
            "start_sec": 15.0,
            "end_sec": 45.0,
            "viral_hook_text": "Rraenee Altına Yaptı 😱",
            "youtube_title": "Rraenee Korku Dolu Anlar #shorts",
            "viral_score": 9,
            "reason": "Yüksek çığlık ve ani tepki"
        }
        ```"""
        transcript = "[15.0s - 20.0s] Ne oluyor orada?\n[20.0s - 45.0s] Aaaaa korktum!"
        res = generate_viral_moment_selection(
            transcript_with_timestamps=transcript,
            total_duration=120.0,
            channel_name="Rraenee",
            video_title="Korku Oyunu Bölüm 1"
        )
        self.assertEqual(res["start_sec"], 15.0)
        self.assertEqual(res["end_sec"], 45.0)
        self.assertEqual(res["viral_hook_text"], "Rraenee Altına Yaptı 😱")
        self.assertEqual(res["viral_score"], 9)

    @patch("app.services.llm.generate_viral_moment_selection")
    def test_hype_detector_detect_viral_moments_llm(self, mock_llm_select):
        mock_llm_select.return_value = {
            "start_sec": 10.0,
            "end_sec": 50.0,
            "viral_hook_text": "Komik An 🚀",
            "youtube_title": "Rraenee Güldüren An #shorts",
            "viral_score": 10,
            "reason": "Komik replik"
        }
        s, e, hook, title, score = hype_detector.detect_viral_moments_llm(
            transcript_with_timestamps="dummy transcript",
            total_duration=100.0,
            channel_name="Rraenee",
            video_title="Test Video"
        )
        self.assertEqual(s, 10.0)
        self.assertEqual(e, 50.0)
        self.assertEqual(hook, "Komik An 🚀")
        self.assertEqual(title, "Rraenee Güldüren An #shorts")
        self.assertEqual(score, 10)


if __name__ == "__main__":
    unittest.main()
