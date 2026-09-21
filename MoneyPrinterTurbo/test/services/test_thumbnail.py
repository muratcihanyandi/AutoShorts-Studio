import os
import unittest
from unittest.mock import MagicMock, patch
import numpy as np
from PIL import Image

from app.services.thumbnail import (
    _clean_title_for_thumbnail,
    generate_thumbnail,
)


class TestThumbnailService(unittest.TestCase):
    def test_clean_title_for_thumbnail(self):
        title = "5 Secrets You Did Not Know! #Shorts #Viral"
        cleaned = _clean_title_for_thumbnail(title, max_words=5)
        self.assertEqual(cleaned, "5 SECRETS YOU DID NOT")
        self.assertNotIn("#shorts", cleaned.lower())

    def test_generate_thumbnail_returns_none_for_missing_video(self):
        res = generate_thumbnail("/non/existent/video.mp4", title="Test")
        self.assertIsNone(res)

    @patch("moviepy.VideoFileClip")
    def test_generate_thumbnail_creates_portrait_image(self, mock_clip_cls):
        # Mock VideoFileClip
        mock_clip = MagicMock()
        mock_clip.duration = 5.0
        # Create a dummy RGB frame (height 1920, width 1080, 3 channels)
        dummy_frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
        dummy_frame[:, :] = [50, 100, 150]
        mock_clip.get_frame.return_value = dummy_frame
        mock_clip.__enter__.return_value = mock_clip
        mock_clip_cls.return_value = mock_clip

        out_path = os.path.abspath("test_dummy_portrait_thumb.jpg")
        try:
            res = generate_thumbnail(
                video_path=__file__,  # exists on disk
                title="Amazing Facts That Blow Minds #Shorts",
                output_path=out_path,
                video_aspect="9:16",
            )
            self.assertEqual(res, out_path)
            self.assertTrue(os.path.exists(out_path))

            # Verify image dimensions
            with Image.open(out_path) as img:
                self.assertEqual(img.size, (1080, 1920))
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)

    @patch("moviepy.VideoFileClip")
    def test_generate_thumbnail_creates_landscape_image(self, mock_clip_cls):
        mock_clip = MagicMock()
        mock_clip.duration = 10.0
        dummy_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        dummy_frame[:, :] = [180, 80, 60]
        mock_clip.get_frame.return_value = dummy_frame
        mock_clip.__enter__.return_value = mock_clip
        mock_clip_cls.return_value = mock_clip

        out_path = os.path.abspath("test_dummy_landscape_thumb.jpg")
        try:
            res = generate_thumbnail(
                video_path=__file__,
                title="The Ultimate History Documentary",
                output_path=out_path,
                video_aspect="16:9",
            )
            self.assertEqual(res, out_path)
            self.assertTrue(os.path.exists(out_path))

            with Image.open(out_path) as img:
                self.assertEqual(img.size, (1280, 720))
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)

    def test_clean_title_with_turkish_characters(self):
        title = "Tarihin en gizemli sırları ve bilinmeyenleri #shorts"
        cleaned = _clean_title_for_thumbnail(title, max_words=5)
        # "ve" is a trailing conjunction and should be stripped, keeping first 4 punchy words
        self.assertEqual(cleaned, "TARİHİN EN GİZEMLİ SIRLARI")

    def test_clean_title_strips_leading_numbering(self):
        title = "1. Dünyanın En Büyük Sırrı"
        cleaned = _clean_title_for_thumbnail(title, max_words=5)
        self.assertEqual(cleaned, "DÜNYANIN EN BÜYÜK SIRRI")

    def test_score_frame_favors_vibrant_scene_over_black(self):
        from app.services.thumbnail import _score_frame
        black_frame = np.zeros((480, 854, 3), dtype=np.uint8)
        scene_frame = np.random.randint(60, 200, (480, 854, 3), dtype=np.uint8)

        black_score = _score_frame(black_frame)
        scene_score = _score_frame(scene_frame)
        self.assertLess(black_score, 0)
        self.assertGreater(scene_score, black_score)

    @patch("moviepy.VideoFileClip")
    @patch("os.path.exists")
    def test_clean_source_selection_prefers_combined_video(self, mock_exists, mock_clip_cls):
        mock_clip = MagicMock()
        mock_clip.duration = 4.0
        dummy_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        mock_clip.get_frame.return_value = dummy_frame
        mock_clip.__enter__.return_value = mock_clip
        mock_clip_cls.return_value = mock_clip

        # When os.path.exists is called, return True for combined-1.mp4 and final-1.mp4
        def side_effect(path):
            return True
        mock_exists.side_effect = side_effect

        out_path = os.path.abspath("test_dummy_clean_thumb.jpg")
        try:
            generate_thumbnail(
                video_path=os.path.abspath("test_task/final-1.mp4"),
                title="Clean Source Test",
                output_path=out_path,
                video_aspect="16:9",
            )
            # Verify VideoFileClip was initialized with combined-1.mp4, not final-1.mp4
            called_path = mock_clip_cls.call_args[0][0]
            self.assertIn("combined-1.mp4", called_path)
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)


if __name__ == "__main__":
    unittest.main()
