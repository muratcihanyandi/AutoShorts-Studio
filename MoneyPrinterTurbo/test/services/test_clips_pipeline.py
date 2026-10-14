import os
import sys

root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import unittest
from fastapi.testclient import TestClient

from app.asgi import app
from app.models.clip_schema import PlatformType, StreamerTarget, ClipStatus, ClipItem
from app.services.clip_db import clip_db
from app.services.clip_scheduler import clip_scheduler
from app.services.hype_detector import hype_detector
from app.services.clip_editor import clip_editor


class TestClipsIntegration(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_fastapi_clips_status(self):
        """FastAPI status endpoint'inin 200 dondugunu ve beklenen anahtarlari icerdigini dogrula."""
        response = self.client.get("/api/v1/clips/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("scheduler_running", data)
        self.assertIn("recent_logs", data)
        self.assertIn("settings", data)

    def test_fastapi_streamers_api(self):
        """API uzerinden yayinci ekleme ve listeleme akisini test et."""
        payload = {
            "platform": "twitch",
            "channel_name": "test_streamer_integration",
            "display_name": "Test Streamer",
            "min_views": 250
        }
        res_post = self.client.post("/api/v1/clips/streamers", json=payload)
        self.assertEqual(res_post.status_code, 200)
        streamer_id = res_post.json()["streamer_id"]

        # Listele
        res_get = self.client.get("/api/v1/clips/streamers")
        self.assertEqual(res_get.status_code, 200)
        streamers = res_get.json()
        found = any(s["channel_name"] == "test_streamer_integration" for s in streamers)
        self.assertTrue(found)

        # Sil
        res_del = self.client.delete(f"/api/v1/clips/streamers/{streamer_id}")
        self.assertEqual(res_del.status_code, 200)

    def test_fastapi_settings_api(self):
        """API uzerinden ayarlari okuma ve guncelleme test et."""
        res = self.client.get("/api/v1/clips/settings")
        self.assertEqual(res.status_code, 200)
        current_settings = res.json()

        current_settings["min_view_threshold"] = 333
        res_post = self.client.post("/api/v1/clips/settings", json=current_settings)
        self.assertEqual(res_post.status_code, 200)

        # Tekrar oku
        res2 = self.client.get("/api/v1/clips/settings")
        self.assertEqual(res2.json()["min_view_threshold"], 333)

    def test_scheduler_lifecycle(self):
        """Zamanlayiciyi baslatma ve durdurma yasam dongusunu test et."""
        from unittest.mock import patch
        from app.services.clip_downloader import clip_downloader

        self.assertFalse(clip_scheduler.is_running)
        # Thread'in ilk turu aga ve gercek stoga takilmasin; stop() icinde temiz kapansin
        with patch.object(clip_downloader, "fetch_streamer_feed", return_value=[]), \
             patch.object(clip_db, "next_discovered_clip", return_value=None), \
             patch.object(clip_db, "next_ready_clip", return_value=None):
            clip_scheduler.start()
            self.assertTrue(clip_scheduler.is_running)
            clip_scheduler.stop()
        self.assertFalse(clip_scheduler.is_running)

    def test_fastapi_stock_and_quota(self):
        """Stok listesi ve gunluk kota endpoint'leri beklenen yapida donmeli."""
        res_stock = self.client.get("/api/v1/clips/stock?target_format=shorts&limit=5")
        self.assertEqual(res_stock.status_code, 200)
        stock = res_stock.json()
        self.assertIn("items", stock)
        self.assertIn("count", stock)

        res_quota = self.client.get("/api/v1/clips/quota")
        self.assertEqual(res_quota.status_code, 200)
        quota = res_quota.json()
        self.assertIn("quota", quota)
        self.assertIn("within_publish_window", quota)
        self.assertIn("shorts", quota["quota"])
        self.assertIn("horizontal", quota["quota"])
        self.assertIn("daily_limit", quota["quota"]["shorts"])

    def test_fastapi_streamer_url_add(self):
        """URL ile yayinci ekleme platform/kanal adini otomatik cozmeli."""
        payload = {
            "channel_url": "https://www.twitch.tv/url_streamer_test",
            "formats": ["shorts"]
        }
        res = self.client.post("/api/v1/clips/streamers", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["platform"], "twitch")
        self.assertEqual(data["channel_name"], "url_streamer_test")
        self.assertEqual(data["formats"], ["shorts"])

        # Gecersiz URL 400 donmeli
        res_bad = self.client.post("/api/v1/clips/streamers", json={"channel_url": "https://example.com/x"})
        self.assertEqual(res_bad.status_code, 400)

        # Temizlik
        self.client.delete(f"/api/v1/clips/streamers/{data['streamer_id']}")

    def test_hype_detector_audio_fallback(self):
        """Hype detector servisi ornek olmayan bir dosyada guvenli sekilde fallback deger dondurur mu?"""
        start_sec, end_sec, score = hype_detector.analyze_audio_volume_peaks("non_existing.mp4", target_window_sec=30.0)
        self.assertGreaterEqual(start_sec, 0.0)
        self.assertGreater(end_sec, start_sec)
        self.assertGreaterEqual(score, 0.0)


if __name__ == "__main__":
    unittest.main()
