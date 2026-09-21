import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services.youtube_uploader import YouTubeUploaderService, upload_to_youtube


class TestYouTubeUploaderService(unittest.TestCase):
    def setUp(self):
        self.service = YouTubeUploaderService()

    @patch("app.services.youtube_uploader.config.app", {"youtube_enabled": False})
    def test_disabled_service_is_not_configured(self):
        self.assertFalse(self.service.is_configured())

    @patch("app.services.youtube_uploader.config.app", {"youtube_enabled": True})
    @patch("os.path.exists", return_value=False)
    def test_enabled_without_files_is_not_configured(self, mock_exists):
        self.assertFalse(self.service.is_configured())

    @patch("app.services.youtube_uploader.config.app", {"youtube_enabled": True})
    @patch("os.path.exists", return_value=True)
    def test_enabled_with_files_is_configured(self, mock_exists):
        self.assertTrue(self.service.is_configured())

    @patch("os.path.exists", return_value=False)
    def test_upload_missing_video_returns_error(self, mock_exists):
        result = self.service.upload_video("/missing/video.mp4", "Title")
        self.assertFalse(result["success"])
        self.assertIn("not found", result["error"])

    @patch("app.services.youtube_uploader.YouTubeUploaderService.get_credentials", return_value=None)
    @patch("os.path.exists", return_value=True)
    def test_upload_unauthenticated_returns_error(self, mock_exists, mock_creds):
        result = self.service.upload_video("/path/to/video.mp4", "Title")
        self.assertFalse(result["success"])
        self.assertIn("not authenticated", result["error"])

    @patch("app.services.youtube_uploader.YouTubeUploaderService.get_credentials")
    @patch("os.path.exists", return_value=True)
    @patch("googleapiclient.discovery.build")
    @patch("googleapiclient.http.MediaFileUpload")
    def test_upload_video_appends_shorts_tag_and_private_status(
        self, mock_media, mock_build, mock_exists, mock_creds
    ):
        mock_creds.return_value = MagicMock(valid=True)
        mock_youtube = MagicMock()
        mock_build.return_value = mock_youtube

        mock_insert = MagicMock()
        mock_insert.next_chunk.return_value = (None, {"id": "test_video_123"})
        mock_youtube.videos().insert.return_value = mock_insert

        result = self.service.upload_video(
            video_path="/path/to/short.mp4",
            title="My Amazing Short Video",
            description="Interesting description",
            tags=["AI", "Tech"],
            privacy_status="private",
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["video_id"], "test_video_123")
        self.assertEqual(result["video_url"], "https://www.youtube.com/shorts/test_video_123")
        self.assertEqual(result["privacy_status"], "private")

        # Verify insert arguments
        call_kwargs = mock_youtube.videos().insert.call_args[1]
        body = call_kwargs["body"]

        self.assertIn("#Shorts", body["snippet"]["title"])
        self.assertIn("Shorts", body["snippet"]["tags"])
        self.assertIn("YouTubeShorts", body["snippet"]["tags"])
        self.assertEqual(body["status"]["privacyStatus"], "private")
        self.assertFalse(body["status"]["selfDeclaredMadeForKids"])

    @patch("app.services.youtube_uploader.YouTubeUploaderService.get_credentials")
    @patch("os.path.exists", return_value=True)
    @patch("googleapiclient.discovery.build")
    @patch("googleapiclient.http.MediaFileUpload")
    def test_upload_helper_function(self, mock_media, mock_build, mock_exists, mock_creds):
        mock_creds.return_value = MagicMock(valid=True)
        mock_youtube = MagicMock()
        mock_build.return_value = mock_youtube
        mock_insert = MagicMock()
        mock_insert.next_chunk.return_value = (None, {"id": "short_999"})
        mock_youtube.videos().insert.return_value = mock_insert

        res = upload_to_youtube(
            video_path="/path/to/test.mp4",
            title="Helper Test",
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["video_id"], "short_999")

    @patch("builtins.open")
    @patch("os.makedirs")
    def test_save_client_secrets(self, mock_makedirs, mock_open):
        success = self.service.save_client_secrets("my_id_123", "my_secret_456")
        self.assertTrue(success)
        mock_open.assert_called_once()

    @patch("app.services.youtube_uploader.config.app", {"youtube_client_id": "test_id", "youtube_client_secret": "test_secret"})
    def test_client_credentials_properties(self):
        self.assertEqual(self.service.client_id, "test_id")
        self.assertEqual(self.service.client_secret, "test_secret")

    def test_cancel_browser_auth(self):
        self.service._auth_status = "running"
        mock_flow = MagicMock()
        mock_flow.redirect_uri = "http://localhost:12345/"
        self.service._auth_flow = mock_flow

        with patch("requests.get") as mock_get:
            self.service.cancel_browser_auth()
            mock_get.assert_called_once_with("http://localhost:12345/?error=access_denied", timeout=1)

        state = self.service.get_auth_state()
        self.assertEqual(state["status"], "cancelled")

    def test_reset_auth_state(self):
        self.service._auth_status = "timeout"
        self.service._auth_error = "some timeout"
        self.service.reset_auth_state()
        state = self.service.get_auth_state()
        self.assertEqual(state["status"], "idle")
        self.assertEqual(state["error"], "")

    @patch("app.services.youtube_uploader.YouTubeUploaderService.get_credentials")
    @patch("os.path.exists", return_value=True)
    @patch("googleapiclient.discovery.build")
    @patch("googleapiclient.http.MediaFileUpload")
    def test_upload_landscape_video(self, mock_media, mock_build, mock_exists, mock_creds):
        mock_creds.return_value = MagicMock(valid=True)
        mock_youtube = MagicMock()
        mock_build.return_value = mock_youtube
        mock_insert = MagicMock()
        mock_insert.next_chunk.return_value = (None, {"id": "vid_landscape_123"})
        mock_youtube.videos().insert.return_value = mock_insert

        res = self.service.upload_video(
            video_path="/path/to/landscape.mp4",
            title="My Great Horizontal Documentary #Shorts",
            tags=["History", "Shorts"],
            video_aspect="16:9",
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["video_type"], "video")
        self.assertEqual(res["video_url"], "https://www.youtube.com/watch?v=vid_landscape_123")
        self.assertNotIn("#Shorts", res["title"])

        # Check the insert body
        call_kwargs = mock_youtube.videos().insert.call_args[1]
        snippet = call_kwargs["body"]["snippet"]
        self.assertNotIn("#Shorts", snippet["title"])
        self.assertNotIn("Shorts", snippet["tags"])

    @patch("app.services.youtube_uploader.YouTubeUploaderService.get_credentials")
    @patch("os.path.exists", return_value=True)
    @patch("googleapiclient.discovery.build")
    @patch("googleapiclient.http.MediaFileUpload")
    def test_upload_portrait_shorts(self, mock_media, mock_build, mock_exists, mock_creds):
        mock_creds.return_value = MagicMock(valid=True)
        mock_youtube = MagicMock()
        mock_build.return_value = mock_youtube
        mock_insert = MagicMock()
        mock_insert.next_chunk.return_value = (None, {"id": "short_portrait_456"})
        mock_youtube.videos().insert.return_value = mock_insert

        res = self.service.upload_video(
            video_path="/path/to/portrait.mp4",
            title="Mindblowing Facts",
            tags=["Facts"],
            video_aspect="9:16",
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["video_type"], "shorts")
        self.assertEqual(res["video_url"], "https://www.youtube.com/shorts/short_portrait_456")
        self.assertIn("#Shorts", res["title"])

    @patch("app.services.youtube_uploader.YouTubeUploaderService.get_credentials")
    @patch("os.path.exists", return_value=True)
    @patch("googleapiclient.discovery.build")
    @patch("googleapiclient.http.MediaFileUpload")
    def test_set_thumbnail_success(self, mock_media, mock_build, mock_exists, mock_creds):
        mock_creds.return_value = MagicMock(valid=True)
        mock_youtube = MagicMock()
        mock_build.return_value = mock_youtube
        mock_set = MagicMock()
        mock_set.execute.return_value = {"items": [{"default": {"url": "http://thumb.jpg"}}]}
        mock_youtube.thumbnails().set.return_value = mock_set

        res = self.service.set_thumbnail("vid_123", "/path/to/thumb.jpg")
        self.assertTrue(res["success"])
        mock_youtube.thumbnails().set.assert_called_once_with(
            videoId="vid_123",
            media_body=mock_media.return_value
        )

    @patch("app.services.youtube_uploader.YouTubeUploaderService.get_credentials")
    @patch("os.path.exists", return_value=True)
    @patch("googleapiclient.discovery.build")
    @patch("googleapiclient.http.MediaFileUpload")
    def test_set_thumbnail_handles_forbidden_error(self, mock_media, mock_build, mock_exists, mock_creds):
        from googleapiclient.errors import HttpError
        from httplib2 import Response

        mock_creds.return_value = MagicMock(valid=True)
        mock_youtube = MagicMock()
        mock_build.return_value = mock_youtube

        resp = Response({"status": "403"})
        http_error = HttpError(resp, b'{"error":{"message":"User not verified for custom thumbnails"}}')
        mock_youtube.thumbnails().set.side_effect = http_error

        res = self.service.set_thumbnail("vid_123", "/path/to/thumb.jpg")
        self.assertFalse(res["success"])
        self.assertIn("User not verified", res["error"])


if __name__ == "__main__":
    unittest.main()
