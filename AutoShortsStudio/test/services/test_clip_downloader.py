import os
import sys
from datetime import datetime, timedelta

root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import unittest
from unittest.mock import patch

from app.models.clip_schema import (
    PlatformType, StreamerTarget, ClipTargetFormat, ClipSourceKind, ClipPipelineSettings,
    ClipItem, ClipStatus,
)
from app.services.clip_downloader import clip_downloader, ClipDownloaderService


class TestParseStreamerUrl(unittest.TestCase):
    def test_twitch_urls(self):
        self.assertEqual(
            ClipDownloaderService.parse_streamer_url("https://www.twitch.tv/Elraenn"),
            (PlatformType.TWITCH, "elraenn")
        )
        self.assertEqual(
            ClipDownloaderService.parse_streamer_url("twitch.tv/elraenn/videos"),
            (PlatformType.TWITCH, "elraenn")
        )

    def test_kick_url(self):
        self.assertEqual(
            ClipDownloaderService.parse_streamer_url("https://kick.com/xqc/"),
            (PlatformType.KICK, "xqc")
        )

    def test_youtube_urls(self):
        self.assertEqual(
            ClipDownloaderService.parse_streamer_url("https://www.youtube.com/@Raenee"),
            (PlatformType.YOUTUBE, "@Raenee")
        )
        self.assertEqual(
            ClipDownloaderService.parse_streamer_url("https://youtube.com/channel/UCabcdef1234567890"),
            (PlatformType.YOUTUBE, "UCabcdef1234567890")
        )

    def test_invalid_urls(self):
        for bad in ("", "https://example.com/foo", "elraenn", "https://www.twitch.tv/videos"):
            with self.assertRaises(ValueError, msg=f"Should reject: {bad}"):
                ClipDownloaderService.parse_streamer_url(bad)


class TestClipFetchFilters(unittest.TestCase):
    def test_twitch_clip_min_views_filter(self):
        entries = [
            {"id": "c1", "title": "Low views", "view_count": 50, "duration": 30},
            {"id": "c2", "title": "Popular", "view_count": 500, "duration": 45, "url": "https://clips.twitch.tv/c2"},
        ]
        with patch.object(clip_downloader, "_run_ytdlp_flat_json", return_value=entries):
            clips = clip_downloader.fetch_twitch_clips("elraenn", limit=10, min_views=100)

        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].clip_id, "twitch_c2")
        self.assertEqual(clips[0].source_key, "twitch_c2")
        self.assertEqual(clips[0].target_format, ClipTargetFormat.SHORTS)

    def test_youtube_search_duration_and_view_filter(self):
        entries = [
            {"id": "v_short", "title": "Too short", "view_count": 10000, "duration": 20},
            {"id": "v_long", "title": "Too long", "view_count": 10000, "duration": 4000},
            {"id": "v_lowviews", "title": "Low", "view_count": 10, "duration": 300},
            {"id": "v_ok", "title": "Good video", "view_count": 5000, "duration": 600,
             "channel": "Raenee", "url": "https://www.youtube.com/watch?v=v_ok"},
        ]
        with patch.object(clip_downloader, "_run_ytdlp_flat_json", return_value=entries):
            items = clip_downloader.fetch_youtube_videos("raenee clips", limit=10, min_views=100, max_duration_sec=1800)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].clip_id, "youtube_v_ok")
        self.assertEqual(items[0].platform, PlatformType.YOUTUBE)
        self.assertEqual(items[0].target_format, ClipTargetFormat.HORIZONTAL)
        self.assertEqual(items[0].source_kind, ClipSourceKind.YOUTUBE_VIDEO)
        self.assertEqual(items[0].channel_name, "Raenee")

    def test_vod_duration_cap(self):
        entries = [
            {"id": "111", "title": "Short VOD", "duration": 3600},
            {"id": "222", "title": "Huge VOD", "duration": 30000},
        ]
        with patch.object(clip_downloader, "_run_ytdlp_flat_json", return_value=entries):
            vods = clip_downloader.fetch_twitch_vods("elraenn", limit=5, max_duration_sec=7200)

        self.assertEqual(len(vods), 1)
        self.assertEqual(vods[0].clip_id, "twitch_vod_111")
        self.assertEqual(vods[0].source_kind, ClipSourceKind.VOD)
        self.assertEqual(vods[0].target_format, ClipTargetFormat.HORIZONTAL)


