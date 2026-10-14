import os
import sys

root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import unittest
from datetime import datetime
from app.models.clip_schema import (
    PlatformType, StreamerTarget, ClipItem, ClipStatus, ClipPipelineSettings,
    ClipTargetFormat, ClipSourceKind,
)
from app.services.clip_db import ClipDatabase


class TestClipDatabase(unittest.TestCase):
    def setUp(self):
        import uuid
        self.test_db_path = f"test_clips_{uuid.uuid4().hex[:8]}.db"
        self.db = ClipDatabase(db_path=self.test_db_path)

    def tearDown(self):
        import time
        time.sleep(0.01)
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except Exception:
                pass

    def test_streamer_crud(self):
        streamer = StreamerTarget(
            platform=PlatformType.TWITCH,
            channel_name="elraenn",
            display_name="Elraenn",
            min_views=500
        )
        s_id = self.db.add_streamer(streamer)
        self.assertIsNotNone(s_id)

        streamers = self.db.list_streamers()
        self.assertEqual(len(streamers), 1)
        self.assertEqual(streamers[0].channel_name, "elraenn")

        self.db.update_streamer_checked("twitch", "elraenn")
        streamers_updated = self.db.list_streamers()
        self.assertIsNotNone(streamers_updated[0].last_checked_at)

        self.db.delete_streamer(s_id)
        self.assertEqual(len(self.db.list_streamers()), 0)

    def test_clip_crud(self):
        clip = ClipItem(
            clip_id="clip_test_123",
            platform=PlatformType.KICK,
            channel_name="xqc",
            title="Crazy Moment",
            url="https://kick.com/xqc/clip_123",
            duration=35.0,
            view_count=1200,
            status=ClipStatus.DISCOVERED
        )
        self.db.save_clip(clip)
        self.assertTrue(self.db.is_clip_processed("clip_test_123"))

        retrieved = self.db.get_clip_by_id("clip_test_123")
        self.assertEqual(retrieved.title, "Crazy Moment")

        self.db.update_clip_status("clip_test_123", ClipStatus.READY, processed_video_path="/path/test.mp4")
        updated = self.db.get_clip_by_id("clip_test_123")
        self.assertEqual(updated.status, ClipStatus.READY)
        self.assertEqual(updated.processed_video_path, "/path/test.mp4")

    def test_settings(self):
        default_s = self.db.get_settings()
        self.assertFalse(default_s.auto_upload)

        default_s.auto_upload = True
        default_s.min_view_threshold = 250
        self.db.save_settings(default_s)

        loaded = self.db.get_settings()
        self.assertTrue(loaded.auto_upload)
        self.assertEqual(loaded.min_view_threshold, 250)

    def test_quota_and_stock_queries(self):
        """Gunluk kota sayimi, stok secimleri ve oncelik siralamasi dogru calismali."""
        shorts_ready = ClipItem(
            clip_id="q_shorts_ready", platform=PlatformType.TWITCH, channel_name="a",
            title="shorts ready", url="u1", target_format=ClipTargetFormat.SHORTS,
            status=ClipStatus.READY, priority=0, hype_score=0.4
        )
        shorts_priority = ClipItem(
            clip_id="q_shorts_prio", platform=PlatformType.TWITCH, channel_name="a",
            title="shorts prio", url="u2", target_format=ClipTargetFormat.SHORTS,
            status=ClipStatus.READY, priority=50, hype_score=0.1
        )
        horiz_ready = ClipItem(
            clip_id="q_horiz_ready", platform=PlatformType.TWITCH, channel_name="a",
            title="horiz ready", url="u3", target_format=ClipTargetFormat.HORIZONTAL,
            status=ClipStatus.READY
        )
        discovered_low = ClipItem(
            clip_id="q_disc_low", platform=PlatformType.TWITCH, channel_name="a",
            title="low views", url="u4", target_format=ClipTargetFormat.SHORTS,
            status=ClipStatus.DISCOVERED, view_count=10
        )
        discovered_high = ClipItem(
            clip_id="q_disc_high", platform=PlatformType.TWITCH, channel_name="a",
            title="high views", url="u5", target_format=ClipTargetFormat.SHORTS,
            status=ClipStatus.DISCOVERED, view_count=9999
        )
        published_today = ClipItem(
            clip_id="q_published", platform=PlatformType.TWITCH, channel_name="a",
            title="published", url="u6", target_format=ClipTargetFormat.SHORTS,
            status=ClipStatus.PUBLISHED, published_at=datetime.now()
        )
        for c in (shorts_ready, shorts_priority, horiz_ready, discovered_low, discovered_high, published_today):
            self.db.save_clip(c)

        # Bugun yayinlanan shorts sayisi
        self.assertEqual(self.db.published_today_count(ClipTargetFormat.SHORTS.value), 1)
        self.assertEqual(self.db.published_today_count(ClipTargetFormat.HORIZONTAL.value), 0)

        # Stok sayimlari (format filtresi)
        self.assertEqual(self.db.count_clips(status=ClipStatus.READY.value,
                                             target_format=ClipTargetFormat.SHORTS.value), 2)
        self.assertEqual(self.db.count_clips(status=ClipStatus.READY.value,
                                             target_format=ClipTargetFormat.HORIZONTAL.value), 1)

        # Siradaki islenecek: en cok izlenen DISCOVERED
        next_disc = self.db.next_discovered_clip(ClipTargetFormat.SHORTS.value)
        self.assertEqual(next_disc.clip_id, "q_disc_high")

        # Siradaki yayinlanacak: oncelik > hype
        next_ready = self.db.next_ready_clip(ClipTargetFormat.SHORTS.value)
        self.assertEqual(next_ready.clip_id, "q_shorts_prio")

        # Yatay stokta tek aday
        self.assertEqual(self.db.next_ready_clip(ClipTargetFormat.HORIZONTAL.value).clip_id, "q_horiz_ready")

    def test_source_siblings_pending(self):
        """Ayni kaynagi paylasan bekleyen ikiz varsa ham dosya silinmemeli (True donmeli)."""
        parent = ClipItem(
            clip_id="sib_a", source_key="sib_src", platform=PlatformType.TWITCH,
            channel_name="a", title="a", url="u", status=ClipStatus.READY
        )
        twin = ClipItem(
            clip_id="sib_b", source_key="sib_src", platform=PlatformType.TWITCH,
            channel_name="a", title="b", url="u", status=ClipStatus.DISCOVERED
        )
        self.db.save_clip(parent)
        self.db.save_clip(twin)

        self.assertTrue(self.db.source_siblings_pending("sib_src", exclude_clip_id="sib_a"))

        # Ikiz de terminal duruma gecince pending kalmamali
        self.db.update_clip_status("sib_b", ClipStatus.READY)
        self.assertFalse(self.db.source_siblings_pending("sib_src", exclude_clip_id="sib_a"))

    def test_clip_failed_recently(self):
        failed_clip = ClipItem(
            clip_id="fail_recent", platform=PlatformType.KICK, channel_name="a",
            title="fail", url="u", status=ClipStatus.FAILED
        )
        ready_clip = ClipItem(
            clip_id="ok_clip", platform=PlatformType.KICK, channel_name="a",
            title="ok", url="u", status=ClipStatus.READY
        )
        self.db.save_clip(failed_clip)
        self.db.save_clip(ready_clip)

        self.assertTrue(self.db.clip_failed_recently("fail_recent", hours=6))
        self.assertFalse(self.db.clip_failed_recently("ok_clip", hours=6))

    def test_legacy_db_migration(self):
        """Eski semali clips.db dosyasi yeni kolonlarla otomatik guncellenmeli."""
        import sqlite3
        legacy_path = f"test_legacy_{self.test_db_path}"
        conn = sqlite3.connect(legacy_path)
        conn.executescript("""
            CREATE TABLE streamers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                display_name TEXT,
                is_active INTEGER DEFAULT 1,
                min_views INTEGER DEFAULT 100,
                max_duration_seconds INTEGER DEFAULT 60,
                check_interval_minutes INTEGER DEFAULT 60,
                last_checked_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(platform, channel_name)
            );
            CREATE TABLE clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clip_id TEXT NOT NULL UNIQUE,
                platform TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                duration REAL DEFAULT 0,
                view_count INTEGER DEFAULT 0,
                thumbnail_url TEXT,
                created_at_platform TEXT,
                status TEXT NOT NULL,
                raw_video_path TEXT,
                processed_video_path TEXT,
                subtitle_path TEXT,
                hype_score REAL DEFAULT 0,
                cut_start_sec REAL DEFAULT 0,
                cut_end_sec REAL DEFAULT 0,
                youtube_title TEXT,
                youtube_description TEXT,
                youtube_tags TEXT,
                youtube_video_id TEXT,
                youtube_status TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO clips (clip_id, platform, channel_name, title, url, status, created_at, updated_at)
            VALUES ('legacy_1', 'twitch', 'oldchannel', 'Old clip', 'http://x', 'discovered', '2026-01-01T00:00:00', '2026-01-01T00:00:00');
        """)
        conn.commit()
        conn.close()

        try:
            migrated_db = ClipDatabase(db_path=legacy_path)
            # Eski kayit yeni alanlarin varsayilanlariyla okunabilmeli
            legacy_clip = migrated_db.get_clip_by_id("legacy_1")
            self.assertIsNotNone(legacy_clip)
            self.assertEqual(legacy_clip.target_format, ClipTargetFormat.SHORTS)
            self.assertEqual(legacy_clip.source_kind, ClipSourceKind.CLIP)
            self.assertEqual(legacy_clip.source_key, "legacy_1")
            self.assertIsNone(legacy_clip.published_at)
            self.assertEqual(legacy_clip.priority, 0)

            # Streamer migrasyonu: formats varsayilan iki format olmali
            streamer = StreamerTarget(platform=PlatformType.TWITCH, channel_name="oldchannel")
            migrated_db.add_streamer(streamer)
            streamers = migrated_db.list_streamers()
            self.assertEqual(len(streamers), 1)
            self.assertEqual(streamers[0].formats, [ClipTargetFormat.SHORTS, ClipTargetFormat.HORIZONTAL])
        finally:
            if os.path.exists(legacy_path):
                try:
                    os.remove(legacy_path)
                except Exception:
                    pass

    def test_openshorts_fields(self):
        """OpenShorts ozel alanlarinin (fan_channel_queries, hook_text, viral_score) kaydi ve okunmasi."""
        streamer = StreamerTarget(
            platform=PlatformType.YOUTUBE,
            channel_name="rraenee",
            display_name="Rraenee",
            fan_channel_queries=["rraenee kesitleri", "rraenee klipleri"]
        )
        self.db.add_streamer(streamer)
        streamers = self.db.list_streamers()
        self.assertEqual(len(streamers), 1)
        self.assertEqual(streamers[0].fan_channel_queries, ["rraenee kesitleri", "rraenee klipleri"])

        clip = ClipItem(
            clip_id="os_test_1",
            platform=PlatformType.YOUTUBE,
            channel_name="rraenee",
            title="Rraenee Komik An",
            url="http://youtube.com/watch?v=123",
            hook_text="Rraenee Sinir Krizi Geçirdi 💀",
            viral_score=9,
            status=ClipStatus.READY
        )
        self.db.save_clip(clip)
        loaded = self.db.get_clip_by_id("os_test_1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.hook_text, "Rraenee Sinir Krizi Geçirdi 💀")
        self.assertEqual(loaded.viral_score, 9)

        # update_clip_status ile guncellenebilmeli
        self.db.update_clip_status("os_test_1", ClipStatus.READY, hook_text="Yeni Hook 🚀", viral_score=10)
        updated = self.db.get_clip_by_id("os_test_1")
        self.assertEqual(updated.hook_text, "Yeni Hook 🚀")
        self.assertEqual(updated.viral_score, 10)

    def test_recover_interrupted_clips(self):
        """Yarim kalan downloading, editing, analyzing klipleri kurtarilmali."""
        # 1. editing durumunda ve raw dosyasi var -> DOWNLOADED olmali
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"x" * 20000)
            temp_raw = f.name

        try:
            interrupted = ClipItem(
                clip_id="interrupted_edit", platform=PlatformType.YOUTUBE, channel_name="test",
                title="test", url="http://test", status=ClipStatus.EDITING,
                raw_video_path=temp_raw
            )
            interrupted_missing = ClipItem(
                clip_id="interrupted_download", platform=PlatformType.YOUTUBE, channel_name="test",
                title="test", url="http://test", status=ClipStatus.DOWNLOADING,
                raw_video_path="/non/existent/path.mp4"
            )
            self.db.save_clip(interrupted)
            self.db.save_clip(interrupted_missing)

            stats = self.db.recover_interrupted_clips()
            self.assertEqual(stats["recovered_downloaded"], 1)
            self.assertEqual(stats["recovered_discovered"], 1)

            c1 = self.db.get_clip_by_id("interrupted_edit")
            self.assertEqual(c1.status, ClipStatus.DOWNLOADED)

            c2 = self.db.get_clip_by_id("interrupted_download")
            self.assertEqual(c2.status, ClipStatus.DISCOVERED)

            # next_discovered_clip oncelikle DOWNLOADED olanı almali
            next_clip = self.db.next_discovered_clip(ClipTargetFormat.SHORTS.value)
            self.assertEqual(next_clip.clip_id, "interrupted_edit")
        finally:
            if os.path.exists(temp_raw):
                os.remove(temp_raw)


if __name__ == "__main__":
    unittest.main()

