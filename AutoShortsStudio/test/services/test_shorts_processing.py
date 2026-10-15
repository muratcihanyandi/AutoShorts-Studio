import os
import unittest
from unittest.mock import patch, MagicMock

from app.models.clip_schema import ClipItem, ClipPipelineSettings, ClipStatus, PlatformType, ClipTargetFormat
from app.services.clip_editor import ClipEditorService


class TestShortsProcessing(unittest.TestCase):
    def setUp(self):
        self.editor = ClipEditorService()

    def test_probe_video_properties_detects_vertical(self):
        with patch("subprocess.run") as mock_run:
            mock_res_v = MagicMock()
            mock_res_v.stdout = "1080x1920\n"
            mock_res_sub = MagicMock()
            mock_res_sub.stdout = "0\n"  # subtitle stream found
            mock_run.side_effect = [mock_res_v, mock_res_sub]

            with patch("os.path.exists", return_value=True):
                props = self.editor.probe_video_properties("fake_vertical.mp4")
                self.assertTrue(props["is_vertical"])
                self.assertEqual(props["width"], 1080)
                self.assertEqual(props["height"], 1920)
                self.assertTrue(props["has_subtitle_stream"])

    def test_probe_video_properties_detects_horizontal(self):
        with patch("subprocess.run") as mock_run:
            mock_res_v = MagicMock()
            mock_res_v.stdout = "1920x1080\n"
            mock_res_sub = MagicMock()
            mock_res_sub.stdout = ""  # no subtitle stream
            mock_run.side_effect = [mock_res_v, mock_res_sub]

            with patch("os.path.exists", return_value=True):
                props = self.editor.probe_video_properties("fake_horizontal.mp4")
                self.assertFalse(props["is_vertical"])
                self.assertEqual(props["width"], 1920)
                self.assertEqual(props["height"], 1080)
                self.assertFalse(props["has_subtitle_stream"])

    def test_native_vertical_video_uses_native_layout_and_skips_whisper_if_subtitles_present(self):
        clip = ClipItem(
            clip_id="test_vertical_clip",
            platform=PlatformType.YOUTUBE,
            channel_name="test_creator",
            title="Native Shorts Klip",
            url="https://youtube.com/shorts/123",
            raw_video_path="dummy_vert.mp4",
            status=ClipStatus.DOWNLOADED
        )
        settings = ClipPipelineSettings(
            video_layout="streamer_facecam",
            subtitle_enabled=True,
            skip_subtitles_if_detected=True
        )

        with (
            patch("os.path.exists", return_value=True),
            patch("os.path.getsize", return_value=500000),
            patch.object(self.editor, "probe_video_properties", return_value={"width": 1080, "height": 1920, "is_vertical": True, "has_subtitle_stream": True}),
            patch.object(self.editor, "generate_animated_ass_subtitles") as mock_gen_sub,
            patch("subprocess.run") as mock_run
        ):
            mock_run.return_value = MagicMock(returncode=0)
            out_path = self.editor.process_clip_to_shorts(clip, settings=settings)

            # Subtitle generator should NOT be called since subtitles already exist
            mock_gen_sub.assert_not_called()
            self.assertTrue(clip.is_vertical)
            self.assertTrue(clip.has_subtitles)

            # Check that ffmpeg cmd used native_vertical scale filter
            ffmpeg_call_cmd = mock_run.call_args[0][0]
            filter_str = None
            for idx, arg in enumerate(ffmpeg_call_cmd):
                if arg == "-filter_complex":
                    filter_str = ffmpeg_call_cmd[idx + 1]
                    break
            self.assertIsNotNone(filter_str)
            self.assertIn("scale=1080:1920", filter_str)
            self.assertNotIn("vstack", filter_str)

    def test_horizontal_video_uses_streamer_facecam_vstack(self):
        clip = ClipItem(
            clip_id="test_horizontal_clip",
            platform=PlatformType.TWITCH,
            channel_name="streamer",
            title="Yatay Yayin Klibi",
            url="https://twitch.tv/clip/456",
            raw_video_path="dummy_horiz.mp4",
            status=ClipStatus.DOWNLOADED
        )
        settings = ClipPipelineSettings(
            video_layout="streamer_facecam",
            facecam_position="bottom_left",
            subtitle_enabled=False
        )

        with (
            patch("os.path.exists", return_value=True),
            patch("os.path.getsize", return_value=500000),
            patch.object(self.editor, "probe_video_properties", return_value={"width": 1920, "height": 1080, "is_vertical": False, "has_subtitle_stream": False}),
            patch("subprocess.run") as mock_run
        ):
            mock_run.return_value = MagicMock(returncode=0)
            out_path = self.editor.process_clip_to_shorts(clip, settings=settings)

            self.assertFalse(clip.is_vertical)
            ffmpeg_call_cmd = mock_run.call_args[0][0]
            filter_str = None
            for idx, arg in enumerate(ffmpeg_call_cmd):
                if arg == "-filter_complex":
                    filter_str = ffmpeg_call_cmd[idx + 1]
                    break
            self.assertIsNotNone(filter_str)
            # Must contain facecam crop (1080:780) + bottom gameplay scale (1080:1140) + vstack
            self.assertIn("crop=1080:780[top_face]", filter_str)
            self.assertIn("crop=1080:1140[bot_game]", filter_str)
            self.assertIn("vstack=inputs=2", filter_str)


if __name__ == "__main__":
    unittest.main()