class TestStreamerFeed(unittest.TestCase):
    def _make_streamer(self, formats, platform=PlatformType.TWITCH):
        return StreamerTarget(
            platform=platform,
            channel_name="elraenn",
            display_name="Elraenn",
            formats=formats,
        )

    def test_clips_create_shorts_and_horizontal_twins(self):
        """Iki format seciliyse her klip icin shorts + yatay ikizi uretilmeli (ayni source_key)."""
        fake_clip = ClipItem(
            clip_id="twitch_abc",
            source_key="twitch_abc",
            platform=PlatformType.TWITCH,
            channel_name="elraenn",
            title="Funny moment",
            url="https://clips.twitch.tv/abc",
            duration=30,
            view_count=1000,
            status=ClipStatus.DISCOVERED,
        )

        settings = ClipPipelineSettings(youtube_search_enabled=False)
        streamer = self._make_streamer([ClipTargetFormat.SHORTS, ClipTargetFormat.HORIZONTAL])

        with patch.object(clip_downloader, "fetch_twitch_clips", return_value=[fake_clip]), \
             patch.object(clip_downloader, "fetch_twitch_vods", return_value=[]):
            items = clip_downloader.fetch_streamer_feed(streamer, settings)

        self.assertEqual(len(items), 2)
        by_id = {i.clip_id: i for i in items}
        self.assertIn("twitch_abc", by_id)
        self.assertIn("twitch_abc__h", by_id)
        self.assertEqual(by_id["twitch_abc"].target_format, ClipTargetFormat.SHORTS)
        self.assertEqual(by_id["twitch_abc__h"].target_format, ClipTargetFormat.HORIZONTAL)
        # Ikiz ayni kaynagi paylasir -> tek indirme
        self.assertEqual(by_id["twitch_abc"].source_key, by_id["twitch_abc__h"].source_key)

    def test_shorts_only_skips_vods_and_youtube(self):
        fake_clip = ClipItem(
            clip_id="twitch_xyz",
            source_key="twitch_xyz",
            platform=PlatformType.TWITCH,
            channel_name="elraenn",
            title="Moment",
            url="https://clips.twitch.tv/xyz",
            duration=20,
            view_count=999,
            status=ClipStatus.DISCOVERED,
        )
        settings = ClipPipelineSettings()
        streamer = self._make_streamer([ClipTargetFormat.SHORTS])

        with patch.object(clip_downloader, "fetch_twitch_clips", return_value=[fake_clip]), \
             patch.object(clip_downloader, "fetch_twitch_vods") as mock_vods, \
             patch.object(clip_downloader, "fetch_youtube_videos") as mock_yt:
            items = clip_downloader.fetch_streamer_feed(streamer, settings)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].target_format, ClipTargetFormat.SHORTS)
        mock_vods.assert_not_called()
        mock_yt.assert_not_called()

    def test_youtube_streamer_fetches_channel_videos(self):
        fake_video = ClipItem(
            clip_id="youtube_vid1",
            source_key="youtube_vid1",
            platform=PlatformType.YOUTUBE,
            channel_name="Raenee",
            title="Best of",
            url="https://www.youtube.com/watch?v=vid1",
            duration=600,
            view_count=50000,
            target_format=ClipTargetFormat.HORIZONTAL,
            source_kind=ClipSourceKind.YOUTUBE_VIDEO,
            status=ClipStatus.DISCOVERED,
        )
        streamer = self._make_streamer([ClipTargetFormat.SHORTS, ClipTargetFormat.HORIZONTAL],
                                       platform=PlatformType.YOUTUBE)
        streamer.channel_name = "@Raenee"

        settings = ClipPipelineSettings()
        with patch.object(clip_downloader, "fetch_youtube_channel_videos", return_value=[fake_video]) as mock_ch:
            items = clip_downloader.fetch_streamer_feed(streamer, settings)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].target_format, ClipTargetFormat.HORIZONTAL)
        mock_ch.assert_called_once()

    def test_fan_channel_queries_triggers_youtube_searches(self):
        fake_yt = ClipItem(
            clip_id="youtube_fan1",
            source_key="youtube_fan1",
            platform=PlatformType.YOUTUBE,
            channel_name="Rraenee Fan",
            title="Rraenee En Iyi Anlar",
            url="https://youtube.com/watch?v=fan1",
            duration=120,
            view_count=20000,
            status=ClipStatus.DISCOVERED,
        )
        streamer = self._make_streamer([ClipTargetFormat.SHORTS], platform=PlatformType.TWITCH)
        streamer.fan_channel_queries = ["rraenee kesitleri", "rraenee klipleri"]

        settings = ClipPipelineSettings(enable_twitch_discovery=False)
        with patch.object(clip_downloader, "fetch_youtube_videos", return_value=[fake_yt]) as mock_yt:
            items = clip_downloader.fetch_streamer_feed(streamer, settings)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].target_format, ClipTargetFormat.SHORTS)
        self.assertEqual(mock_yt.call_count, 2)

    def test_vods_create_shorts_twin_when_both_formats_selected(self):
        fake_vod = ClipItem(
            clip_id="twitch_vod_999",
            source_key="twitch_vod_999",
            platform=PlatformType.TWITCH,
            channel_name="elraenn",
            title="Efsane Yayin",
            url="https://twitch.tv/videos/999",
            duration=3600,
            view_count=5000,
            target_format=ClipTargetFormat.HORIZONTAL,
            source_kind=ClipSourceKind.VOD,
            status=ClipStatus.DISCOVERED,
        )
        streamer = self._make_streamer([ClipTargetFormat.SHORTS, ClipTargetFormat.HORIZONTAL])
        settings = ClipPipelineSettings(youtube_search_enabled=False)

        with patch.object(clip_downloader, "fetch_twitch_clips", return_value=[]), \
             patch.object(clip_downloader, "fetch_twitch_vods", return_value=[fake_vod]):
            items = clip_downloader.fetch_streamer_feed(streamer, settings)

        self.assertEqual(len(items), 2)
        by_id = {i.clip_id: i for i in items}
        self.assertIn("twitch_vod_999", by_id)
        self.assertIn("twitch_vod_999__s", by_id)
        self.assertEqual(by_id["twitch_vod_999"].target_format, ClipTargetFormat.HORIZONTAL)
        self.assertEqual(by_id["twitch_vod_999__s"].target_format, ClipTargetFormat.SHORTS)


class TestDownloadDedupe(unittest.TestCase):
    def test_existing_raw_file_short_circuits(self):
        """Ayni source_key icin dosya zaten varsa yeniden indirilmemeli."""
        source_key = "test_dedupe_unique_123"
        out_path = os.path.join(clip_downloader.raw_dir, f"{source_key}.mp4")
        with open(out_path, "wb") as f:
            f.write(b"\x00" * 4096)

        try:
            clip = ClipItem(
                clip_id="test_dedupe_row",
                source_key=source_key,
                platform=PlatformType.TWITCH,
                channel_name="test",
                title="Dedupe test",
                url="https://example.com/never-downloaded",
                status=ClipStatus.DISCOVERED,
            )
            with patch("app.services.clip_downloader.subprocess.run") as mock_run:
                result = clip_downloader.download_clip(clip)
                mock_run.assert_not_called()

            self.assertEqual(result, out_path)
            self.assertEqual(clip.status, ClipStatus.DOWNLOADED)
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)


class TestFiltersAndRecency(unittest.TestCase):
    def test_title_blacklist_matches_keywords(self):
        blacklist = "açıklama, sorun, duyuru, bilgilendirme"
        self.assertTrue(clip_downloader._is_title_blacklisted("Kick'te sorun yaşayanlar için açıklama", blacklist))
        self.assertTrue(clip_downloader._is_title_blacklisted("Önemli Duyuru ve Bilgilendirme", blacklist))
        self.assertFalse(clip_downloader._is_title_blacklisted("Rraenee Komik Anlar CS2", blacklist))

    def test_recency_and_viral_filter(self):
        # 1. Taze video (3 gun once) -> kabul
        fresh_date = (datetime.utcnow() - timedelta(days=3)).strftime("%Y%m%d")
        self.assertTrue(clip_downloader._is_video_fresh_or_viral(
            {"upload_date": fresh_date}, view_count=200, max_age_days=7, viral_threshold=50000
        ))

        # 2. Eski video (20 gun once) ama cok viral (100.000 izlenme) -> kabul
        old_date = (datetime.utcnow() - timedelta(days=20)).strftime("%Y%m%d")
        self.assertTrue(clip_downloader._is_video_fresh_or_viral(
            {"upload_date": old_date}, view_count=100000, max_age_days=7, viral_threshold=50000
        ))

        # 3. Eski video (20 gun once) ve viral degil (5.000 izlenme) -> red
        self.assertFalse(clip_downloader._is_video_fresh_or_viral(
            {"upload_date": old_date}, view_count=5000, max_age_days=7, viral_threshold=50000
        ))


if __name__ == "__main__":
    unittest.main()
